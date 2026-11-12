"""
title: Chat Inspector
description: Context, in the chat's ⋯ menu: a pop-up showing how much of the model's context window the chat uses, Claude-style, with a grid of what fills it, the model's own token counts, every tool call, web search and source it used, and a warning before it overflows.
author: Muhammad Sohail
version: 1.2.0
license: MIT
required_open_webui_version: 0.13.0
"""

# Chat Inspector 1.0.0: a rewrite of 0.2.0 as a tab beside Chat Gallery, Files and Canvas. The two protocol blocks
# below are Classic298's Shared Assets Protocol and its live reload add-on
# (BSD 3-Clause), copied byte for byte.

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


INSPECTOR_CSS = r"""
#owui-chat-inspector-button svg,#owui-chat-inspector-header svg{pointer-events:none}
#owui-chat-inspector-header svg{width:1.125rem;height:1.125rem}
#owui-chat-inspector-header.w-7 svg{width:.875rem;height:.875rem}
#owui-chat-inspector-header[aria-pressed=true]{color:var(--color-gray-700,#4e4e4e)}
.dark #owui-chat-inspector-header[aria-pressed=true]{color:var(--color-gray-200,#e3e3e3)}
#owui-chat-inspector-tabs{scrollbar-width:none}
.ci-tabrow{overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.ci-tabrow::-webkit-scrollbar{display:none}
.ci-dialog{--ci-surface:#fff;--ci-text:var(--color-gray-800,#262626);--ci-muted:var(--color-gray-600,#5d5d5d);--ci-soft:var(--color-gray-50,#f9f9f9);--ci-hover:var(--color-gray-100,#ececec);--ci-line:var(--color-gray-100,#ececec);--ci-accent:var(--color-blue-600,#2563eb);--ci-ok:#166534;--ci-warn:#92400e;--ci-danger-text:#b91c1c;
--ci-c-system:#7c3aed;--ci-c-extras:#d97706;--ci-c-summary:#64748b;--ci-c-messages:#2563eb;--ci-c-latest:#0d9488;--ci-c-newer:#0ea5e9;--ci-c-tools:#db2777;--ci-bad:#b91c1c;--ci-c-free:var(--color-gray-200,#e3e3e3);--ci-c-reserve:var(--color-gray-300,#cdcdcd)}
.dark .ci-dialog{--ci-surface:var(--color-gray-900,#171717);--ci-text:var(--color-gray-100,#ececec);--ci-muted:var(--color-gray-400,#b4b4b4);--ci-soft:var(--color-gray-850,#262626);--ci-hover:var(--color-gray-800,#333);--ci-line:var(--color-gray-800,#333);--ci-accent:var(--color-blue-400,#60a5fa);--ci-ok:#4ade80;--ci-warn:#fbbf24;--ci-danger-text:#f87171;color-scheme:dark;
--ci-c-system:#a78bfa;--ci-c-extras:#fbbf24;--ci-c-summary:#94a3b8;--ci-c-messages:#60a5fa;--ci-c-latest:#2dd4bf;--ci-c-newer:#7dd3fc;--ci-c-tools:#f472b6;--ci-bad:#f87171;--ci-c-free:var(--color-gray-800,#333);--ci-c-reserve:var(--color-gray-700,#4e4e4e)}
.ci-dialog{position:fixed;left:0;top:0;z-index:45;box-sizing:border-box;display:flex;flex-direction:column;container:ci/inline-size;margin:0;padding:0;border:0;background:transparent;color:var(--ci-text);overflow:hidden;font-family:inherit;font-size:.875rem;line-height:1.45}
.ci-dialog[hidden]{display:none}
.ci-dialog.ci-solo{background:var(--ci-surface)}
[data-ci-covered]{opacity:0!important;pointer-events:none!important}
.panel-tab-strip.ci-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){background:transparent!important;color:var(--color-gray-500,#6b7280)!important}
.dark .panel-tab-strip.ci-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){color:var(--color-gray-400,#9ca3af)!important}
.ci-dialog *{box-sizing:border-box}
.ci-dialog button,.ci-dialog select{font:inherit;color:inherit}
.ci-dialog :focus-visible{outline:2px solid var(--ci-accent);outline-offset:2px}
.ci-head{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;padding:1rem 1.25rem .5rem}
.ci-heading{margin:0;font-size:1rem;font-weight:600}
.ci-spacer{flex:1}
.ci-updated{color:var(--ci-muted);font-size:.75rem;font-variant-numeric:tabular-nums}
.ci-updated.ci-busy{color:var(--ci-accent)}
.ci-select{appearance:auto;height:2rem;max-width:16rem;padding:0 .5rem;border:0;border-radius:.75rem;background:var(--ci-soft);cursor:pointer}
.dark .ci-select{box-shadow:inset 0 0 0 1px var(--ci-line)}
.ci-select[hidden]{display:none}
.ci-select option{background:var(--ci-surface);color:var(--ci-text)}
.ci-btn{display:inline-flex;align-items:center;justify-content:center;gap:.375rem;height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--ci-soft);cursor:pointer;white-space:nowrap}
.ci-btn:hover{background:var(--ci-hover)}
.ci-icon{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;border:0;border-radius:.75rem;background:transparent;cursor:pointer;color:var(--ci-muted)}
.ci-icon:hover{background:var(--ci-hover);color:var(--ci-text)}
.ci-icon:disabled{opacity:.4;cursor:default}
.ci-icon[hidden]{display:none}
.ci-icon svg{width:1.125rem;height:1.125rem}
.ci-body{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:.25rem 1.25rem 1.5rem;display:flex;flex-direction:column;gap:1.25rem;outline:none}
.ci-body>*{width:100%;max-width:44rem}
.ci-summary{display:flex;flex-direction:column;gap:.375rem}
.ci-big{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:0;font-size:1.25rem;font-variant-numeric:tabular-nums}
.ci-big strong{font-weight:600}
.ci-state{padding:.125rem .5rem;border-radius:999px;font-size:.75rem;font-weight:600;color:var(--ci-ok);background:color-mix(in srgb,var(--ci-ok) 13%,transparent)}
.ci-state[data-state=warn]{color:var(--ci-warn);background:color-mix(in srgb,var(--ci-warn) 14%,transparent)}
.ci-state[data-state=full]{color:var(--ci-danger-text);background:color-mix(in srgb,var(--ci-danger-text) 12%,transparent)}
.ci-sub{margin:0;color:var(--ci-muted);font-size:.8125rem}
.ci-alert{margin:.25rem 0 0;padding:.5rem .75rem;border-radius:.75rem;color:var(--ci-warn);background:color-mix(in srgb,var(--ci-warn) 12%,transparent);font-size:.8125rem}
.ci-alert[data-state=full]{color:var(--ci-danger-text);background:color-mix(in srgb,var(--ci-danger-text) 10%,transparent)}
.ci-warn{margin:0;padding:.5rem .75rem;border-radius:.75rem;color:var(--ci-warn);background:color-mix(in srgb,var(--ci-warn) 12%,transparent);font-size:.8125rem}
.ci-gridwrap{display:flex;flex-direction:column;gap:.375rem;margin:.5rem 0 .25rem}
.ci-grid{display:grid;grid-template-columns:repeat(20,minmax(0,1fr));gap:3px;max-width:30rem}
.ci-cell{aspect-ratio:1;border-radius:3px;background:var(--ci-cell)}
.ci-cell[data-c=free]{background:transparent;box-shadow:inset 0 0 0 1px var(--ci-c-free)}
.ci-cell[data-c=reserve]{background:repeating-linear-gradient(45deg,var(--ci-c-reserve) 0 2px,transparent 2px 5px);box-shadow:inset 0 0 0 1px var(--ci-c-reserve)}
[data-c=system]{--ci-cell:var(--ci-c-system)}[data-c=extras]{--ci-cell:var(--ci-c-extras)}[data-c=summary]{--ci-cell:var(--ci-c-summary)}[data-c=messages]{--ci-cell:var(--ci-c-messages)}[data-c=latest]{--ci-cell:var(--ci-c-latest)}[data-c=newer]{--ci-cell:var(--ci-c-newer)}[data-c=tools]{--ci-cell:var(--ci-c-tools)}
.ci-section h3{margin:0 0 .5rem;font-size:.75rem;font-weight:600;color:var(--ci-muted);text-transform:uppercase;letter-spacing:.04em}
.ci-legend{display:flex;flex-direction:column;gap:.25rem;margin:0;padding:0;list-style:none}
.ci-legend li{display:grid;grid-template-columns:auto minmax(0,1fr) auto 3rem auto;align-items:center;gap:.5rem;padding:.25rem 0;border-bottom:1px solid var(--ci-line);font-variant-numeric:tabular-nums}
.ci-swatch{width:.875rem;height:.875rem;border-radius:3px;background:var(--ci-cell)}
.ci-swatch[data-c=free]{background:transparent;box-shadow:inset 0 0 0 1.5px var(--ci-muted)}
.ci-swatch[data-c=reserve]{background:repeating-linear-gradient(45deg,var(--ci-muted) 0 2px,transparent 2px 4px)}
.ci-lname{overflow-wrap:anywhere}
.ci-lvalue{text-align:end}
.ci-lshare{text-align:end;color:var(--ci-muted)}
.ci-tag{padding:0 .375rem;border-radius:.375rem;font-size:.6875rem;color:var(--ci-muted);background:var(--ci-soft);cursor:help;white-space:nowrap}
.ci-tag[data-kind=reported]{color:var(--ci-ok);background:color-mix(in srgb,var(--ci-ok) 13%,transparent)}
.ci-tag[data-kind=inferred]{color:var(--ci-warn);background:color-mix(in srgb,var(--ci-warn) 13%,transparent)}
.ci-legend li:not(:has(.ci-tag)){grid-template-columns:auto minmax(0,1fr) auto 3rem auto}
.ci-facts{display:grid;grid-template-columns:auto 1fr;gap:.375rem 1rem;margin:0}
.ci-facts dt{color:var(--ci-muted)}
.ci-facts dd{margin:0;text-align:end;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.ci-chips{display:flex;flex-wrap:wrap;gap:.375rem;margin:0;padding:0;list-style:none}
.ci-chips li{padding:.125rem .625rem;border-radius:999px;background:var(--ci-soft);box-shadow:inset 0 0 0 1px var(--ci-line);font-size:.8125rem}
.ci-line{display:flex;flex-wrap:wrap;gap:.125rem .5rem}
.ci-line>span+span::before{content:"·";margin-inline-end:.5rem;color:var(--ci-muted)}
.ci-line>span[title]{cursor:help}
.ci-gridwrap[title]{cursor:help}
.ci-facts-wide{margin-top:.625rem}
.ci-facts-wide dd{text-align:start}
.ci-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(7.5rem,1fr));gap:.5rem;margin:0 0 .75rem;padding:0;list-style:none}
.ci-tiles li{display:flex;flex-direction:column;gap:.125rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--ci-soft);box-shadow:inset 0 0 0 1px var(--ci-line)}
.ci-tiles strong{font-size:1.125rem;font-weight:600;font-variant-numeric:tabular-nums}
.ci-tiles span{color:var(--ci-muted);font-size:.75rem}
.ci-tiles em{font-style:normal;font-size:.75rem;color:var(--ci-bad)}
.ci-tools,.ci-calls{display:flex;flex-direction:column;margin:0;padding:0;list-style:none}
.ci-tools li{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:baseline;gap:0 .75rem;padding:.375rem 0;border-bottom:1px solid var(--ci-line)}
.ci-tname{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.8125rem;overflow-wrap:anywhere}
.ci-tcount{text-align:end;font-variant-numeric:tabular-nums;font-weight:600}
.ci-tkind{color:var(--ci-muted);font-size:.75rem;overflow-wrap:anywhere}
.ci-tmeta{color:var(--ci-muted);font-size:.75rem;text-align:end;font-variant-numeric:tabular-nums}
.ci-tmeta[data-bad=true]{color:var(--ci-bad)}
.ci-queries{margin-top:.5rem}
.ci-q{color:var(--ci-text)}
.ci-calls{margin-top:.75rem;gap:.25rem}
.ci-calls details{border-radius:.625rem;background:var(--ci-soft);box-shadow:inset 0 0 0 1px var(--ci-line)}
.ci-calls summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:.125rem .5rem;padding:.375rem .625rem;cursor:pointer;list-style:none;border-radius:.625rem}
.ci-calls summary::-webkit-details-marker{display:none}
.ci-calls summary:hover{background:var(--ci-hover)}
.ci-calls summary .ci-tkind{grid-column:2/3}
.ci-calls summary .ci-tmeta{grid-row:1;grid-column:3}
.ci-dot{grid-row:1/3;width:.5rem;height:.5rem;border-radius:999px;background:var(--ci-ok)}
.ci-dot[data-status=failed],.ci-dot[data-status=noresult]{background:var(--ci-bad)}
.ci-dot[data-status=denied]{background:var(--ci-muted)}
.ci-dot[data-status=running]{background:var(--ci-accent)}
.ci-io{display:flex;flex-direction:column;gap:.25rem;padding:0 .625rem .5rem}
.ci-io>span{color:var(--ci-muted);font-size:.6875rem;text-transform:uppercase;letter-spacing:.04em}
.ci-io pre{margin:0;max-height:14rem;overflow:auto;padding:.5rem;border-radius:.5rem;background:var(--ci-surface);box-shadow:inset 0 0 0 1px var(--ci-line);font:.75rem/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.ci-notice{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.375rem;min-height:12rem;padding:2rem;color:var(--ci-muted);text-align:center;max-width:none}
.ci-notice strong{color:var(--ci-text);font-weight:500}
.ci-notice .ci-btn{margin-top:.5rem;color:var(--ci-text)}
.ci-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.ci-dialog.ci-pop{z-index:60;background:var(--ci-surface);border:1px solid var(--ci-line);border-radius:1rem;box-shadow:0 24px 48px -16px rgb(0 0 0/.35),0 2px 8px rgb(0 0 0/.08)}
.dark .ci-dialog.ci-pop{box-shadow:0 24px 48px -16px rgb(0 0 0/.75),0 0 0 1px rgb(255 255 255/.04)}
.ci-pop .ci-head{padding:.625rem .5rem .5rem 1rem;border-bottom:1px solid var(--ci-line)}
.ci-pop .ci-updated{order:10;flex-basis:100%}
.ci-pop .ci-close{order:9!important;margin-inline-start:0!important}
.ci-pop .ci-body{flex:1 1 auto;padding:.75rem 1rem 1rem;gap:1rem}
.ci-pop .ci-notice{min-height:8rem;padding:1.25rem}
.ci-dialog.ci-sheet{border-radius:1rem 1rem 0 0;border-bottom:0}
.ci-backdrop{position:fixed;inset:0;z-index:59;background:rgb(0 0 0/.35)}
.ci-backdrop[hidden]{display:none}
@media(prefers-reduced-motion:no-preference){.ci-dialog.ci-pop:not([hidden]){animation:ci-in .14s ease-out}@keyframes ci-in{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}.ci-dialog.ci-sheet:not([hidden]){animation:ci-up .18s ease-out}@keyframes ci-up{from{transform:translateY(16px);opacity:.4}to{transform:none;opacity:1}}}
@media(forced-colors:active){.ci-cell,.ci-swatch{forced-color-adjust:none}.ci-btn,.ci-select{border:1px solid CanvasText}}
@container ci (max-width:520px){
.ci-tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
.ci-head{padding:.75rem .75rem .5rem}
.ci-body{padding:.25rem .75rem 1rem}
.ci-grid{grid-template-columns:repeat(10,minmax(0,1fr));max-width:16rem}
.ci-legend li{grid-template-columns:auto minmax(0,1fr) auto 2.5rem;row-gap:0}
.ci-tag{grid-column:2/-1;justify-self:start}
.ci-close{order:-1;margin-inline-start:auto}
}
"""

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiChatInspector) return;
  window.__owuiChatInspector = true;
  const CONFIG = __CONFIG__;

  /* ---- words, in the language Open WebUI is set to ---- */
  const STRINGS = {
    en: {
      context: 'Context', close_context: 'Close Context', refresh: 'Refresh', model: 'Model', updated: 'Updated {time}', updating: 'An answer is being written; this updates when it finishes',
      loading: 'Reading the chat…', no_chat: 'No chat open', no_chat_hint: 'Open a chat to see how much of the model’s context it uses.',
      unsaved: 'This chat isn’t saved yet', unsaved_hint: 'A new or temporary chat has nothing stored to read. Send a message, then look again.',
      denied: 'You no longer have access to this chat.', failed: 'Couldn’t read this chat', failed_hint: 'Check your connection, then try again.', retry: 'Try again',
      empty_chat: 'Nothing in this chat yet', empty_chat_hint: 'Send a message and its context appears here.',
      of_window: '{used} of {window} tokens', used_only: '{used} tokens in use', pct: '{pct}% full', free: '{n} free', over_by: '{n} over the window',
      window_admin: 'Window set by your administrator', window_model: 'Window from the model’s settings', window_chat: 'Window from this chat’s settings', window_user: 'Window from your settings', window_provider: 'Window reported by the provider',
      window_unknown: 'Window not known', window_unknown_admin: 'An administrator can set it in the Chat Inspector valves.',
      st_warn: 'Getting full', st_full: 'Over the window', st_warn_hint: 'Older messages get cut or summarised once it’s full.', st_full_hint: 'The oldest messages are cut or summarised before each answer.',
      compaction_at: 'Summarised at {n}', compaction_left: '{n} to go', compaction_past: 'past that point',
      exchanges: '≈ {n} more exchanges fit', exchanges_one: '≈ 1 more exchange fits', exchanges_none: 'No room for another exchange',
      grid_label: 'Each square is one percent of the context window.', grid_share: 'Each square is one percent of what the chat uses.',
      c_system: 'System prompt', c_extras: 'Tool definitions, documents and instructions {app} added', c_summary: 'Summary of earlier messages', c_messages: 'Messages', c_tools: 'Tool calls and results', c_latest: 'Latest answer', c_newer: 'Since the last count', c_free: 'Free space', c_reserve: 'Reserved before summarising',
      k_reported: 'reported', k_estimated: 'estimated', k_inferred: 'worked out', k_reported_hint: 'Counted by the model itself', k_estimated_hint: 'Estimated from the text', k_inferred_hint: 'The model’s own count, minus what could be estimated',
      count_reported: 'Model’s own count', count_estimated: 'Estimated', count_family: 'Estimates tuned for {family} models', count_general: 'Estimates use general tuning',
      breakdown: 'What is using the context', last: 'Last answer', branch: 'This conversation', capabilities: 'This model', activity: 'Tools and activity',
      f_model: 'Model', f_input: 'Read by the model', f_output: 'Written', f_reasoning: 'of which reasoning', f_cached: 'Read from cache', f_total: 'Total', f_speed: 'Speed', f_time: 'Time', f_cost: 'Cost reported', f_rounds: 'Model calls', f_thinking: 'Thinking',
      tokens: '{n} tokens', per_sec: '{n} tokens/s', seconds: '{n} s', not_reported: 'Not reported',
      b_messages: 'Messages on this branch', b_answers: 'Answers', b_reported: 'Answers the model counted', b_read: 'Tokens read in all', b_written: 'Tokens written in all', b_summarised: 'Older messages', b_summarised_yes: 'Summarised by {app}', b_rounds: 'Model calls in all',
      cap_vision: 'Reads images', cap_file_upload: 'Takes files', cap_web_search: 'Searches the web', cap_image_generation: 'Makes images', cap_code_interpreter: 'Runs code', cap_citations: 'Cites sources', cap_builtin_tools: 'Built-in tools', cap_memory: 'Memory',
      tools_n: '{n} tools attached', knowledge_n: '{n} knowledge bases attached', no_caps: 'No features are set on this model.', model_missing: 'This model isn’t in the list of models you can use.',
      m_tools: 'Tools', m_filters: 'Filters', m_actions: 'Actions', m_pipe: 'Answered by', fn_global: 'everywhere', fn_toggle: 'when switched on',
      a_calls: 'Tool calls', a_failed: 'failed', a_denied: 'declined', a_running: 'running', a_searches: 'Web searches', a_pages: 'Pages read', a_knowledge: 'Knowledge lookups', a_sources: 'Sources used', a_code: 'Code runs', a_images: 'Images made', a_auto: 'Added by {app}', a_tool_tokens: 'Tool results in the chat',
      a_none: 'No tools, searches or sources in this conversation.', a_queries: 'Searched for', a_more: '+{n} more', a_conn: 'Couldn’t connect: {what}', a_args: 'Input', a_result: 'Result', a_empty: '(empty)', a_cut: '… {n} more characters', a_noquery: '(no query saved)',
      s_ok: 'done', s_failed: 'failed', s_noresult: 'no result', s_denied: 'declined', s_running: 'running', s_prompted: 'called through the prompt',
      kind_mcp: 'MCP', kind_workspace: 'Workspace tool', kind_server: 'Tool server', kind_builtin: 'Built-in', kind_provider: 'Run by the provider', kind_auto: 'Automatic', kind_other: 'Tool',
      cat_time: 'Time', cat_web: 'Web search', cat_knowledge: 'Knowledge', cat_files: 'Chat files', cat_memory: 'Memory', cat_images: 'Images', cat_code: 'Code', cat_notes: 'Notes', cat_chats: 'Past chats', cat_channels: 'Channels', cat_skills: 'Skills', cat_agents: 'Sub-agents', cat_ask: 'Questions', cat_tasks: 'Tasks', cat_automations: 'Automations', cat_calendar: 'Calendar', cat_notify: 'Notifications', cat_make_tools: 'Tool builder', cat_computer: 'Computer use', cat_mcp: 'MCP',
      last_calls: 'Calls in this answer'
    },
    fr: {
      context: 'Contexte', close_context: 'Fermer le contexte', refresh: 'Actualiser', model: 'Modèle', updated: 'Mis à jour à {time}', updating: 'Une réponse est en cours ; les chiffres se mettent à jour à la fin',
      loading: 'Lecture de la conversation…', no_chat: 'Aucune conversation ouverte', no_chat_hint: 'Ouvrez une conversation pour voir quelle part du contexte du modèle elle utilise.',
      unsaved: 'Cette conversation n’est pas encore enregistrée', unsaved_hint: 'Une conversation nouvelle ou temporaire n’a encore rien d’enregistré. Envoyez un message, puis regardez à nouveau.',
      denied: 'Vous n’avez plus accès à cette conversation.', failed: 'Impossible de lire cette conversation', failed_hint: 'Vérifiez votre connexion, puis réessayez.', retry: 'Réessayer',
      empty_chat: 'Rien dans cette conversation pour l’instant', empty_chat_hint: 'Envoyez un message et son contexte apparaîtra ici.',
      of_window: '{used} sur {window} jetons', used_only: '{used} jetons utilisés', pct: 'Rempli à {pct} %', free: '{n} libres', over_by: '{n} au-delà de la fenêtre',
      window_admin: 'Fenêtre définie par votre administrateur', window_model: 'Fenêtre issue des réglages du modèle', window_chat: 'Fenêtre issue des réglages de cette conversation', window_user: 'Fenêtre issue de vos réglages', window_provider: 'Fenêtre indiquée par le fournisseur',
      window_unknown: 'Fenêtre inconnue', window_unknown_admin: 'Un administrateur peut la définir dans les valves de Chat Inspector.',
      st_warn: 'Bientôt plein', st_full: 'Au-delà de la fenêtre', st_warn_hint: 'Une fois plein, les anciens messages sont coupés ou résumés.', st_full_hint: 'Les messages les plus anciens sont coupés ou résumés avant chaque réponse.',
      compaction_at: 'Résumé à {n}', compaction_left: 'encore {n}', compaction_past: 'seuil dépassé',
      exchanges: '≈ {n} échanges de plus tiennent', exchanges_one: '≈ 1 échange de plus tient', exchanges_none: 'Plus de place pour un autre échange',
      grid_label: 'Chaque carré représente un pour cent de la fenêtre de contexte.', grid_share: 'Chaque carré représente un pour cent de ce que la conversation utilise.',
      c_system: 'Prompt système', c_extras: 'Définitions d’outils, documents et instructions ajoutés par {app}', c_summary: 'Résumé des messages précédents', c_messages: 'Messages', c_tools: 'Appels d’outils et résultats', c_latest: 'Dernière réponse', c_newer: 'Depuis le dernier comptage', c_free: 'Espace libre', c_reserve: 'Réservé avant le résumé',
      k_reported: 'compté', k_estimated: 'estimé', k_inferred: 'déduit', k_reported_hint: 'Compté par le modèle lui-même', k_estimated_hint: 'Estimé à partir du texte', k_inferred_hint: 'Le comptage du modèle, moins ce qui a pu être estimé',
      count_reported: 'Comptage du modèle', count_estimated: 'Estimation', count_family: 'Estimations ajustées pour les modèles {family}', count_general: 'Estimations avec un réglage général',
      breakdown: 'Ce qui occupe le contexte', last: 'Dernière réponse', branch: 'Cette conversation', capabilities: 'Ce modèle', activity: 'Outils et activité',
      f_model: 'Modèle', f_input: 'Lu par le modèle', f_output: 'Écrit', f_reasoning: 'dont raisonnement', f_cached: 'Lu depuis le cache', f_total: 'Total', f_speed: 'Vitesse', f_time: 'Durée', f_cost: 'Coût indiqué', f_rounds: 'Appels au modèle', f_thinking: 'Réflexion',
      tokens: '{n} jetons', per_sec: '{n} jetons/s', seconds: '{n} s', not_reported: 'Non indiqué',
      b_messages: 'Messages sur cette branche', b_answers: 'Réponses', b_reported: 'Réponses comptées par le modèle', b_read: 'Jetons lus au total', b_written: 'Jetons écrits au total', b_summarised: 'Anciens messages', b_summarised_yes: 'Résumés par {app}', b_rounds: 'Appels au modèle au total',
      cap_vision: 'Lit les images', cap_file_upload: 'Accepte des fichiers', cap_web_search: 'Cherche sur le web', cap_image_generation: 'Crée des images', cap_code_interpreter: 'Exécute du code', cap_citations: 'Cite ses sources', cap_builtin_tools: 'Outils intégrés', cap_memory: 'Mémoire',
      tools_n: '{n} outils associés', knowledge_n: '{n} bases de connaissances associées', no_caps: 'Aucune fonctionnalité n’est définie pour ce modèle.', model_missing: 'Ce modèle ne figure pas parmi ceux que vous pouvez utiliser.',
      m_tools: 'Outils', m_filters: 'Filtres', m_actions: 'Actions', m_pipe: 'Répond via', fn_global: 'partout', fn_toggle: 'si activé',
      a_calls: 'Appels d’outils', a_failed: 'en échec', a_denied: 'refusés', a_running: 'en cours', a_searches: 'Recherches web', a_pages: 'Pages lues', a_knowledge: 'Recherches dans les connaissances', a_sources: 'Sources utilisées', a_code: 'Exécutions de code', a_images: 'Images créées', a_auto: 'Ajoutés par {app}', a_tool_tokens: 'Résultats d’outils dans la conversation',
      a_none: 'Aucun outil, recherche ni source dans cette conversation.', a_queries: 'Recherché', a_more: '+{n} de plus', a_conn: 'Connexion impossible : {what}', a_args: 'Entrée', a_result: 'Résultat', a_empty: '(vide)', a_cut: '… {n} caractères de plus', a_noquery: '(requête non enregistrée)',
      s_ok: 'terminé', s_failed: 'échec', s_noresult: 'sans résultat', s_denied: 'refusé', s_running: 'en cours', s_prompted: 'appelé via le prompt',
      kind_mcp: 'MCP', kind_workspace: 'Outil de l’espace de travail', kind_server: 'Serveur d’outils', kind_builtin: 'Intégré', kind_provider: 'Exécuté par le fournisseur', kind_auto: 'Automatique', kind_other: 'Outil',
      cat_time: 'Heure', cat_web: 'Recherche web', cat_knowledge: 'Connaissances', cat_files: 'Fichiers de la conversation', cat_memory: 'Mémoire', cat_images: 'Images', cat_code: 'Code', cat_notes: 'Notes', cat_chats: 'Anciennes conversations', cat_channels: 'Canaux', cat_skills: 'Compétences', cat_agents: 'Sous-agents', cat_ask: 'Questions', cat_tasks: 'Tâches', cat_automations: 'Automatisations', cat_calendar: 'Agenda', cat_notify: 'Notifications', cat_make_tools: 'Création d’outils', cat_computer: 'Utilisation de l’ordinateur', cat_mcp: 'MCP',
      last_calls: 'Appels dans cette réponse'
    }
  };
  const icons = {
    gauge: 'M10.5 6a7.5 7.5 0 1 0 7.5 7.5h-7.5V6Z M13.5 10.5H21A7.5 7.5 0 0 0 13.5 3v7.5Z',
    close: 'M6 18 18 6M6 6l12 12',
    refresh: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99'
  };

  /* ---- counting tokens ---- */
  // Fitted against the real tokenizers of GPT-4o, GPT-4, Llama 3.1, Qwen 2.5 and Mistral on 1,109 passages in
  // 20 languages plus code, and checked on 1,109 others: 7 to 12 % average error for a known family, against
  // 19 to 42 % for "characters / 4". The order matches the classes counted in features().
  const TUNING = {
    'GPT-4o': [0.9088, 0.5659, 0.8865, 0.3585, 0.705, 0.5483, 0.4898, 0, 0.7713, 0.7437, 0.4151, 0.3367, 0.4607, 0.3941, 0.3895, 0.4302, 3.0552],
    'GPT-4': [1.0048, 0.7177, 0.8972, 0.2396, 0.454, 0.5865, 1.24, 0, 1.1303, 1.2423, 1.0551, 0.5816, 1.1127, 0.9682, 1.2455, 0.9664, 3.1793],
    'Llama': [1.0314, 0.7562, 0.9158, 0.2167, 0.4214, 0.5453, 0.3189, 0, 0.7663, 0.7706, 0.4455, 0.4051, 1.113, 0.5446, 0.6482, 0.4873, 3.1804],
    'Qwen': [1.0092, 0.7602, 1.3225, 0.2333, 0.3256, 0.6154, 0.6612, 0, 0.6581, 0.9325, 1.0214, 0.4383, 0.4688, 0.539, 1.1718, 0.5763, 3.1813],
    'Mistral': [1.0697, 0.8661, 1.4495, 0.4193, 0.9146, 0.8961, 1.7966, 0.4446, 1.1305, 1.2668, 1.188, 0.4774, 1.1105, 1.1582, 1.3013, 1.0338, 3.2259]
  };
  // Llama's tuning had the smallest worst-case error against all five, so it stands in for unknown families
  const DEFAULT_FAMILY = 'Llama';
  const FAMILIES = [['GPT-4o', /gpt-?4o|gpt-?4\.[15]|gpt-?5|(^|[^a-z])o[1-9](-|$)|chatgpt|gpt-oss/i], ['GPT-4', /gpt-?4(?![o.\d])|gpt-?3\.5|turbo/i], ['Qwen', /qwen|qwq/i], ['Mistral', /mistral|mixtral|codestral|ministral|magistral|devstral|pixtral/i], ['Llama', /llama|meta-/i]];
  const familyOf = (...names) => { for (const name of names) for (const [family, re] of FAMILIES) if (name && re.test(name)) return family; return null; };
  function features(text) {
    const f = new Float64Array(17);
    let run = 0, spaces = 0;
    for (let i = 0; i < text.length; i++) {
      let c = text.charCodeAt(i);
      if (c >= 0xD800 && c <= 0xDBFF && i + 1 < text.length) { c = text.codePointAt(i); i++; }
      const letter = (c >= 65 && c <= 90) || (c >= 97 && c <= 122);
      if (letter) { run++; spaces = 0; continue; }
      if (run) { f[0]++; if (run > 6) f[1] += run - 6; run = 0; }
      // A run of two or more spaces, as in indented code, counts once
      if (c === 32) { if (++spaces === 2) f[5]++; continue; }
      spaces = 0;
      if (c >= 48 && c <= 57) f[2]++;
      else if ((c >= 33 && c <= 47) || (c >= 58 && c <= 64) || (c >= 91 && c <= 96) || (c >= 123 && c <= 126)) f[3]++;
      else if (c === 10) f[4]++;
      else if (c < 128) {}
      else if (c >= 0xC0 && c <= 0x24F) f[6]++;
      else if (c >= 0x1E00 && c <= 0x1EFF) f[7]++;
      else if ((c >= 0x3040 && c <= 0x30FF) || (c >= 0x3400 && c <= 0x4DBF) || (c >= 0x4E00 && c <= 0x9FFF) || (c >= 0xF900 && c <= 0xFAFF) || (c >= 0x3000 && c <= 0x303F) || (c >= 0xFF00 && c <= 0xFFEF)) f[8]++;
      else if (c >= 0xAC00 && c <= 0xD7AF) f[9]++;
      else if (c >= 0x370 && c <= 0x3FF) f[10]++;
      else if (c >= 0x400 && c <= 0x52F) f[11]++;
      else if (c >= 0x590 && c <= 0x5FF) f[12]++;
      else if ((c >= 0x600 && c <= 0x6FF) || (c >= 0x750 && c <= 0x77F) || (c >= 0x8A0 && c <= 0x8FF) || (c >= 0xFB50 && c <= 0xFDFF) || (c >= 0xFE70 && c <= 0xFEFC)) f[13]++;
      else if (c >= 0x900 && c <= 0x97F) f[14]++;
      else if (c >= 0xE00 && c <= 0xE7F) f[15]++;
      else if (c >= 0x1F000 || (c >= 0x2600 && c <= 0x27BF)) f[16]++;
      // Anything else, such as other Indic scripts, counts like Devanagari
      else f[14]++;
    }
    if (run) { f[0]++; if (run > 6) f[1] += run - 6; }
    return f;
  }
  const DATA_URI = /data:[\w/+.;=%-]*;base64,[A-Za-z0-9+/=]*/g;
  function countTokens(value, family) {
    if (value == null || value === '') return 0;
    let text = typeof value === 'string' ? value : (() => { try { return JSON.stringify(value); } catch { return String(value); } })();
    text = text.replace(DATA_URI, '');
    if (!text) return 0;
    const f = features(text), w = TUNING[family] || TUNING[DEFAULT_FAMILY];
    let sum = 0;
    for (let i = 0; i < f.length; i++) sum += f[i] * w[i];
    return Math.max(1, Math.round(sum));
  }
  const language = () => {
    if (STRINGS[CONFIG.language]) return CONFIG.language;
    let locale = '';
    try { locale = localStorage.getItem('locale') || ''; } catch {}
    locale = String(locale || document.documentElement.lang || navigator.language || 'en').toLowerCase();
    return STRINGS[locale.slice(0, 2)] ? locale.slice(0, 2) : 'en';
  };
  let lang = 'en';
  // The platform's own name, as its administrator set it (WEBUI_NAME), so the words say "EveGPT" rather than "Open WebUI"
  let appName = String(CONFIG.platformName || '').trim() || 'Open WebUI';
  const resolveAppName = async () => {
    if (String(CONFIG.platformName || '').trim()) return;
    try {
      // Open WebUI appends " (Open WebUI)" to a custom WEBUI_NAME
      const name = String((await api('/api/config'))?.name || '').replace(/\s*\(Open WebUI\)\s*$/, '').trim();
      if (name && name !== appName) { appName = name; if (typeof render === 'function' && dialog && !dialog.hidden) render(); }
    } catch {}
  };
  const t = (key, vars) => {
    const text = String(STRINGS[lang][key] ?? STRINGS.en[key] ?? key).replace(/\{app\}/g, appName);
    return vars ? text.replace(/\{(\w+)\}/g, (whole, name) => name in vars ? vars[name] : whole) : text;
  };
  const tn = (key, n, vars) => t(key + (n === 1 ? '_one' : '_other'), {n, ...vars});
  const svg = name => {
    const node = document.createElementNS('http://www.w3.org/2000/svg','svg');
    for (const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':name === 'check' ? '2.5' : '1.5','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'})) node.setAttribute(key,value);
    const path = document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d',icons[name]); node.append(path);
    return node;
  };
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const setLabel = (node, label) => { node.setAttribute('aria-label', label); node.title = label; };
  const iconButton = (label, name, action, cls = 'cv-icon') => {
    const node = el('button', cls); node.type = 'button';
    setLabel(node, label);
    node.append(svg(name)); node.addEventListener('click', action);
    return node;
  };
  const textButton = (label, action) => {
    const node = el('button', 'cv-btn', label); node.type = 'button';
    node.addEventListener('click', action); return node;
  };
  // Names from other people's uploads can be in any script; each one gets its own direction
  const nameSpan = (cls, text) => { const node = el('span', cls, text); node.dir = 'auto'; return node; };
  /* ---- talking to Open WebUI ---- */
  const token = () => { try { return localStorage.token || ''; } catch { return ''; } };
  const authHeaders = () => token() ? {Authorization: 'Bearer ' + token()} : {};
  const sameOrigin = url => { try { return new URL(url, location.href).origin === location.origin; } catch { return false; } };
  const RETRY_STATUS = new Set([408, 429, 502, 503, 504]);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  // A fetch that gives up after a while and tries a failed read once more. The sign-in token goes only to this site.
  async function request(url, options = {}, timeout = 30000) {
    const signal = options.signal, reading = !options.method || options.method === 'GET';
    const own = sameOrigin(url);
    const headers = {...(options.headers || {}), ...(own ? authHeaders() : {})};
    for (let attempt = 0; ; attempt++) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      const timer = new AbortController(), stop = () => timer.abort();
      signal?.addEventListener('abort', stop, {once: true});
      const clock = setTimeout(stop, timeout);
      try {
        const response = await fetch(url, {...options, headers, credentials: own ? 'same-origin' : 'omit', signal: timer.signal});
        if (!(reading && attempt < 1 && RETRY_STATUS.has(response.status))) return response;
      } catch (error) {
        if (signal?.aborted || !reading || attempt >= 1) throw error;
      } finally { clearTimeout(clock); signal?.removeEventListener('abort', stop); }
      await pause(600);
    }
  }
  async function api(path, signal, options = {}) {
    const response = await request(path, {headers: {Accept: 'application/json', ...(options.body ? {'Content-Type': 'application/json'} : {})}, signal, ...options});
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    return response.json();
  }
  async function fetchBlob(url, signal) {
    const response = await request(url, {signal}, 120000);
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    return response.blob();
  }
  // Reads at most `limit` bytes: asks for a range, and stops reading if the server sends the whole file anyway
  const chatIdFromPath = () => { const match = /^\/c\/([^/?#]+)/.exec(location.pathname); return match ? decodeURIComponent(match[1]) : null; };
  const isChatRoute = () => location.pathname === '/' || /^\/c\//.test(location.pathname);
  const decodeEntities = text => text.replace(/&(quot|amp|lt|gt|#39|#x27|#x2F);/g, (_, name) => ({quot:'"',amp:'&',lt:'<',gt:'>','#39':"'",'#x27':"'",'#x2F':'/'})[name]);
  const formatBytes = n => {
    if (!n) return '0 B';
    if (n < 1024) return n + ' B';
    const units = ['KB', 'MB', 'GB', 'TB']; let value = n / 1024, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    try { return new Intl.NumberFormat(lang, {maximumFractionDigits: value < 10 ? 1 : 0}).format(value) + ' ' + units[unit]; } catch { return value.toFixed(1) + ' ' + units[unit]; }
  };
  const toMs = seconds => !seconds ? 0 : seconds > 1e14 ? seconds / 1e6 : seconds > 1e11 ? seconds : seconds * 1000;
  const formatDate = (seconds, long) => {
    const ms = toMs(seconds); if (!ms) return '';
    try { return new Date(ms).toLocaleString(lang, long ? {dateStyle: 'medium', timeStyle: 'short'} : {dateStyle: 'medium'}); } catch { return ''; }
  };

  /* ---- what the chat sends to the model ---- */
  const isNum = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const firstNum = (object, keys) => { for (const key of keys) { const value = key.split('.').reduce((o, k) => o?.[k], object); if (isNum(value)) return value; } return null; };
  const usageOf = message => { const usage = message?.usage ?? message?.info?.usage; return usage && typeof usage === 'object' ? usage : null; };
  const INPUT = ['input_tokens', 'prompt_tokens', 'prompt_eval_count', 'usage.prompt_tokens'], OUTPUT = ['output_tokens', 'completion_tokens', 'eval_count'];
  function readUsage(usage) {
    if (!usage) return null;
    let input = firstNum(usage, INPUT), output = firstNum(usage, OUTPUT);
    const total = firstNum(usage, ['total_tokens']);
    if (input == null && total != null && output != null) input = Math.max(0, total - output);
    const reasoning = firstNum(usage, ['completion_tokens_details.reasoning_tokens', 'output_tokens_details.reasoning_tokens', 'reasoning_tokens']);
    const cached = firstNum(usage, ['prompt_tokens_details.cached_tokens', 'input_tokens_details.cached_tokens', 'cache_read_input_tokens', 'cached_tokens']);
    let speed = firstNum(usage, ['response_token/s']);
    const evalCount = firstNum(usage, ['eval_count']), evalNs = firstNum(usage, ['eval_duration']);
    if (speed == null && evalCount && evalNs) speed = evalCount / (evalNs / 1e9);
    const totalNs = firstNum(usage, ['total_duration']);
    if (input == null && output == null && total == null) return null;
    // Across tool-call rounds Open WebUI adds input_tokens up, but keeps the provider's own prompt_tokens from the
    // last round: that one is what the model last held in its context
    const prompt = firstNum(usage, ['prompt_tokens', 'prompt_eval_count']);
    let cost = null;
    for (const key of ['cost', 'total_cost']) { const value = usage[key]; if (typeof value === 'number' && Number.isFinite(value) && value > 0) { cost = value; break; } }
    return {input, output, prompt, cost, total: total ?? (input != null || output != null ? (input || 0) + (output || 0) : null), reasoning, cached, speed, seconds: totalNs ? totalNs / 1e9 : null};
  }
  // The path from the first message to the one shown, as Open WebUI follows it
  function branchOf(chat) {
    const history = chat?.chat?.history, messages = history?.messages;
    if (!messages || typeof messages !== 'object') return [];
    let id = chat.current_message_id || history.currentId;
    const out = [], seen = new Set();
    while (typeof id === 'string' && id && !seen.has(id) && seen.size < 20000) {
      seen.add(id);
      const message = Object.prototype.hasOwnProperty.call(messages, id) ? messages[id] : null;
      if (!message || typeof message !== 'object') break;
      out.push(message); id = message.parentId;
    }
    return out.reverse();
  }
  // What of a saved output item goes back to the model: the text, a call's name and arguments, a result's text
  const partsText = value => Array.isArray(value) ? value.map(part => typeof part === 'string' ? part : typeof part?.text === 'string' ? part.text : '').join('') : typeof value === 'string' ? value : '';
  const itemText = item => {
    if (!item || typeof item !== 'object') return '';
    if (item.type === 'message') return partsText(item.content);
    if (item.type === 'function_call') return String(item.name || '') + ' ' + (typeof item.arguments === 'string' ? item.arguments : (() => { try { return JSON.stringify(item.arguments ?? ''); } catch { return ''; } })());
    if (item.type === 'function_call_output') return partsText(item.output) || (typeof item.output === 'object' ? (() => { try { return JSON.stringify(item.output); } catch { return ''; } })() : '');
    if (item.type === 'open_webui:code_interpreter') return [item.code, typeof item.output === 'string' ? item.output : item.output ? JSON.stringify(item.output) : ''].filter(Boolean).join('\n');
    try { return JSON.stringify(item); } catch { return ''; }
  };
  // Each call and each result also costs a few tokens of framing
  const ITEM_OVERHEAD = {function_call: 8, function_call_output: 4};
  const textOf = message => {
    // Structured output already holds the visible text and the tool calls; reasoning blocks aren't sent back
    if (Array.isArray(message.output) && message.output.length) return message.output.filter(part => part?.type !== 'reasoning').map(itemText);
    const content = typeof message.content === 'string' ? message.content.replace(/<details\b[^>]*type="reasoning"[^>]*>[\s\S]*?<\/details>/gi, '') : message.content;
    return [content, message.tool_calls].filter(Boolean);
  };
  const messageTokens = (message, family) => 4 + textOf(message).reduce((sum, part) => sum + countTokens(part, family), 0) + (Array.isArray(message.output) ? message.output.reduce((sum, item) => sum + (ITEM_OVERHEAD[item?.type] || 0), 0) : 0);
  const imagesIn = message => (Array.isArray(message.files) ? message.files.filter(file => file?.type === 'image' || String(file?.content_type || file?.meta?.content_type || '').startsWith('image/')).length : 0) + ((typeof message.content === 'string' ? message.content.match(/!\[[^\]]*\]\(/g) : null)?.length || 0);
  // "gpt-4o* = 128000, llama3* = 128k": the first pattern that matches wins
  function adminWindow(...names) {
    const rules = String(CONFIG.contextWindows || '').split(/[\n,;]+/).map(line => line.trim()).filter(Boolean).map(line => {
      const match = /^(.+?)\s*[=:]\s*([\d.]+)\s*([km]?)\s*$/i.exec(line);
      if (!match) return null;
      const n = Math.round(parseFloat(match[2]) * ({k: 1000, m: 1000000}[match[3].toLowerCase()] || 1));
      const pattern = new RegExp('^' + match[1].trim().replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$', 'i');
      return n > 0 ? {pattern, n} : null;
    }).filter(Boolean);
    for (const rule of rules) for (const name of names) if (name && rule.pattern.test(name)) return rule.n;
    return null;
  }
  const positive = value => { const n = typeof value === 'string' ? parseInt(value, 10) : value; return Number.isSafeInteger(n) && n > 0 ? n : null; };
  function windowOf(modelId, model, chat, settings, baseModel) {
    const raw = model?.openai || model?.ollama || model || {};
    const base = model?.info?.base_model_id;
    const admin = adminWindow(modelId, base, model?.name);
    if (admin) return {tokens: admin, source: 'admin'};
    const fromModel = positive(model?.info?.params?.num_ctx);
    if (fromModel) return {tokens: fromModel, source: 'model'};
    const fromChat = positive(chat?.chat?.params?.num_ctx);
    if (fromChat) return {tokens: fromChat, source: 'chat'};
    // A person's own context size only reaches models served by Ollama
    const fromUser = positive(settings?.ui?.params?.num_ctx ?? settings?.params?.num_ctx);
    if (fromUser && (model?.owned_by === 'ollama' || model?.ollama)) return {tokens: fromUser, source: 'user'};
    const provider = positive(raw.context_length) || positive(raw.top_provider?.context_length) || positive(model?.context_length) || positive(model?.info?.meta?.context_length);
    if (provider) return {tokens: provider, source: 'provider'};
    // A model made in the workspace on top of another one has the window of the one underneath
    if (baseModel && baseModel !== model) { const below = windowOf(base, baseModel, null, settings, null); if (below.tokens && below.source !== 'chat') return below; }
    return {tokens: null, source: null};
  }
  /* ---- tools, searches and functions, read from what each answer saved ---- */
  // Open WebUI saves a native tool call as a function_call item and its result as a function_call_output with the
  // same call_id. The kind of tool isn't saved, so it is told from the name: the built-ins below, the workspace
  // tools and tool servers this person can see, and MCP servers, whose tools are named "<server id>_<tool>".
  const BUILTIN_TOOLS = {
    time: ['get_current_timestamp', 'calculate_timestamp'],
    web: ['search_web', 'fetch_url'],
    knowledge: ['kb_exec', 'list_knowledge', 'list_knowledge_bases', 'search_knowledge_bases', 'query_knowledge_bases', 'search_knowledge_files', 'query_knowledge_files', 'grep_knowledge_files', 'view_knowledge_file'],
    files: ['list_chat_files', 'query_chat_files', 'grep_chat_files', 'view_file', 'display_file'],
    memory: ['search_memories', 'list_memory_paths', 'read_memory_path', 'list_memories', 'update_memory', 'add_memory', 'replace_memory_content', 'delete_memory', 'recall_memories'],
    images: ['generate_image', 'edit_image'],
    code: ['execute_code'],
    notes: ['search_notes', 'view_note', 'write_note', 'replace_note_content'],
    chats: ['search_chats', 'view_chat'],
    channels: ['search_channels', 'search_channel_messages', 'view_channel_thread', 'view_channel_message'],
    skills: ['view_skill', 'read_skill_file', 'grep_skill_files', 'update_skill_files', 'create_skill', 'list_available_skills'],
    agents: ['delegate_task', 'timer'],
    ask: ['ask_user'],
    tasks: ['create_tasks', 'update_task'],
    automations: ['create_automation', 'update_automation', 'list_automations', 'toggle_automation', 'delete_automation'],
    calendar: ['search_calendar_events', 'create_calendar_event', 'update_calendar_event', 'delete_calendar_event'],
    notify: ['notify'],
    make_tools: ['create_tool']
  };
  const BUILTIN_BY_NAME = new Map();
  for (const [category, names] of Object.entries(BUILTIN_TOOLS)) for (const name of names) if (!BUILTIN_BY_NAME.has(name)) BUILTIN_BY_NAME.set(name, category);
  BUILTIN_BY_NAME.set('list_attached_knowledge', 'knowledge');
  // Tools the model's provider runs itself (OpenAI Responses), saved as their own item types
  const PROVIDER_ITEMS = {web_search_call: 'web', file_search_call: 'files', computer_call: 'computer', code_interpreter_call: 'code', image_generation_call: 'images', mcp_call: 'mcp'};
  const OK = new Set(['completed', 'success', 'answered', 'done']), FAILED = new Set(['failed', 'error', 'incomplete']), DENIED = new Set(['rejected', 'cancelled', 'canceled', 'denied']);
  const RUNNING = new Set(['in_progress', 'pending', 'queued', 'requires_approval', 'searching', 'running']);
  // Who provides each tool name, from the lists this person is allowed to read
  function catalogOf(tools) {
    const exact = new Map(), prefixes = [], byId = new Map();
    for (const tool of Array.isArray(tools) ? tools : []) {
      const id = String(tool?.id || ''); if (!id) continue;
      const name = String(tool.name || id);
      if (id.startsWith('server:mcp:')) { const server = id.slice(11); if (server) prefixes.push({prefix: server + '_', kind: 'mcp', source: name}); byId.set(id, {kind: 'mcp', source: name}); continue; }
      const kind = id.startsWith('server:') ? 'server' : 'workspace';
      byId.set(id, {kind, source: name});
      const own = kind === 'server' ? id.slice(7) : id;
      for (const spec of Array.isArray(tool.specs) ? tool.specs : []) {
        const fn = typeof spec?.name === 'string' ? spec.name : '';
        if (!fn) continue;
        if (!exact.has(fn)) exact.set(fn, {kind, source: name, id});
        // Open WebUI puts the tool's id in front of a name two tools share
        exact.set(own + '_' + fn, {kind, source: name, id});
      }
    }
    prefixes.sort((a, b) => b.prefix.length - a.prefix.length);
    return {exact, prefixes, byId};
  }
  function classify(name, catalog, hidden) {
    const known = catalog.exact.get(name);
    if (known) return {kind: known.kind, source: known.source};
    for (const entry of catalog.prefixes) if (name.startsWith(entry.prefix) && name.length > entry.prefix.length) return {kind: 'mcp', source: entry.source, tool: name.slice(entry.prefix.length)};
    const category = BUILTIN_BY_NAME.get(name);
    if (category) return {kind: hidden ? 'auto' : 'builtin', category};
    if (hidden) return {kind: 'auto', category: null};
    return {kind: 'other'};
  }
  const asText = value => {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(part => typeof part === 'string' ? part : typeof part?.text === 'string' ? part.text : part?.type === 'input_image' || part?.image_url ? '[image]' : (() => { try { return JSON.stringify(part); } catch { return ''; } })()).join('\n');
    try { return JSON.stringify(value); } catch { return String(value); }
  };
  const parseArgs = value => { if (value && typeof value === 'object') return value; try { const parsed = JSON.parse(value); return parsed && typeof parsed === 'object' ? parsed : {}; } catch { return {}; } };
  const failedResult = text => { const trimmed = text.trim(); if (!trimmed.startsWith('{')) return false; try { const value = JSON.parse(trimmed); return value && typeof value === 'object' && !Array.isArray(value) && typeof value.error === 'string' && Object.keys(value).length <= 2; } catch { return false; } };
  const statusOf = (raw, fallback) => { const s = String(raw || '').toLowerCase(); return OK.has(s) ? 'ok' : FAILED.has(s) ? 'failed' : DENIED.has(s) ? 'denied' : RUNNING.has(s) ? 'running' : fallback; };
  const seconds = (start, end, duration) => isNum(duration) ? duration : isNum(start) && isNum(end) && end >= start ? end - start : null;
  // Older Open WebUI wrote tool calls into the answer text as <details type="tool_calls" ...> blocks
  const DETAILS = /<details\b([^>]*)>([\s\S]*?)<\/details>/gi;
  const attrsOf = text => { const out = {}; text.replace(/([\w-]+)="([^"]*)"/g, (_, key, value) => { out[key] = decodeEntities(value); return ''; }); return out; };
  // Everything one answer did
  function activityOf(message, catalog, family) {
    const calls = [], other = {code: 0, reasoning: 0, thinking: 0, provider: 0};
    const done = message.done !== false;
    let rounds = 0, inCalls = false;
    const output = Array.isArray(message.output) ? message.output : [];
    const results = new Map();
    for (const item of output) if (item?.type === 'function_call_output' && item.call_id) results.set(item.call_id, item);
    const paired = new Set();
    for (const item of output) {
      if (!item || typeof item !== 'object') continue;
      const type = item.type;
      if (type === 'function_call') {
        const name = String(item.name || '?'), hidden = item.hidden === true;
        if (!hidden && !inCalls) { rounds++; inCalls = true; }
        const result = results.get(item.call_id ?? item.id);
        if (result) paired.add(result);
        const text = asText(result?.output);
        let status = statusOf(item.status, done ? 'ok' : 'running');
        if (status === 'ok' && result) status = statusOf(result.status, 'ok');
        if (status === 'ok' && !result && done) status = 'noresult';
        if (status === 'ok' && failedResult(text)) status = 'failed';
        const args = typeof item.arguments === 'string' ? item.arguments : asText(item.arguments);
        calls.push({name, hidden, ...classify(name, catalog, hidden), status, seconds: seconds(item.started_at, item.ended_at, item.duration), args, result: text,
          tokens: countTokens(name + ' ' + args, family) + 8 + (result ? countTokens(text, family) + 4 : 0), files: (Array.isArray(result?.files) ? result.files.length : 0) + (Array.isArray(result?.embeds) ? result.embeds.length : 0), query: parseArgs(item.arguments)});
        continue;
      }
      if (type === 'function_call_output') continue;
      inCalls = false;
      if (type === 'reasoning') { other.reasoning++; const s = seconds(item.started_at, item.ended_at, item.duration); if (s) other.thinking += s; continue; }
      if (type === 'open_webui:code_interpreter') { other.code++; calls.push({name: 'code_interpreter', kind: 'builtin', category: 'code', status: statusOf(item.status, done ? 'ok' : 'running'), seconds: seconds(item.started_at, item.ended_at, item.duration), args: asText(item.code ?? item.content), result: asText(item.output ?? item.result), tokens: 0, files: 0, query: {}, interpreter: true}); continue; }
      if (PROVIDER_ITEMS[type]) {
        other.provider++;
        const action = item.action || {};
        calls.push({name: type.replace(/_call$/, ''), kind: 'provider', category: PROVIDER_ITEMS[type], status: statusOf(item.status, done ? 'ok' : 'running'), seconds: null, args: asText(action.query ?? action.queries ?? item.queries ?? item.arguments ?? ''), result: '', tokens: 0, files: 0, query: {query: action.query ?? (Array.isArray(item.queries) ? item.queries[0] : null)}});
      }
    }
    // Results whose call wasn't saved still count as a call
    for (const item of results.values()) if (!paired.has(item)) calls.push({name: '?', kind: 'other', status: statusOf(item.status, 'ok'), seconds: null, args: '', result: asText(item.output), tokens: countTokens(asText(item.output), family), files: 0, query: {}});
    if (!output.length && typeof message.content === 'string' && message.content.includes('<details')) {
      for (const match of message.content.matchAll(DETAILS)) {
        const attrs = attrsOf(match[1]);
        if (attrs.type === 'tool_calls') {
          const name = attrs.name || '?', result = attrs.result || '', args = attrs.arguments || '';
          if (!inCalls) { rounds++; inCalls = true; }
          calls.push({name, ...classify(name, catalog, false), status: attrs.done === 'false' ? (done ? 'noresult' : 'running') : failedResult(result) ? 'failed' : 'ok', seconds: null, args, result, tokens: countTokens(args, family) + countTokens(result, family), files: 0, query: parseArgs(args)});
        } else {
          inCalls = false;
          if (attrs.type === 'reasoning') { other.reasoning++; const s = parseFloat(attrs.duration); if (s > 0) other.thinking += s; }
          else if (attrs.type === 'code_interpreter') { other.code++; calls.push({name: 'code_interpreter', kind: 'builtin', category: 'code', status: attrs.done === 'false' && !done ? 'running' : 'ok', seconds: null, args: match[2] || '', result: attrs.output || '', tokens: 0, files: 0, query: {}, interpreter: true}); }
        }
      }
    }
    // Tools called without native function calling leave their result in the answer's sources
    const sources = Array.isArray(message.sources) ? message.sources : [];
    let retrieved = 0, documents = 0;
    const sourceNames = new Set();
    for (const source of sources) {
      if (!source || typeof source !== 'object') continue;
      if (source.tool_result === true) {
        const full = String(source.source?.name || source.metadata?.[0]?.source || '?');
        const slash = full.indexOf('/'), id = slash > 0 ? full.slice(0, slash) : '', fn = slash > 0 ? full.slice(slash + 1) : full;
        const owner = id && catalog.byId.get(id);
        const result = asText(source.document);
        calls.push({name: fn, ...(owner ? {kind: owner.kind, source: owner.source} : classify(fn, catalog, false)), status: failedResult(result) ? 'failed' : 'ok', seconds: null, args: asText(source.metadata?.[0]?.parameters ?? ''), result, tokens: countTokens(result, family), files: 0, query: parseArgs(source.metadata?.[0]?.parameters), prompted: true});
        continue;
      }
      retrieved++;
      documents += Array.isArray(source.document) ? source.document.length : 1;
      const label = source.source?.name || source.source?.id || source.source?.url || source.metadata?.[0]?.source || source.metadata?.[0]?.name;
      if (label) sourceNames.add(String(label));
    }
    // Web searches and knowledge lookups Open WebUI ran before the answer, from its status lines
    const searches = [], pages = new Set();
    let knowledge = 0, connectionErrors = [];
    for (const status of Array.isArray(message.statusHistory) ? message.statusHistory : []) {
      if (!status || typeof status !== 'object') continue;
      const action = status.action;
      if (action === 'web_search_queries_generated' || (action === 'queries_generated' && status.type !== 'knowledge')) for (const q of Array.isArray(status.queries) ? status.queries : []) { if (typeof q === 'string' && q.trim() && !searches.includes(q)) searches.push(q); }
      else if (action === 'web_search' && Array.isArray(status.urls)) for (const url of status.urls) pages.add(String(url));
      else if (action === 'knowledge_search') knowledge++;
      else if (action === 'tool_connection' && status.error) connectionErrors.push(String(status.description || ''));
    }
    for (const call of calls) {
      if (call.category !== 'web') continue;
      const q = call.query?.query ?? call.query?.q ?? (Array.isArray(call.query?.queries) ? call.query.queries.join(' · ') : null);
      if (call.name === 'fetch_url' || call.query?.url) { if (call.query?.url) pages.add(String(call.query.url)); }
      else if (typeof q === 'string' && q.trim()) searches.push(q);
      else searches.push('');
    }
    const images = (Array.isArray(message.files) ? message.files.filter(file => file?.type === 'image' || String(file?.content_type || file?.meta?.content_type || '').startsWith('image/')).length : 0);
    return {calls, rounds: rounds + (output.length || message.content ? 1 : 0), searches, pages: pages.size, knowledge, retrieved, documents, sourceNames: [...sourceNames], images, connectionErrors, ...other};
  }
  // The token cost of tool calls and their results inside one saved answer, as sent back to the model later
  function toolTokensOf(message, family) {
    if (Array.isArray(message.output) && message.output.length) return message.output.reduce((sum, item) => sum + (ITEM_OVERHEAD[item?.type] ? countTokens(itemText(item), family) + ITEM_OVERHEAD[item.type] : 0), 0);
    if (typeof message.content !== 'string' || !message.content.includes('type="tool_calls"')) return 0;
    let sum = 0;
    for (const match of message.content.matchAll(DETAILS)) if (/type="tool_calls"/.test(match[1])) sum += countTokens(match[0], family);
    return sum;
  }
  // Adds up the answers of a conversation, tool by tool
  function tally(activities) {
    const byTool = new Map(), totals = {calls: 0, failed: 0, denied: 0, running: 0, hidden: 0, searches: 0, pages: 0, knowledge: 0, retrieved: 0, code: 0, images: 0, rounds: 0, reasoning: 0, thinking: 0, seconds: 0, tokens: 0, errors: [], queries: []};
    for (const a of activities) {
      for (const call of a.calls) {
        if (call.hidden) totals.hidden++; else totals.calls++;
        if (call.status === 'failed' || call.status === 'noresult') totals.failed++;
        if (call.status === 'denied') totals.denied++;
        if (call.status === 'running') totals.running++;
        if (call.seconds) totals.seconds += call.seconds;
        totals.tokens += call.tokens || 0;
        const key = call.kind + '\u0000' + (call.source || call.category || '') + '\u0000' + call.name;
        const row = byTool.get(key) || {name: call.name, kind: call.kind, source: call.source, category: call.category, tool: call.tool, hidden: !!call.hidden, count: 0, failed: 0, denied: 0, seconds: 0, tokens: 0};
        row.count++; if (call.status === 'failed' || call.status === 'noresult') row.failed++; if (call.status === 'denied') row.denied++;
        row.seconds += call.seconds || 0; row.tokens += call.tokens || 0;
        byTool.set(key, row);
      }
      totals.searches += a.searches.length; totals.pages += a.pages; totals.knowledge += a.knowledge; totals.retrieved += a.retrieved; totals.code += a.code; totals.images += a.images;
      totals.rounds += a.rounds; totals.reasoning += a.reasoning; totals.thinking += a.thinking;
      for (const q of a.searches) if (q && !totals.queries.includes(q)) totals.queries.push(q);
      for (const error of a.connectionErrors) if (error && !totals.errors.includes(error)) totals.errors.push(error);
    }
    const order = {mcp: 0, workspace: 1, server: 2, builtin: 3, provider: 4, other: 5, auto: 6};
    const tools = [...byTool.values()].sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9) || b.count - a.count || a.name.localeCompare(b.name));
    return {tools, ...totals};
  }
  // The filters, actions and pipe that apply to a model, from the functions this person can read
  function functionsFor(model, functions) {
    if (!model || !Array.isArray(functions)) return null;
    const meta = model.info?.meta || {};
    const toggles = new Set((Array.isArray(model.filters) ? model.filters : []).map(f => f?.id).filter(Boolean));
    const own = id => Array.isArray(meta[id]) ? meta[id] : [];
    const pick = (type, ids) => functions.filter(f => f && f.type === type && f.is_active && (f.is_global || ids.includes(f.id)));
    const filters = pick('filter', own('filterIds')).map(f => ({name: f.name || f.id, global: !!f.is_global, toggle: toggles.has(f.id)}));
    const actions = pick('action', own('actionIds')).map(f => ({name: f.name || f.id, global: !!f.is_global}));
    let pipe = null;
    if (model.pipe || model.owned_by === 'function') {
      const id = String(model.info?.base_model_id || model.id || '').split('.')[0];
      const fn = functions.find(f => f?.id === id && f.type === 'pipe');
      pipe = fn ? fn.name || fn.id : id || null;
    }
    return {filters, actions, pipe};
  }
  const CAPS = ['vision', 'file_upload', 'web_search', 'image_generation', 'code_interpreter', 'citations', 'builtin_tools', 'memory'];
  function snapshotOf(chat, models, settings, folder, pick, extra = {}) {
    const branch = branchOf(chat);
    const used = [...new Set(branch.filter(m => m.role === 'assistant' && typeof m.model === 'string').map(m => m.model))];
    const listed = Array.isArray(chat?.chat?.models) ? chat.chat.models.filter(id => typeof id === 'string' && id) : [];
    const choices = [...new Set([...listed, ...used])];
    const lastAnswer = [...branch].reverse().find(m => m.role === 'assistant');
    const modelId = choices.includes(pick) ? pick : lastAnswer?.model || listed[0] || null;
    const model = models.find(each => each?.id === modelId) || null;
    const family = familyOf(modelId, model?.info?.base_model_id, model?.name);
    const count = value => countTokens(value, family);
    // The system prompt: the model's own, plus the chat's or the person's, plus the folder's
    const parts = [model?.info?.params?.system, chat?.chat?.params?.system || settings?.ui?.system, folder?.data?.system_prompt].filter(value => typeof value === 'string' && value.trim());
    const system = parts.reduce((sum, part) => sum + count(part), 0) + (parts.length ? 4 : 0);
    // Open WebUI replaces older messages with a summary once it compacts a chat
    let start = 0, summary = '';
    branch.forEach((message, index) => { const value = message.contextSummary || message.context_summary; if (typeof value === 'string' && value.trim()) { start = index; summary = value; } });
    const active = branch.slice(start);
    const summaryTokens = summary ? count(summary) + 4 : 0;
    // In a chat with several models, each answer of the last turn is a sibling; the chosen model's one counts
    const catalog = catalogOf(extra.tools);
    // What the model held in context for an answer: the last round's prompt when the provider gave it, and otherwise
    // the reported input, unless tool rounds were added together into it
    const contextCount = m => { const u = readUsage(usageOf(m)); if (!u) return null; if (u.prompt != null) return u.prompt; return u.input != null && activityOf(m, catalog, family).rounds <= 1 ? u.input : null; };
    const siblings = id => Object.values(chat?.chat?.history?.messages || {}).filter(m => m?.parentId === id && m.role === 'assistant');
    let anchor = -1, anchorMessage = null;
    for (let i = active.length - 1; i >= 0; i--) {
      const m = active[i];
      if (m.role !== 'assistant') continue;
      const candidate = m.model === modelId || !modelId ? m : siblings(m.parentId).find(s => s.model === modelId) || null;
      if (candidate && contextCount(candidate) != null) { anchor = i; anchorMessage = candidate; break; }
    }
    const tokensOf = list => list.reduce((sum, m) => sum + messageTokens(m, family), 0);
    const toolsOf = list => list.reduce((sum, m) => sum + Math.min(messageTokens(m, family), toolTokensOf(m, family)), 0);
    let categories, total, reported = null;
    const lastIndex = active.length - 1;
    const tail = active[lastIndex];
    const streaming = tail?.role === 'assistant' && tail.done === false;
    if (anchor >= 0) {
      reported = contextCount(anchorMessage);
      const earlier = active.slice(0, anchor), later = active.slice(anchor + 1);
      const before = tokensOf(earlier), toolsBefore = toolsOf(earlier);
      // The answer's own tool calls and results were already in the prompt of its last round
      const answerAll = messageTokens(anchorMessage, family), answerTools = Math.min(answerAll, toolTokensOf(anchorMessage, family));
      const base = system + summaryTokens + before + answerTools;
      // When the estimate runs high, it is scaled down to the model's count rather than going over it
      const scale = base > reported && base > 0 ? reported / base : 1;
      const newer = tokensOf(later), toolsNewer = toolsOf(later);
      categories = [
        {key: 'system', tokens: system * scale, kind: 'estimated'},
        {key: 'extras', tokens: Math.max(0, reported - base), kind: 'inferred'},
        {key: 'summary', tokens: summaryTokens * scale, kind: 'estimated'},
        {key: 'messages', tokens: (before - toolsBefore) * scale, kind: 'estimated'},
        {key: 'tools', tokens: (toolsBefore + answerTools + toolsNewer) * scale, kind: 'estimated'},
        {key: 'latest', tokens: (answerAll - answerTools) * scale, kind: 'estimated'},
        {key: 'newer', tokens: (newer - toolsNewer) * scale, kind: 'estimated'}
      ];
    } else {
      const latestIndex = tail?.role === 'assistant' ? lastIndex : -1;
      const earlier = latestIndex >= 0 ? active.slice(0, latestIndex) : active;
      const tailAll = latestIndex >= 0 ? messageTokens(tail, family) : 0, tailTools = latestIndex >= 0 ? Math.min(tailAll, toolTokensOf(tail, family)) : 0;
      categories = [
        {key: 'system', tokens: system, kind: 'estimated'},
        {key: 'summary', tokens: summaryTokens, kind: 'estimated'},
        {key: 'messages', tokens: tokensOf(earlier) - toolsOf(earlier), kind: 'estimated'},
        {key: 'tools', tokens: toolsOf(earlier) + tailTools, kind: 'estimated'},
        {key: 'latest', tokens: tailAll - tailTools, kind: 'estimated'}
      ];
    }
    categories = categories.filter(c => c.tokens > 0.5).map(c => ({...c, tokens: Math.round(c.tokens)}));
    total = categories.reduce((sum, c) => sum + c.tokens, 0);
    // The last answer's own numbers, from the chosen model
    let last = null;
    for (let i = branch.length - 1; i >= 0 && !last; i--) {
      const m = branch[i];
      if (m.role !== 'assistant') continue;
      const candidate = m.model === modelId || !modelId ? m : siblings(m.parentId).find(s => s.model === modelId);
      if (candidate) last = {model: candidate.model || modelId, ...(readUsage(usageOf(candidate)) || {}), activity: activityOf(candidate, catalog, family)};
    }
    // How much a recent exchange costs, to say how many more fit
    const exchanges = [];
    for (let i = active.length - 1; i > 0 && exchanges.length < 5; i--) if (active[i].role === 'assistant' && active[i - 1]?.role === 'user') exchanges.push(messageTokens(active[i], family) + messageTokens(active[i - 1], family));
    const perExchange = exchanges.length ? exchanges.reduce((a, b) => a + b, 0) / exchanges.length : 0;
    const answers = branch.filter(m => m.role === 'assistant');
    // Every answer the chosen model gave on this branch, the other models' answers in a shared turn left out
    const mine = [];
    for (const m of answers) { const own = m.model === modelId || !modelId ? m : siblings(m.parentId).find(s => s.model === modelId); if (own && !mine.includes(own)) mine.push(own); }
    const activity = tally(mine.map(m => activityOf(m, catalog, family)));
    const counted = answers.map(m => readUsage(usageOf(m))).filter(Boolean);
    const lastUser = [...branch].reverse().find(m => m.role === 'user');
    const sources = Array.isArray(anchorMessage?.sources) ? anchorMessage.sources.length : Array.isArray(lastAnswer?.sources) ? lastAnswer.sources.length : 0;
    const caps = model?.info?.meta?.capabilities || {};
    return {
      empty: !branch.length, branch, choices, modelId, modelName: model?.name || modelId, model, family,
      window: windowOf(modelId, model, chat, settings, model?.info?.base_model_id ? models.find(each => each?.id === model.info.base_model_id) || null : null), threshold: isNum(chat?.context_usage?.threshold) && chat.context_usage.threshold > 0 ? chat.context_usage.threshold : null,
      categories, total, reported, streaming, summary: !!summary, last, perExchange,
      stats: {messages: branch.length, answers: answers.length, counted: counted.length, read: counted.reduce((s, u) => s + (u.input || 0), 0), written: counted.reduce((s, u) => s + (u.output || 0), 0), cost: counted.reduce((s, u) => s + (u.cost || 0), 0)},
      images: anchor >= 0 ? 0 : branch.reduce((sum, m) => sum + imagesIn(m), 0), sources,
      caps: CAPS.filter(key => caps[key] === true), tools: (model?.info?.meta?.toolIds || []).length, knowledge: (model?.info?.meta?.knowledge || []).length,
      hasLastUser: !!lastUser, activity, functions: functionsFor(model, extra.functions),
      toolNames: (Array.isArray(model?.info?.meta?.toolIds) ? model.info.meta.toolIds : []).map(id => catalog.byId.get(id)?.source || (String(id).startsWith('server:mcp:') ? String(id).slice(11) : id))
    };
  }

  /* ---- the panel ---- */
  let dialog, head, body, live, refreshButton, soloClose, modelSelect, updatedEl, controller = null;
  let snapshot = null, loading = false, loadedAt = 0, loadedChat = null, builtLang = '', opener = null, headerButton = null;
  let modelsCache = null, modelsAt = 0, failure = '', pick = null;
  const folders = new Map();
  const n = value => Math.round(value).toLocaleString(lang);
  function build() {
    lang = builtLang = language();
    dialog = el('div', 'ci-dialog'); dialog.id = 'owui-chat-inspector'; dialog.hidden = true; dialog.lang = lang;
    dialog.setAttribute('role', 'tabpanel'); dialog.setAttribute('aria-labelledby', 'owui-chat-inspector-tab');
    head = el('div', 'ci-head');
    const title = el('h2', 'ci-heading', t('context')); title.id = 'ci-title';
    modelSelect = el('select', 'ci-select'); modelSelect.setAttribute('aria-label', t('model')); modelSelect.hidden = true;
    modelSelect.addEventListener('change', () => { pick = modelSelect.value; load(true); });
    updatedEl = el('span', 'ci-updated'); updatedEl.setAttribute('aria-live', 'polite');
    refreshButton = iconButton(t('refresh'), 'refresh', () => load(true));
    soloClose = iconButton(t('close_context'), 'close', () => popOpen ? closePop() : closeTab(), 'ci-icon ci-close'); soloClose.hidden = true;
    head.append(title, modelSelect, el('span', 'ci-spacer'), updatedEl, refreshButton, soloClose);
    body = el('div', 'ci-body'); body.tabIndex = -1;
    live = el('div', 'ci-sr'); live.setAttribute('aria-live', 'polite');
    dialog.append(head, body, live);
    dialog.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !event.defaultPrevented && dialog.classList.contains('ci-solo')) { event.preventDefault(); deactivate(); headerButton?.focus({preventScroll: true}); }
    });
    document.body.append(dialog);
  }
  async function modelsList(signal) {
    if (modelsCache && Date.now() - modelsAt < 300000) return modelsCache;
    try { const data = await api('/api/models', signal); modelsCache = Array.isArray(data?.data) ? data.data : []; modelsAt = Date.now(); }
    catch (error) { if (signal.aborted) throw error; modelsCache = modelsCache || []; }
    return modelsCache;
  }
  // The tools and functions this person can see, to tell what kind each called tool is. Either list can be refused
  // or turned off; the panel then names tools without saying where they come from.
  const lists = {};
  async function listOf(path, signal) {
    const entry = lists[path];
    if (entry && Date.now() - entry.at < 300000) return entry.data;
    try { const data = await api(path, signal); lists[path] = {data: Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [], at: Date.now()}; }
    catch (error) { if (signal.aborted) throw error; lists[path] = {data: entry?.data || null, at: Date.now() - 240000}; }
    return lists[path].data;
  }
  async function folderOf(id, signal) {
    if (!id) return null;
    if (!folders.has(id)) folders.set(id, api('/api/v1/folders/' + encodeURIComponent(id), signal).catch(() => null));
    return folders.get(id);
  }
  // quiet: keep the numbers on screen while they are read again
  async function load(quiet) {
    controller?.abort();
    const current = controller = new AbortController(), signal = current.signal;
    const chatId = chatIdFromPath();
    if (chatId !== loadedChat) { snapshot = null; pick = null; quiet = false; }
    loading = true; failure = '';
    if (!quiet) render();
    paintUpdated();
    if (!chatId) { loading = false; loadedChat = null; snapshot = null; render(); return; }
    try {
      const [chat, models, settings, tools, functions] = await Promise.all([
        api('/api/v1/chats/' + encodeURIComponent(chatId), signal),
        modelsList(signal),
        api('/api/v1/users/user/settings', signal).catch(() => null),
        listOf('/api/v1/tools/', signal),
        listOf('/api/v1/functions/', signal)
      ]);
      const folder = await folderOf(chat?.folder_id, signal);
      if (signal.aborted) return;
      snapshot = snapshotOf(chat, models, settings, folder, pick, {tools, functions});
      loadedChat = chatId; loadedAt = Date.now();
    } catch (error) {
      if (signal.aborted) return;
      failure = error.status === 404 ? 'unsaved' : error.status === 401 || error.status === 403 ? 'denied' : 'failed';
      if (failure !== 'failed' || !quiet) snapshot = null;
    }
    if (controller === current) { loading = false; render(); }
  }
  const notice = (title, hint, action) => { const box = el('div', 'ci-notice'); box.append(el('strong', null, title)); if (hint) box.append(el('span', null, hint)); if (action) box.append(action); return box; };
  function paintUpdated() {
    if (!updatedEl) return;
    const streaming = snapshot?.streaming || watching;
    updatedEl.textContent = loading ? t('loading') : streaming ? t('updating') : loadedAt && snapshot ? t('updated', {time: new Date(loadedAt).toLocaleTimeString(lang, {hour: '2-digit', minute: '2-digit', second: '2-digit'})}) : '';
    updatedEl.classList.toggle('ci-busy', !!(loading || streaming));
    refreshButton.disabled = loading || !chatIdFromPath();
  }
  function section(title) { const box = el('section', 'ci-section'); box.append(el('h3', null, title)); body.append(box); return box; }
  function facts(box, rows) {
    const list = el('dl', 'ci-facts');
    for (const [key, value] of rows) { if (value == null) continue; list.append(el('dt', null, key), el('dd', null, value)); }
    box.append(list); return list;
  }
  const tokensText = value => value == null ? t('not_reported') : t('tokens', {n: n(value)});
  function render() {
    if (!dialog) return;
    paintUpdated();
    // What is on screen, for the page and for tests to check against
    dialog.dataset.chat = snapshot && loadedChat ? loadedChat : '';
    dialog.dataset.state = loading ? 'loading' : 'ready';
    const scroll = body.scrollTop;
    body.replaceChildren();
    const s = snapshot;
    if (!chatIdFromPath()) { body.append(notice(t('no_chat'), t('no_chat_hint'))); modelSelect.hidden = true; return; }
    if (!s) {
      if (loading) { body.append(notice(t('loading'))); return; }
      if (failure === 'unsaved') body.append(notice(t('unsaved'), t('unsaved_hint')));
      else if (failure === 'denied') body.append(notice(t('failed'), t('denied')));
      else body.append(notice(t('failed'), t('failed_hint'), textButton(t('retry'), () => load())));
      modelSelect.hidden = true;
      return;
    }
    if (failure === 'failed') body.append(el('p', 'ci-warn', t('failed') + ' · ' + t('failed_hint')));
    // The model picker, for chats that ask several models
    modelSelect.hidden = s.choices.length < 2;
    if (!modelSelect.hidden) {
      const signature = s.choices.join('|');
      if (modelSelect.dataset.choices !== signature) { modelSelect.replaceChildren(...s.choices.map(id => { const option = el('option', null, id); option.value = id; return option; })); modelSelect.dataset.choices = signature; }
      modelSelect.value = s.modelId;
    }
    if (s.empty) { body.append(notice(t('empty_chat'), t('empty_chat_hint'))); return; }
    paintSummary(s);
    paintBreakdown(s);
    paintActivity(s);
    paintLast(s);
    paintConversation(s);
    if (CONFIG.showCapabilities) paintModel(s);
    body.scrollTop = scroll;
  }
  function paintSummary(s) {
    const box = el('section', 'ci-summary'); body.append(box);
    const win = s.window.tokens, pct = win ? s.total / win * 100 : null;
    const state = pct == null ? null : pct >= 100 ? 'full' : pct >= CONFIG.warnPercent ? 'warn' : 'ok';
    const big = el('p', 'ci-big');
    big.append(el('strong', null, win ? t('of_window', {used: n(s.total), window: n(win)}) : t('used_only', {used: n(s.total)})));
    if (pct != null) { const badge = el('span', 'ci-state', t('pct', {pct: pct < 1 && pct > 0 ? '<1' : Math.round(pct)})); badge.dataset.state = state; big.append(badge); }
    box.append(big);
    // One short line: room left, where the window comes from, and whether the model counted
    const line = el('p', 'ci-sub ci-line');
    const bit = (text, title) => { const span = el('span', null, text); if (title) span.title = title; line.append(span); };
    if (win) bit(s.total <= win ? t('free', {n: n(win - s.total)}) : t('over_by', {n: n(s.total - win)}));
    bit(t(win ? 'window_' + s.window.source : 'window_unknown'), win ? null : t('window_unknown_admin'));
    bit(t(s.reported != null ? 'count_reported' : 'count_estimated'), s.reported != null ? t('tokens', {n: n(s.reported)}) : s.family ? t('count_family', {family: s.family}) : t('count_general'));
    box.append(line);
    box.append(grid(s));
    if (state && state !== 'ok') { const warn = el('p', 'ci-alert'); warn.dataset.state = state; warn.append(el('strong', null, t('st_' + state) + '. '), document.createTextNode(t('st_' + state + '_hint'))); box.append(warn); }
    const more = el('p', 'ci-sub ci-line');
    if (s.threshold) { const left = s.threshold - s.total; more.append(el('span', null, t('compaction_at', {n: n(s.threshold)}) + ' · ' + (left > 0 ? t('compaction_left', {n: n(left)}) : t('compaction_past')))); }
    if (win && s.perExchange > 0) {
      const room = Math.min(win, s.threshold || win) - s.total, count = Math.floor(room / s.perExchange);
      more.append(el('span', null, count <= 0 ? t('exchanges_none') : count === 1 ? t('exchanges_one') : t('exchanges', {n: count > 999 ? '999+' : count})));
    }
    if (more.children.length) box.append(more);
  }
  // A 10 by 10 grid, one square per percent, coloured by what fills it
  function grid(s) {
    const win = s.window.tokens, scaleTo = win || s.total || 1;
    const cells = [];
    const shares = s.categories.map(c => ({key: c.key, exact: c.tokens / scaleTo * 100}));
    // As many squares as the percentage shown, shared out by largest remainder
    const exactUsed = shares.reduce((sum, share) => sum + share.exact, 0);
    const used = Math.min(100, Math.max(exactUsed > 0 ? 1 : 0, Math.round(exactUsed)));
    const counts = shares.map(share => Math.floor(share.exact / (exactUsed || 1) * used));
    let left = used - counts.reduce((a, b) => a + b, 0);
    shares.map((share, i) => ({i, rest: share.exact / (exactUsed || 1) * used - counts[i]})).sort((a, b) => b.rest - a.rest).forEach(({i}) => { if (left > 0) { counts[i]++; left--; } });
    // A part that is there but too small for a square borrows one from the largest part
    for (let i = 0; i < counts.length; i++) {
      if (counts[i] || !(shares[i].exact > 0)) continue;
      const largest = counts.indexOf(Math.max(...counts));
      if (counts[largest] > 1) { counts[largest]--; counts[i] = 1; }
    }
    shares.forEach((share, i) => { for (let k = 0; k < counts[i]; k++) cells.push(share.key); });
    const reserveFrom = win && s.threshold && s.threshold < win ? Math.round(s.threshold / win * 100) : 101;
    while (cells.length < 100) cells.push(win ? (cells.length >= reserveFrom ? 'reserve' : 'free') : cells[cells.length - 1] || 'free');
    const wrap = el('div', 'ci-gridwrap');
    const board = el('div', 'ci-grid'); board.setAttribute('aria-hidden', 'true');
    for (const key of cells.slice(0, 100)) { const cell = el('span', 'ci-cell'); cell.dataset.c = key; board.append(cell); }
    wrap.title = t(win ? 'grid_label' : 'grid_share');
    wrap.append(board);
    return wrap;
  }
  function paintBreakdown(s) {
    const box = section(t('breakdown'));
    const list = el('ul', 'ci-legend');
    const win = s.window.tokens, denominator = win || s.total || 1;
    const rows = [...s.categories];
    if (win) { if (s.threshold && s.threshold < win && s.total < s.threshold) rows.push({key: 'free', tokens: s.threshold - s.total}, {key: 'reserve', tokens: win - s.threshold}); else if (win > s.total) rows.push({key: 'free', tokens: win - s.total}); }
    for (const row of rows) {
      const li = el('li');
      const swatch = el('span', 'ci-swatch'); swatch.dataset.c = row.key; swatch.setAttribute('aria-hidden', 'true');
      const name = el('span', 'ci-lname', t('c_' + row.key));
      const value = el('span', 'ci-lvalue', n(row.tokens));
      const share = el('span', 'ci-lshare', (row.tokens / denominator * 100 < 1 && row.tokens > 0 ? '<1' : Math.round(row.tokens / denominator * 100)) + '%');
      li.append(swatch, name, value, share);
      if (row.kind) { const tag = el('span', 'ci-tag', t('k_' + row.kind)); tag.dataset.kind = row.kind; tag.title = t('k_' + row.kind + '_hint'); li.append(tag); }
      list.append(li);
    }
    box.append(list);
  }
  const kindLabel = call => {
    if (call.kind === 'mcp') return t('kind_mcp') + (call.source ? ' · ' + call.source : '');
    if (call.kind === 'workspace' || call.kind === 'server') return call.source || t('kind_' + call.kind);
    if (call.kind === 'auto') return t('a_auto');
    if (call.kind === 'builtin' || call.kind === 'provider') return t('kind_' + call.kind) + (call.category ? ' · ' + t('cat_' + call.category) : '');
    return t('kind_other');
  };
  const secondsText = value => value >= 60 ? Math.floor(value / 60) + ' min ' + Math.round(value % 60) + ' s' : t('seconds', {n: value.toLocaleString(lang, {maximumFractionDigits: value < 10 ? 1 : 0})});
  const costText = value => value.toLocaleString(lang, {maximumSignificantDigits: 3, maximumFractionDigits: 6});
  const clip = (text, max = 1200) => text.length > max ? text.slice(0, max) + '\n' + t('a_cut', {n: n(text.length - max)}) : text;
  const pretty = text => { const trimmed = String(text || '').trim(); if (!/^[[{]/.test(trimmed)) return trimmed; try { return JSON.stringify(JSON.parse(trimmed), null, 2); } catch { return trimmed; } };
  // Tool calls, searches and sources over the whole conversation, the way Claude lists what it used
  function paintActivity(s) {
    const a = s.activity, box = section(t('activity'));
    box.classList.add('ci-activity');
    const tiles = el('ul', 'ci-tiles');
    const tile = (value, label, note, key) => { if (!value && !note) return; const li = el('li'); li.dataset.k = key; li.append(el('strong', null, n(value)), el('span', null, label)); if (note) li.append(el('em', null, note)); tiles.append(li); };
    const problems = [a.failed && a.failed + ' ' + t('a_failed'), a.denied && a.denied + ' ' + t('a_denied'), a.running && a.running + ' ' + t('a_running')].filter(Boolean).join(' · ');
    tile(a.calls, t('a_calls'), problems, 'calls');
    tile(a.searches, t('a_searches'), '', 'searches');
    tile(a.pages, t('a_pages'), '', 'pages');
    tile(a.knowledge, t('a_knowledge'), '', 'knowledge');
    tile(a.retrieved, t('a_sources'), '', 'sources');
    tile(a.code, t('a_code'), '', 'code');
    tile(a.images, t('a_images'), '', 'images');
    if (!tiles.children.length && !a.hidden && !a.errors.length) { box.append(el('p', 'ci-sub', t('a_none'))); return; }
    if (tiles.children.length) box.append(tiles);
    for (const error of a.errors.slice(0, 3)) box.append(el('p', 'ci-warn', t('a_conn', {what: error})));
    if (a.tools.length) {
      const list = el('ul', 'ci-tools');
      for (const row of a.tools) {
        const li = el('li'); li.dataset.kind = row.kind;
        const name = nameSpan('ci-tname', row.tool || row.name);
        const where = el('span', 'ci-tkind', kindLabel(row));
        const count = el('span', 'ci-tcount', '× ' + n(row.count));
        const info = [];
        if (row.failed) info.push(row.failed + ' ' + t('a_failed'));
        if (row.denied) info.push(row.denied + ' ' + t('a_denied'));
        if (row.seconds >= 0.05) info.push(secondsText(row.seconds));
        if (row.tokens) info.push(t('tokens', {n: n(row.tokens)}));
        const meta = el('span', 'ci-tmeta', info.join(' · '));
        if (row.failed || row.denied) meta.dataset.bad = 'true';
        li.append(name, count, where, meta);
        list.append(li);
      }
      box.append(list);
    }
    const queries = a.queries;
    if (queries.length) {
      const p = el('p', 'ci-sub ci-queries'); p.append(el('span', null, t('a_queries') + ': '));
      queries.slice(0, 5).forEach((q, i) => { if (i) p.append(document.createTextNode(' · ')); p.append(nameSpan('ci-q', '“' + q + '”')); });
      if (queries.length > 5) p.append(document.createTextNode(' ' + t('a_more', {n: queries.length - 5})));
      box.append(p);
    }
  }
  function paintCalls(box, activity) {
    const calls = activity.calls;
    if (!calls.length) return;
    const list = el('ol', 'ci-calls'); list.setAttribute('aria-label', t('last_calls'));
    const shown = calls.slice(0, 40);
    for (const call of shown) {
      const li = el('li');
      const details = el('details');
      const summary = el('summary');
      const mark = el('span', 'ci-dot'); mark.dataset.status = call.status; mark.setAttribute('aria-hidden', 'true');
      const state = [t('s_' + call.status)];
      if (call.prompted) state.push(t('s_prompted'));
      if (call.seconds >= 0.05) state.push(secondsText(call.seconds));
      summary.append(mark, nameSpan('ci-tname', call.tool || call.name), el('span', 'ci-tkind', kindLabel(call)), el('span', 'ci-tmeta', state.join(' · ')));
      details.append(summary);
      const pre = (label, text) => { const wrap = el('div', 'ci-io'); wrap.append(el('span', null, label)); const code = el('pre', null, clip(pretty(text)) || t('a_empty')); code.dir = 'auto'; wrap.append(code); details.append(wrap); };
      // Built lazily: a long result is only laid out once someone opens it
      details.addEventListener('toggle', () => { if (details.open && details.children.length === 1) { pre(t('a_args'), call.args); pre(t('a_result'), call.result); } });
      li.append(details); list.append(li);
    }
    box.append(list);
    if (calls.length > shown.length) box.append(el('p', 'ci-sub', t('a_more', {n: calls.length - shown.length})));
  }
  function paintLast(s) {
    if (!s.last) return;
    const u = s.last, box = section(t('last'));
    const rows = [[t('f_model'), u.model || s.modelId], [t('f_input'), tokensText(u.input)], [t('f_output'), tokensText(u.output)]];
    if (u.reasoning) rows.push([t('f_reasoning'), t('tokens', {n: n(u.reasoning)})]);
    if (u.cached) rows.push([t('f_cached'), t('tokens', {n: n(u.cached)})]);
    if (u.total != null) rows.push([t('f_total'), t('tokens', {n: n(u.total)})]);
    if (u.activity?.rounds > 1) rows.push([t('f_rounds'), n(u.activity.rounds)]);
    if (u.activity?.thinking >= 0.05) rows.push([t('f_thinking'), secondsText(u.activity.thinking)]);
    if (u.speed) rows.push([t('f_speed'), t('per_sec', {n: u.speed.toLocaleString(lang, {maximumFractionDigits: 1})})]);
    if (u.seconds) rows.push([t('f_time'), t('seconds', {n: u.seconds.toLocaleString(lang, {maximumFractionDigits: 1})})]);
    if (u.cost) rows.push([t('f_cost'), costText(u.cost)]);
    facts(box, rows);
    if (u.activity) paintCalls(box, u.activity);
  }
  function paintConversation(s) {
    const box = section(t('branch'));
    const rows = [[t('b_messages'), n(s.stats.messages)], [t('b_answers'), n(s.stats.answers)], [t('b_reported'), n(s.stats.counted) + ' / ' + n(s.stats.answers)]];
    if (s.stats.counted) rows.push([t('b_read'), t('tokens', {n: n(s.stats.read)})], [t('b_written'), t('tokens', {n: n(s.stats.written)})]);
    if (s.activity.rounds > s.stats.answers) rows.push([t('b_rounds'), n(s.activity.rounds)]);
    if (s.activity.tokens) rows.push([t('a_tool_tokens'), t('tokens', {n: n(s.activity.tokens)})]);
    if (s.stats.cost) rows.push([t('f_cost'), costText(s.stats.cost)]);
    if (s.summary) rows.push([t('b_summarised'), t('b_summarised_yes')]);
    facts(box, rows);
  }
  function paintModel(s) {
    const box = section(t('capabilities'));
    if (!s.model) { box.append(el('p', 'ci-sub', t('model_missing'))); return; }
    const chips = el('ul', 'ci-chips');
    for (const key of s.caps) chips.append(el('li', null, t('cap_' + key)));
    if (s.knowledge) chips.append(el('li', null, t('knowledge_n', {n: s.knowledge})));
    if (chips.children.length) box.append(chips); else box.append(el('p', 'ci-sub', t('no_caps')));
    const rows = [];
    if (s.toolNames.length) rows.push([t('m_tools'), s.toolNames.join(', ')]);
    const fn = s.functions;
    if (fn?.pipe) rows.push([t('m_pipe'), fn.pipe]);
    if (fn?.filters.length) rows.push([t('m_filters'), fn.filters.map(f => f.name + (f.toggle ? ' (' + t('fn_toggle') + ')' : f.global ? ' (' + t('fn_global') + ')' : '')).join(', ')]);
    if (fn?.actions.length) rows.push([t('m_actions'), fn.actions.map(f => f.name).join(', ')]);
    if (rows.length) facts(box, rows).classList.add('ci-facts-wide');
  }

  // While the tab is in front, a new or growing answer is read once it has stopped changing
  let watchSignal = '', watchStable = 0, watching = false;
  setInterval(() => {
    if (!((tabOpen && active) || popOpen) || !dialog || dialog.hidden || document.hidden || loading || !chatIdFromPath()) return;
    const messages = document.querySelectorAll('#messages-container [id^="message-"]');
    const last = messages[messages.length - 1];
    const signal = messages.length + ':' + (last ? last.id + ':' + last.textContent.length : '');
    if (signal !== watchSignal) { const first = !watchSignal; watchSignal = signal; watchStable = 0; if (!first && !watching) { watching = true; paintUpdated(); } return; }
    if (watchStable >= 0 && ++watchStable === 2) { watchStable = -1; if (watching) { watching = false; load(true); } }
  }, 1200);
  /* ---- the Context tab in the Tabs panel ---- */
  // Found by markers that stayed stable from 0.13.0 to 0.13.3: the tab rows (.panel-tab-strip) and the split-pane
  // groups (data-panel-group). Chat Gallery, Files and Canvas add tabs the same way; this one always sits after theirs.
  const tabBase = 'flex items-center gap-1.5 h-7 rounded-lg text-sm font-normal whitespace-nowrap shrink-0 transition';
  const activeClass = 'bg-gray-100/40 dark:bg-gray-800/25 text-gray-700 dark:text-gray-200';
  const idleClass = 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300';
  const headerClass = 'flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200';
  const barClass = 'flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0';
  let tabOpen = false, active = false, solo = false, strip = null, tabEl = null, tabButton = null, homeGroup = null, menuGroup = null;
  let lastNative = '', lastGroups = -1, lastPath = null, keepUntil = 0, tracking = 0;
  const plusOf = list => list.parentElement?.querySelector(':scope > :not([role="tablist"]) > button[aria-haspopup="true"]') ?? null;
  function panel() {
    const container = document.getElementById('controls-container');
    const lists = container ? [...container.querySelectorAll('.panel-tab-strip')] : [];
    if (!lists.length || container.classList.contains('hidden')) return null;
    const list = lists.find(each => each.closest('[data-panel-group]')?.dataset.panelGroup === homeGroup) || lists[0];
    const group = list.closest('[data-panel-group]'), bar = group ? list.closest('[data-panel-tab-bar]') || list.parentElement.parentElement : list.parentElement.parentElement;
    let content = null;
    if (!group) {
      content = bar?.nextElementSibling;
      while (content && !content.classList.contains('relative')) content = content.nextElementSibling;
      if (!content) return null;
    }
    return {container, lists, list, group, bar, content, full: container.classList.contains('contents')};
  }
  const nativeSignature = list => list ? [...list.querySelectorAll('[role="tab"]')].map(tab => tab.id + (tab.getAttribute('aria-selected') === 'true' ? '*' : '')).join() : '';
  const soloArea = () => isChatRoute() ? document.getElementById('chat-pane') || document.getElementById('chat-container') : null;
  function target() {
    if (!active) return null;
    const found = panel();
    if (!found) {
      const box = solo ? soloArea()?.getBoundingClientRect() : null;
      if (!box) return null;
      const bar = document.querySelector('#chat-container nav'), top = bar?.getClientRects().length ? Math.max(box.top, Math.min(bar.getBoundingClientRect().bottom, box.top + 64)) : box.top;
      const rect = {left: box.left, top, width: box.width, height: box.bottom - top};
      return rect.width > 0 && rect.height > 0 ? {rect, layer: 45} : null;
    }
    let rect;
    if (found.group) {
      const box = found.group.getBoundingClientRect(), top = found.bar.getBoundingClientRect().bottom;
      rect = {left: box.left, top, width: box.width, height: box.bottom - top};
    } else {
      let area = found.content;
      if (found.full && area.classList.contains('hidden')) area = document.getElementById('chat-panel-main-chat-view');
      rect = area?.getBoundingClientRect();
    }
    return rect && rect.width > 0 && rect.height > 0 ? {rect, layer: found.container.closest('.modal') ? 1000 : 45} : null;
  }
  function place() {
    tracking = 0;
    if (popOpen) return;
    const spot = target();
    dialog.hidden = !spot;
    if (!spot) { if (tabOpen && active) tracking = requestAnimationFrame(place); return; }
    const {rect, layer} = spot, style = dialog.style;
    const next = [rect.left, rect.top, rect.width, rect.height].map(value => Math.round(value) + 'px');
    if (style.left !== next[0]) style.left = next[0];
    if (style.top !== next[1]) style.top = next[1];
    if (style.width !== next[2]) style.width = next[2];
    if (style.height !== next[3]) style.height = next[3];
    if (style.zIndex !== String(layer)) style.zIndex = String(layer);
    tracking = requestAnimationFrame(place);
  }
  const track = () => { if (dialog && !tracking) tracking = requestAnimationFrame(place); };
  function cover() {
    const found = active && panel(), want = new Set();
    if (active && solo && !found) { const area = soloArea(); if (area) want.add(area); }
    if (found?.group) {
      const id = found.group.dataset.panelGroup;
      for (const node of document.querySelectorAll('[data-panel-group]')) {
        if (node.dataset.panelGroup === id && node !== found.group && (node.getAttribute('role') === 'tabpanel' || node.id === 'chat-panel-main-chat-view')) want.add(node);
      }
    } else if (found) {
      if (found.content) want.add(found.content);
      if (found.full) { const main = document.getElementById('chat-panel-main-chat-view'); if (main) want.add(main); }
    }
    for (const node of document.querySelectorAll('[data-ci-covered]')) if (!want.has(node)) { node.removeAttribute('data-ci-covered'); if (node.dataset.ciInert) { node.inert = false; delete node.dataset.ciInert; } }
    for (const node of want) if (!node.hasAttribute('data-ci-covered')) { node.setAttribute('data-ci-covered', ''); if (!node.inert) { node.inert = true; node.dataset.ciInert = '1'; } }
  }
  function paintTab() {
    cover();
    if (headerButton) headerButton.setAttribute('aria-pressed', String(tabOpen && active));
    if (!tabEl) return;
    const found = panel();
    const padding = found?.list?.querySelector('[data-panel-tab]')?.classList.contains('ps-2.5') ? 'ps-2.5 pe-2' : 'px-2.5';
    const wanted = tabBase + ' ' + padding + ' ' + (active ? activeClass : idleClass);
    if (tabEl.className !== wanted) tabEl.className = wanted;
    if (tabButton.getAttribute('aria-selected') !== String(active)) tabButton.setAttribute('aria-selected', String(active));
    tabButton.tabIndex = active ? 0 : -1;
    for (const list of document.querySelectorAll('.panel-tab-strip')) list.classList.toggle('ci-dim', active && list === found?.list);
    const alone = solo && !found;
    if (dialog && !popOpen) { dialog.classList.toggle('ci-solo', alone); soloClose.hidden = !alone; }
    track();
  }
  function buildTab() {
    strip = el('div', 'flex items-center shrink-0'); strip.id = 'owui-chat-inspector-tabs';
    strip.setAttribute('role', 'tablist'); strip.setAttribute('aria-label', t('context'));
    tabEl = el('div');
    tabButton = el('button', 'flex h-full items-center gap-1.5 min-w-0'); tabButton.type = 'button'; tabButton.id = 'owui-chat-inspector-tab';
    tabButton.setAttribute('role', 'tab'); tabButton.setAttribute('aria-controls', 'owui-chat-inspector');
    const icon = svg('gauge'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14'); icon.classList.add('shrink-0');
    tabButton.append(icon, el('span', 'max-w-30 overflow-hidden text-ellipsis', t('context')));
    tabButton.addEventListener('click', () => activate());
    const close = el('button', 'flex items-center justify-center w-4 h-4 rounded text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white');
    close.type = 'button'; setLabel(close, t('close_context'));
    const x = svg('close'); x.setAttribute('width', '12'); x.setAttribute('height', '12'); close.append(x);
    close.addEventListener('click', closeTab);
    tabEl.append(tabButton, close); strip.append(tabEl);
  }
  const announce = () => dispatchEvent(new CustomEvent('owui-panel-tab-front', {detail: {owner: 'chat-inspector'}}));
  const tellState = () => dispatchEvent(new CustomEvent('owui-chat-inspector-state', {detail: {open: tabOpen}}));
  function activate() {
    if (!tabOpen) return;
    const back = !active;
    // A chat that is still loading rearranges the panel's own tabs; that must not put away a tab the person just chose
    if (back) keepUntil = Math.max(keepUntil, Date.now() + 2000);
    active = true; lastNative = nativeSignature(panel()?.list); announce(); paintTab();
    if (tabEl.isConnected) tabEl.scrollIntoView({block: 'nearest', inline: 'nearest'});
    if (back && loadedAt && !loading && Date.now() - loadedAt > 3000) load(true);
  }
  function deactivate() { if (active) { active = false; paintTab(); } }
  function openTab() {
    if (popOpen) closePop(true);
    if (dialog && !tabOpen && !popOpen && builtLang !== language()) { dialog.remove(); strip?.remove(); opener?.remove(); headerButton?.remove(); dialog = strip = opener = headerButton = null; }
    if (!dialog) build();
    if (menuGroup) homeGroup = menuGroup;
    if (!strip) buildTab();
    const fresh = !tabOpen;
    tabOpen = true; solo = !panel();
    schedule(); activate(); tellState();
    if (fresh) load();
    setTimeout(() => { if (tabOpen) (body.querySelector('select,button') || refreshButton).focus({preventScroll: true}); });
  }
  function closeTab() {
    controller?.abort(); snapshot = null; body.replaceChildren(); loadedChat = null;
    for (const row of document.querySelectorAll('.ci-tabrow')) row.classList.remove('ci-tabrow');
    const found = panel(), wasSolo = solo && !found;
    tabOpen = false; active = false; solo = false; strip?.remove(); paintTab(); dialog.hidden = true;
    if (found?.list) (found.list.querySelector('[role="tab"][aria-selected="true"]') || plusOf(found.list))?.focus({preventScroll: true});
    else if (wasSolo) headerButton?.focus({preventScroll: true});
    tellState();
  }
  // The way in for accounts with no + menu. With Chat Gallery installed its button stays nearest the edge and this one sits beside it.
  function syncHeader(found) {
    let host = null, inBar = false;
    if (CONFIG.headerButton && isChatRoute()) {
      const group = [...document.querySelectorAll('#chat-container nav .flex.flex-none.items-center.self-center')].pop();
      if (group && group.getClientRects().length) host = group;
      else if (found?.lists?.length) { host = found.lists[0].parentElement; inBar = true; }
    }
    if (!host) { headerButton?.remove(); return; }
    if (!headerButton) {
      lang = language();
      headerButton = iconButton(t('context'), 'gauge', () => {
        if (CONFIG.openAs !== 'tab') { popOpen ? closePop() : openPop(headerButton); return; }
        if (tabOpen && active) { deactivate(); return; }
        if (tabOpen) { solo = !panel(); activate(); return; }
        menuGroup = headerButton.closest('[data-panel-group]')?.dataset.panelGroup ?? null; openTab();
      }, headerClass);
      headerButton.id = 'owui-chat-inspector-header'; headerButton.setAttribute('aria-pressed', 'false');
    }
    const wantedClass = inBar ? barClass : headerClass;
    if (headerButton.className !== wantedClass) headerButton.className = wantedClass;
    // Gallery's button stays nearest the edge, then Files', then Canvas', then this one
    const neighbour = ['owui-chat-canvas-header', 'owui-chat-files-header', 'owui-chat-gallery-header'].map(id => document.getElementById(id)).find(node => node?.parentElement === host) || null;
    if (inBar) {
      const next = headerButton.nextElementSibling;
      if (headerButton.parentElement !== host || (next && next !== neighbour)) neighbour ? host.insertBefore(headerButton, neighbour) : host.append(headerButton);
    } else {
      const previous = headerButton.previousElementSibling;
      if (headerButton.parentElement !== host || (previous && previous !== neighbour)) neighbour ? neighbour.after(headerButton) : host.prepend(headerButton);
    }
  }
  /* ---- the pop-up, opened from the chat's own ⋯ menu ---- */
  let popOpen = false, popAnchor = null, popFrame = 0, popRect = '', backdrop = null, chatMenu = null, chatTrigger = null, menuItem = null;
  const visibleNode = node => !!node && node.isConnected && node.getClientRects().length > 0;
  function openPop(anchor) {
    if (tabOpen) closeTab();
    if (dialog && !popOpen && builtLang !== language()) { dialog.remove(); backdrop?.remove(); opener?.remove(); headerButton?.remove(); menuItem?.remove(); dialog = backdrop = opener = headerButton = menuItem = null; }
    if (!dialog) build();
    if (!backdrop) {
      // Only on a phone, where the pop-up is a sheet over the chat; a tap outside it closes it
      backdrop = el('div', 'ci-backdrop'); backdrop.hidden = true;
      backdrop.addEventListener('click', () => closePop());
      document.body.append(backdrop);
    }
    const fresh = !popOpen;
    popOpen = true; popAnchor = anchor || popAnchor;
    dialog.classList.add('ci-pop'); dialog.classList.remove('ci-solo');
    dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'false'); dialog.setAttribute('aria-labelledby', 'ci-title');
    soloClose.hidden = false;
    dialog.hidden = false;
    popRect = ''; placePop();
    if (fresh) load();
    setTimeout(() => { if (popOpen) soloClose.focus({preventScroll: true}); });
  }
  function closePop(silent) {
    if (!popOpen) return;
    popOpen = false; cancelAnimationFrame(popFrame); popFrame = 0;
    controller?.abort(); snapshot = null; body.replaceChildren(); loadedChat = null;
    dialog.hidden = true; backdrop.hidden = true;
    dialog.classList.remove('ci-pop', 'ci-sheet');
    dialog.setAttribute('role', 'tabpanel'); dialog.removeAttribute('aria-modal'); dialog.setAttribute('aria-labelledby', 'owui-chat-inspector-tab');
    for (const key of ['left', 'top', 'width', 'height', 'maxHeight', 'bottom']) dialog.style[key] = '';
    if (!silent) (visibleNode(popAnchor) ? popAnchor : document.getElementById('chat-input'))?.focus({preventScroll: true});
  }
  // Under the button it was opened from, kept on screen and clear of the message box; on a phone, a sheet from the bottom
  function placePop() {
    popFrame = 0;
    if (!popOpen) return;
    const vw = innerWidth, vh = innerHeight, phone = vw < 640;
    let rect;
    if (phone) rect = {left: 0, top: Math.round(vh * 0.18), width: vw, maxHeight: Math.round(vh * 0.82), sheet: true};
    else {
      const width = Math.min(460, vw - 24);
      const anchor = visibleNode(popAnchor) ? popAnchor.getBoundingClientRect() : null;
      const area = [document.getElementById('chat-panel-main-chat-view'), document.getElementById('chat-pane'), document.getElementById('chat-container')].find(visibleNode)?.getBoundingClientRect();
      const nav = [...document.querySelectorAll('#chat-container nav, [data-panel-tab-bar]')].find(visibleNode)?.getBoundingClientRect();
      let left = anchor ? anchor.left - 8 : (area ? area.right - width - 16 : vw - width - 16);
      left = Math.max(12, Math.min(left, vw - width - 12));
      const top = Math.max(8, Math.round(anchor ? anchor.bottom + 8 : (nav ? nav.bottom + 8 : 56)));
      // Stop above the message box where the two would overlap, so typing stays possible with the pop-up open
      const composer = document.getElementById('chat-input')?.closest('form') || document.getElementById('chat-input');
      const box = visibleNode(composer) ? composer.getBoundingClientRect() : null;
      const overlaps = box && box.left < left + width && box.right > left && box.top > top + 200;
      const bottom = overlaps ? box.top - 12 : vh - 12;
      rect = {left: Math.round(left), top, width: Math.round(width), maxHeight: Math.max(220, Math.round(bottom - top)), sheet: false};
    }
    const key = JSON.stringify(rect);
    if (key !== popRect) {
      popRect = key;
      const style = dialog.style;
      style.left = rect.left + 'px'; style.top = rect.top + 'px'; style.width = rect.width + 'px'; style.height = ''; style.maxHeight = rect.maxHeight + 'px';
      dialog.classList.toggle('ci-sheet', rect.sheet);
      backdrop.hidden = !rect.sheet;
    }
    popFrame = requestAnimationFrame(placePop);
  }
  function itemIcon() { const icon = svg('gauge'); icon.setAttribute('class', 'size-3.5'); return icon; }
  // Adds "Context" to the chat's ⋯ menu, under Overview, styled like Open WebUI's own items
  function syncChatMenu() {
    const trigger = CONFIG.chatMenu ? [...document.querySelectorAll('#chat-context-menu-button[aria-expanded="true"]')].find(visibleNode) : null;
    if (!trigger || !isChatRoute() || !chatIdFromPath()) { chatMenu = chatTrigger = null; menuItem?.remove(); return; }
    if (!chatMenu?.isConnected || chatTrigger !== trigger) {
      // The main menu opens on its own; its Download and Move submenus only appear later, on hover,
      // so the first menu seen after the ⋯ button opens is the one to use, and it is kept until it closes
      const menus = [...document.querySelectorAll('body > [role="menu"] .app-dropdown-menu')].filter(menu => !menu.contains(opener));
      chatMenu = menus.length === 1 ? menus[0] : menus.find(menu => menu.querySelector(':scope > button[aria-haspopup="dialog"]')) || null;
      chatTrigger = trigger;
    }
    if (!chatMenu) return;
    if (!menuItem) {
      lang = language();
      menuItem = el('button', 'flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] cursor-pointer select-none hover:bg-gray-50/40 dark:hover:bg-gray-800/40');
      menuItem.type = 'button'; menuItem.id = 'owui-chat-inspector-menu-item'; menuItem.draggable = false;
      menuItem.setAttribute('aria-haspopup', 'dialog');
      menuItem.append(itemIcon(), el('div', 'flex items-center', t('context')));
      // Open WebUI closes its menu after the click; the trigger stays behind as the pop-up's anchor
      menuItem.addEventListener('click', () => { const anchor = chatTrigger; CONFIG.openAs === 'tab' ? openTab() : openPop(anchor); });
    }
    if (menuItem.parentElement !== chatMenu) {
      const overview = chatMenu.querySelector(':scope > button[aria-haspopup="dialog"]:not(#owui-chat-inspector-menu-item)');
      overview ? overview.after(menuItem) : chatMenu.prepend(menuItem);
    }
  }
  addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !popOpen || event.defaultPrevented) return;
    // Escape belongs to an open menu or dialog of Open WebUI first
    if (document.querySelector('body > [role="menu"], dialog[open], .modal')) return;
    event.preventDefault(); closePop();
  }, true);
  function sync() {
    syncPending = false;
    const found = panel();
    if (lastPath === null) lastPath = location.pathname;
    if (location.pathname !== lastPath) {
      lastPath = location.pathname; keepUntil = Date.now() + 1500;
      if (tabOpen || popOpen) load();
    }
    syncHeader(found);
    const open = found?.lists.find(list => plusOf(list)?.getAttribute('aria-expanded') === 'true');
    syncChatMenu();
    const menu = CONFIG.plusMenu && open && [...document.querySelectorAll('body > [role="menu"]')].pop()?.querySelector('.app-dropdown-menu');
    if (menu) {
      menuGroup = open.closest('[data-panel-group]')?.dataset.panelGroup ?? null;
      if (!opener) {
        lang = language();
        opener = el('button'); opener.type = 'button'; opener.id = 'owui-chat-inspector-button';
        const icon = svg('gauge'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14');
        opener.append(icon, t('context'));
        opener.addEventListener('click', () => openTab());
      }
      if (opener.parentElement !== menu) menu.append(opener);
    } else opener?.remove();
    if (!tabOpen || !found) { if (tabOpen) paintTab(); else track(); return; }
    if (found.group && found.group.dataset.panelGroup !== homeGroup) homeGroup = found.group.dataset.panelGroup;
    let anchor = found.list;
    if (anchor.nextElementSibling?.id === 'owui-chat-workspace-tabs') anchor = anchor.nextElementSibling;
    if (anchor.nextElementSibling?.id === 'owui-chat-gallery-tabs') anchor = anchor.nextElementSibling;
    if (anchor.nextElementSibling?.id === 'owui-chat-files-tabs') anchor = anchor.nextElementSibling;
    if (anchor.nextElementSibling?.id === 'owui-chat-canvas-tabs') anchor = anchor.nextElementSibling;
    const moved = strip.previousElementSibling !== anchor;
    if (moved) anchor.after(strip);
    // In a narrow side panel the extra tabs don't fit; the row scrolls sideways instead of sliding under Open WebUI's buttons
    if (!strip.parentElement.classList.contains('ci-tabrow')) strip.parentElement.classList.add('ci-tabrow');
    const signature = nativeSignature(found.list), groups = found.lists.length;
    if (active && !moved && signature !== lastNative && !(groups !== lastGroups && lastGroups !== -1) && Date.now() > keepUntil) deactivate();
    lastNative = signature; lastGroups = groups;
    paintTab();
  }
  let syncPending = false;
  const schedule = () => { if (!syncPending) { syncPending = true; requestAnimationFrame(sync); } };
  const start = () => {
    const style = document.createElement('style'); style.textContent = __CSS__; document.head.append(style);
    new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-selected', 'aria-expanded']});
    addEventListener('owui-panel-tab-front', event => { if (event.detail?.owner !== 'chat-inspector') deactivate(); });
    addEventListener('owui-chat-inspector-open', () => { menuGroup = null; CONFIG.openAs === 'tab' ? openTab() : openPop(null); });
    resolveAppName();
    document.documentElement.dataset.chatInspector = '1';
    document.addEventListener('click', event => {
      const clicked = event.target.closest?.('#controls-container .panel-tab-strip [role="tab"]');
      if (clicked && clicked.closest('.panel-tab-strip') === panel()?.list) deactivate();
      const choice = event.target.closest?.('.app-dropdown-menu > button');
      if (choice && choice !== opener && opener?.parentElement === choice.parentElement && (menuGroup ?? null) === (homeGroup ?? menuGroup ?? null)) deactivate();
    }, true);
    addEventListener('popstate', schedule);
    sync();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
"""


class Event:
    class Valves(BaseModel):
        context_windows: str = Field(
            default="",
            description="Context window per model, for models whose provider doesn't report one. One rule per line or comma: a model id or pattern, '=', and a size, for example 'gpt-4o* = 128000, llama3.1* = 128k, my-local-model = 32768'. The first rule that matches wins, and a rule overrides what the provider reports.",
        )
        warn_percent: int = Field(
            default=80,
            ge=50,
            le=99,
            description="How full the context window is, in percent, before Context says it is getting full.",
        )
        show_capabilities: bool = Field(
            default=True,
            description="Show what the chosen model is set up to do, and how many tools and knowledge bases are attached to it.",
        )
        platform_name: str = Field(
            default="",
            description="The name to use in Context's wording, such as 'EveGPT'. Empty uses the name your administrator gave the platform (WEBUI_NAME), and 'Open WebUI' if none was set.",
        )
        open_as: Literal["popup", "tab"] = Field(
            default="popup",
            description="How Context opens: 'popup' is a panel under the button it was opened from, leaving the chat in view; 'tab' fills the chat's pane like the Tabs panel's own tabs.",
        )
        show_in_chat_menu: bool = Field(
            default=True,
            description="Add Context to the chat's ⋯ menu, under Overview. Every account has that menu.",
        )
        show_in_plus_menu: bool = Field(
            default=False,
            description="Also add Context to the Tabs panel's + menu, where it always opens as a tab. Many accounts have no + menu.",
        )
        header_button: bool = Field(
            default=False,
            description="Also add a Context button to the chat's tab row or top bar. It opens Context the way open_as says.",
        )
        language: Literal["auto", "en", "fr"] = Field(
            default="auto",
            description="Language of the Context tab. 'auto' follows each person's Open WebUI language, and uses English where there is no translation.",
        )

    LOADER_BLOCK_START = "// owui-chat-inspector:start"
    LOADER_BLOCK_END = "// owui-chat-inspector:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment_key = None
        self._fragment = ""
        reload_bootstrap()

    def _config(self) -> dict:
        valves = self.valves
        return {
            "contextWindows": str(valves.context_windows)[:4000],
            "warnPercent": int(valves.warn_percent),
            "showCapabilities": bool(valves.show_capabilities),
            "headerButton": bool(valves.header_button),
            "platformName": str(valves.platform_name).strip()[:60],
            "openAs": valves.open_as,
            "chatMenu": bool(valves.show_in_chat_menu),
            "plusMenu": bool(valves.show_in_plus_menu),
            "language": valves.language,
        }

    def _loader_fragment(self) -> str:
        # Runs on the event loop for every page load: rebuild only when a valve changed.
        key = _json.dumps(self._config(), sort_keys=True)
        if key != self._fragment_key:
            # The configuration is JSON inside a script; "</" is escaped so no value can end the script early
            config = key.replace("</", "<\\/")
            script = LOADER_SCRIPT.replace("__CSS__", _json.dumps(INSPECTOR_CSS)).replace("__CONFIG__", config)
            self._fragment = (
                self.LOADER_BLOCK_START + "\n" + script + "\n" + self.LOADER_BLOCK_END
            )
            self._fragment_key = key
        return self._fragment

    def _publish(self, app):
        asset_register(
            app,
            LOADER_PATH,
            self.LOADER_BLOCK_START,
            self.LOADER_BLOCK_END,
            lambda: Event.instance._loader_fragment() if reload_active(app) else "",
        )

    async def event(
        self,
        event: Optional[dict] = None,
        __event_name__: str = "",
        __app__: Any = None,
        **kwargs,
    ) -> None:
        if __app__ is not None:
            await reload_on_event(__app__, event, __event_name__)


def register(app: Any) -> None:
    Event.instance._publish(app)
