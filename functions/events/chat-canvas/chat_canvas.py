"""
title: Chat Canvas
description: A Canvas tab that turns the code and documents in a chat into editable pages: edit, preview, compare changes, ask the model for a change, and save to your files or notes.
author: Muhammad Sohail
version: 1.0.0
license: MIT
required_open_webui_version: 0.13.0
"""

# A companion to Chat Gallery and Chat Files, built the same way. The two protocol blocks
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


CANVAS_CSS = r"""
#owui-chat-canvas-button svg,#owui-chat-canvas-header svg{pointer-events:none}
#owui-chat-canvas-header svg{width:1.125rem;height:1.125rem}
#owui-chat-canvas-header.w-7 svg{width:.875rem;height:.875rem}
#owui-chat-canvas-header[aria-pressed=true]{color:var(--color-gray-700,#4e4e4e)}
.dark #owui-chat-canvas-header[aria-pressed=true]{color:var(--color-gray-200,#e3e3e3)}
.cv-dialog{--cv-surface:#fff;--cv-text:var(--color-gray-800,#262626);--cv-muted:var(--color-gray-600,#5d5d5d);--cv-soft:var(--color-gray-50,#f9f9f9);--cv-hover:var(--color-gray-100,#ececec);--cv-line:var(--color-gray-100,#ececec);--cv-primary:#000;--cv-on-primary:#fff;--cv-accent:var(--color-blue-600,#2563eb);--cv-danger:#dc2626;--cv-danger-text:#b91c1c;--cv-warn:#92400e;
--cv-k-pdf:#b91c1c;--cv-k-doc:#1d4ed8;--cv-k-sheet:#166534;--cv-k-slide:#9a3412;--cv-k-code:#6d28d9;--cv-k-audio:#9d174d;--cv-k-video:#115e59;--cv-k-image:#854d0e;--cv-k-archive:#92400e;--cv-k-ref:#075985;--cv-k-other:#4b5563}
.dark .cv-dialog{--cv-surface:var(--color-gray-900,#171717);--cv-text:var(--color-gray-100,#ececec);--cv-muted:var(--color-gray-400,#b4b4b4);--cv-soft:var(--color-gray-850,#262626);--cv-hover:var(--color-gray-800,#333);--cv-line:var(--color-gray-800,#333);--cv-primary:#fff;--cv-on-primary:#000;--cv-accent:var(--color-blue-400,#60a5fa);--cv-danger:#dc2626;--cv-danger-text:#f87171;--cv-warn:#fbbf24;color-scheme:dark;
--cv-k-pdf:#f87171;--cv-k-doc:#60a5fa;--cv-k-sheet:#4ade80;--cv-k-slide:#fb923c;--cv-k-code:#a78bfa;--cv-k-audio:#f472b6;--cv-k-video:#2dd4bf;--cv-k-image:#facc15;--cv-k-archive:#fbbf24;--cv-k-ref:#38bdf8;--cv-k-other:#9ca3af}
.cv-dialog{position:fixed;left:0;top:0;z-index:45;box-sizing:border-box;display:flex;flex-direction:column;container:cv/inline-size;margin:0;padding:0;border:0;background:transparent;color:var(--cv-text);overflow:hidden;overscroll-behavior:contain;font-family:inherit;font-size:.875rem;line-height:1.4}
.cv-dialog[hidden]{display:none}
.cv-dialog.cv-solo{background:var(--cv-surface)}
[data-cv-covered]{opacity:0!important;pointer-events:none!important}
.cv-viewing>.cv-head{display:none}
.cv-viewing .cv-scroll,.cv-viewing .cv-foot{visibility:hidden}
.panel-tab-strip.cv-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){background:transparent!important;color:var(--color-gray-500,#6b7280)!important}
.dark .panel-tab-strip.cv-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){color:var(--color-gray-400,#9ca3af)!important}
#owui-chat-canvas-tabs{scrollbar-width:none}
.cv-tabrow{overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.cv-tabrow::-webkit-scrollbar{display:none}
.cv-dialog *{box-sizing:border-box}
.cv-dialog button,.cv-dialog select,.cv-dialog input{font:inherit;color:inherit}
.cv-dialog :focus-visible{outline:2px solid var(--cv-accent);outline-offset:2px}
.cv-head{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;padding:1rem 1.25rem .75rem}
.cv-tabs{display:flex;gap:2px;padding:2px;border-radius:.75rem;background:var(--cv-soft)}
.cv-tab{border:0;background:transparent;padding:.25rem .75rem;border-radius:.625rem;cursor:pointer;color:var(--cv-muted);white-space:nowrap}
.cv-tab[aria-selected=true]{background:var(--cv-surface);color:var(--cv-text);box-shadow:0 1px 2px rgb(0 0 0/.08)}
.dark .cv-tab[aria-selected=true]{background:var(--cv-hover)}
.cv-tab[hidden]{display:none}
.cv-spacer{flex:1}
.cv-search{width:min(14rem,100%);height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--cv-soft);color:inherit;font-size:.875rem;outline:none}
.cv-search::placeholder{color:var(--cv-muted);opacity:1}
.cv-select{appearance:auto;height:2rem;max-width:11rem;padding:0 .5rem;border:0;border-radius:.75rem;background:var(--cv-soft);cursor:pointer}
.dark .cv-select,.dark .cv-search{box-shadow:inset 0 0 0 1px var(--cv-line)}
.cv-select option{background:var(--cv-surface);color:var(--cv-text)}
.cv-btn{display:inline-flex;align-items:center;justify-content:center;gap:.375rem;height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--cv-soft);cursor:pointer;white-space:nowrap}
.cv-btn:hover{background:var(--cv-hover)}
.cv-btn:disabled{opacity:.5;cursor:default}
.cv-btn[aria-pressed=true],.cv-btn.cv-primary{background:var(--cv-primary);color:var(--cv-on-primary)}
.cv-btn.cv-danger{background:var(--cv-danger);color:#fff}
.cv-btn.cv-danger:hover,.cv-btn.cv-primary:hover{filter:brightness(1.1)}
.cv-btn[hidden],.cv-icon[hidden]{display:none}
.cv-icon{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;border:0;border-radius:.75rem;background:transparent;cursor:pointer;color:var(--cv-muted)}
.cv-icon:hover{background:var(--cv-hover);color:var(--cv-text)}
.cv-icon:disabled{opacity:.4;cursor:default}
.cv-icon svg,.cv-btn svg{width:1.125rem;height:1.125rem}
.cv-modal{box-sizing:border-box;width:min(26rem,calc(100vw - 2rem));margin:auto;padding:1.25rem;border:1px solid var(--cv-line);border-radius:1.25rem;background:var(--cv-surface);color:var(--cv-text);box-shadow:0 20px 40px -12px rgb(0 0 0/.35);font-size:.875rem;line-height:1.45}
.cv-modal::backdrop{background:rgb(0 0 0/.4)}
.cv-modal h3{margin:0 0 .5rem;font-size:1rem;font-weight:600}
.cv-modal p{margin:0 0 1rem;color:var(--cv-muted)}
.cv-modal .cv-bad{color:var(--cv-danger-text)}
.cv-label{display:flex;flex-direction:column;gap:.375rem;margin:0 0 .5rem;color:var(--cv-muted)}
.cv-input{height:2.25rem;padding:0 .75rem;border:1px solid var(--cv-line);border-radius:.75rem;background:var(--cv-soft);color:var(--cv-text);outline:none}
.cv-input:focus{border-color:var(--cv-accent)}
.cv-actions{display:flex;justify-content:flex-end;gap:.5rem}
.cv-body{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
.cv-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:0 1.25rem 1rem}
.cv-list{display:flex;flex-direction:column;gap:2px;margin:0;padding:0;list-style:none}
.cv-list li{margin:0;min-width:0}
.cv-list li:not(.cv-empty){content-visibility:auto;contain-intrinsic-size:auto 3.25rem}
.cv-row{display:flex;align-items:center;gap:.75rem;width:100%;min-height:3.25rem;padding:.5rem .75rem;border:0;border-radius:.75rem;background:transparent;text-align:start;cursor:pointer}
.cv-row:hover{background:var(--cv-soft)}
.cv-row[aria-pressed=true]{background:color-mix(in srgb,var(--cv-accent) 14%,transparent)}
.cv-badge{flex:none;display:inline-flex;align-items:center;justify-content:center;width:2.5rem;height:2.25rem;border-radius:.5rem;font-size:.625rem;font-weight:700;letter-spacing:.02em;color:var(--cv-tone);background:color-mix(in srgb,var(--cv-tone) 13%,transparent)}
.cv-badge[data-tone=pdf]{--cv-tone:var(--cv-k-pdf)}.cv-badge[data-tone=doc]{--cv-tone:var(--cv-k-doc)}.cv-badge[data-tone=sheet]{--cv-tone:var(--cv-k-sheet)}.cv-badge[data-tone=slide]{--cv-tone:var(--cv-k-slide)}
.cv-badge[data-tone=code]{--cv-tone:var(--cv-k-code)}.cv-badge[data-tone=audio]{--cv-tone:var(--cv-k-audio)}.cv-badge[data-tone=video]{--cv-tone:var(--cv-k-video)}.cv-badge[data-tone=image]{--cv-tone:var(--cv-k-image)}
.cv-badge[data-tone=archive]{--cv-tone:var(--cv-k-archive)}.cv-badge[data-tone=ref]{--cv-tone:var(--cv-k-ref)}.cv-badge[data-tone=other]{--cv-tone:var(--cv-k-other)}
.cv-text{display:flex;flex-direction:column;min-width:0;flex:1}
.cv-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;unicode-bidi:plaintext}
.cv-sub{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;color:var(--cv-muted);font-size:.75rem;font-variant-numeric:tabular-nums}
.cv-flag{padding:0 .375rem;border-radius:.375rem;font-size:.6875rem;font-weight:600;color:var(--cv-warn);background:color-mix(in srgb,var(--cv-warn) 14%,transparent)}
.cv-flag[data-state=failed],.cv-flag[data-state=missing]{color:var(--cv-danger-text);background:color-mix(in srgb,var(--cv-danger-text) 12%,transparent)}
.cv-empty strong{color:var(--cv-text);font-weight:500}
.cv-empty .cv-btn{margin-top:.5rem;color:var(--cv-text)}
.cv-foot{display:flex;align-items:center;gap:.5rem;min-height:2.5rem;padding:.25rem 1.25rem .75rem;color:var(--cv-muted);font-size:.8125rem;font-variant-numeric:tabular-nums}
.cv-foot .cv-btn{height:1.625rem;padding:0 .5rem;font-size:.75rem;color:var(--cv-text)}
.cv-viewer{position:absolute;inset:0;z-index:2;display:none;grid-template-columns:minmax(0,1fr);grid-template-rows:auto auto minmax(0,1fr) auto}
.cv-solo .cv-viewer{background:var(--cv-surface)}
.cv-viewer.cv-open{display:grid}
.cv-vbar{grid-column:1/-1;display:flex;align-items:center;gap:.125rem;min-width:0;padding:.75rem 1rem;border-bottom:1px solid var(--cv-line);overflow-x:auto;scrollbar-width:none}
.cv-count{margin:0 .25rem;color:var(--cv-muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.cv-stage{position:relative;min-width:0;min-height:0;overflow:auto;display:flex;flex-direction:column}
.cv-stage:focus{outline:none}
.cv-stage.cv-loading>*{opacity:.6}
audioimg.cv-cut{margin:0 0 .5rem;color:var(--cv-muted);font-size:.75rem}
.cv-pre{flex:1;margin:0;padding:1rem;border-radius:.75rem;background:var(--cv-soft);white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8125rem;line-height:1.55;tab-size:4;unicode-bidi:plaintext}
.cv-pre.cv-code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;overflow:auto;overflow-wrap:normal}
.cv-tablebox{width:fit-content;max-width:100%;overflow:auto;max-height:100%;border:1px solid var(--cv-line);border-radius:.75rem}
.cv-table{border-collapse:collapse;font-size:.8125rem;font-variant-numeric:tabular-nums}
.cv-table th,.cv-table td{padding:.375rem .625rem;border-bottom:1px solid var(--cv-line);text-align:start;white-space:nowrap;max-width:24rem;overflow:hidden;text-overflow:ellipsis}
.cv-table th{position:sticky;top:0;background:var(--cv-soft);font-weight:600}
.cv-notice{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.375rem;flex:1;min-height:12rem;padding:2rem;color:var(--cv-muted);text-align:center;overflow-wrap:anywhere}
.cv-notice strong{color:var(--cv-text);font-weight:500}
.cv-notice .cv-btn{margin-top:.5rem;color:var(--cv-text)}
.cv-toast{position:absolute;left:50%;bottom:1rem;translate:-50% 0;z-index:3;max-width:calc(100% - 2rem);padding:.5rem .875rem;border-radius:.75rem;background:var(--cv-primary);color:var(--cv-on-primary);font-size:.8125rem;pointer-events:none;opacity:0;transition:opacity .15s}
.cv-toast.cv-show{opacity:1}
.cv-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media(prefers-reduced-motion:reduce){.cv-toast{transition:none}}
@media(forced-colors:active){.cv-row,.cv-btn,.cv-tab,.cv-select,.cv-search{border:1px solid CanvasText}.cv-row[aria-pressed=true]{outline:2px solid Highlight}}
@container cv (max-width:760px){
.cv-head{padding:.75rem .75rem .5rem}
.cv-search{order:9;flex:1;width:auto;min-width:8rem}
.cv-scroll{padding:0 .5rem .75rem}
.cv-row{padding:.5rem}
.cv-foot{padding:.25rem .75rem .75rem}

.cv-vbar{padding:.5rem}
.cv-tabs{order:0}.cv-close{order:1;margin-inline-start:auto}.cv-head>:not(.cv-tabs,.cv-close,.cv-search,.cv-spacer){order:2}.cv-spacer{display:none}
}
.cv-vbar .cv-tabs{margin-inline:.5rem;flex:none}
.cv-titlebar{display:flex;align-items:center;gap:.5rem;min-width:0;padding:.5rem 1rem;border-bottom:1px solid var(--cv-line)}
.cv-title{flex:1;min-width:6rem;height:2rem;padding:0 .5rem;border:1px solid transparent;border-radius:.5rem;background:transparent;color:var(--cv-text);font-weight:600;font-size:.9375rem;text-overflow:ellipsis;outline:none}
.cv-title:not([readonly]){border-color:var(--cv-line);background:var(--cv-soft)}
.cv-title:not([readonly]):focus{border-color:var(--cv-accent)}
.cv-wrap{display:inline-flex;align-items:center;gap:.375rem;flex:none;color:var(--cv-muted);font-size:.8125rem;white-space:nowrap;cursor:pointer}
.cv-wrap[hidden]{display:none}
.cv-wrap input{accent-color:var(--cv-accent)}
.cv-stage{overflow:hidden}
.cv-stage>[hidden]{display:none}
.cv-edit{flex:1;min-height:0;display:flex;background:var(--cv-surface)}
.cv-dialog:not(.cv-solo) .cv-edit{background:color-mix(in srgb,var(--cv-surface) 88%,transparent)}
.cv-gutter{flex:none;margin:0;padding:1rem .5rem 1rem .75rem;min-width:3rem;overflow:hidden;text-align:end;color:var(--cv-muted);background:var(--cv-soft);border-inline-end:1px solid var(--cv-line);user-select:none}
.cv-gutter[hidden]{display:none}
.cv-gutter,.cv-area{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:20px;tab-size:4;font-variant-ligatures:none}
.cv-area{flex:1;min-width:0;margin:0;padding:1rem;border:0;outline:none;resize:none;background:transparent;color:var(--cv-text);white-space:pre;overflow:auto;overscroll-behavior:contain;caret-color:var(--cv-accent)}
.cv-area:focus-visible{outline:2px solid var(--cv-accent);outline-offset:-2px}
.cv-area[readonly]{color:var(--cv-muted)}
.cv-wrapped .cv-area{white-space:pre-wrap;overflow-wrap:anywhere}
.cv-prose .cv-area{font-family:inherit;font-size:.9375rem;line-height:1.65;max-width:52rem;margin-inline:auto}
.cv-preview,.cv-diff{flex:1;min-height:0;display:flex;flex-direction:column;overflow:auto}
.cv-frame{flex:1;width:100%;min-height:16rem;border:0;background:#fff;color-scheme:light}
.cv-drawing{max-width:100%;max-height:100%;margin:auto;padding:1rem;object-fit:contain}
.cv-preview>.cv-pre{margin:1rem 1.25rem;flex:none}
.cv-preview>.cv-cut{margin:.5rem 1.25rem 0}
.cv-page{width:100%;max-width:52rem;margin:0 auto;padding:1.25rem;font-size:.9375rem;line-height:1.65;overflow-wrap:anywhere}
.cv-page>:first-child{margin-top:0}
.cv-page h2,.cv-page h3,.cv-page h4,.cv-page h5,.cv-page h6{margin:1.4em 0 .5em;font-weight:600;line-height:1.3}
.cv-page h2{font-size:1.5rem}.cv-page h3{font-size:1.25rem}.cv-page h4{font-size:1.0625rem}.cv-page h5,.cv-page h6{font-size:1rem}
.cv-page p{margin:0 0 .9em;white-space:pre-line}
.cv-page ul,.cv-page ol{margin:0 0 .9em;padding-inline-start:1.5rem}
.cv-page ul{list-style:disc}.cv-page ol{list-style:decimal}
.cv-page li{margin:.2em 0}
.cv-page a{color:var(--cv-accent);text-decoration:underline;text-underline-offset:2px}
.cv-page code{padding:.1em .35em;border-radius:.375rem;background:var(--cv-soft);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.875em}
.cv-page pre{margin:0 0 .9em;flex:none}
.cv-page pre code{padding:0;background:transparent}
.cv-page blockquote{margin:0 0 .9em;padding:.1rem 1rem;border-inline-start:3px solid var(--cv-line);color:var(--cv-muted)}
.cv-page hr{margin:1.5em 0;border:0;border-top:1px solid var(--cv-line)}
.cv-page .cv-tablebox{margin:0 0 .9em}
.cv-diffbar{display:flex;flex-wrap:wrap;align-items:center;gap:.75rem;padding:.5rem 1rem;border-bottom:1px solid var(--cv-line)}
.cv-diffsum{color:var(--cv-muted);font-size:.8125rem;font-variant-numeric:tabular-nums}
.cv-difflines{flex:1;min-height:0;overflow:auto;padding:.5rem 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.8125rem;line-height:1.55}
.cv-line{display:flex;min-width:max-content}
.cv-sign{flex:none;width:2rem;text-align:center;font-weight:700;user-select:none}
.cv-linetext{white-space:pre;padding-inline-end:1rem;unicode-bidi:plaintext}
.cv-line[data-sign=add]{background:color-mix(in srgb,var(--cv-k-sheet) 16%,transparent)}
.cv-line[data-sign=add] .cv-sign{color:var(--cv-k-sheet)}
.cv-line[data-sign=del]{background:color-mix(in srgb,var(--cv-danger-text) 14%,transparent)}
.cv-line[data-sign=del] .cv-sign{color:var(--cv-danger-text)}
.cv-line[data-sign=same] .cv-linetext{color:var(--cv-muted)}
.cv-skip{padding:.25rem 2rem;color:var(--cv-muted);font-family:inherit;font-size:.75rem;border-block:1px dashed var(--cv-line);margin:.25rem 0}
.cv-status{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem .5rem;min-height:2rem;padding:.375rem 1rem;border-top:1px solid var(--cv-line);color:var(--cv-muted);font-size:.75rem;font-variant-numeric:tabular-nums}
.cv-kept{margin-inline-start:auto}
.cv-kept[data-state=not_kept],.cv-kept[data-state=too_big]{color:var(--cv-danger-text);font-weight:600}
.cv-flag[data-state=edited]{color:var(--cv-k-doc);background:color-mix(in srgb,var(--cv-k-doc) 13%,transparent)}
.cv-about{margin:0 0 .5rem!important;font-size:.8125rem}
.cv-quote{margin:0 0 .75rem;padding:.5rem .75rem;max-height:8rem;overflow:auto;border-radius:.5rem;background:var(--cv-soft);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.75rem;white-space:pre-wrap;overflow-wrap:anywhere}
textarea.cv-input{height:auto;min-height:4.5rem;padding:.5rem .75rem;resize:vertical;line-height:1.45}
.cv-hint{font-size:.8125rem}
@container cv (max-width:760px){
.cv-vbar .cv-tabs{margin-inline:.25rem}
.cv-tab{padding:.25rem .5rem}
.cv-titlebar{padding:.375rem .5rem;flex-wrap:wrap}
.cv-gutter{min-width:2.25rem;padding-inline:.25rem .375rem}
.cv-area{padding:.75rem}
.cv-page{padding:.75rem}
.cv-status{padding:.375rem .5rem}
.cv-head>.cv-btn:first-child{order:0}
}
"""

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiChatCanvas) return;
  window.__owuiChatCanvas = true;
  const CONFIG = __CONFIG__;
  // Longer than this, a canvas goes to the chat as an attached file, not as text in the message box
  const INLINE_LIMIT = 6000;
  const DRAFT_LIMIT = 400000;
  const STORE_LIMIT = 3000000;
  const DIFF_CELLS = 4000000;
  const GUTTER_LINES = 50000;
  const STORE = 'owui-chat-canvas:';

  /* ---- words, in the language Open WebUI is set to ---- */
  const STRINGS = {
    en: {
      canvas: 'Canvas', close_canvas: 'Close Canvas', list: 'Canvases', search: 'Search canvases', search_label: 'Search titles, languages and text', refresh: 'Refresh',
      new_canvas: 'New', new_label: 'New blank canvas', untitled: 'Untitled', download_all: 'Download all', cancel: 'Cancel',
      kind: 'Kind of canvas', kind_all: 'All kinds', kind_code: 'Code', kind_documents: 'Documents', kind_web: 'Web pages and drawings', kind_data: 'Data', kind_mine: 'Started by you',
      block_title: '{lang} {n}', from_you: 'From you', from_model: 'From the answer', mine: 'Started by you',
      lines_one: '{n} line', lines_other: '{n} lines', edited: 'Edited', writing: 'Still being written',
      count_one: '{n} canvas', count_other: '{n} canvases', edited_count: '{n} edited', of: '{shown} of {total}', collecting: 'Reading the chat…',
      none_match: 'No matching canvases', none_match_hint: 'Try other words, another kind, or clear the search.',
      empty: 'Nothing for the canvas yet', empty_hint: 'Code and longer documents from this chat appear here, ready to edit. You can also start a blank one.',
      no_chat_hint: 'Open a chat to work on its code and documents, or start a blank canvas.',
      chat_failed: 'Couldn’t read this chat', err_access: 'You no longer have access to this chat.', err_unsaved: 'This chat isn’t saved yet.', err_generic: 'Try again in a moment.', retry: 'Try again',
      editor: 'Canvas editor', back: 'Back to the list', previous: 'Previous canvas', next: 'Next canvas', view: 'View', mode_edit: 'Edit', mode_preview: 'Preview', mode_changes: 'Changes',
      title_label: 'Title', lang_label: 'Language', text_label: 'Text of the canvas', wrap: 'Wrap long lines',
      ask: 'Ask for a change', send: 'Put in the message box', copy: 'Copy', download: 'Download', save_file: 'Save to my files', save_note: 'Save as a note',
      show_in_chat: 'Show in chat', reset: 'Go back to the original', delete: 'Delete this canvas',
      status: 'Ln {line}, Col {col}', kept: 'Edits kept on this device', not_kept: 'Edits not kept: they are lost when the page closes', too_big: 'Too large to keep on this device', saved_now: 'Kept on this device',
      tab_released: 'Tab now moves to the next control. Press Escape again to go back to the list.',
      copied: 'Copied', copy_failed: 'Couldn’t copy', download_failed: 'Couldn’t download',
      file_saved: 'Saved to your files as {name}', file_failed: 'Couldn’t save to your files', note_saved: 'Saved as a note', note_failed: 'Couldn’t save the note', note_denied: 'Notes aren’t available for your account',
      no_preview: 'No preview for this kind of canvas', preview_empty: 'Nothing to show yet', json_bad: 'This isn’t valid JSON', svg_bad: 'This doesn’t look like an SVG drawing',
      scripts_off: 'Scripts are turned off in this preview.', table_cut: 'Showing the first {rows} rows and {cols} columns.',
      compare: 'Compare with', original: 'The original', no_changes: 'No changes', nothing_to_compare: 'Nothing to compare with yet', diff_big: 'Too large to compare line by line',
      diff_sum: '{added} added, {removed} removed', unchanged_one: '{n} unchanged line', unchanged_other: '{n} unchanged lines', added: 'Added', removed: 'Removed',
      ask_title: 'Ask for a change', ask_label: 'What should change?', ask_placeholder: 'For example: make this shorter', ask_whole: 'About the whole canvas', ask_part: 'About the selected part',
      ask_go: 'Put in the message box', ask_empty: 'Say what should change.', ask_note: 'Your request is put in the message box for you to check and send. Nothing is sent for you.',
      in_box: 'It’s in the message box. Check it, then send.', in_box_file: 'The canvas is attached and your request is in the message box. Check it, then send.',
      copied_paste: 'Copied. Paste it into the message box.', no_composer: 'Open a chat to send this',
      p_part: 'Change this part of "{title}":', p_lines: 'Change lines {from} to {to} of the attached file "{name}".', p_full: 'The full current version, for context:', p_this: 'This is the current version of "{title}":',
      p_attached: 'The current version is in the attached file "{name}".', p_reply: 'Reply with the complete updated version in one code block.', p_send: 'Here is my edited version of "{title}":',
      confirm_reset: 'Go back to the original?', confirm_reset_text: 'Your edits to this canvas are discarded. This can’t be undone.', discard: 'Discard edits',
      confirm_delete: 'Delete this canvas?', confirm_delete_text: 'It exists only on this device. This can’t be undone.', delete_short: 'Delete',
      zip_none: 'Nothing to download', saved_one: '{n} canvas saved', saved_other: '{n} canvases saved'
    },
    fr: {
      canvas: 'Canevas', close_canvas: 'Fermer le canevas', list: 'Canevas', search: 'Rechercher', search_label: 'Rechercher dans les titres, les langages et le texte', refresh: 'Actualiser',
      new_canvas: 'Nouveau', new_label: 'Nouveau canevas vide', untitled: 'Sans titre', download_all: 'Tout télécharger', cancel: 'Annuler',
      kind: 'Type de canevas', kind_all: 'Tous les types', kind_code: 'Code', kind_documents: 'Documents', kind_web: 'Pages web et dessins', kind_data: 'Données', kind_mine: 'Créés par vous',
      block_title: '{lang} {n}', from_you: 'De vous', from_model: 'De la réponse', mine: 'Créé par vous',
      lines_one: '{n} ligne', lines_other: '{n} lignes', edited: 'Modifié', writing: 'En cours d’écriture',
      count_one: '{n} canevas', count_other: '{n} canevas', edited_count: '{n} modifiés', of: '{shown} sur {total}', collecting: 'Lecture de la conversation…',
      none_match: 'Aucun canevas correspondant', none_match_hint: 'Essayez d’autres mots, un autre type, ou effacez la recherche.',
      empty: 'Rien pour le canevas pour l’instant', empty_hint: 'Le code et les longs documents de cette conversation apparaissent ici, prêts à être modifiés. Vous pouvez aussi partir d’une page vide.',
      no_chat_hint: 'Ouvrez une conversation pour travailler sur son code et ses documents, ou créez un canevas vide.',
      chat_failed: 'Impossible de lire cette conversation', err_access: 'Vous n’avez plus accès à cette conversation.', err_unsaved: 'Cette conversation n’est pas encore enregistrée.', err_generic: 'Réessayez dans un instant.', retry: 'Réessayer',
      editor: 'Éditeur du canevas', back: 'Retour à la liste', previous: 'Canevas précédent', next: 'Canevas suivant', view: 'Affichage', mode_edit: 'Modifier', mode_preview: 'Aperçu', mode_changes: 'Modifications',
      title_label: 'Titre', lang_label: 'Langage', text_label: 'Texte du canevas', wrap: 'Renvoyer les longues lignes',
      ask: 'Demander une modification', send: 'Mettre dans la zone de message', copy: 'Copier', download: 'Télécharger', save_file: 'Enregistrer dans mes fichiers', save_note: 'Enregistrer comme note',
      show_in_chat: 'Afficher dans la conversation', reset: 'Revenir à l’original', delete: 'Supprimer ce canevas',
      status: 'Ln {line}, Col {col}', kept: 'Modifications conservées sur cet appareil', not_kept: 'Modifications non conservées : elles sont perdues à la fermeture de la page', too_big: 'Trop volumineux pour être conservé sur cet appareil', saved_now: 'Conservé sur cet appareil',
      tab_released: 'Tab passe maintenant au contrôle suivant. Appuyez de nouveau sur Échap pour revenir à la liste.',
      copied: 'Copié', copy_failed: 'Copie impossible', download_failed: 'Téléchargement impossible',
      file_saved: 'Enregistré dans vos fichiers sous {name}', file_failed: 'Impossible d’enregistrer dans vos fichiers', note_saved: 'Enregistré comme note', note_failed: 'Impossible d’enregistrer la note', note_denied: 'Les notes ne sont pas disponibles pour votre compte',
      no_preview: 'Pas d’aperçu pour ce type de canevas', preview_empty: 'Rien à afficher pour l’instant', json_bad: 'Ce n’est pas du JSON valide', svg_bad: 'Cela ne ressemble pas à un dessin SVG',
      scripts_off: 'Les scripts sont désactivés dans cet aperçu.', table_cut: 'Affichage des {rows} premières lignes et {cols} premières colonnes.',
      compare: 'Comparer avec', original: 'L’original', no_changes: 'Aucune modification', nothing_to_compare: 'Rien à comparer pour l’instant', diff_big: 'Trop volumineux pour une comparaison ligne à ligne',
      diff_sum: '{added} ajoutées, {removed} supprimées', unchanged_one: '{n} ligne inchangée', unchanged_other: '{n} lignes inchangées', added: 'Ajoutée', removed: 'Supprimée',
      ask_title: 'Demander une modification', ask_label: 'Que faut-il changer ?', ask_placeholder: 'Par exemple : raccourcir ce texte', ask_whole: 'À propos de tout le canevas', ask_part: 'À propos de la partie sélectionnée',
      ask_go: 'Mettre dans la zone de message', ask_empty: 'Indiquez ce qu’il faut changer.', ask_note: 'Votre demande est placée dans la zone de message pour que vous la vérifiiez et l’envoyiez. Rien n’est envoyé à votre place.',
      in_box: 'C’est dans la zone de message. Vérifiez, puis envoyez.', in_box_file: 'Le canevas est joint et votre demande est dans la zone de message. Vérifiez, puis envoyez.',
      copied_paste: 'Copié. Collez-le dans la zone de message.', no_composer: 'Ouvrez une conversation pour envoyer ceci',
      p_part: 'Modifie cette partie de « {title} » :', p_lines: 'Modifie les lignes {from} à {to} du fichier joint « {name} ».', p_full: 'La version actuelle complète, pour le contexte :', p_this: 'Voici la version actuelle de « {title} » :',
      p_attached: 'La version actuelle se trouve dans le fichier joint « {name} ».', p_reply: 'Réponds avec la version complète mise à jour dans un seul bloc de code.', p_send: 'Voici ma version modifiée de « {title} » :',
      confirm_reset: 'Revenir à l’original ?', confirm_reset_text: 'Vos modifications de ce canevas sont abandonnées. Cette action est irréversible.', discard: 'Abandonner les modifications',
      confirm_delete: 'Supprimer ce canevas ?', confirm_delete_text: 'Il n’existe que sur cet appareil. Cette action est irréversible.', delete_short: 'Supprimer',
      zip_none: 'Rien à télécharger', saved_one: '{n} canevas enregistré', saved_other: '{n} canevas enregistrés'
    }
  };
  const language = () => {
    if (STRINGS[CONFIG.language]) return CONFIG.language;
    let locale = '';
    try { locale = localStorage.getItem('locale') || ''; } catch {}
    locale = String(locale || document.documentElement.lang || navigator.language || 'en').toLowerCase();
    return STRINGS[locale.slice(0, 2)] ? locale.slice(0, 2) : 'en';
  };
  let lang = 'en';
  const t = (key, vars) => {
    const text = STRINGS[lang][key] ?? STRINGS.en[key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (whole, name) => name in vars ? vars[name] : whole) : text;
  };
  const tn = (key, n, vars) => t(key + (n === 1 ? '_one' : '_other'), {n, ...vars});
  const icons = {
    canvas: 'm16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10',
    close: 'M6 18 18 6M6 6l12 12',
    prev: 'M15.75 19.5 8.25 12l7.5-7.5',
    next: 'm8.25 4.5 7.5 7.5-7.5 7.5',
    back: 'M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18',
    plus: 'M12 4.5v15m7.5-7.5h-15',
    download: 'M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3',
    copy: 'M8 8V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H16M5.5 8h9A1.5 1.5 0 0 1 16 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 18.5v-9A1.5 1.5 0 0 1 5.5 8Z',
    chat: 'M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z',
    files: 'M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m3.75 9v6m3-3H9m1.5-12H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z',
    note: 'M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25',
    sparkle: 'M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z',
    send: 'M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5',
    undo: 'M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3',
    refresh: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99',
    trash: 'm14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0'
  };
  const extOf = name => { const match = /\.([A-Za-z0-9]{1,10})$/.exec(String(name || '').trim()); return match ? match[1].toLowerCase() : ''; };
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
  const fileUrl = id => '/api/v1/files/' + id + '/content';
  const fileIdOf = url => { FILE_RE.lastIndex = 0; const match = FILE_RE.exec(url || ''); return match ? match[1] : null; };
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

  /* ---- reading code and documents out of a chat ---- */
  const ALIAS = {js:'javascript',jsx:'javascript',mjs:'javascript',node:'javascript',ts:'typescript',tsx:'typescript',py:'python',python3:'python',sh:'bash',shell:'bash',zsh:'bash',console:'bash',yml:'yaml',md:'markdown',htm:'html',xhtml:'html','c++':'cpp',cs:'csharp','c#':'csharp',rb:'ruby',rs:'rust',kt:'kotlin',golang:'go',plaintext:'text',plain:'text',txt:'text',tex:'latex',ps1:'powershell',mmd:'mermaid'};
  const EXTS = {javascript:'js',typescript:'ts',python:'py',bash:'sh',yaml:'yml',markdown:'md',html:'html',css:'css',scss:'scss',json:'json',csv:'csv',tsv:'tsv',xml:'xml',svg:'svg',sql:'sql',java:'java',c:'c',cpp:'cpp',csharp:'cs',go:'go',rust:'rs',ruby:'rb',php:'php',swift:'swift',kotlin:'kt',r:'r',lua:'lua',toml:'toml',ini:'ini',text:'txt',latex:'tex',diff:'diff',powershell:'ps1',mermaid:'mmd',vue:'vue',svelte:'svelte',dart:'dart',scala:'scala',perl:'pl'};
  const BY_EXT = Object.fromEntries([...Object.entries(EXTS).map(([lang, ext]) => [ext, lang]), ...Object.entries(ALIAS)]);
  const LOCAL_LANGS = ['markdown', 'text', 'html', 'css', 'javascript', 'typescript', 'python', 'json', 'csv', 'sql', 'bash', 'yaml', 'svg'];
  const LABELS = {javascript:'JavaScript',typescript:'TypeScript',html:'HTML',css:'CSS',scss:'SCSS',json:'JSON',csv:'CSV',tsv:'TSV',xml:'XML',svg:'SVG',sql:'SQL',yaml:'YAML',toml:'TOML',php:'PHP',cpp:'C++',csharp:'C#',latex:'LaTeX',markdown:'Markdown',text:'Text',bash:'Shell',powershell:'PowerShell'};
  const labelOf = lang => LABELS[lang] || lang.charAt(0).toUpperCase() + lang.slice(1);
  const kindOf = item => item.local ? 'mine' : item.lang === 'markdown' || item.lang === 'text' || item.lang === 'latex' ? 'documents' : item.lang === 'html' || item.lang === 'svg' ? 'web' : ['json','csv','tsv','yaml','xml','toml'].includes(item.lang) ? 'data' : 'code';
  const toneOf = item => ({mine: 'ref', documents: 'doc', web: 'slide', data: 'sheet', code: 'code'})[kindOf(item)];
  const countLines = text => { if (!text) return 0; let n = 1; for (let i = text.indexOf('\n'); i >= 0; i = text.indexOf('\n', i + 1)) n++; return text.endsWith('\n') ? n - 1 : n; };
  // Reasoning and tool calls arrive wrapped in <details>; their code isn't part of the answer
  function stripDetails(text) {
    let previous;
    do { previous = text; text = text.replace(/<details\b[^>]*>(?:(?!<details\b)[\s\S])*?<\/details>/gi, ''); } while (text !== previous);
    return text.replace(/<details\b[^>]*>[\s\S]*$/i, '');
  }
  const FENCE_RE = /^([ \t]*(?:>[ \t]?)*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?)(`{3,}|~{3,})[ \t]*(.*)$/;
  // Fenced blocks, as CommonMark reads them: a longer fence can hold a shorter one, and one left open runs to the end
  function parseBlocks(text) {
    const lines = text.replace(/\r\n?/g, '\n').split('\n'), blocks = [], prose = [];
    let open = null;
    for (const line of lines) {
      if (!open) {
        const match = FENCE_RE.exec(line);
        if (match && !(match[2][0] === '`' && match[3].includes('`'))) { open = {char: match[2][0], size: match[2].length, indent: match[1].length, info: match[3].trim(), lines: []}; continue; }
        prose.push(line);
        continue;
      }
      const rest = line.replace(/^[ \t>]*/, '');
      if (rest.length >= open.size && rest.trimEnd() === open.char.repeat(rest.trimEnd().length) && rest[0] === open.char) { blocks.push({info: open.info, text: open.lines.join('\n'), unfinished: false}); open = null; continue; }
      let cut = 0;
      while (cut < open.indent && cut < line.length && /[ \t>]/.test(line[cut])) cut++;
      open.lines.push(line.slice(cut));
    }
    if (open) blocks.push({info: open.info, text: open.lines.join('\n'), unfinished: true});
    return {blocks, prose: prose.join('\n')};
  }
  const cleanLang = value => { const lang = String(value || '').toLowerCase(); const known = ALIAS[lang] ?? lang; return /^[a-z0-9+#_.-]{1,24}$/.test(known) ? known : 'text'; };
  function readInfo(info, text) {
    let title = (/(?:title|filename|file|name)\s*=\s*["']?([^"'\s}]+)/i.exec(info) || [])[1] || '';
    let first = (info.split(/[\s,{}]+/).filter(Boolean)[0] || '').replace(/^\./, '');
    const colon = first.indexOf(':');
    if (colon > 0) { const after = first.slice(colon + 1); if (!title && /\.\w{1,8}$/.test(after)) title = after; first = first.slice(0, colon); }
    if (/=/.test(first)) first = '';
    let lang = '';
    if (!title && /^[\w.\-/\\]+\.\w{1,8}$/.test(first) && !ALIAS[first.toLowerCase()] && !EXTS[first.toLowerCase()]) { title = first; first = ''; }
    if (first) lang = cleanLang(first);
    if (!title) title = (/^\s*(?:#|\/\/|--|;|<!--|\/\*)\s*(?:file(?:name)?\s*:\s*)?([\w.\-/\\]+\.\w{1,8})\s*(?:-->|\*\/)?\s*$/i.exec(text.split('\n', 1)[0]) || [])[1] || '';
    if (!lang && title) lang = cleanLang(BY_EXT[(/\.(\w{1,8})$/.exec(title) || [])[1]?.toLowerCase()] || '');
    if ((!lang || lang === 'xml' || lang === 'html' || lang === 'text') && /^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--[\s\S]*?-->\s*)?<svg[\s>]/i.test(text)) lang = 'svg';
    if (!title && lang === 'html') title = ((/<title[^>]*>([^<]{1,80})<\/title>/i.exec(text) || [])[1] || '').trim();
    return {lang: lang || 'text', title: title.split(/[\\/]/).pop().slice(0, 120)};
  }
  const docTitle = text => {
    for (const line of text.split('\n')) { const clean = line.replace(/^\s{0,3}#{1,6}\s+/, '').replace(/[*_`#>]/g, '').trim(); if (clean) return clean.length > 70 ? clean.slice(0, 67).trimEnd() + '…' : clean; }
    return '';
  };
  const makeItem = fields => ({key: '', messageId: null, chatId: null, role: 'assistant', at: 0, lang: 'text', title: '', original: '', text: '', unfinished: false, local: false, doc: false, ...fields});
  function itemsOf(chat, chatId) {
    const messages = Object.values(chat?.chat?.history?.messages || {}).sort((a, b) => (a?.timestamp || 0) - (b?.timestamp || 0));
    const out = [], counts = {};
    for (const message of messages) {
      if (!message || typeof message.content !== 'string' || !message.id) continue;
      const content = stripDetails(message.content);
      const {blocks, prose} = parseBlocks(content);
      const base = {messageId: message.id, chatId, role: message.role === 'user' ? 'user' : 'assistant', at: message.timestamp || 0};
      // A long answer is a document in its own right, code and all
      if (base.role === 'assistant' && CONFIG.minDocumentChars > 0 && prose.trim().length >= CONFIG.minDocumentChars) {
        const text = content.replace(/\r\n?/g, '\n').trim();
        out.push(makeItem({...base, key: message.id + ':doc', lang: 'markdown', doc: true, title: docTitle(prose), original: text, text, unfinished: message.done === false}));
      }
      blocks.forEach((block, n) => {
        if (countLines(block.text) < CONFIG.minCodeLines || !block.text.trim()) return;
        const {lang, title} = readInfo(block.info, block.text);
        counts[lang] = (counts[lang] || 0) + 1;
        out.push(makeItem({...base, key: message.id + ':' + n, lang, title: title || t('block_title', {lang: labelOf(lang), n: counts[lang]}), original: block.text, text: block.text, unfinished: block.unfinished}));
      });
    }
    return out;
  }

  /* ---- edits are kept on this device, per person and per chat ---- */
  const memory = new Map();
  let keptState = 'kept';
  const storeKey = chatId => STORE + (me?.id || 'anon') + ':' + (chatId || 'home');
  const blankStore = () => ({at: 0, drafts: {}, locals: []});
  function readStore(chatId) {
    const key = storeKey(chatId);
    if (memory.has(key)) return memory.get(key);
    let data = blankStore();
    if (CONFIG.keepDrafts) {
      try {
        const parsed = JSON.parse(localStorage.getItem(key) || 'null');
        if (parsed && typeof parsed === 'object') data = {at: +parsed.at || 0, drafts: parsed.drafts && typeof parsed.drafts === 'object' ? parsed.drafts : {}, locals: Array.isArray(parsed.locals) ? parsed.locals.filter(each => each && typeof each.id === 'string' && typeof each.text === 'string') : []};
      } catch {}
    }
    memory.set(key, data);
    return data;
  }
  function ourKeys() {
    const found = [];
    try { for (let i = 0; i < localStorage.length; i++) { const key = localStorage.key(i); if (key && key.startsWith(STORE)) found.push(key); } } catch {}
    return found;
  }
  // Oldest chats' edits make room for new ones, so the browser's small storage is never the reason an edit is lost silently
  function makeRoom(keep, need) {
    const others = ourKeys().filter(key => key !== keep).map(key => { let at = 0, size = 0; try { const raw = localStorage.getItem(key) || ''; size = raw.length; at = +JSON.parse(raw).at || 0; } catch {} return {key, at, size}; }).sort((a, b) => a.at - b.at);
    let total = others.reduce((sum, each) => sum + each.size, 0) + need, freed = false;
    while (others.length && (total > STORE_LIMIT || !freed && need < 0)) { const oldest = others.shift(); try { localStorage.removeItem(oldest.key); } catch {} total -= oldest.size; freed = true; }
    return freed;
  }
  function writeStore(chatId) {
    const key = storeKey(chatId), data = readStore(chatId);
    data.at = Date.now();
    if (!CONFIG.keepDrafts) { keptState = 'not_kept'; return; }
    const kept = {at: data.at, drafts: {}, locals: []};
    let dropped = false;
    for (const [id, draft] of Object.entries(data.drafts)) { if (draft.text.length <= DRAFT_LIMIT) kept.drafts[id] = draft; else dropped = true; }
    for (const local of data.locals) { if (local.text.length <= DRAFT_LIMIT) kept.locals.push(local); else dropped = true; }
    try {
      if (!Object.keys(kept.drafts).length && !kept.locals.length) { localStorage.removeItem(key); keptState = dropped ? 'too_big' : 'kept'; return; }
      const raw = JSON.stringify(kept);
      makeRoom(key, raw.length);
      try { localStorage.setItem(key, raw); }
      catch { if (!makeRoom(key, -1)) throw new Error('full'); localStorage.setItem(key, raw); }
      keptState = dropped ? 'too_big' : 'kept';
    } catch { keptState = 'not_kept'; }
  }
  function applyStore(found, chatId) {
    const data = readStore(chatId);
    for (const item of found) { const draft = data.drafts[item.key]; if (draft && typeof draft.text === 'string') item.text = draft.text; }
    const locals = data.locals.map(local => makeItem({key: local.id, chatId, local: true, role: 'user', at: local.at || 0, lang: cleanLang(local.lang), title: String(local.title || ''), original: '', text: local.text}));
    return [...found, ...locals];
  }
  let saveTimer = 0;
  function persist(item) {
    const data = readStore(item.chatId);
    if (item.local) {
      const at = data.locals.findIndex(each => each.id === item.key);
      const record = {id: item.key, title: item.title, lang: item.lang, text: item.text, at: item.at};
      if (at >= 0) data.locals[at] = record; else data.locals.push(record);
    } else if (item.text === item.original) delete data.drafts[item.key];
    else data.drafts[item.key] = {text: item.text, at: Date.now()};
    writeStore(item.chatId);
  }
  const persistSoon = item => { clearTimeout(saveTimer); saveTimer = setTimeout(() => { saveTimer = 0; persist(item); paintStatus(); }, 400); };
  const flush = () => { if (saveTimer && viewItem) { clearTimeout(saveTimer); saveTimer = 0; persist(viewItem); } };
  const isEdited = item => !item.local && item.text !== item.original;

  /* ---- the panel ---- */
  let dialog, head, search, kindSelect, refreshButton, newButton, zipButton, soloClose, scroller, listEl, foot, footText, live, toast;
  let viewer, stage, statusEl, countEl, prevButton, nextButton, modeTabs = {}, titleInput, langSelect, wrapBox, editBox, gutter, area, previewBox, diffBox;
  let askButton, sendButton, copyButton, downloadButton, fileButton, noteButton, chatButton, resetButton, deleteButton, confirmBox, askBox;
  let me = null, meAsked = null;
  let items = [], shown = [], opener = null, headerButton = null, collectController = null;
  let chatLoading = false, chatError = '', chatLoadedAt = 0, loadedChat = null, kind = 'all', builtLang = '';
  const rows = new Map();

  function flash(text) {
    toast.textContent = text; toast.classList.add('cv-show');
    clearTimeout(flash.timer); flash.timer = setTimeout(() => toast.classList.remove('cv-show'), 3200);
    live.textContent = text;
  }
  function build() {
    lang = builtLang = language();
    dialog = el('div', 'cv-dialog'); dialog.id = 'owui-chat-canvas'; dialog.hidden = true; dialog.lang = lang;
    dialog.setAttribute('role', 'tabpanel'); dialog.setAttribute('aria-labelledby', 'owui-chat-canvas-tab');
    head = el('div', 'cv-head');
    search = el('input', 'cv-search'); search.type = 'search'; search.placeholder = t('search');
    search.setAttribute('aria-label', t('search_label')); search.title = t('search_label');
    let searchTimer = 0;
    search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(render, 120); });
    kindSelect = el('select', 'cv-select'); kindSelect.setAttribute('aria-label', t('kind'));
    kindSelect.addEventListener('change', () => { kind = kindSelect.value; scroller.scrollTop = 0; render(); });
    refreshButton = iconButton(t('refresh'), 'refresh', () => loadChat(true));
    newButton = textButton(t('new_canvas'), newCanvas); newButton.prepend(svg('plus')); setLabel(newButton, t('new_label'));
    zipButton = textButton(t('download_all'), downloadZip); zipButton.prepend(svg('download'));
    soloClose = iconButton(t('close_canvas'), 'close', () => closeTab(), 'cv-icon cv-close'); soloClose.hidden = true;
    head.append(newButton, el('span', 'cv-spacer'), search, kindSelect, refreshButton, zipButton, soloClose);
    const body = el('div', 'cv-body');
    scroller = el('div', 'cv-scroll');
    listEl = el('ul', 'cv-list'); listEl.setAttribute('aria-label', t('list'));
    listEl.addEventListener('keydown', moveFocus);
    scroller.append(listEl);
    foot = el('div', 'cv-foot'); footText = el('span'); foot.append(footText);
    live = el('div', 'cv-sr'); live.setAttribute('aria-live', 'polite');
    toast = el('div', 'cv-toast'); toast.setAttribute('aria-hidden', 'true');
    body.append(scroller, foot, buildViewer(), toast, live);
    confirmBox = el('dialog', 'cv-modal'); confirmBox.setAttribute('aria-labelledby', 'cv-confirm-title'); confirmBox.setAttribute('aria-describedby', 'cv-confirm-text');
    askBox = el('dialog', 'cv-modal'); askBox.setAttribute('aria-labelledby', 'cv-ask-title');
    dialog.append(head, body, confirmBox, askBox);
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.target.closest('dialog')) return;
      if (viewer.classList.contains('cv-open')) { event.preventDefault(); closeViewer(); }
      else if (dialog.classList.contains('cv-solo')) { event.preventDefault(); deactivate(); headerButton?.focus({preventScroll: true}); }
    });
    document.body.append(dialog);
  }
  function moveFocus(event) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !event.target.classList?.contains('cv-row')) return;
    const all = [...listEl.querySelectorAll('.cv-row')];
    const at = all.indexOf(event.target);
    const next = {ArrowDown: all[at + 1], ArrowUp: all[at - 1], Home: all[0], End: all[all.length - 1]}[event.key];
    if (!next) return;
    event.preventDefault(); next.focus(); next.scrollIntoView({block: 'nearest'});
  }
  // Who is signed in decides whose edits are read; ask once, and carry on without an answer
  function whoAmI() {
    if (!meAsked) meAsked = api('/api/v1/auths/').then(user => { me = user; }).catch(() => {});
    return meAsked;
  }
  // quiet: keep what is on screen while the chat is read again, and keep the same objects so the open editor survives
  async function loadChat(quiet) {
    collectController?.abort();
    const controller = collectController = new AbortController();
    const chatId = chatIdFromPath();
    if (!quiet || chatId !== loadedChat) { items = []; quiet = false; }
    chatError = ''; chatLoading = true;
    render();
    await whoAmI();
    if (controller.signal.aborted) return;
    let found = [];
    try {
      if (chatId) found = itemsOf(await api('/api/v1/chats/' + encodeURIComponent(chatId), controller.signal), chatId);
      if (controller.signal.aborted) return;
    } catch (error) {
      if (controller.signal.aborted) return;
      // A chat that isn't saved yet is simply empty; anything else is worth saying
      if (error.status !== 404) chatError = error.status === 401 || error.status === 403 ? t('err_access') : t('err_generic');
      if (quiet && chatError) { chatLoading = false; render(); return; }
    }
    found = applyStore(found, chatId);
    if (quiet) {
      const known = new Map(items.map(item => [item.key, item]));
      found = found.map(item => {
        const old = known.get(item.key);
        if (!old) return item;
        // An answer still being written keeps growing; follow it unless the person has started editing
        const untouched = old.text === old.original;
        Object.assign(old, {original: item.original, unfinished: item.unfinished, title: old.local ? old.title : item.title, lang: old.local ? old.lang : item.lang});
        if (untouched && !old.local) old.text = item.original;
        return old;
      });
    }
    items = found; loadedChat = chatId;
    chatLoading = false; chatLoadedAt = Date.now();
    render();
    if (viewItem) { if (items.includes(viewItem)) { index = Math.max(0, shown.indexOf(viewItem)); refreshEditor(); } else closeViewer(true); }
  }
  function matches(item, query) {
    if (kind !== 'all' && kindOf(item) !== kind) return false;
    if (!query) return true;
    return [item.title, item.lang, labelOf(item.lang), item.text.length < 200000 ? item.text : ''].some(value => String(value).toLocaleLowerCase(lang).includes(query));
  }
  function rowFor(item) {
    let entry = rows.get(item);
    if (entry) return entry;
    const li = el('li');
    const row = el('button', 'cv-row'); row.type = 'button';
    const badge = el('span', 'cv-badge');
    const text = el('span', 'cv-text');
    const name = nameSpan('cv-name'), sub = el('span', 'cv-sub');
    text.append(name, sub); row.append(badge, text); li.append(row);
    entry = {item, li, row, badge, name, sub, index: 0};
    row.addEventListener('click', () => openViewer(entry.index));
    rows.set(item, entry);
    return entry;
  }
  function paintRow(entry) {
    const item = entry.item;
    entry.badge.textContent = (EXTS[item.lang] || item.lang).slice(0, 4).toUpperCase(); entry.badge.dataset.tone = toneOf(item);
    const title = item.title || t('untitled');
    entry.name.textContent = title; entry.name.title = title;
    const parts = [labelOf(item.lang), tn('lines', countLines(item.text)), t(item.local ? 'mine' : item.role === 'user' ? 'from_you' : 'from_model')];
    const when = formatDate(item.at, true);
    if (when) parts.push(when);
    entry.sub.replaceChildren(document.createTextNode(parts.join(' · ')));
    const flags = [];
    if (isEdited(item)) flags.push(['edited', 'edited']);
    if (item.unfinished) flags.push(['writing', 'writing']);
    for (const [word, state] of flags) { const flag = el('span', 'cv-flag', t(word)); flag.dataset.state = state; entry.sub.append(flag); }
    entry.row.setAttribute('aria-label', [title, ...parts, ...flags.map(([word]) => t(word))].join(', '));
  }
  function fillKinds(list) {
    const present = new Set(list.map(kindOf));
    const kinds = ['all', 'code', 'documents', 'web', 'data', 'mine'].filter(value => value === 'all' || present.has(value) || value === kind);
    const signature = kinds.join();
    if (kindSelect.dataset.kinds !== signature) {
      kindSelect.replaceChildren(...kinds.map(value => { const option = el('option', null, t('kind_' + value)); option.value = value; return option; }));
      kindSelect.dataset.kinds = signature;
    }
    if (kindSelect.value !== kind) kindSelect.value = kind;
    kindSelect.hidden = kinds.length < 3;
  }
  function render() {
    const query = search.value.trim().toLocaleLowerCase(lang);
    fillKinds(items);
    // The newest work first, and what the person started themselves on top
    shown = items.filter(item => matches(item, query)).sort((a, b) => (b.local - a.local) || (b.at - a.at) || 0);
    for (const item of rows.keys()) if (!items.includes(item)) rows.delete(item);
    let node = listEl.firstElementChild;
    shown.forEach((item, at) => {
      const entry = rowFor(item);
      entry.index = at; paintRow(entry);
      if (node === entry.li) node = node.nextElementSibling; else listEl.insertBefore(entry.li, node);
    });
    while (node) { const next = node.nextElementSibling; node.remove(); node = next; }
    const filtered = !!query || kind !== 'all';
    if (!shown.length && !chatLoading) {
      const empty = el('li', 'cv-empty');
      const words = (title, hint) => empty.append(el('strong', null, t(title)), el('span', null, hint));
      if (chatError) { words('chat_failed', chatError); empty.append(textButton(t('retry'), () => loadChat())); }
      else if (filtered && items.length) words('none_match', t('none_match_hint'));
      else { words('empty', t(chatIdFromPath() ? 'empty_hint' : 'no_chat_hint')); const start = textButton(t('new_label'), newCanvas); start.prepend(svg('plus')); empty.append(start); }
      listEl.append(empty);
    }
    const edited = items.filter(isEdited).length;
    let status = '';
    if (chatLoading && !items.length) status = t('collecting');
    else if (items.length) status = (filtered ? t('of', {shown: shown.length, total: tn('count', items.length)}) : tn('count', items.length)) + (edited ? ' · ' + t('edited_count', {n: edited}) : '');
    footText.textContent = status;
    refreshButton.disabled = chatLoading;
    zipButton.disabled = !shown.length;
  }
  function newCanvas() {
    const chatId = chatIdFromPath();
    const item = makeItem({key: 'local:' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8), chatId, local: true, role: 'user', at: Math.floor(Date.now() / 1000), lang: 'markdown', title: t('untitled')});
    items.push(item); persist(item);
    search.value = ''; kind = 'all'; render();
    openViewer(shown.indexOf(item));
    area.focus();
  }

  /* ---- the editor ---- */
  let index = 0, viewItem = null, mode = 'edit', tabTrap = true, objectUrls = [], compareWith = 'original', previewTimer = 0;
  const canPreview = item => ['html', 'svg', 'markdown', 'json', 'csv', 'tsv'].includes(item.lang);
  function buildViewer() {
    viewer = el('div', 'cv-viewer'); viewer.setAttribute('role', 'group'); viewer.setAttribute('aria-roledescription', t('editor')); viewer.setAttribute('aria-label', t('editor'));
    const bar = el('div', 'cv-vbar');
    countEl = el('span', 'cv-count');
    prevButton = iconButton(t('previous'), 'prev', () => step(-1));
    nextButton = iconButton(t('next'), 'next', () => step(1));
    const modes = el('div', 'cv-tabs'); modes.setAttribute('role', 'tablist'); modes.setAttribute('aria-label', t('view'));
    for (const value of ['edit', 'preview', 'changes']) {
      const node = el('button', 'cv-tab', t('mode_' + value)); node.type = 'button'; node.setAttribute('role', 'tab'); node.id = 'cv-mode-' + value; node.setAttribute('aria-controls', 'cv-stage');
      node.addEventListener('click', () => setMode(value));
      node.addEventListener('keydown', event => {
        const visible = Object.values(modeTabs).filter(each => !each.hidden), at = visible.indexOf(node);
        const forward = getComputedStyle(node).direction === 'rtl' ? 'ArrowLeft' : 'ArrowRight';
        const target = event.key === forward ? visible[at + 1] || visible[0] : event.key === (forward === 'ArrowRight' ? 'ArrowLeft' : 'ArrowRight') ? visible[at - 1] || visible[visible.length - 1] : null;
        if (target) { event.preventDefault(); target.focus(); target.click(); }
      });
      modeTabs[value] = node; modes.append(node);
    }
    askButton = iconButton(t('ask'), 'sparkle', askForChange);
    sendButton = iconButton(t('send'), 'send', () => toComposer(viewItem, '', null));
    copyButton = iconButton(t('copy'), 'copy', copyAll);
    downloadButton = iconButton(t('download'), 'download', () => { try { save(new Blob([viewItem.text], {type: 'text/plain;charset=utf-8'}), fileNameOf(viewItem)); } catch { flash(t('download_failed')); } });
    fileButton = iconButton(t('save_file'), 'files', saveToFiles);
    noteButton = iconButton(t('save_note'), 'note', saveAsNote);
    chatButton = iconButton(t('show_in_chat'), 'chat', showInChat);
    resetButton = iconButton(t('reset'), 'undo', resetItem);
    deleteButton = iconButton(t('delete'), 'trash', deleteItem);
    bar.append(iconButton(t('back'), 'back', () => closeViewer()), prevButton, countEl, nextButton, modes, el('span', 'cv-spacer'), askButton, sendButton, copyButton, downloadButton, fileButton, noteButton, chatButton, resetButton, deleteButton);
    const titleBar = el('div', 'cv-titlebar');
    titleInput = el('input', 'cv-title'); titleInput.type = 'text'; titleInput.dir = 'auto'; titleInput.maxLength = 120; titleInput.setAttribute('aria-label', t('title_label')); titleInput.spellcheck = false;
    titleInput.addEventListener('input', () => { if (!viewItem?.local) return; viewItem.title = titleInput.value; persistSoon(viewItem); const entry = rows.get(viewItem); if (entry) paintRow(entry); });
    langSelect = el('select', 'cv-select'); langSelect.setAttribute('aria-label', t('lang_label'));
    for (const value of LOCAL_LANGS) { const option = el('option', null, labelOf(value)); option.value = value; langSelect.append(option); }
    langSelect.addEventListener('change', () => { if (!viewItem?.local) return; viewItem.lang = langSelect.value; persist(viewItem); const entry = rows.get(viewItem); if (entry) paintRow(entry); refreshEditor(); });
    const wrapLabel = el('label', 'cv-wrap');
    wrapBox = el('input'); wrapBox.type = 'checkbox';
    wrapBox.addEventListener('change', () => { editBox.classList.toggle('cv-wrapped', wrapBox.checked); paintGutter(true); });
    wrapLabel.append(wrapBox, document.createTextNode(t('wrap')));
    titleBar.append(titleInput, langSelect, wrapLabel);
    stage = el('div', 'cv-stage'); stage.id = 'cv-stage'; stage.setAttribute('role', 'tabpanel');
    editBox = el('div', 'cv-edit');
    gutter = el('pre', 'cv-gutter'); gutter.setAttribute('aria-hidden', 'true');
    area = el('textarea', 'cv-area'); area.setAttribute('aria-label', t('text_label')); area.spellcheck = false; area.autocapitalize = 'off'; area.setAttribute('autocorrect', 'off'); area.setAttribute('autocomplete', 'off');
    area.addEventListener('input', onEdit);
    area.addEventListener('scroll', () => { gutter.scrollTop = area.scrollTop; });
    for (const type of ['keyup', 'click', 'select', 'focus']) area.addEventListener(type, paintStatus);
    area.addEventListener('focus', () => { tabTrap = true; });
    area.addEventListener('keydown', onAreaKey);
    editBox.append(gutter, area);
    previewBox = el('div', 'cv-preview'); previewBox.hidden = true;
    diffBox = el('div', 'cv-diff'); diffBox.hidden = true;
    stage.append(editBox, previewBox, diffBox);
    statusEl = el('div', 'cv-status');
    viewer.append(bar, titleBar, stage, statusEl);
    viewer.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 's') { event.preventDefault(); flush(); if (viewItem) persist(viewItem); paintStatus(); flash(t(keptState === 'kept' ? 'saved_now' : keptState)); }
    });
    return viewer;
  }
  function onAreaKey(event) {
    if (event.key === 'Escape') {
      // The first Escape frees the Tab key for moving between controls; the next one leaves the editor
      if (tabTrap) { tabTrap = false; event.preventDefault(); event.stopPropagation(); flash(t('tab_released')); }
      return;
    }
    if (event.key !== 'Tab' || !tabTrap || event.ctrlKey || event.metaKey || event.altKey || area.readOnly) return;
    event.preventDefault();
    const unit = /^\t/m.test(area.value) ? '\t' : '  ';
    const {selectionStart: from, selectionEnd: to, value} = area;
    const insert = (text, start, end, selectFrom, selectTo) => {
      area.setSelectionRange(start, end);
      let done = false;
      try { done = document.execCommand('insertText', false, text); } catch {}
      if (!done) { area.setRangeText(text, start, end, 'end'); onEdit(); }
      area.setSelectionRange(selectFrom, selectTo);
    };
    if (from === to && !event.shiftKey) { insert(unit, from, to, from + unit.length, from + unit.length); return; }
    const lineStart = value.lastIndexOf('\n', from - 1) + 1;
    const lineEnd = to > from && value[to - 1] === '\n' ? to - 1 : to;
    const block = value.slice(lineStart, lineEnd).split('\n');
    const changed = block.map(line => event.shiftKey ? (line.startsWith(unit) ? line.slice(unit.length) : line.replace(/^[ \t]/, '')) : (line ? unit + line : line));
    const text = changed.join('\n');
    if (text === block.join('\n')) return;
    const first = changed[0].length - block[0].length;
    insert(text, lineStart, lineEnd, Math.max(lineStart, from + first), Math.max(lineStart, from + first) === from + first && from === to ? from + first : lineStart + text.length + (lineEnd < to ? 1 : 0));
  }
  function onEdit() {
    const item = viewItem;
    if (!item) return;
    item.text = area.value;
    // A canvas that is just being started finds its direction as the first words are typed
    if (item.text.length < 400) { const direction = directionOf(item.text); if (area.dir !== direction) area.dir = direction; }
    persistSoon(item);
    paintGutter(); paintStatus(); paintButtons();
    const entry = rows.get(item); if (entry) paintRow(entry);
  }
  // dir="auto" makes the browser work out the direction of every line, seconds of work on a long file;
  // the first letter with a direction, near the start, decides for the whole canvas instead
  const directionOf = text => { const match = /[A-Za-z\u00C0-\u024F\u0370-\u03FF\u0400-\u04FF\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/.exec(text.slice(0, 4000)); return match && /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/.test(match[0]) ? 'rtl' : 'ltr'; };
  let gutterLines = -1;
  function paintGutter(force) {
    const show = !wrapBox.checked;
    gutter.hidden = !show;
    if (!show) { gutterLines = -1; return; }
    const count = Math.max(1, area.value.split('\n').length);
    if (count === gutterLines && !force) return;
    gutterLines = count;
    if (count > GUTTER_LINES) { gutter.hidden = true; return; }
    let text = '';
    for (let n = 1; n <= count; n++) text += n + '\n';
    gutter.textContent = text;
    gutter.scrollTop = area.scrollTop;
  }
  function paintStatus() {
    const item = viewItem;
    if (!item) return;
    const parts = [];
    if (mode === 'edit') { const before = area.value.slice(0, area.selectionStart); const line = before.split('\n').length; parts.push(t('status', {line, col: before.length - before.lastIndexOf('\n')})); }
    parts.push(tn('lines', countLines(item.text)), formatBytes(new Blob([item.text]).size));
    statusEl.replaceChildren(document.createTextNode(parts.join(' · ')));
    if (item.unfinished) { const flag = el('span', 'cv-flag', t('writing')); flag.dataset.state = 'writing'; statusEl.append(flag); }
    if (isEdited(item)) { const flag = el('span', 'cv-flag', t('edited')); flag.dataset.state = 'edited'; statusEl.append(flag); }
    if (isEdited(item) || item.local) { const kept = el('span', 'cv-kept', t(item.text.length > DRAFT_LIMIT && CONFIG.keepDrafts ? 'too_big' : keptState)); kept.dataset.state = item.text.length > DRAFT_LIMIT ? 'too_big' : keptState; statusEl.append(kept); }
  }
  function paintButtons() {
    const item = viewItem;
    if (!item) return;
    resetButton.hidden = !isEdited(item);
    deleteButton.hidden = !item.local;
    chatButton.hidden = !(item.messageId && item.chatId);
    fileButton.hidden = noteButton.hidden = !CONFIG.allowSave;
    const empty = !item.text.trim();
    for (const button of [askButton, sendButton, copyButton, downloadButton, fileButton, noteButton]) button.disabled = empty;
    modeTabs.preview.hidden = !canPreview(item);
    modeTabs.changes.hidden = !compareChoices(item).length;
  }
  function openViewer(at) {
    viewer.classList.add('cv-open'); dialog.classList.add('cv-viewing');
    head.inert = scroller.inert = true;
    show(at);
    viewer.querySelector('.cv-vbar .cv-icon').focus();
  }
  function releaseUrls() { for (const url of objectUrls) URL.revokeObjectURL(url); objectUrls = []; }
  function closeViewer(silent) {
    if (!viewer.classList.contains('cv-open')) return;
    flush(); releaseUrls(); clearTimeout(previewTimer);
    viewer.classList.remove('cv-open'); dialog.classList.remove('cv-viewing');
    previewBox.replaceChildren(); diffBox.replaceChildren(); area.value = '';
    head.inert = scroller.inert = false;
    const item = viewItem; viewItem = null;
    // A blank canvas nobody wrote in isn't worth keeping
    if (item?.local && !item.text && (!item.title || item.title === t('untitled'))) removeLocal(item);
    render();
    if (!silent) (rows.get(item)?.li.isConnected ? rows.get(item).row : listEl.querySelector('.cv-row') || newButton)?.focus();
  }
  function step(delta) {
    if (shown.length < 2) return;
    flush();
    show((index + delta + shown.length) % shown.length);
  }
  function show(at) {
    index = at;
    viewItem = shown[at];
    compareWith = 'original';
    mode = 'edit';
    wrapBox.checked = kindOf(viewItem) === 'documents' || (viewItem.local && viewItem.lang === 'markdown');
    refreshEditor(true);
  }
  // Called when another canvas is shown, and when the chat was read again under an open editor
  function refreshEditor(fresh) {
    const item = viewItem;
    if (!item) return;
    countEl.textContent = (index + 1) + ' / ' + shown.length;
    prevButton.hidden = nextButton.hidden = shown.length < 2;
    titleInput.value = item.title || t('untitled'); titleInput.readOnly = !item.local;
    langSelect.hidden = !item.local;
    if (item.local && !LOCAL_LANGS.includes(item.lang)) { const option = el('option', null, labelOf(item.lang)); option.value = item.lang; langSelect.append(option); }
    if (item.local) langSelect.value = item.lang;
    editBox.classList.toggle('cv-wrapped', wrapBox.checked);
    editBox.classList.toggle('cv-prose', kindOf(item) === 'documents' || item.lang === 'markdown');
    const direction = directionOf(item.text); if (area.dir !== direction) area.dir = direction;
    if (area.value !== item.text) { const at = fresh ? 0 : area.selectionStart; area.value = item.text; if (fresh) { area.scrollTop = 0; area.setSelectionRange(0, 0); } else area.setSelectionRange(Math.min(at, item.text.length), Math.min(at, item.text.length)); }
    // While the answer is still being written, typing would be overwritten by the next refresh
    area.readOnly = item.unfinished && !isEdited(item);
    paintGutter(true); paintButtons();
    if (modeTabs[mode].hidden) mode = 'edit';
    setMode(mode);
  }
  function setMode(value) {
    mode = value;
    for (const [name, node] of Object.entries(modeTabs)) { node.setAttribute('aria-selected', String(name === value)); node.tabIndex = name === value ? 0 : -1; }
    stage.setAttribute('aria-labelledby', 'cv-mode-' + value);
    editBox.hidden = value !== 'edit'; previewBox.hidden = value !== 'preview'; diffBox.hidden = value !== 'changes';
    wrapBox.closest('label').hidden = value !== 'edit';
    releaseUrls();
    if (value === 'preview') paintPreview();
    if (value === 'changes') paintDiff();
    paintStatus();
  }
  const notice = (title, hint) => { const box = el('div', 'cv-notice'); box.append(el('strong', null, title)); if (hint) box.append(el('span', null, hint)); return box; };

  /* ---- preview ---- */
  function paintPreview() {
    const item = viewItem;
    releaseUrls();
    if (!item.text.trim()) { previewBox.replaceChildren(notice(t('preview_empty'))); return; }
    if (item.lang === 'html') {
      // An opaque-origin sandbox: the page can run, but it can't reach Open WebUI, its cookies, or this window
      const frame = el('iframe', 'cv-frame'); frame.title = item.title || t('mode_preview');
      frame.setAttribute('sandbox', CONFIG.runScripts ? 'allow-scripts' : ''); frame.referrerPolicy = 'no-referrer';
      frame.srcdoc = item.text;
      previewBox.replaceChildren(frame);
      if (!CONFIG.runScripts && /<script\b/i.test(item.text)) previewBox.prepend(el('p', 'cv-cut', t('scripts_off')));
      return;
    }
    if (item.lang === 'svg') {
      if (!/^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--[\s\S]*?-->\s*)*(?:<!DOCTYPE[^>]*>\s*)?<svg[\s>]/i.test(item.text)) { previewBox.replaceChildren(notice(t('svg_bad'))); return; }
      // As an image, a drawing can't run scripts or load anything from elsewhere
      const url = URL.createObjectURL(new Blob([item.text], {type: 'image/svg+xml'})); objectUrls.push(url);
      const img = el('img', 'cv-drawing'); img.alt = item.title || ''; img.src = url;
      img.addEventListener('error', () => previewBox.replaceChildren(notice(t('svg_bad'))), {once: true});
      previewBox.replaceChildren(img);
      return;
    }
    if (item.lang === 'json') {
      try { const pre = el('pre', 'cv-pre cv-code', JSON.stringify(JSON.parse(item.text), null, 2)); pre.tabIndex = 0; previewBox.replaceChildren(pre); }
      catch (error) { previewBox.replaceChildren(notice(t('json_bad'), String(error.message || '').slice(0, 200))); }
      return;
    }
    if (item.lang === 'csv' || item.lang === 'tsv') { previewBox.replaceChildren(tableOf(item.text, item.lang)); return; }
    if (item.lang === 'markdown') { const page = el('div', 'cv-page'); page.dir = 'auto'; page.append(renderMarkdown(item.text)); previewBox.replaceChildren(page); return; }
    previewBox.replaceChildren(notice(t('no_preview')));
  }
  function parseDelimited(text, delimiter, maxRows) {
    const out = []; let row = [], field = '', quoted = false;
    for (let i = 0; i < text.length && out.length < maxRows; i++) {
      const c = text[i];
      if (quoted) { if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false; } else field += c; }
      else if (c === '"' && field === '') quoted = true;
      else if (c === delimiter) { row.push(field); field = ''; }
      else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(field); out.push(row); row = []; field = ''; }
      else field += c;
    }
    if ((field || row.length) && out.length < maxRows) { row.push(field); out.push(row); }
    return out;
  }
  function tableOf(text, kindOfTable) {
    const firstLine = text.split('\n', 1)[0];
    const delimiter = kindOfTable === 'tsv' ? '\t' : firstLine.split(';').length > firstLine.split(',').length ? ';' : ',';
    const maxRows = 500, maxCols = 50;
    const data = parseDelimited(text, delimiter, maxRows + 1);
    const cols = Math.min(maxCols, Math.max(0, ...data.map(row => row.length)));
    const box = el('div', 'cv-tablebox'); box.tabIndex = 0;
    const table = el('table', 'cv-table');
    data.slice(0, maxRows).forEach((row, r) => {
      const tr = el('tr');
      for (let c = 0; c < cols; c++) { const cell = el(r === 0 ? 'th' : 'td', null, row[c] ?? ''); cell.dir = 'auto'; tr.append(cell); }
      (r === 0 ? (table.tHead || table.createTHead()) : (table.tBodies[0] || table.createTBody())).append(tr);
    });
    box.append(table);
    const wrap = el('div', 'cv-page');
    if (data.length > maxRows || data.some(row => row.length > maxCols)) wrap.append(el('p', 'cv-cut', t('table_cut', {rows: maxRows, cols: maxCols})));
    wrap.append(box);
    return wrap;
  }
  // A small Markdown reader that only ever creates text and a fixed set of elements, so nothing in a document can run
  const INLINE_RE = /(`+)(?!`)([\s\S]*?[^`])\1(?!`)|\*\*([^\n]+?)\*\*|__([^\n]+?)__|~~([^\n]+?)~~|\*([^*\s](?:[^*\n]*?[^*\s])?)\*|(?<![\w])_([^_\s](?:[^_\n]*?[^_\s])?)_(?![\w])|(!?)\[([^\]\n]*)\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))+)>?(?:\s+"[^"]*")?\s*\)|(https?:\/\/[^\s<>]*[^\s<>().,;:!?'"*_])/g;
  function inline(target, text, depth = 0) {
    let last = 0;
    const link = (label, href) => {
      if (!/^(https?:\/\/|mailto:)/i.test(href)) { target.append(label); return; }
      const a = el('a', null, label); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer nofollow'; target.append(a);
    };
    for (const match of text.matchAll(INLINE_RE)) {
      if (match.index > last) target.append(text.slice(last, match.index));
      last = match.index + match[0].length;
      if (match[1]) target.append(el('code', null, match[2].replace(/^ (.*) $/, '$1')));
      else if (match[3] != null || match[4] != null) { const node = el('strong'); depth < 4 ? inline(node, match[3] ?? match[4], depth + 1) : node.append(match[3] ?? match[4]); target.append(node); }
      else if (match[5] != null) { const node = el('del'); depth < 4 ? inline(node, match[5], depth + 1) : node.append(match[5]); target.append(node); }
      else if (match[6] != null || match[7] != null) { const node = el('em'); depth < 4 ? inline(node, match[6] ?? match[7], depth + 1) : node.append(match[6] ?? match[7]); target.append(node); }
      // A picture is named, not loaded: a preview shouldn't fetch from other sites
      else if (match[10] != null) link(match[9] || match[10], match[10]);
      else if (match[11]) link(match[11], match[11]);
    }
    if (last < text.length) target.append(text.slice(last));
  }
  function renderMarkdown(text, depth = 0) {
    const out = document.createDocumentFragment();
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    let para = [];
    const endPara = () => { if (para.length) { const p = el('p'); inline(p, para.join('\n')); out.append(p); para = []; } };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = FENCE_RE.exec(line);
      if (fence && !fence[1].trim() && !(fence[2][0] === '`' && fence[3].includes('`'))) {
        endPara();
        const body = [];
        for (i++; i < lines.length; i++) { const rest = lines[i].trim(); if (rest.length >= fence[2].length && rest === fence[2][0].repeat(rest.length)) break; body.push(lines[i]); }
        const pre = el('pre', 'cv-pre cv-code'); pre.append(el('code', null, body.join('\n'))); pre.tabIndex = 0; out.append(pre);
        continue;
      }
      if (!line.trim()) { endPara(); continue; }
      const heading = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
      if (heading) { endPara(); const node = el('h' + Math.min(6, heading[1].length + 1)); inline(node, heading[2]); out.append(node); continue; }
      if (/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) { endPara(); out.append(el('hr')); continue; }
      if (/^ {0,3}>/.test(line)) {
        endPara();
        const body = [];
        for (; i < lines.length && /^ {0,3}>/.test(lines[i]); i++) body.push(lines[i].replace(/^ {0,3}> ?/, ''));
        i--;
        const quote = el('blockquote'); depth < 4 ? quote.append(renderMarkdown(body.join('\n'), depth + 1)) : quote.append(body.join('\n')); out.append(quote);
        continue;
      }
      if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        endPara();
        const cells = row => row.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
        const box = el('div', 'cv-tablebox'), table = el('table', 'cv-table');
        const headRow = el('tr'); for (const cell of cells(line)) { const th = el('th'); inline(th, cell); headRow.append(th); }
        table.createTHead().append(headRow);
        const body = table.createTBody();
        for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) { const tr = el('tr'); for (const cell of cells(lines[i])) { const td = el('td'); inline(td, cell); tr.append(td); } body.append(tr); }
        i--;
        box.append(table); out.append(box);
        continue;
      }
      const bullet = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/.exec(line);
      if (bullet) {
        endPara();
        const list = el(/\d/.test(bullet[2]) ? 'ol' : 'ul');
        if (list.tagName === 'OL' && parseInt(bullet[2], 10) !== 1) list.start = parseInt(bullet[2], 10);
        const base = bullet[1].length;
        let item = null;
        for (; i < lines.length; i++) {
          const next = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/.exec(lines[i]);
          if (next) {
            item = el('li');
            const level = Math.min(4, Math.max(0, Math.floor((next[1].length - base) / 2)));
            if (level) item.style.marginInlineStart = (level * 1.25) + 'rem';
            const task = /^\[([ xX])\]\s+(.*)$/.exec(next[3]);
            inline(item, task ? (task[1] === ' ' ? '☐ ' : '☑ ') + task[2] : next[3]);
            list.append(item);
          } else if (lines[i].trim() && /^\s+/.test(lines[i]) && item) { item.append(' '); inline(item, lines[i].trim()); }
          else break;
        }
        i--;
        out.append(list);
        continue;
      }
      para.push(line);
    }
    endPara();
    return out;
  }

  /* ---- what changed ---- */
  function compareChoices(item) {
    const choices = [];
    if (!item.local && item.text !== item.original) choices.push(['original', t('original')]);
    for (const other of items) if (other !== item && other.text && kindOf(other) === kindOf(item) && (other.lang === item.lang || kindOf(item) === 'documents')) choices.push([other.key, other.title || t('untitled')]);
    return choices.slice(0, 40);
  }
  // Longest common subsequence over lines, after setting aside what the two share at the start and the end
  function diffLines(before, after) {
    const a = before.split('\n'), b = after.split('\n');
    let start = 0, endA = a.length, endB = b.length;
    while (start < endA && start < endB && a[start] === b[start]) start++;
    while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
    const n = endA - start, m = endB - start;
    if (n * m > DIFF_CELLS) return null;
    const width = m + 1, table = new Uint16Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) table[i * width + j] = a[start + i] === b[start + j] ? table[(i + 1) * width + j + 1] + 1 : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    const ops = [];
    for (let k = 0; k < start; k++) ops.push([' ', a[k]]);
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (a[start + i] === b[start + j]) { ops.push([' ', a[start + i]]); i++; j++; }
      else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) ops.push(['-', a[start + i++]]);
      else ops.push(['+', b[start + j++]]);
    }
    while (i < n) ops.push(['-', a[start + i++]]);
    while (j < m) ops.push(['+', b[start + j++]]);
    for (let k = endA; k < a.length; k++) ops.push([' ', a[k]]);
    return ops;
  }
  function paintDiff() {
    const item = viewItem, choices = compareChoices(item);
    diffBox.replaceChildren();
    if (!choices.length) { diffBox.append(notice(t('nothing_to_compare'))); return; }
    if (!choices.some(([value]) => value === compareWith)) compareWith = choices[0][0];
    const bar = el('div', 'cv-diffbar');
    const label = el('label', 'cv-wrap', t('compare') + ' ');
    const select = el('select', 'cv-select');
    for (const [value, name] of choices) { const option = el('option', null, name); option.value = value; select.append(option); }
    select.value = compareWith;
    select.addEventListener('change', () => { compareWith = select.value; paintDiff(); });
    label.append(select);
    const summary = el('span', 'cv-diffsum');
    bar.append(label, summary);
    diffBox.append(bar);
    const base = compareWith === 'original' ? item.original : (items.find(other => other.key === compareWith)?.text ?? '');
    const ops = diffLines(base, item.text);
    if (!ops) { diffBox.append(notice(t('diff_big'))); return; }
    const added = ops.filter(([sign]) => sign === '+').length, removed = ops.filter(([sign]) => sign === '-').length;
    if (!added && !removed) { diffBox.append(notice(t('no_changes'))); return; }
    summary.textContent = t('diff_sum', {added, removed});
    const view = el('div', 'cv-difflines'); view.tabIndex = 0; view.setAttribute('role', 'group'); view.setAttribute('aria-label', t('mode_changes'));
    const context = 3;
    for (let k = 0; k < ops.length; k++) {
      if (ops[k][0] === ' ') {
        let run = k; while (run < ops.length && ops[run][0] === ' ') run++;
        const length = run - k, head = k === 0 ? 0 : context, tail = run === ops.length ? 0 : context;
        if (length > head + tail + 1) {
          for (let p = k; p < k + head; p++) view.append(diffRow(' ', ops[p][1]));
          view.append(el('div', 'cv-skip', tn('unchanged', length - head - tail)));
          for (let p = run - tail; p < run; p++) view.append(diffRow(' ', ops[p][1]));
          k = run - 1; continue;
        }
      }
      view.append(diffRow(ops[k][0], ops[k][1]));
    }
    diffBox.append(view);
  }
  function diffRow(sign, text) {
    const row = el('div', 'cv-line'); row.dataset.sign = sign === '+' ? 'add' : sign === '-' ? 'del' : 'same';
    const mark = el('span', 'cv-sign', sign === ' ' ? '' : sign);
    if (sign !== ' ') mark.setAttribute('aria-label', t(sign === '+' ? 'added' : 'removed'));
    const body = el('span', 'cv-linetext', text); body.dir = 'auto';
    row.append(mark, body);
    return row;
  }

  /* ---- actions ---- */
  const safeTitle = item => (item.title || t('untitled')).replace(/["«»\n\r]/g, ' ').trim().slice(0, 80);
  function fileNameOf(item) {
    const ext = EXTS[item.lang] || 'txt';
    const title = safeName(item.title || '', 'canvas');
    return /\.[A-Za-z0-9]{1,8}$/.test(title) ? title : (title.replace(/\s+/g, '-').slice(0, 80) || 'canvas') + '.' + ext;
  }
  // Keeps to plain letters: browsers and upload forms drop names they can't use
  const asciiName = name => { const ext = (/\.([A-Za-z0-9]{1,8})$/.exec(name) || [])[1] || 'txt'; const stem = name.replace(/\.[A-Za-z0-9]{1,8}$/, '').normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, ''); return (stem || 'canvas') + '.' + ext; };
  async function writeClipboard(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch {
      const box = el('textarea'); box.value = text; box.style.cssText = 'position:fixed;opacity:0';
      dialog.append(box); box.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch {}
      box.remove(); return ok;
    }
  }
  async function copyAll() { flash(t(await writeClipboard(viewItem.text) ? 'copied' : 'copy_failed')); }
  async function saveToFiles() {
    const item = viewItem;
    if (!item || fileButton.disabled) return;
    fileButton.disabled = true;
    try {
      const name = fileNameOf(item), form = new FormData();
      form.append('file', new File([item.text], name, {type: 'text/plain'}));
      const response = await request('/api/v1/files/?process=false', {method: 'POST', body: form}, 60000);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      flash(t('file_saved', {name}));
    } catch { flash(t('file_failed')); }
    if (viewItem === item) paintButtons();
  }
  async function saveAsNote() {
    const item = viewItem;
    if (!item || noteButton.disabled) return;
    noteButton.disabled = true;
    try {
      const fence = fenceFor(item.text);
      const md = item.lang === 'markdown' || item.lang === 'text' ? item.text : fence + item.lang + '\n' + item.text + '\n' + fence;
      const response = await request('/api/v1/notes/create', {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({title: safeTitle(item), data: {content: {json: null, html: '', md}}, meta: null, access_grants: []})});
      if (response.status === 401 || response.status === 403) flash(t('note_denied'));
      else if (!response.ok) throw new Error('HTTP ' + response.status);
      else flash(t('note_saved'));
    } catch { flash(t('note_failed')); }
    if (viewItem === item) paintButtons();
  }
  const navigate = href => { const link = el('a'); link.href = href; document.body.append(link); link.click(); link.remove(); };
  function showInChat() {
    const item = viewItem;
    if (!item?.chatId || !item.messageId) return;
    flush(); deactivate();
    if (item.chatId === chatIdFromPath() && new URLSearchParams(location.search).get('message') === item.messageId) { document.getElementById('message-' + item.messageId)?.scrollIntoView({block: 'center', behavior: 'smooth'}); return; }
    navigate('/c/' + encodeURIComponent(item.chatId) + '?message=' + encodeURIComponent(item.messageId));
  }
  function confirmAction(title, text, action, danger) {
    return new Promise(done => {
      const heading = el('h3', null, title); heading.id = 'cv-confirm-title';
      const body = el('p', null, text); body.id = 'cv-confirm-text';
      const cancel = textButton(t('cancel'), () => confirmBox.close('cancel'));
      const ok = textButton(action, () => confirmBox.close('ok')); ok.classList.add(danger ? 'cv-danger' : 'cv-primary');
      const actions = el('div', 'cv-actions'); actions.append(cancel, ok);
      confirmBox.replaceChildren(heading, body, actions);
      confirmBox.returnValue = '';
      confirmBox.addEventListener('close', () => done(confirmBox.returnValue === 'ok'), {once: true});
      confirmBox.showModal(); cancel.focus();
    });
  }
  async function resetItem() {
    const item = viewItem;
    if (!item || !isEdited(item) || !(await confirmAction(t('confirm_reset'), t('confirm_reset_text'), t('discard'), true))) return;
    clearTimeout(saveTimer); saveTimer = 0;
    item.text = item.original; persist(item);
    const entry = rows.get(item); if (entry) paintRow(entry);
    if (viewItem === item) refreshEditor(true);
  }
  function removeLocal(item) {
    const data = readStore(item.chatId);
    data.locals = data.locals.filter(each => each.id !== item.key);
    writeStore(item.chatId);
    items = items.filter(each => each !== item);
  }
  async function deleteItem() {
    const item = viewItem;
    if (!item?.local || !(await confirmAction(t('confirm_delete'), t('confirm_delete_text'), t('delete_short'), true))) return;
    clearTimeout(saveTimer); saveTimer = 0;
    item.text = ''; item.title = '';
    closeViewer();
  }

  /* ---- handing work back to the chat ---- */
  const fenceFor = text => '`'.repeat(Math.max(3, ...[...text.matchAll(/`+/g)].map(match => match[0].length + 1)));
  const fenced = (text, language) => { const fence = fenceFor(text); return fence + (language === 'text' ? '' : language) + '\n' + text.replace(/\n$/, '') + '\n' + fence; };
  function composer() {
    const input = document.querySelector('#chat-input');
    if (!input || !isChatRoute()) return null;
    let parent = input.parentElement;
    while (parent && parent !== document.body) {
      const file = parent.querySelector('input[type="file"][multiple]');
      if (file) return file;
      parent = parent.parentElement;
    }
    return null;
  }
  // Open WebUI fills {{...}} in text given to its message box, which would rewrite templates in code; such text travels as a file
  const inlineSafe = text => text.length <= INLINE_LIMIT && !text.includes('{{');
  async function toComposer(item, instruction, selection) {
    if (!item) return;
    flush();
    if (!document.getElementById('chat-input') || !isChatRoute()) { flash(t('no_composer')); return; }
    const title = safeTitle(item), name = asciiName(fileNameOf(item));
    const parts = [];
    let attach = false;
    if (instruction) parts.push(instruction);
    if (inlineSafe(item.text)) {
      if (!instruction) parts.push(t('p_send', {title}), fenced(item.text, item.lang));
      else {
        if (selection) parts.push(t('p_part', {title}), fenced(selection.text, item.lang), t('p_full'));
        else parts.push(t('p_this', {title}));
        parts.push(fenced(item.text, item.lang), t('p_reply'));
      }
    } else {
      attach = true;
      if (!instruction) parts.push(t('p_send', {title}));
      if (selection && inlineSafe(selection.text) && selection.text.length <= 1500) parts.push(t('p_part', {title}), fenced(selection.text, item.lang));
      else if (selection) parts.push(t('p_lines', {from: selection.from, to: selection.to, name}));
      parts.push(t('p_attached', {name}));
      if (instruction) parts.push(t('p_reply'));
    }
    const text = parts.join('\n\n');
    if (attach) {
      const input = composer();
      if (!input) {
        // No way to attach here: the whole request goes to the clipboard instead, for the person to paste
        const whole = [instruction, t('p_this', {title}), fenced(item.text, item.lang), instruction ? t('p_reply') : ''].filter(Boolean).join('\n\n');
        flash(t(await writeClipboard(whole) ? 'copied_paste' : 'copy_failed'));
        return;
      }
      const transfer = new DataTransfer();
      transfer.items.add(new File([item.text], name, {type: 'text/plain'}));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
    }
    window.postMessage({type: 'input:prompt', text}, location.origin);
    deactivate();
    setTimeout(() => document.getElementById('chat-input')?.focus({preventScroll: true}), 50);
    live.textContent = t(attach ? 'in_box_file' : 'in_box');
  }
  function askForChange() {
    const item = viewItem;
    if (!item || askButton.disabled) return;
    let selection = null;
    if (mode === 'edit' && area.selectionEnd > area.selectionStart) {
      const text = area.value.slice(area.selectionStart, area.selectionEnd);
      if (text.trim() && text.length < area.value.length) selection = {text, from: area.value.slice(0, area.selectionStart).split('\n').length, to: area.value.slice(0, area.selectionEnd).replace(/\n$/, '').split('\n').length};
    }
    const heading = el('h3', null, t('ask_title')); heading.id = 'cv-ask-title';
    const about = el('p', 'cv-about', t(selection ? 'ask_part' : 'ask_whole'));
    const form = el('form'); form.method = 'dialog';
    form.append(heading, about);
    if (selection) { const quote = el('pre', 'cv-quote', selection.text.length > 600 ? selection.text.slice(0, 600) + '…' : selection.text); quote.dir = 'auto'; form.append(quote); }
    const label = el('label', 'cv-label', t('ask_label'));
    const input = el('textarea', 'cv-input'); input.rows = 3; input.placeholder = t('ask_placeholder'); input.dir = 'auto'; input.maxLength = 2000;
    label.append(input);
    const message = el('p', 'cv-hint', t('ask_note')); message.setAttribute('aria-live', 'polite');
    const cancel = textButton(t('cancel'), () => askBox.close());
    const go = el('button', 'cv-btn cv-primary', t('ask_go')); go.type = 'submit';
    const actions = el('div', 'cv-actions'); actions.append(cancel, go);
    form.append(label, message, actions);
    input.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); form.requestSubmit(); } });
    form.addEventListener('submit', event => {
      event.preventDefault();
      const instruction = input.value.trim();
      if (!instruction) { message.textContent = t('ask_empty'); message.classList.add('cv-bad'); input.focus(); return; }
      askBox.close();
      toComposer(item, instruction, selection);
    });
    askBox.replaceChildren(form);
    askBox.showModal(); input.focus();
  }
  const safeName = (name, fallback = 'file') => {
    let clean = String(name || '').replace(/[\x00-\x1f\x7f/\\:*?"<>|]/g, '_').replace(/^[\s.]+|[\s.]+$/g, '');
    if (!clean) clean = fallback;
    if (clean.length > 180) { const ext = extOf(clean); clean = clean.slice(0, 170 - ext.length) + (ext ? '.' + ext : ''); }
    return clean;
  };
  function save(blob, name) {
    const link = el('a'); const url = URL.createObjectURL(blob);
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
  }
  const CRC = (() => { const table = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; table[n] = c >>> 0; } return table; })();
  const crc32 = bytes => { let c = 0xFFFFFFFF; for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
  function makeZip(files) {
    const encoder = new TextEncoder(), parts = [], central = [];
    let offset = 0;
    const d = new Date(), date = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(), time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    for (const file of files) {
      const name = encoder.encode(file.name), crc = crc32(file.data), size = file.data.length;
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
      local.setUint16(10, time, true); local.setUint16(12, date, true); local.setUint32(14, crc, true);
      local.setUint32(18, size, true); local.setUint32(22, size, true); local.setUint16(26, name.length, true);
      parts.push(local.buffer, name, file.data);
      const entry = new DataView(new ArrayBuffer(46));
      entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true); entry.setUint16(8, 0x0800, true);
      entry.setUint16(12, time, true); entry.setUint16(14, date, true); entry.setUint32(16, crc, true);
      entry.setUint32(20, size, true); entry.setUint32(24, size, true); entry.setUint16(28, name.length, true); entry.setUint32(42, offset, true);
      central.push(entry.buffer, name);
      offset += 30 + name.length + size;
    }
    const centralSize = central.reduce((sum, part) => sum + part.byteLength, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, centralSize, true); end.setUint32(16, offset, true);
    return new Blob([...parts, ...central, end.buffer], {type: 'application/zip'});
  }
  // Two files may share a name; the second becomes "name (2).ext", as a desktop does
  function uniqueName(name, taken) {
    const ext = extOf(name), stem = ext ? name.slice(0, -(ext.length + 1)) : name;
    let candidate = name, n = 2;
    while (taken.has(candidate.toLocaleLowerCase())) candidate = stem + ' (' + (n++) + ')' + (ext ? '.' + ext : '');
    taken.add(candidate.toLocaleLowerCase());
    return candidate;
  }
  function downloadZip() {
    const list = shown.filter(item => item.text);
    if (!list.length) { flash(t('zip_none')); return; }
    flush();
    const taken = new Set(), encoder = new TextEncoder();
    const files = list.map(item => ({name: uniqueName(fileNameOf(item), taken), data: encoder.encode(item.text)}));
    const title = (document.querySelector('#chat-container nav h1')?.textContent || document.title || '').replace(/\s*[·|•-]\s*Open WebUI\s*$/i, '').trim();
    const ascii = title.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
    save(makeZip(files), 'canvas-' + (ascii || 'chat') + '.zip');
    flash(tn('saved', files.length));
  }
  // While Canvas is in front, a new or growing answer is picked up once it has stopped changing
  let watchSignal = '', watchStable = 0;
  setInterval(() => {
    if (!tabOpen || !active || !dialog || dialog.hidden || document.hidden || chatLoading || !chatIdFromPath()) return;
    const messages = document.querySelectorAll('#messages-container [id^="message-"]');
    const last = messages[messages.length - 1];
    const signal = messages.length + ':' + (last ? last.id + ':' + last.textContent.length : '');
    if (signal !== watchSignal) { watchSignal = signal; watchStable = 0; return; }
    if (watchStable >= 0 && ++watchStable === 2 && Date.now() - chatLoadedAt > 2500) { watchStable = -1; loadChat(true); }
  }, 1500);
  addEventListener('pagehide', flush);
  document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });
  /* ---- the Canvas tab in the Tabs panel ---- */
  // Found by markers that stayed stable from 0.13.0 to 0.13.3: the tab rows (.panel-tab-strip) and the split-pane
  // groups (data-panel-group). Chat Gallery and Chat Files add tabs the same way; this one always sits after theirs.
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
    for (const node of document.querySelectorAll('[data-cv-covered]')) if (!want.has(node)) { node.removeAttribute('data-cv-covered'); if (node.dataset.cvInert) { node.inert = false; delete node.dataset.cvInert; } }
    for (const node of want) if (!node.hasAttribute('data-cv-covered')) { node.setAttribute('data-cv-covered', ''); if (!node.inert) { node.inert = true; node.dataset.cvInert = '1'; } }
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
    for (const list of document.querySelectorAll('.panel-tab-strip')) list.classList.toggle('cv-dim', active && list === found?.list);
    const alone = solo && !found;
    if (dialog) { dialog.classList.toggle('cv-solo', alone); soloClose.hidden = !alone; }
    track();
  }
  function buildTab() {
    strip = el('div', 'flex items-center shrink-0'); strip.id = 'owui-chat-canvas-tabs';
    strip.setAttribute('role', 'tablist'); strip.setAttribute('aria-label', t('canvas'));
    tabEl = el('div');
    tabButton = el('button', 'flex h-full items-center gap-1.5 min-w-0'); tabButton.type = 'button'; tabButton.id = 'owui-chat-canvas-tab';
    tabButton.setAttribute('role', 'tab'); tabButton.setAttribute('aria-controls', 'owui-chat-canvas');
    const icon = svg('canvas'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14'); icon.classList.add('shrink-0');
    tabButton.append(icon, el('span', 'max-w-30 overflow-hidden text-ellipsis', t('canvas')));
    tabButton.addEventListener('click', () => activate());
    const close = el('button', 'flex items-center justify-center w-4 h-4 rounded text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white');
    close.type = 'button'; setLabel(close, t('close_canvas'));
    const x = svg('close'); x.setAttribute('width', '12'); x.setAttribute('height', '12'); close.append(x);
    close.addEventListener('click', closeTab);
    tabEl.append(tabButton, close); strip.append(tabEl);
  }
  const announce = () => dispatchEvent(new CustomEvent('owui-panel-tab-front', {detail: {owner: 'chat-canvas'}}));
  const tellState = () => dispatchEvent(new CustomEvent('owui-chat-canvas-state', {detail: {open: tabOpen}}));
  function activate() {
    if (!tabOpen) return;
    const back = !active;
    // A chat that is still loading rearranges the panel's own tabs; that must not put away a tab the person just chose
    if (back) keepUntil = Math.max(keepUntil, Date.now() + 2000);
    active = true; lastNative = nativeSignature(panel()?.list); announce(); paintTab();
    if (tabEl.isConnected) tabEl.scrollIntoView({block: 'nearest', inline: 'nearest'});
    if (back && chatLoadedAt && !chatLoading && Date.now() - chatLoadedAt > 3000) loadChat(true);
  }
  function deactivate() { if (active) { flush(); active = false; paintTab(); } }
  function openTab() {
    if (dialog && !tabOpen && builtLang !== language()) { dialog.remove(); strip?.remove(); opener?.remove(); headerButton?.remove(); dialog = strip = opener = headerButton = null; rows.clear(); modeTabs = {}; }
    if (!dialog) build();
    if (menuGroup) homeGroup = menuGroup;
    if (!strip) buildTab();
    const fresh = !tabOpen;
    tabOpen = true; solo = !panel();
    if (fresh) { search.value = ''; kind = 'all'; }
    schedule(); activate(); tellState();
    if (fresh) loadChat();
    setTimeout(() => { if (tabOpen && !viewer.classList.contains('cv-open')) (listEl.querySelector('.cv-row') || newButton).focus({preventScroll: true}); });
  }
  function closeTab() {
    collectController?.abort(); closeViewer(true);
    items = []; loadedChat = null; rows.clear(); listEl.replaceChildren(); memory.clear();
    const found = panel(), wasSolo = solo && !found;
    for (const row of document.querySelectorAll('.cv-tabrow')) row.classList.remove('cv-tabrow');
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
      headerButton = iconButton(t('canvas'), 'canvas', () => {
        if (tabOpen && active) { deactivate(); return; }
        if (tabOpen) { solo = !panel(); activate(); return; }
        menuGroup = headerButton.closest('[data-panel-group]')?.dataset.panelGroup ?? null; openTab();
      }, headerClass);
      headerButton.id = 'owui-chat-canvas-header'; headerButton.setAttribute('aria-pressed', 'false');
    }
    const wantedClass = inBar ? barClass : headerClass;
    if (headerButton.className !== wantedClass) headerButton.className = wantedClass;
    // Gallery's button stays nearest the edge, then Files', then this one
    const neighbour = ['owui-chat-files-header', 'owui-chat-gallery-header'].map(id => document.getElementById(id)).find(node => node?.parentElement === host) || null;
    if (inBar) {
      const next = headerButton.nextElementSibling;
      if (headerButton.parentElement !== host || (next && next !== neighbour)) neighbour ? host.insertBefore(headerButton, neighbour) : host.append(headerButton);
    } else {
      const previous = headerButton.previousElementSibling;
      if (headerButton.parentElement !== host || (previous && previous !== neighbour)) neighbour ? neighbour.after(headerButton) : host.prepend(headerButton);
    }
  }
  function sync() {
    syncPending = false;
    const found = panel();
    if (lastPath === null) lastPath = location.pathname;
    if (location.pathname !== lastPath) {
      lastPath = location.pathname; keepUntil = Date.now() + 1500;
      // Edits are kept per chat, so the open editor is put away and the new chat's canvases are read
      if (tabOpen) { closeViewer(true); loadChat(); }
    }
    syncHeader(found);
    const open = found?.lists.find(list => plusOf(list)?.getAttribute('aria-expanded') === 'true');
    const menu = open && [...document.querySelectorAll('body > [role="menu"]')].pop()?.querySelector('.app-dropdown-menu');
    if (menu) {
      menuGroup = open.closest('[data-panel-group]')?.dataset.panelGroup ?? null;
      if (!opener) {
        lang = language();
        opener = el('button'); opener.type = 'button'; opener.id = 'owui-chat-canvas-button';
        const icon = svg('canvas'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14');
        opener.append(icon, t('canvas'));
        opener.addEventListener('click', openTab);
      }
      if (opener.parentElement !== menu) menu.append(opener);
    } else opener?.remove();
    if (!tabOpen || !found) { if (tabOpen) paintTab(); else track(); return; }
    if (found.group && found.group.dataset.panelGroup !== homeGroup) homeGroup = found.group.dataset.panelGroup;
    let anchor = found.list;
    if (anchor.nextElementSibling?.id === 'owui-chat-workspace-tabs') anchor = anchor.nextElementSibling;
    if (anchor.nextElementSibling?.id === 'owui-chat-gallery-tabs') anchor = anchor.nextElementSibling;
    if (anchor.nextElementSibling?.id === 'owui-chat-files-tabs') anchor = anchor.nextElementSibling;
    const moved = strip.previousElementSibling !== anchor;
    if (moved) anchor.after(strip);
    // In a narrow side panel the extra tabs don't fit; the row scrolls sideways instead of sliding under Open WebUI's buttons
    if (!strip.parentElement.classList.contains('cv-tabrow')) strip.parentElement.classList.add('cv-tabrow');
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
    addEventListener('owui-panel-tab-front', event => { if (event.detail?.owner !== 'chat-canvas') deactivate(); });
    addEventListener('owui-chat-canvas-open', () => { menuGroup = null; openTab(); });
    document.documentElement.dataset.chatCanvas = '1';
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
        min_code_lines: int = Field(
            default=3,
            ge=1,
            le=200,
            description="Shortest code block, in lines, that becomes a canvas. 1 lists every block, one-line commands included.",
        )
        min_document_chars: int = Field(
            default=1200,
            ge=0,
            le=100000,
            description="An answer with at least this much text outside code blocks is also offered as a document canvas. 0 turns document canvases off.",
        )
        run_scripts: bool = Field(
            default=True,
            description="Let scripts run in the preview of an HTML canvas. The preview is sandboxed either way and can't reach Open WebUI or the person's session; off also stops the page's own scripts.",
        )
        keep_drafts: bool = Field(
            default=True,
            description="Keep each person's edits in their browser, so they survive a reload. Off keeps edits only until the page is closed; use it on shared computers.",
        )
        allow_save: bool = Field(
            default=True,
            description="Offer Save to my files and Save as a note, which store a canvas in Open WebUI under the person's account.",
        )
        header_button: bool = Field(
            default=True,
            description="Add a Canvas button to the chat's tab row or top bar. Without it the tab opens only from the Tabs panel's + menu, which many user accounts don't have.",
        )
        language: Literal["auto", "en", "fr"] = Field(
            default="auto",
            description="Language of the Canvas tab. 'auto' follows each person's Open WebUI language, and uses English where there is no translation.",
        )

    LOADER_BLOCK_START = "// owui-chat-canvas:start"
    LOADER_BLOCK_END = "// owui-chat-canvas:end"
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
            "minCodeLines": int(valves.min_code_lines),
            "minDocumentChars": int(valves.min_document_chars),
            "runScripts": bool(valves.run_scripts),
            "keepDrafts": bool(valves.keep_drafts),
            "allowSave": bool(valves.allow_save),
            "headerButton": bool(valves.header_button),
            "language": valves.language,
        }

    def _loader_fragment(self) -> str:
        # Runs on the event loop for every page load: rebuild only when a valve changed.
        key = _json.dumps(self._config(), sort_keys=True)
        if key != self._fragment_key:
            script = LOADER_SCRIPT.replace("__CSS__", _json.dumps(CANVAS_CSS)).replace(
                "__CONFIG__", key
            )
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
