"""
title: Chat Inspector
description: Inspect live chat context estimates, response usage, and saved model and tool settings.
author: Muhammad Sohail
version: 0.2.0
license: MIT
required_open_webui_version: 0.13.3
"""

# Shared Assets Protocol and live reload blocks are by Classic298 (BSD-3-Clause),
# copied byte-for-byte from the Chat Gallery distribution. See its attribution.
import json as _json

from typing import Literal

from pydantic import BaseModel, Field


import asyncio
import logging
import re
import uuid
from typing import Any, Optional

from open_webui.utils.json_codec import JSONCodec

# ===========================================================================
# Live reload broadcast
# ---------------------------------------------------------------------------
# Each load publishes over Redis; a peer whose cached code differs from the
# database re-executes it. register(app) is yours and must be idempotent; gate
# each producer on reload_active(app).
# ===========================================================================
RELOAD_STATE_ATTR = "_owui_live_reload"  # {key: {active, exec_id, listener, impl}}
# Bump when the listener changes: a newer copy replaces a running listener.
RELOAD_IMPL_VERSION = 2
RELOAD_EXEC_ID = uuid.uuid4().hex  # new on every exec of this source
# Open WebUI runs each function as module "function_<id>_<hex>" (older: "function_<id>").
RELOAD_FUNCTION_ID = re.sub(r"_[0-9a-f]{32}$", "", __name__.removeprefix("function_"))

reload_log = logging.getLogger("owui-live-reload")
# asyncio only keeps weak references to tasks.
reload_tasks: set = set()


def reload_state(app: Any) -> dict:
    registry = getattr(app.state, RELOAD_STATE_ATTR, None)
    if not isinstance(registry, dict):
        registry = {}
        app.state.__setattr__(RELOAD_STATE_ATTR, registry)
    return registry.setdefault(ASSET_KEY, {})


def reload_active(app: Any) -> bool:
    return bool(reload_state(app).get("active"))


def reload_channel(key: Optional[str] = None) -> str:
    from open_webui.env import REDIS_KEY_PREFIX

    return f"{REDIS_KEY_PREFIX}:live-reload:{key or ASSET_KEY}"


def reload_mark_loaded(app: Any) -> None:
    register(app)
    state = reload_state(app)
    state["active"] = True
    state["exec_id"] = RELOAD_EXEC_ID


async def reload_publish(app: Any, active: bool, key: Optional[str] = None) -> None:
    redis = getattr(app.state, "redis", None)
    if redis is None:
        return
    try:
        await redis.publish(reload_channel(key), JSONCodec.dumps({"active": active}))
    except Exception as e:
        reload_log.warning("[%s] publish failed: %s", ASSET_KEY, type(e).__name__)


async def reload_ensure_listener(app: Any) -> None:
    state = reload_state(app)
    listener = state.get("listener")
    if listener is not None:
        # True: an older copy is still subscribing; it gets replaced on a later event.
        if listener is True or state.get("impl", 0) >= RELOAD_IMPL_VERSION:
            return
        listener.cancel()
    redis = getattr(app.state, "redis", None)
    if redis is None:
        return

    from types import SimpleNamespace

    from open_webui.models.functions import Functions
    from open_webui.utils.plugin import (
        get_function_module_from_cache,
        get_functions_cache,
    )

    async def listen():
        delay = 1
        while True:
            pubsub = redis.pubsub()
            connected_at = None
            try:
                await pubsub.subscribe(reload_channel())
                connected_at = asyncio.get_running_loop().time()
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    if not JSONCodec.loads(message["data"])["active"]:
                        state["active"] = False
                        state["disabled"] = True
                        continue
                    state["disabled"] = False
                    was_active = state.get("active")
                    # Enable broadcasts arrive before is_active commits; bootstrap trusts this flag.
                    state["active"] = True
                    context = SimpleNamespace(app=app)
                    if not was_active:
                        # Force a fresh exec so its bootstrap registers again.
                        get_functions_cache(context).pop(RELOAD_FUNCTION_ID, None)
                    try:
                        function_module, _, _ = await get_function_module_from_cache(
                            context, RELOAD_FUNCTION_ID
                        )
                        # Peers never see a valves save; re-read them like Open WebUI's dispatch does.
                        if hasattr(function_module, "Valves"):
                            valves = await Functions.get_function_valves_by_id(
                                RELOAD_FUNCTION_ID
                            )
                            function_module.valves = function_module.Valves(
                                **(valves or {})
                            )
                    except Exception as e:
                        reload_log.warning(
                            "[%s] reload from db failed: %s",
                            ASSET_KEY,
                            type(e).__name__,
                        )
            except Exception as e:
                # Only a link that held for a minute starts a new outage; flaps don't.
                now = asyncio.get_running_loop().time()
                if connected_at is not None and now - connected_at > 60:
                    delay = 1
                if delay == 1:
                    reload_log.warning(
                        "[%s] listener failed: %s, retrying until Redis is back",
                        ASSET_KEY,
                        type(e).__name__,
                    )
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    state["listener"] = asyncio.create_task(listen())
    state["impl"] = RELOAD_IMPL_VERSION


def reload_bootstrap() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def bootstrap():
        try:
            from open_webui.main import app
            from open_webui.models.functions import Functions

            own = await Functions.get_function_by_id(RELOAD_FUNCTION_ID)
            # Loading a disabled function (e.g. for its valves) must not switch it on.
            if own is None or not (own.is_active or reload_active(app)):
                return
            reload_mark_loaded(app)
            await reload_ensure_listener(app)
            await reload_publish(app, True)
        except Exception as e:
            reload_log.warning("[%s] bootstrap failed: %s", ASSET_KEY, type(e).__name__)

    task = loop.create_task(bootstrap())
    reload_tasks.add(task)
    task.add_done_callback(reload_tasks.discard)


async def reload_on_event(
    app: Any, event: Optional[dict], event_name: Optional[str]
) -> None:
    await reload_ensure_listener(app)
    state = reload_state(app)

    subject_id = ((event or {}).get("subject") or {}).get("id")
    asset_withdraw_deleted(app, event, event_name)
    if event_name == "function.deleted" and subject_id:
        # Its own listeners on other containers switch its fragment off.
        await reload_publish(app, False, f"function_{subject_id}")

    is_own = subject_id == RELOAD_FUNCTION_ID
    if is_own and event_name == "function.disable_started":
        state["active"] = False
        state["disabled"] = True
        await reload_publish(app, False)
        return
    if is_own and event_name == "function.enable_started":
        state["disabled"] = False
    elif state.get("disabled"):
        # Events read the function list before is_active commits and still arrive.
        return
    if is_own and event_name == "function.valves_updated":
        await reload_publish(app, True)

    current = state.get("active") and state.get("exec_id") == RELOAD_EXEC_ID
    if current and event_name != "system.startup.completed":
        return
    reload_mark_loaded(app)
    await reload_publish(app, True)


# =========================== end live reload block =========================


import hashlib
import re
from pathlib import Path
from typing import Any

# ===========================================================================
# Shared static-asset registry
# --- KEEP BYTE-IDENTICAL IN EVERY PLUGIN THAT USES IT ----------------------
# ---------------------------------------------------------------------------
# Each plugin publishes a fragment into one app.state registry and the route
# composes them per request, so load order is irrelevant. Per-process: each
# container serves what it has loaded.
#
# Contract:
#   * ASSET_REGISTRY_ATTR, ASSET_ROUTE_ATTR, the entry shape and the paths are
#     the interop surface. The route owner's copy serves everyone.
#   * Fragments are keyed by the plugin's module name (ASSET_KEY), which Open
#     WebUI keeps unique per install.
#   * Lower `order` composes first (default 0).
#   * Producers run on the event loop for every request, 304s included: return
#     a prebuilt string, no I/O, no locks.
#   * Return "" to withdraw. A deleted plugin never sees its own deletion; any
#     other plugin calling asset_withdraw_deleted() drops its fragments.
#   * Inline fragments only: <script src> / @import lands after hydration.
# ===========================================================================
LOADER_PATH = "/static/loader.js"
CUSTOM_CSS_PATH = "/static/custom.css"
SHARED_ASSET_TYPES = {
    LOADER_PATH: "application/javascript; charset=utf-8",
    CUSTOM_CSS_PATH: "text/css; charset=utf-8",
}
ASSET_REGISTRY_ATTR = "_owui_static_fragments"  # {path: {key: entry}}
ASSET_ROUTE_ATTR = "_owui_shared_asset"  # set to the path the route serves
ASSET_IMPL_ATTR = "_owui_shared_asset_impl"  # implementation version of the route
# Bump on any behaviour change: the newest copy takes over the route.
ASSET_IMPL_VERSION = 5
# "function_<id>" or "tool_<id>"; newer Open WebUI adds a random "_<hex>".
ASSET_KEY = re.sub(r"^((?:function|tool)_.+?)_[0-9a-f]{32}$", r"\1", __name__)

# Warn once per (path, key, exception type): compose runs on every page load.
_ASSET_WARNED: set = set()


def asset_fragments(app: Any, path: str) -> dict:
    registry = getattr(app.state, ASSET_REGISTRY_ATTR, None)
    if not isinstance(registry, dict):
        registry = {}
        app.state.__setattr__(ASSET_REGISTRY_ATTR, registry)
    bucket = registry.get(path)
    if not isinstance(bucket, dict):
        bucket = {}
        registry[path] = bucket
    return bucket


def asset_sort_key(item):
    """(order, key); a non-int order falls back to 0 so it cannot break sorted()."""
    key, entry = item
    try:
        order = int(entry.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return (order, key)


def asset_withdraw_deleted(app: Any, event: Any, event_name: Any) -> None:
    """Drop a deleted plugin's fragments; call it from your event handler."""
    if event_name not in ("function.deleted", "tool.deleted"):
        return
    kind = event_name.split(".")[0]
    subject_id = ((event or {}).get("subject") or {}).get("id")
    for bucket in getattr(app.state, ASSET_REGISTRY_ATTR, {}).values():
        bucket.pop(f"{kind}_{subject_id}", None)


def asset_strip_block(content: str, start_marker: str, end_marker: str) -> str:
    """Remove every marker-wrapped block, leaving other content untouched."""
    while start_marker in content:
        start = content.find(start_marker)
        end = content.find(end_marker, start)
        if end == -1:
            # No end marker: the block was appended last, so drop to EOF.
            content = content[:start]
            break
        end += len(end_marker)
        if content[end : end + 1] == "\n":
            end += 1
        content = content[:start] + content[end:]
    return content


def asset_compose(app: Any, path: str) -> str:
    """Disk file plus every registered fragment, in (order, key) order."""
    import logging

    try:
        from open_webui.env import STATIC_DIR

        target = Path(STATIC_DIR) / path.rsplit("/", 1)[-1]
        body = (
            ""
            if (target.is_symlink() or not target.is_file())
            else target.read_text(encoding="utf-8")
        )
    except Exception:
        body = ""

    ordered = sorted(asset_fragments(app, path).items(), key=asset_sort_key)
    # A bare newline lets one fragment run into the next: "a\n(b)" is a call.
    separator = "\n;\n" if path == LOADER_PATH else "\n\n"
    # Strip first: an older file-writing build may have left a block on disk.
    for _key, entry in ordered:
        body = asset_strip_block(body, entry["start"], entry["end"])
    body = body.rstrip()

    for key, entry in ordered:
        try:
            block = (entry["js"]() or "").strip()
        except Exception as exc:
            mark = (path, key, type(exc).__name__)
            if mark not in _ASSET_WARNED:
                if len(_ASSET_WARNED) > 256:
                    _ASSET_WARNED.clear()
                _ASSET_WARNED.add(mark)
                logging.getLogger("owui-shared-assets").warning(
                    "fragment %r failed for %s - it will be omitted",
                    key,
                    path,
                    exc_info=True,
                )
            continue
        if block:
            body = (body + separator if body else "") + block
    return body + "\n" if body else ""


def asset_register(
    app: Any, path: str, start: str, end: str, producer, order: int = 0
) -> None:
    """Publish a fragment and ensure the route exists. Idempotent."""
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    asset_fragments(app, path)[ASSET_KEY] = {
        "start": start,
        "end": end,
        "js": producer,
        "order": order,
    }

    for existing in app.routes:
        if getattr(existing, ASSET_ROUTE_ATTR, None) != path:
            continue
        if getattr(existing, ASSET_IMPL_ATTR, 0) >= ASSET_IMPL_VERSION:
            return  # an equal or newer implementation already owns the route
        break  # ours is newer - fall through and replace it

    # Fragments live on app.state, so replacing the route loses nothing.
    app.routes[:] = [r for r in app.routes if getattr(r, "path", "") != path]
    media_type = SHARED_ASSET_TYPES.get(path, "text/plain; charset=utf-8")

    async def serve_asset(request):
        content = asset_compose(app, path)
        etag = (
            '"owui-'
            # Cache validator only; a bare md5() raises on FIPS hosts.
            + hashlib.md5(
                (path + "\x00" + content).encode("utf-8"), usedforsecurity=False
            ).hexdigest()
            + '"'
        )
        # no-cache, not no-store: no-store would disable the 304 below.
        headers = {
            "Cache-Control": "no-cache, must-revalidate, private",
            "ETag": etag,
        }
        # Gzipping proxies send the tag back weakened to W/"...".
        if request.headers.get("if-none-match", "").removeprefix("W/") == etag:
            return Response(status_code=304, headers=headers)
        # Starlette only adds charset for text/*.
        return Response(content, media_type=media_type, headers=headers)

    insert_at = len(app.routes)
    for position, existing in enumerate(app.routes):
        if isinstance(existing, Mount) and getattr(existing, "name", "") == "static":
            insert_at = position
            break
    shared = Route(path, serve_asset, methods=["GET"])
    setattr(shared, ASSET_ROUTE_ATTR, path)
    setattr(shared, ASSET_IMPL_ATTR, ASSET_IMPL_VERSION)
    app.routes.insert(insert_at, shared)


# =========================== end shared asset block ========================

LOADER_SCRIPT = '/* Chat Inspector: source for the embedded browser fragment. */\n(() => {\n  \'use strict\';\n  if (window.__owuiChatInspector) return;\n  window.__owuiChatInspector = true;\n  const labels = {\n    vision: \'Vision\', file_upload: \'File uploads\', file_context: \'File context\',\n    web_search: \'Web search\', image_generation: \'Image generation\',\n    code_interpreter: \'Code interpreter\', terminal: \'Terminal\', usage: \'Usage display\',\n    citations: \'Citations\', status_updates: \'Status updates\', memory: \'Memory\', builtin_tools: \'Built-in tools\'\n  };\n  let button, menuButton, dialog, content, controller, openedPath, openedToken, sequence = 0;\n  let pollTimer = null, lastModelId = null, cachedModels = null, lastModelFetch = 0, failedPolls = 0;\n  const sessionToken = () => { try { return localStorage.getItem(\'token\') || \'\'; } catch { return \'\'; } };\n  const el = (tag, cls, value) => {\n    const node = document.createElement(tag);\n    if (cls) node.className = cls;\n    if (value != null) node.textContent = String(value);\n    return node;\n  };\n  const route = () => {\n    const match = /^\\/c\\/([a-zA-Z0-9-]+)\\/?$/.exec(location.pathname);\n    return match ? match[1] : null;\n  };\n  const number = value => typeof value === \'number\' && Number.isSafeInteger(value) && value >= 0 ? value : null;\n  const firstNumber = (object, keys) => {\n    for (const key of keys) { const n = number(object?.[key]); if (n !== null) return n; }\n    return null;\n  };\n  const fmt = n => n === null ? \'Not reported\' : n.toLocaleString();\n  function row(list, name, value) {\n    const pair = el(\'div\', \'ci-row\');\n    pair.append(el(\'dt\', \'\', name), el(\'dd\', \'\', value)); list.append(pair);\n  }\n  function section(title, description) {\n    const block = el(\'section\'); block.append(el(\'h3\', \'\', title));\n    if (description) block.append(el(\'p\', \'ci-note\', description));\n    const list = el(\'dl\'); block.append(list); content.append(block); return list;\n  }\n  function branchMessages(chat) {\n    const history = chat?.chat?.history;\n    const messages = history?.messages;\n    if (!messages || typeof messages !== \'object\') return [];\n    let id = chat.current_message_id || history.currentId;\n    const seen = new Set(), result = [];\n    while (typeof id === \'string\' && id && !seen.has(id) && seen.size < 10000) {\n      seen.add(id);\n      const msg = Object.prototype.hasOwnProperty.call(messages, id) ? messages[id] : null;\n      if (!msg || typeof msg !== \'object\') break;\n      result.push(msg); id = msg.parentId;\n    }\n    return result.reverse();\n  }\n  function estimateTokens(value) {\n    if (value === null || value === undefined || value === \'\') return 0;\n    let serialized;\n    try { serialized = typeof value === \'string\' ? value : JSON.stringify(value); }\n    catch { return 0; }\n    // Image and other data URIs do not represent that many text tokens.\n    if (typeof serialized !== \'string\') return 0;\n    serialized = serialized.replace(/data:[\\w/+.;=%-]*;base64,[A-Za-z0-9+/=]*/g, \'\');\n    return serialized ? Math.max(1, Math.floor(serialized.length / 4)) : 0;\n  }\n  function estimateMessage(message) {\n    return 4 + (Array.isArray(message.output) && message.output.length\n      ? estimateTokens(message.output)\n      : estimateTokens(message.content) + estimateTokens(message.tool_calls));\n  }\n  function localEstimate(branch) {\n    if (!branch.length) return null;\n    let start = 0, summary = \'\';\n    branch.forEach((message, index) => {\n      const value = message.contextSummary || message.context_summary;\n      if (typeof value === \'string\' && value.trim()) { start = index; summary = value; }\n    });\n    const active = branch.slice(start);\n    for (let index = active.length - 1; index >= 0; index--) {\n      const usage = active[index].usage ?? active[index].info?.usage;\n      const input = firstNumber(usage, [\'input_tokens\', \'prompt_tokens\', \'prompt_eval_count\']);\n      const output = firstNumber(usage, [\'output_tokens\', \'completion_tokens\', \'eval_count\']);\n      const count = firstNumber(usage, [\'total_tokens\']) ?? (input !== null || output !== null ? (input ?? 0) + (output ?? 0) : null);\n      if (count !== null && count > 0) return count + active.slice(index + 1).reduce((sum, message) => sum + estimateMessage(message), 0);\n    }\n    return estimateTokens(summary) + active.reduce((sum, message) => sum + estimateMessage(message), 0);\n  }\n  function renderChat(data) {\n    const branch = branchMessages(data);\n    const msg = [...branch].reverse().find(item => item.role === \'assistant\') ?? null;\n    const usage = msg?.usage ?? msg?.info?.usage;\n    const model = typeof msg?.model === \'string\' ? msg.model : null;\n    const context = data?.context_usage;\n    const serverEstimate = firstNumber(context, [\'estimated_tokens\', \'tokens\']);\n    const estimated = serverEstimate ?? localEstimate(branch);\n    const threshold = firstNumber(context, [\'threshold\']);\n    const ctx = section(\'Context\', \'Estimated from the saved conversation branch. Local approximation omits unknown prompt, tool and attachment costs. The compaction threshold, when available, is not necessarily the model context window.\');\n    row(ctx, \'Source\', serverEstimate !== null ? \'Open WebUI server estimate\' : estimated !== null ? \'Local approximation\' : \'Not available\');\n    if (estimated !== null && threshold !== null && threshold > 0) {\n      const percent = Math.round(estimated / threshold * 100);\n      row(ctx, \'Estimated use\', fmt(estimated) + \' / \' + fmt(threshold) + \' tokens · \' + percent + \'%\');\n      const bar = el(\'div\', \'ci-progress\'); bar.setAttribute(\'role\', \'progressbar\');\n      bar.setAttribute(\'aria-label\', \'Estimated use of compaction threshold\');\n      bar.setAttribute(\'aria-valuemin\', \'0\'); bar.setAttribute(\'aria-valuemax\', String(threshold));\n      bar.setAttribute(\'aria-valuenow\', String(Math.min(estimated, threshold)));\n      const fill = el(\'div\', \'ci-progress-fill\'); fill.style.width = Math.min(100, percent) + \'%\';\n      bar.append(fill); ctx.parentElement.append(bar);\n      row(ctx, estimated > threshold ? \'Over threshold\' : \'Until threshold\', fmt(Math.abs(threshold - estimated)) + \' tokens\');\n    } else {\n      row(ctx, \'Estimated use\', estimated === null ? \'Not available\' : fmt(estimated) + \' tokens\');\n      row(ctx, \'Threshold\', threshold === null ? \'Not available\' : fmt(threshold) + \' tokens\');\n    }\n    row(ctx, \'Messages on this branch\', fmt(branch.length));\n    row(ctx, \'Breakdown by prompt, tools and skills\', \'Not measured by Open WebUI\');\n    const list = section(\'Last assistant response\', \'Provider or Open WebUI usage for one response. Counts may be absent or incomplete during generation.\');\n    row(list, \'Model used\', model || \'Not recorded\');\n    row(list, \'Input tokens\', fmt(firstNumber(usage, [\'input_tokens\', \'prompt_tokens\', \'prompt_eval_count\'])));\n    row(list, \'Output tokens\', fmt(firstNumber(usage, [\'output_tokens\', \'completion_tokens\', \'eval_count\'])));\n    row(list, \'Total tokens\', fmt(firstNumber(usage, [\'total_tokens\'])));\n    const inventory = section(\'Saved chat configuration\', \'This is saved metadata, not the live composer selection or a list of tools actually sent to the model.\');\n    const chat = data?.chat;\n    for (const [field, label] of [[\'tool_ids\',\'Workspace tools\'], [\'tool_servers\',\'Tool servers\'], [\'filter_ids\',\'Filters\'], [\'skill_ids\',\'Skills\']]) {\n      const value = chat?.[field];\n      row(inventory, label, Array.isArray(value) ? fmt(value.length) + \' listed\' : \'Not recorded\');\n    }\n    return model;\n  }\n  function renderModel(data, modelId) {\n    const list = section(\'Model configuration\', \'These are explicit model settings, not proof a feature is enabled for this user or selected in this chat. Provider support, permissions, global settings and tools also matter.\');\n    if (!modelId) { row(list, \'Settings\', \'No saved response model\'); return; }\n    const models = Array.isArray(data?.data) ? data.data : [];\n    const model = models.find(item => item && item.id === modelId);\n    if (!model) { row(list, \'Settings\', \'Model not found in the available server catalog\'); return; }\n    const caps = model?.info?.meta?.capabilities;\n    for (const [key, label] of Object.entries(labels)) {\n      const value = caps && Object.prototype.hasOwnProperty.call(caps, key) ? caps[key] : undefined;\n      row(list, label, value === true ? \'Configured on\' : value === false ? \'Configured off\' : \'Not explicitly set\');\n    }\n  }\n  async function get(path, signal, token) {\n    const headers = {Accept: \'application/json\'};\n    if (token) headers.Authorization = \'Bearer \' + token;\n    const response = await fetch(path, {method: \'GET\', headers, credentials: \'same-origin\', cache: \'no-store\', redirect: \'error\', signal});\n    if (!response.ok) throw new Error(\'Request unavailable\');\n    return response.json();\n  }\n  function clearRequest() { sequence++; controller?.abort(); controller = null; }\n  function schedulePoll(delay = 30000) {\n    clearTimeout(pollTimer);\n    if (dialog?.open && !document.hidden) pollTimer = setTimeout(() => refresh(false), delay);\n  }\n  async function refresh(showLoading = true) {\n    if (!showLoading && (controller || document.hidden || !dialog?.open)) return;\n    if (dialog?.open && openedPath !== location.pathname) {\n      if (openedPath === \'/\' && route()) openedPath = location.pathname;\n      else { close(); return; }\n    }\n    clearRequest(); const mySequence = sequence;\n    controller = new AbortController(); const current = controller;\n    const timeout = setTimeout(() => current.abort(), 15000);\n    if (showLoading) content.replaceChildren(el(\'p\', \'ci-note\', \'Loading chat information…\'));\n    try {\n      const id = route();\n      if (sessionToken() !== openedToken) { close(); return; }\n      if (!id) { content.replaceChildren(el(\'p\', \'ci-note\', \'Open a saved chat to inspect its last response. Temporary and new chats are not available here.\')); return; }\n      const chat = await get(\'/api/v1/chats/\' + encodeURIComponent(id), current.signal, openedToken);\n      if (mySequence !== sequence || location.pathname !== openedPath || sessionToken() !== openedToken || !dialog?.open) return;\n      content.replaceChildren();\n      const modelId = renderChat(chat);\n      if (modelId) {\n        try {\n          if (!cachedModels || lastModelId !== modelId || Date.now() - lastModelFetch > 300000) {\n            cachedModels = await get(\'/api/models\', current.signal, openedToken);\n            lastModelFetch = Date.now(); lastModelId = modelId;\n          }\n          if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken && dialog?.open) renderModel(cachedModels, modelId);\n        } catch (e) {\n          if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken && dialog?.open) {\n            const list = section(\'Model configuration\'); row(list, \'Settings\', current.signal.aborted ? \'Request timed out\' : \'Unavailable\');\n          }\n        }\n      } else renderModel(null, null);\n      failedPolls = 0;\n    } catch (e) {\n      if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken && dialog?.open) {\n        failedPolls = Math.min(failedPolls + 1, 3);\n        if (showLoading) content.replaceChildren(el(\'p\', \'ci-note\', current.signal.aborted ? \'The request timed out. Try Refresh.\' : \'Could not read this chat. Check your access and try Refresh.\'));\n        else { content.querySelector(\'.ci-error\')?.remove(); content.prepend(el(\'p\', \'ci-note ci-error\', \'Update unavailable; showing the previous snapshot.\')); }\n      }\n    } finally {\n      clearTimeout(timeout); if (controller === current) controller = null;\n      if (mySequence === sequence) schedulePoll(30000 * (2 ** failedPolls));\n    }\n  }\n  function close() {\n    clearTimeout(pollTimer); clearRequest(); openedToken = undefined; cachedModels = null;\n    if (dialog?.open) dialog.close();\n    content?.replaceChildren();\n    if (button?.isConnected) button.focus();\n  }\n  function open() {\n    if (dialog?.open) { close(); return; }\n    if (!dialog) {\n      dialog = el(\'dialog\', \'ci-dialog\'); dialog.setAttribute(\'aria-labelledby\', \'ci-title\');\n      const header = el(\'header\', \'ci-header\'); const title = el(\'h2\', \'\', \'Chat context\'); title.id = \'ci-title\';\n      const dismiss = el(\'button\', \'ci-button\', \'Close\'); dismiss.type = \'button\'; dismiss.addEventListener(\'click\', close);\n      header.append(title, dismiss);\n      const note = el(\'p\', \'ci-note\', \'Updates every 30 seconds while visible. The chat stays usable. Refresh for an immediate snapshot.\');\n      content = el(\'div\', \'ci-content\');\n      const reload = el(\'button\', \'ci-button\', \'Refresh\'); reload.type = \'button\'; reload.addEventListener(\'click\', () => refresh(true));\n      dialog.append(header, note, content, reload); document.body.append(dialog);\n      dialog.addEventListener(\'cancel\', event => { event.preventDefault(); close(); });\n      dialog.addEventListener(\'close\', () => { clearTimeout(pollTimer); clearRequest(); openedToken = undefined; cachedModels = null; content.replaceChildren(); });\n    }\n    openedPath = location.pathname; openedToken = sessionToken(); failedPolls = 0;\n    try { dialog.show(); } catch { return; }\n    refresh(true);\n  }\n  // The native navbar can stay mounted but hidden when the full-width tab bar\n  // appears. Only attach to a visible host; otherwise the button disappears.\n  function visible(node) { return !!node && node.isConnected && node.getClientRects().length > 0; }\n  function sync() {\n    if (dialog?.open && openedToken !== sessionToken()) close();\n    if (dialog?.open && openedPath !== location.pathname) {\n      if (openedPath === \'/\' && route()) { openedPath = location.pathname; refresh(true); }\n      else close();\n    }\n    const chatRoute = location.pathname === \'/\' || !!route();\n    const groups = [...document.querySelectorAll(\'#chat-container nav .flex.flex-none.items-center.self-center\')];\n    const nav = groups.reverse().find(visible);\n    const controls = document.getElementById(\'controls-container\');\n    const lists = controls && visible(controls)\n      ? [...controls.querySelectorAll(\'.panel-tab-strip\')].filter(visible) : [];\n    const host = chatRoute ? (nav || lists[0]?.parentElement) : null;\n    if (!host) button?.remove();\n    else {\n      if (!button) {\n        button = el(\'button\', \'ci-trigger\', \'ⓘ\'); button.type = \'button\'; button.id = \'owui-chat-inspector\';\n        button.setAttribute(\'aria-label\', \'Chat inspector\'); button.title = \'Chat inspector\'; button.addEventListener(\'click\', open);\n      }\n      // Gallery keeps its button at the end of the tab row. Insert before it,\n      // otherwise the two independent MutationObservers would fight forever.\n      const gallery = host.querySelector(\':scope > #owui-chat-gallery-header\');\n      if (button.parentElement !== host) host.insertBefore(button, gallery || null);\n    }\n\n    // Match Gallery\'s + menu integration. Never attach to a chat\'s unrelated\n    // overflow menu; only use the visible panel whose + button is expanded.\n    const expanded = chatRoute && lists.find(list => {\n      const plus = list.parentElement?.querySelector(\':scope > :not([role="tablist"]) > button[aria-haspopup="true"]\');\n      return plus?.getAttribute(\'aria-expanded\') === \'true\';\n    });\n    const menus = [...document.querySelectorAll(\'body > [role="menu"]\')].filter(visible);\n    const menu = expanded ? menus.at(-1)?.querySelector(\'.app-dropdown-menu\') : null;\n    if (!menu) menuButton?.remove();\n    else {\n      if (!menuButton) {\n        menuButton = el(\'button\', \'ci-menu-button\', \'ⓘ  Chat inspector\');\n        menuButton.type = \'button\'; menuButton.id = \'owui-chat-inspector-menu\';\n        menuButton.addEventListener(\'click\', open);\n      }\n      if (menuButton.parentElement !== menu) menu.append(menuButton);\n    }\n  }\n  let pending = false;\n  function schedule() { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; sync(); }); } }\n  function start() {\n    const style = el(\'style\'); style.textContent = `\n      .ci-trigger{border:0;background:transparent;color:inherit;font:inherit;font-size:1.1rem;cursor:pointer;padding:.4rem;border-radius:.5rem}\n      .ci-trigger:hover,.ci-button:hover{background:rgba(127,127,127,.15)}\n      .ci-dialog{position:fixed;inset:3rem 1rem auto auto;margin:0;width:min(22rem,calc(100vw - 2rem));max-height:min(80vh,48rem);overflow:auto;box-sizing:border-box;border:1px solid #8886;border-radius:1rem;padding:1.2rem;background:var(--color-white,#fff);color:var(--color-gray-900,#171717);font:inherit;font-size:.85rem;box-shadow:0 12px 40px #0005;z-index:55}\n      .dark .ci-dialog{background:var(--color-gray-900,#171717);color:var(--color-gray-100,#eee);color-scheme:dark}\n      .ci-progress{height:.4rem;border-radius:1rem;background:#8883;overflow:hidden;margin:.5rem 0}.ci-progress-fill{height:100%;background:#3b82f6}.ci-error{color:#b45309}.ci-dialog::backdrop{background:#0008}.ci-header{display:flex;align-items:center;justify-content:space-between;gap:1rem}.ci-header h2{font-size:1.15rem;font-weight:650;margin:0}\n      .ci-dialog h3{font-weight:650;margin:1rem 0 .25rem}.ci-note{font-size:.85rem;opacity:.8;margin:.5rem 0}.ci-row{display:flex;justify-content:space-between;gap:1rem;padding:.3rem 0;border-bottom:1px solid #8883}.ci-row dd{margin:0;text-align:right;overflow-wrap:anywhere}.ci-button{font:inherit;color:inherit;background:transparent;border:1px solid #8887;border-radius:.5rem;padding:.4rem .7rem;cursor:pointer}.ci-dialog :focus-visible,.ci-trigger:focus-visible{outline:2px solid #3b82f6;outline-offset:2px}\n    `; document.head.append(style);\n    new MutationObserver(schedule).observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:[\'class\', \'style\', \'hidden\', \'aria-expanded\']});\n    window.addEventListener(\'popstate\', schedule); window.addEventListener(\'storage\', schedule);\n    window.addEventListener(\'focus\', () => { schedule(); if (dialog?.open && !controller) refresh(false); });\n    document.addEventListener(\'visibilitychange\', () => { if (document.hidden) clearTimeout(pollTimer); else if (dialog?.open && !controller) refresh(false); }); schedule();\n  }\n  if (document.readyState === \'loading\') document.addEventListener(\'DOMContentLoaded\', start, {once:true}); else start();\n})();\n'


class Event:
    class Valves(BaseModel):
        pass

    LOADER_BLOCK_START = "// owui-chat-inspector:start"
    LOADER_BLOCK_END = "// owui-chat-inspector:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment = self.LOADER_BLOCK_START + "\n" + LOADER_SCRIPT + "\n" + self.LOADER_BLOCK_END
        reload_bootstrap()

    def _publish(self, app):
        asset_register(app, LOADER_PATH, self.LOADER_BLOCK_START, self.LOADER_BLOCK_END,
                       lambda: Event.instance._fragment if reload_active(app) else "")

    async def event(self, event: Optional[dict] = None, __event_name__: str = "",
                    __app__: Any = None, **kwargs) -> None:
        if __app__ is not None:
            await reload_on_event(__app__, event, __event_name__)


def register(app: Any) -> None:
    Event.instance._publish(app)
