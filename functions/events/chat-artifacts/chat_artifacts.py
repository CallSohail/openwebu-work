"""
title: Artifacts
description: Claude-style Artifacts for Open WebUI: documents, slides, spreadsheets, diagrams, web pages, React apps, drawings and data open in a panel beside the chat, with versions, editing, exports (PDF, Word, Excel, PNG) and a sandbox. Pair it with Artifacts mode, the switch that tells the model when and how to make them.
author: Muhammad Sohail
version: 1.4.0
license: MIT
required_open_webui_version: 0.13.0
"""

# Artifacts replaces Chat Canvas. The two protocol blocks below are Classic298's Shared Assets Protocol and its
# live reload add-on (BSD 3-Clause), copied byte for byte.

import json as _json
import re as _re

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
ARTIFACTS_CSS = r"""
:root{--ar-w:640px}
.ar-panel,.ar-cards,#owui-chat-artifacts-header.ar-floating{--ar-surface:#fff;--ar-text:var(--color-gray-800,#262626);--ar-muted:var(--color-gray-600,#5d5d5d);--ar-soft:var(--color-gray-50,#f9f9f9);--ar-hover:var(--color-gray-100,#ececec);--ar-line:var(--color-gray-100,#ececec);--ar-strong-line:var(--color-gray-200,#e3e3e3);--ar-accent:#2563eb;--ar-accent-text:#fff;--ar-primary-bg:var(--color-gray-900,#171717);--ar-primary-fg:#fff;--ar-primary-hover:#000;--ar-second:var(--color-gray-100,#ececec);--ar-second-hover:color-mix(in srgb,var(--color-gray-100,#ececec) 70%,transparent);--ar-modal:#fff;--ar-modal-line:#fff;--ar-ok:#166534;--ar-warn:#92400e;--ar-bad:#b91c1c;--ar-add:#166534;--ar-add-bg:#dcfce7;--ar-del:#991b1b;--ar-del-bg:#fee2e2;--ar-t-document:#2563eb;--ar-t-slides:#c2410c;--ar-t-sheet:#15803d;--ar-t-diagram:#7c3aed;--ar-t-html:#0e7490;--ar-t-react:#0369a1;--ar-t-svg:#be185d;--ar-t-motion:#4338ca;--ar-t-data:#a16207;--ar-t-code:#475569}
.dark .ar-panel,.dark .ar-cards,.dark #owui-chat-artifacts-header.ar-floating{--ar-surface:var(--color-gray-900,#171717);--ar-text:var(--color-gray-100,#ececec);--ar-muted:var(--color-gray-400,#b4b4b4);--ar-soft:var(--color-gray-850,#262626);--ar-hover:var(--color-gray-800,#333);--ar-line:var(--color-gray-800,#333);--ar-strong-line:var(--color-gray-700,#4e4e4e);--ar-accent:#60a5fa;--ar-accent-text:#0b1220;--ar-primary-bg:var(--color-gray-100,#ececec);--ar-primary-fg:var(--color-gray-900,#171717);--ar-primary-hover:#fff;--ar-second:var(--color-gray-850,#262626);--ar-second-hover:color-mix(in srgb,var(--color-gray-850,#262626) 60%,transparent);--ar-modal:var(--color-gray-950,#0d0d0d);--ar-modal-line:var(--color-gray-900,#171717);--ar-ok:#4ade80;--ar-warn:#fbbf24;--ar-bad:#f87171;--ar-add:#86efac;--ar-add-bg:rgb(22 101 52/.35);--ar-del:#fca5a5;--ar-del-bg:rgb(153 27 27/.35);--ar-t-document:#93c5fd;--ar-t-slides:#fdba74;--ar-t-sheet:#86efac;--ar-t-diagram:#c4b5fd;--ar-t-html:#67e8f9;--ar-t-react:#7dd3fc;--ar-t-svg:#f9a8d4;--ar-t-motion:#a5b4fc;--ar-t-data:#fde047;--ar-t-code:#cbd5e1;color-scheme:dark}
.ar-docked{box-sizing:border-box!important;padding-inline-end:var(--ar-w)!important}
.ar-panel{position:fixed;top:0;inset-inline-end:0;height:100vh;height:100dvh;width:var(--ar-w);z-index:40;display:flex;flex-direction:column;box-sizing:border-box;background:var(--ar-surface);color:var(--ar-text);border-inline-start:1px solid var(--ar-line);font-family:inherit;font-size:.875rem;line-height:1.45;container:ar/inline-size}
.ar-panel[hidden]{display:none}
.ar-panel.ar-overlay{top:0!important;height:100dvh!important;width:100%;inset-inline:0;border:0;z-index:55}
.ar-panel.ar-full{z-index:56}
.ar-panel *{box-sizing:border-box}
.ar-panel button,.ar-panel select,.ar-panel input,.ar-panel textarea{font:inherit;color:inherit}
.ar-panel :focus-visible,.ar-cards :focus-visible,.ar-dialog :focus-visible{outline:2px solid var(--ar-text)!important;outline-offset:2px!important}
.ar-resizer{position:absolute;inset-block:0;inset-inline-start:-3px;width:6px;cursor:col-resize;z-index:3}
.ar-resizer:hover,.ar-resizer:focus-visible,.ar-dragging .ar-resizer{background:var(--ar-strong-line)}
.ar-overlay .ar-resizer{display:none}
.ar-dragging,.ar-dragging *{user-select:none}
.ar-dragging .ar-frame{pointer-events:none}
.ar-head{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;padding:.5rem .625rem;border-bottom:1px solid var(--ar-line);min-height:3rem}
.ar-hrow{display:flex;align-items:center;gap:.25rem;width:100%;min-width:0}
.ar-headtools{gap:.375rem;min-height:2rem}
.ar-headtools:not(:has(.ar-versions>*)) .ar-versions{display:none}
.ar-actionsrow{display:flex;align-items:center;gap:.125rem;flex:none}
.ar-headicon{width:1.25rem;height:1.25rem;margin-inline:.25rem;color:var(--ar-muted);flex:none}
.ar-titlebox{display:flex;flex-direction:column;min-width:0;flex:1 1 8rem;padding-inline:.25rem}
.ar-title{margin:0;font-size:.9375rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ar-meta{color:var(--ar-muted);font-size:.75rem}
.ar-spacer{flex:1}
.ar-versions{display:flex;align-items:center;gap:.125rem}
.ar-modes{display:inline-flex;gap:2px;padding:2px;border-radius:.625rem;background:var(--ar-second)}
.ar-mode{height:1.75rem;padding:0 .625rem;border:0;border-radius:.5rem;background:transparent;cursor:pointer;color:var(--ar-muted);font-size:.8125rem}
.ar-mode[aria-selected=true]{background:var(--ar-surface);color:var(--ar-text);box-shadow:0 1px 2px rgb(0 0 0/.1);font-weight:500}
.ar-icon{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;border:0;border-radius:.5rem;background:transparent;cursor:pointer;color:var(--ar-muted)}
.ar-icon:hover{background:var(--ar-hover);color:var(--ar-text)}
.ar-icon:disabled{opacity:.35;cursor:default;background:transparent}
.ar-icon svg{width:1.125rem;height:1.125rem}
.ar-btn{display:inline-flex;align-items:center;justify-content:center;gap:.375rem;min-height:1.875rem;padding:0 .75rem;border:0;border-radius:.5rem;background:var(--ar-second);color:var(--ar-text);cursor:pointer;white-space:nowrap;font-size:.8125rem;font-weight:400;transition:background .1s}
.ar-btn:hover{background:var(--ar-second-hover)}
.ar-btn svg{width:1rem;height:1rem}
.ar-btn[aria-pressed=true]{background:var(--ar-primary-bg);color:var(--ar-primary-fg)}
.ar-primary{background:var(--ar-primary-bg);color:var(--ar-primary-fg)}
.ar-primary:hover{background:var(--ar-primary-hover)}
.ar-danger{background:#dc2626;color:#fff}
.ar-danger:hover{background:#b91c1c}
.ar-danger-text{color:var(--ar-bad)!important}
.ar-chip{min-height:1.75rem;padding:0 .625rem;font-size:.75rem}
.ar-select{height:1.875rem;max-width:12rem;padding:0 .375rem;border:0;border-radius:.5rem;background:var(--ar-second);color:var(--ar-text);cursor:pointer;font-size:.8125rem}
.ar-select option{background:var(--ar-surface);color:var(--ar-text)}
.ar-vselect{max-width:8.5rem}
.ar-body{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;display:flex;flex-direction:column}
.ar-view{flex:1;min-height:0;display:flex;flex-direction:column}
.ar-view>*{flex:1;min-height:0}
.ar-foot{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem .75rem;padding:.5rem .75rem;border-top:1px solid var(--ar-line);color:var(--ar-muted);font-size:.75rem}
.ar-foot[hidden]{display:none}
.ar-footinfo{flex:1 1 12rem}
.ar-badge{display:inline-flex;align-items:center;padding:.0625rem .5rem;border-radius:999px;font-size:.6875rem;font-weight:600;color:var(--ar-warn);background:color-mix(in srgb,var(--ar-warn) 14%,transparent);white-space:nowrap}
.ar-draft{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;width:100%;color:var(--ar-text)}
.ar-draft>span{flex:1 1 12rem}
.ar-error{display:flex;align-items:flex-start;gap:.5rem;width:100%;padding:.5rem .625rem;border-radius:.625rem;color:var(--ar-bad);background:color-mix(in srgb,var(--ar-bad) 10%,transparent)}
.ar-error>svg{width:1.125rem;height:1.125rem;flex:none;margin-top:.125rem}
.ar-errortext{flex:1;min-width:0;display:flex;flex-direction:column;gap:.125rem}
.ar-errortext code{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.75rem;color:var(--ar-text)}
.ar-alert{margin:0 0 .5rem;padding:.5rem .75rem;border-radius:.625rem;color:var(--ar-warn);background:color-mix(in srgb,var(--ar-warn) 12%,transparent);font-size:.8125rem}
.ar-muted{color:var(--ar-muted)}
.ar-small{font-size:.75rem;margin:.25rem 0}
.ar-pad{padding:1rem}
.ar-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.ar-notice{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.5rem;flex:1;min-height:14rem;padding:2rem;color:var(--ar-muted);text-align:center}
.ar-notice strong{color:var(--ar-text);font-weight:600;font-size:.9375rem}
.ar-notice .ar-btn{margin-top:.5rem;color:var(--ar-text)}
.ar-search{margin:.75rem .75rem .25rem;height:2.125rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--ar-second);color:var(--ar-text);font-size:.8125rem;outline:none}
.ar-filter{margin:0;height:2rem;min-width:7rem;flex:0 1 11rem}
.ar-list{list-style:none;margin:0;padding:.25rem .5rem 1rem;display:flex;flex-direction:column;gap:.125rem}
.ar-item,.ar-newitem,.ar-card{display:flex;align-items:center;gap:.75rem;width:100%;padding:.5rem;border:0;border-radius:.75rem;background:transparent;cursor:pointer;text-align:start;color:inherit}
.ar-item:hover,.ar-newitem:hover{background:var(--ar-hover)}
.ar-itemicon{display:inline-flex;align-items:center;justify-content:center;width:2.25rem;height:2.25rem;flex:none;border-radius:.625rem;color:var(--ar-tone,var(--ar-t-code));background:color-mix(in srgb,var(--ar-tone,var(--ar-t-code)) 14%,transparent)}
.ar-itemicon svg{width:1.25rem;height:1.25rem}
[data-type=document]{--ar-tone:var(--ar-t-document)}[data-type=slides]{--ar-tone:var(--ar-t-slides)}[data-type=motion]{--ar-tone:var(--ar-t-motion)}[data-type=sheet]{--ar-tone:var(--ar-t-sheet)}[data-type=diagram]{--ar-tone:var(--ar-t-diagram)}[data-type=html]{--ar-tone:var(--ar-t-html)}[data-type=react]{--ar-tone:var(--ar-t-react)}[data-type=svg]{--ar-tone:var(--ar-t-svg)}[data-type=data]{--ar-tone:var(--ar-t-data)}[data-type=code]{--ar-tone:var(--ar-t-code)}
.ar-itemtext{display:flex;flex-direction:column;min-width:0;flex:1;gap:.0625rem}
.ar-itemtitle{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ar-text)}
.ar-itemmeta{color:var(--ar-muted);font-size:.75rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ar-shared{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem;margin:.75rem .75rem 0;padding:.5rem .75rem;border-radius:.75rem;background:var(--ar-soft);box-shadow:inset 0 0 0 1px var(--ar-line);color:var(--ar-muted);font-size:.8125rem}
.ar-subbar{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;padding:.5rem .75rem;border-bottom:1px solid var(--ar-line);flex:none!important}
.ar-framebox{display:flex;flex-direction:column;min-height:0}
.ar-framestage{flex:1;min-height:0;display:flex;justify-content:center;background:var(--ar-soft);overflow:auto}
.ar-framestage[data-device=tablet]>.ar-frame{width:768px;max-width:100%;box-shadow:0 0 0 1px var(--ar-strong-line);margin:.75rem auto}
.ar-framestage[data-device=phone]>.ar-frame{width:390px;max-width:100%;box-shadow:0 0 0 1px var(--ar-strong-line);margin:.75rem auto;border-radius:1rem}
.ar-frame{display:block;width:100%;height:100%;min-height:20rem;border:0;background:#fff;color-scheme:light}
.ar-view[data-mode=preview]>.ar-framebox:only-child .ar-framestage[data-device=desktop]{background:#fff}
.ar-svgbox{display:flex;align-items:center;justify-content:center;padding:1rem;overflow:auto;background:repeating-conic-gradient(var(--ar-soft) 0 25%,transparent 0 50%) 0 0/20px 20px}
.ar-svgbox img{max-width:100%;max-height:100%;height:auto}
.ar-docwrap{display:flex;gap:1.5rem;padding:1.25rem 1.5rem 2rem;overflow:auto}
.ar-doc{flex:1;min-width:0;max-width:46rem;margin:0 auto;font-size:.9375rem;line-height:1.65;overflow-wrap:anywhere}
.ar-doc h1{font-size:1.75rem;line-height:1.2;margin:0 0 .75rem;font-weight:700}
.ar-doc h2{font-size:1.3rem;margin:1.5rem 0 .5rem;font-weight:650}
.ar-doc h3{font-size:1.1rem;margin:1.25rem 0 .375rem;font-weight:600}
.ar-doc h4,.ar-doc h5,.ar-doc h6{font-size:1rem;margin:1rem 0 .25rem}
.ar-doc p{margin:.5rem 0}
.ar-doc ul,.ar-doc ol{padding-inline-start:1.5rem;margin:.5rem 0}
.ar-doc li{margin:.15rem 0}
.ar-task{list-style:none;margin-inline-start:-1.25rem}
.ar-doc blockquote{margin:.75rem 0;padding:.25rem 1rem;border-inline-start:3px solid var(--ar-strong-line);color:var(--ar-muted)}
.ar-doc hr{border:0;border-top:1px solid var(--ar-line);margin:1.5rem 0}
.ar-doc a{color:var(--ar-accent);text-decoration:underline}
.ar-doc code,.ar-slide code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85em;padding:.1em .3em;border-radius:.3em;background:var(--ar-soft)}
.ar-pre{margin:.75rem 0;padding:.75rem;border-radius:.625rem;background:var(--ar-soft);box-shadow:inset 0 0 0 1px var(--ar-line);overflow:auto;font:.8125rem/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre}
.ar-pre code{background:none;padding:0}
.ar-img{color:var(--ar-muted);font-style:italic}
.ar-pic{max-width:100%;height:auto;border-radius:.375rem}
mark{background:#fde68a;color:#111;padding:0 .15em;border-radius:.2em}
.ar-callout{margin:.75rem 0;padding:.6rem .9rem;border-radius:.625rem;border-inline-start:4px solid #2563eb;background:color-mix(in srgb,#2563eb 9%,transparent)}
.ar-callout[data-kind=tip]{border-color:#16a34a;background:color-mix(in srgb,#16a34a 9%,transparent)}
.ar-callout[data-kind=important]{border-color:#7c3aed;background:color-mix(in srgb,#7c3aed 9%,transparent)}
.ar-callout[data-kind=warning]{border-color:#d97706;background:color-mix(in srgb,#d97706 11%,transparent)}
.ar-callout-title{display:block;font-weight:700;margin-bottom:.15rem}
.ar-callout>p{margin:.2rem 0}
.ar-figure{margin:.75rem 0;text-align:center}
.ar-figure .ar-chart{margin:0 auto}
.ar-diagram{background:#fff;border-radius:.625rem;padding:.5rem;box-shadow:inset 0 0 0 1px var(--ar-line)}
.ar-diagram[data-state=loading]{min-height:4rem;display:flex;align-items:center;justify-content:center}
.ar-diagram .ar-muted{color:#5d5d5d}
.ar-cols{display:grid;grid-template-columns:repeat(var(--ar-cols,2),minmax(0,1fr));gap:1.5em;align-items:start}
.ar-hiddenframe{position:fixed;left:-10000px;top:0;width:900px;height:700px;opacity:0;pointer-events:none;border:0}
.ar-tablebox{overflow:auto;margin:.75rem 0;max-width:100%}
.ar-table{border-collapse:collapse;font-size:.8125rem;min-width:50%}
.ar-table th,.ar-table td{border:1px solid var(--ar-strong-line);padding:.3rem .55rem;text-align:start;vertical-align:top}
.ar-table th{background:var(--ar-soft);font-weight:600}
.ar-outline{position:sticky;top:0;align-self:flex-start;width:12rem;flex:none;display:none;flex-direction:column;gap:.125rem;font-size:.8125rem}
.ar-outline-title{margin:0 0 .25rem;font-size:.6875rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--ar-muted)}
.ar-outline-link{border:0;background:none;text-align:start;padding:.2rem .4rem;border-radius:.375rem;cursor:pointer;color:var(--ar-muted)}
.ar-outline-link:hover{background:var(--ar-hover);color:var(--ar-text)}
.ar-outline-link[data-level="2"]{padding-inline-start:1rem}.ar-outline-link[data-level="3"]{padding-inline-start:1.6rem}
@container ar (min-width:820px){.ar-outline{display:flex}}
.ar-deck{display:flex;flex-direction:column;min-height:0}
.ar-stage{flex:none!important;margin:1rem auto .5rem;width:min(100% - 2rem,64rem);aspect-ratio:16/9;outline:none;border-radius:.75rem;box-shadow:0 0 0 1px var(--ar-strong-line),0 8px 24px -12px rgb(0 0 0/.35);overflow:hidden;background:#fff}
.ar-panel .ar-stage:focus-visible{outline:2px solid var(--ar-text)!important;outline-offset:3px!important}
.ar-stage:fullscreen{width:100vw;height:100vh;aspect-ratio:auto;border-radius:0;display:flex;align-items:center;justify-content:center;background:#000;box-shadow:none}
.ar-stage:fullscreen .ar-slide{width:min(100vw,177.78vh);height:min(56.25vw,100vh)}
.ar-slide{width:100%;height:100%;container-type:inline-size;background:#fff;color:#111;overflow:hidden;color-scheme:light}
.ar-slide-inner{height:100%;padding:6cqw 8cqw;font-size:2.4cqw;line-height:1.45;display:flex;flex-direction:column;justify-content:center;overflow:hidden}
.ar-slide h1{font-size:2.4em;line-height:1.1;margin:0 0 .4em;font-weight:700}
.ar-slide h2{font-size:1.8em;line-height:1.15;margin:0 0 .4em;font-weight:650}
.ar-slide h3{font-size:1.3em;margin:.2em 0 .3em}
.ar-slide p{margin:.3em 0}
.ar-slide ul,.ar-slide ol{margin:.2em 0;padding-inline-start:1.3em}
.ar-slide li{margin:.25em 0}
.ar-slide .ar-pre{font-size:.75em;background:#f3f3f3;box-shadow:none}
.ar-slide .ar-table{font-size:.8em}
.ar-slide .ar-table th,.ar-slide .ar-table td{border-color:#ccc}
.ar-slide .ar-table th{background:#f3f3f3}
.ar-slide a{color:#1d4ed8}
.ar-slide-title .ar-slide-inner,.ar-slide-center .ar-slide-inner{align-items:center;text-align:center}
.ar-slide-section{background:#1e293b;color:#fff}
.ar-slide-section .ar-slide-inner{justify-content:flex-end}
.ar-slide-visual .ar-slide-inner{justify-content:flex-start}
.ar-slide .ar-figure{flex:1;min-height:0;margin:.3em 0;display:flex;align-items:center;justify-content:center}
.ar-slide .ar-figure img,.ar-slide .ar-figure svg{max-height:100%;max-width:100%}
.ar-slide .ar-diagram{box-shadow:none;padding:0}
.ar-slide .ar-callout{font-size:.9em}
.ar-slide .ar-chart text{font-size:17px}
.ar-slide .ar-chart .ar-caption{font-size:19px}
.ar-slide-title h1{font-size:3.2em}
.ar-notes{flex:none!important;margin:0 auto .5rem;width:min(100% - 2rem,64rem);padding:.625rem .875rem;border-radius:.625rem;background:var(--ar-soft);white-space:pre-wrap;font-size:.8125rem}
.ar-notes[hidden]{display:none}
.ar-counter{font-variant-numeric:tabular-nums;color:var(--ar-muted);min-width:3.5rem;text-align:center}
.ar-thumbs{flex:none!important;display:flex;gap:.5rem;overflow-x:auto;padding:.5rem 1rem 1rem}
.ar-thumb{flex:none;width:8.5rem;aspect-ratio:16/9;padding:0;border:0;border-radius:.375rem;overflow:hidden;cursor:pointer;box-shadow:0 0 0 1px var(--ar-strong-line);background:#fff}
.ar-thumb[aria-current=true]{box-shadow:0 0 0 2px var(--ar-text)}
.ar-slide-small{pointer-events:none}
.ar-sheet{display:flex;flex-direction:column;min-height:0}
.ar-ref{min-width:3rem;font:600 .75rem ui-monospace,Menlo,monospace;color:var(--ar-muted);text-align:center}
.ar-formula{flex:1 1 10rem;height:2rem;padding:0 .625rem;border:0;border-radius:.625rem;background:var(--ar-soft);box-shadow:inset 0 0 0 1px var(--ar-line);font:.8125rem ui-monospace,Menlo,monospace!important}
.ar-sheetbody{flex:1;min-height:0;overflow:auto;padding:0 .75rem}
.ar-gridbox{margin:.5rem 0;max-height:none}
.ar-grid{font-size:.8125rem;font-variant-numeric:tabular-nums}
.ar-grid thead th{position:sticky;top:0;z-index:1;background:var(--ar-soft)}
.ar-grid td{cursor:cell;max-width:22rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ar-grid{user-select:none}
.ar-grid td.ar-sel{background:color-mix(in srgb,#16a34a 12%,transparent)}
.ar-grid td.ar-cur{outline:2px solid #16a34a;outline-offset:-2px}
.ar-grid td.ar-editing{padding:0;position:relative}
.ar-celledit{width:100%;min-width:6rem;height:100%;min-height:1.75rem;padding:.25rem .5rem;border:0;outline:2px solid #16a34a;outline-offset:-2px;background:var(--ar-surface);color:var(--ar-text);font:inherit;font-variant-numeric:tabular-nums}
.ar-panel .ar-gridbox:focus,.ar-panel .ar-gridbox:focus-visible{outline:none!important;box-shadow:none}
.ar-sheetbar2{border-bottom:1px solid var(--ar-line);padding-top:.375rem}
.ar-sheetbar2[hidden]{display:none}
.ar-sheettools{display:flex;align-items:center;gap:.125rem;flex-wrap:wrap}
.ar-tool{width:1.875rem;height:1.875rem}
.ar-statref{font:600 .75rem ui-monospace,Menlo,monospace;color:var(--ar-text)}
.ar-slideedit{flex:none!important;margin:0 auto .5rem;width:min(100% - 2rem,64rem)}
.ar-slideedit[hidden]{display:none}
.ar-slidearea{font:.8125rem/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace!important}
.ar-grid .ar-headrow td{font-weight:600;background:var(--ar-soft)}
.ar-grid .ar-num{text-align:end}
.ar-grid .ar-fx{color:inherit}
.ar-grid .ar-errcell{color:var(--ar-bad)}
.ar-rownum,.ar-corner{position:sticky;inset-inline-start:0;background:var(--ar-soft);color:var(--ar-muted);font-weight:500!important;text-align:end!important;min-width:2.5rem}
.ar-sortbtn{border:0;background:none;cursor:pointer;font-weight:600;color:var(--ar-muted);width:100%;text-align:center}
.ar-sheetfoot{display:flex;flex-wrap:wrap;gap:.25rem 1rem;padding:.375rem .75rem;border-top:1px solid var(--ar-line);font-size:.75rem;color:var(--ar-muted);flex:none!important;min-height:2rem}
.ar-stat strong{color:var(--ar-text);font-variant-numeric:tabular-nums}
.ar-chartbar{display:flex;gap:.375rem;margin:.75rem 0 .25rem}
.ar-chart{width:100%;max-width:56rem;height:auto;display:block;margin:0 auto 1rem}
.ar-gridline{stroke:var(--ar-line)}
.ar-axis{fill:var(--ar-muted);font-size:11px}
.ar-caption{fill:var(--ar-text);font-size:13px;font-weight:600}
.ar-data{display:flex;flex-direction:column;min-height:0}
.ar-tree{flex:1;overflow:auto;padding:.75rem 1rem 1.5rem;font:.8125rem/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.ar-tree details>summary{cursor:pointer;border-radius:.25rem;padding-inline:.25rem}
.ar-tree details>summary:hover{background:var(--ar-hover)}
.ar-children{padding-inline-start:1.25rem;border-inline-start:1px dashed var(--ar-line);margin-inline-start:.35rem}
.ar-leaf{padding-inline:.25rem;overflow-wrap:anywhere}
.ar-key{color:var(--ar-t-diagram)}
.ar-type{color:var(--ar-muted)}
.ar-string{color:var(--ar-t-sheet)}.ar-number{color:var(--ar-t-react)}.ar-boolean,.ar-null{color:var(--ar-t-slides)}
.ar-editor{position:relative;display:flex;min-height:0;overflow:hidden;background:var(--ar-surface)}
.ar-gutter{flex:none;min-width:3rem;padding:.75rem .5rem .75rem .25rem;text-align:end;color:var(--ar-muted);font:.8125rem/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;user-select:none;background:var(--ar-soft);border-inline-end:1px solid var(--ar-line);overflow:hidden}
.ar-area{flex:1;min-width:0;resize:none;border:0;outline:none;padding:.75rem;background:transparent;font:.8125rem/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace!important;white-space:pre;overflow:auto;tab-size:2}
.ar-wrapped .ar-area{white-space:pre-wrap}
.ar-wrapped .ar-gutter{display:none}
.ar-area[readonly]{cursor:text}
.ar-livearea{margin:0;flex:1;border-radius:0;box-shadow:none;white-space:pre-wrap}
.ar-diff{display:flex;flex-direction:column;padding:.5rem .75rem 1rem;min-height:0}
.ar-difflines{overflow:auto;border-radius:.625rem;box-shadow:inset 0 0 0 1px var(--ar-line);font:.8125rem/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.ar-dline{display:flex;white-space:pre-wrap;overflow-wrap:anywhere}
.ar-dsign{flex:none;width:1.5rem;text-align:center;user-select:none;color:var(--ar-muted)}
.ar-dline[data-sign=add]{background:var(--ar-add-bg);color:var(--ar-add)}
.ar-dline[data-sign=add] .ar-dsign{color:var(--ar-add)}
.ar-dline[data-sign=del]{background:var(--ar-del-bg);color:var(--ar-del)}
.ar-dline[data-sign=del] .ar-dsign{color:var(--ar-del)}
.ar-dtext{flex:1;padding-inline-end:.5rem}
.ar-dskip{padding:.125rem 1.5rem;color:var(--ar-muted);background:var(--ar-soft);font-style:italic}
.ar-menu{position:absolute;z-index:20;min-width:13rem;max-width:min(20rem,90%);padding:.25rem;border-radius:1rem;background:var(--ar-surface);border:1px solid var(--ar-line);box-shadow:0 10px 30px -10px rgb(0 0 0/.25);display:flex;flex-direction:column}
.ar-menuitem{display:flex;align-items:center;min-height:1.875rem;padding:0 .625rem;border:0;border-radius:.75rem;background:transparent;text-align:start;cursor:pointer;font-size:.8125rem;color:var(--ar-text)}
.ar-menuitem:hover,.ar-menuitem:focus-visible{background:var(--ar-soft);outline:none!important}
.ar-menuitem:disabled{opacity:.4}
.ar-menusep{height:1px;margin:.25rem .375rem;background:var(--ar-line)}
.ar-dialog{position:fixed;inset:0;margin:auto;height:fit-content;max-height:calc(100dvh - 2rem);overflow:auto;width:min(32rem,calc(100vw - 1rem));padding:1.25rem;border:1px solid var(--ar-modal-line);border-radius:1.5rem;background:var(--ar-modal);color:var(--ar-text);box-shadow:0 25px 50px -12px rgb(0 0 0/.35)}
.ar-dialog[open]{animation:ar-pop .1s ease-out}
@keyframes ar-pop{from{transform:scale(.985);opacity:0}to{transform:none;opacity:1}}
.ar-dialog::backdrop{background:rgb(0 0 0/.6)}
.ar-dialog h3{margin:0 0 .625rem;font-size:1rem;font-weight:500}
.ar-dialog p{margin:0 0 .75rem;color:var(--ar-muted);font-size:.875rem}
.ar-newdialog{width:min(40rem,calc(100vw - 2rem))}
.ar-newgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:.25rem;margin:.5rem 0 1rem}
.ar-newitem{box-shadow:none;background:transparent}
.ar-newitem:hover,.ar-newitem:focus-visible{background:var(--ar-soft)}
.ar-actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:.5rem;margin-top:.75rem}
.ar-label{display:flex;flex-direction:column;gap:.25rem;margin:.5rem 0;font-weight:500}
.ar-input{width:100%;min-height:2.25rem;padding:.5rem .75rem;border:0;border-radius:.5rem;background:var(--ar-second);color:var(--ar-text);resize:vertical;font-size:.875rem;outline:none}
.ar-linkrow{display:flex;gap:.375rem;margin:.5rem 0}
.ar-linkrow[hidden]{display:none}
.ar-quote{max-height:9rem;overflow:auto;margin:0 0 .75rem;padding:.5rem .625rem;border-radius:.5rem;background:var(--ar-soft);font:.75rem/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap}
.ar-toast{position:absolute;left:50%;transform:translateX(-50%);bottom:3.25rem;z-index:30;max-width:min(22rem,calc(100% - 2rem));padding:.3rem .75rem;border-radius:999px;background:var(--ar-text);color:var(--ar-surface);font-size:.75rem;line-height:1.3;box-shadow:0 4px 12px -4px rgb(0 0 0/.35);text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.ar-toast[hidden]{display:none}
.ar-askbtn{position:absolute;z-index:25;min-height:1.625rem;padding:0 .5rem;gap:.25rem;font-size:.75rem;border-radius:999px;box-shadow:0 3px 10px -3px rgb(0 0 0/.35)}
.ar-askbtn svg{width:.8125rem;height:.8125rem}
.ar-printframe{position:fixed;inset-inline-end:0;bottom:0;width:0;height:0;border:0}
.ar-cards{display:flex;flex-direction:column;gap:.375rem;margin:.75rem 0 .25rem;max-width:34rem}
.ar-card{display:flex;align-items:center;gap:.25rem;padding:.25rem;border-radius:.875rem;background:var(--ar-surface);box-shadow:inset 0 0 0 1px var(--ar-strong-line);color:var(--ar-text)}
.ar-cardmain{display:flex;align-items:center;gap:.75rem;flex:1;min-width:0;padding:.375rem .5rem;border:0;border-radius:.625rem;background:transparent;cursor:pointer;text-align:start;color:inherit;font:inherit}
.ar-cardmain:hover{background:var(--ar-soft)}
.ar-cardcode{width:1.875rem;height:1.875rem;color:var(--ar-muted)}
.ar-cardcode[aria-pressed=true]{background:var(--ar-soft);color:var(--ar-text)}
.ar-card-writing{padding:.625rem .75rem;gap:.75rem}
.ar-spinner{flex:none;width:1rem;height:1rem;border-radius:999px;border:2px solid var(--ar-strong-line);border-top-color:var(--ar-text);animation:ar-spin .8s linear infinite}
@keyframes ar-spin{to{transform:rotate(360deg)}}
.ar-cardopen{flex:none;font-size:.75rem;font-weight:500;color:var(--ar-text);padding:.25rem .625rem;border-radius:.5rem;background:var(--ar-second)}
.ar-openbtn svg{width:.875rem;height:.875rem}
html.ar-replace #chat-artifacts-button{display:none!important}
.ar-collapsed:not(.ar-shown)>:not(:first-child){display:none!important}
.ar-headerbtn svg{width:1.25rem;height:1.25rem}
.ar-headerbtn[aria-pressed=true]{background:var(--color-gray-100,#ececec)}
.dark .ar-headerbtn[aria-pressed=true]{background:var(--color-gray-800,#333)}
#owui-chat-artifacts-header.ar-floating{position:fixed;inset-inline-end:1rem;bottom:1rem;z-index:39;display:inline-flex;align-items:center;justify-content:center;width:3rem;height:3rem;border:0;border-radius:999px;background:var(--ar-surface);color:var(--ar-text);box-shadow:0 0 0 1px var(--ar-strong-line),0 8px 20px -6px rgb(0 0 0/.35);cursor:pointer}
#owui-chat-artifacts-header.ar-floating svg{width:1.375rem;height:1.375rem}
.ar-flash{animation:ar-flash 1.6s ease-out}
@keyframes ar-flash{0%,40%{background:color-mix(in srgb,#2563eb 14%,transparent)}100%{background:transparent}}
@container ar (max-width:560px){.ar-head{gap:.25rem}.ar-actionsrow .ar-icon{width:1.875rem}.ar-docwrap{padding:1rem}.ar-filter{flex-basis:100%;order:9}}
@media (prefers-reduced-motion:no-preference){.ar-panel:not([hidden]){animation:ar-in .16s ease-out}@keyframes ar-in{from{opacity:.4;transform:translateX(12px)}to{opacity:1;transform:none}}.ar-docked{transition:padding .16s ease-out}}
@media (forced-colors:active){.ar-btn,.ar-select,.ar-input,.ar-card,.ar-mode{border:1px solid CanvasText}.ar-mode[aria-selected=true]{outline:2px solid Highlight}}
@media print{.ar-panel,.ar-cards,#owui-chat-artifacts-header{display:none!important}.ar-docked{padding:0!important}}
.ar-motion{display:flex;flex-direction:column;min-height:0}
.ar-motionstage{flex:none;position:relative;margin:1rem auto .25rem;width:min(100% - 2rem,64rem);max-height:62vh;border-radius:.75rem;overflow:hidden;cursor:pointer;outline:none;box-shadow:0 0 0 1px var(--ar-strong-line),0 8px 24px -12px rgb(0 0 0/.25);background:#000}
.ar-motionstage:focus-visible{outline:2px solid var(--ar-text)!important;outline-offset:3px!important}
.ar-motionstage:fullscreen{width:100vw;max-height:none;border-radius:0;display:flex;align-items:center;justify-content:center}
.ar-motioncanvas{display:block;width:100%;height:100%;object-fit:contain}
.ar-motionseekrow{flex:none;display:flex;padding:0 1rem;margin:0 auto;width:min(100% - 2rem,64rem)}
.ar-motionseek{flex:1;accent-color:var(--ar-text);margin:.25rem 0}
.ar-motionthumbs .ar-thumb{background:transparent}
.ar-motionthumbs .ar-thumb canvas{display:block;width:100%;height:100%}
.ar-motionfields{display:flex;flex-wrap:wrap;gap:.75rem;margin-bottom:.5rem}
.ar-inline{flex-direction:row!important;align-items:center;gap:.5rem}
.ar-motionlen{width:6rem}
.ar-motionwarn{flex:none;margin:.25rem 1rem .75rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--ar-soft);color:var(--ar-muted);font-size:.8125rem;list-style:none}
.ar-motionwarn[hidden]{display:none}
.ar-progressdialog .ar-progress{width:100%;height:.5rem;accent-color:var(--ar-text);margin:.25rem 0 .5rem}
"""

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiChatArtifacts) return;
  window.__owuiChatArtifacts = true;
  const CONFIG = __CONFIG__;
  const CSS = __CSS__;

  /* ---- words, in the language Open WebUI is set to ---- */
  const STRINGS = {
    en: {
      artifacts: 'Artifacts', artifact: 'Artifact', open_artifacts: 'Open Artifacts', close: 'Close', back: 'All artifacts', open: 'Open',
      empty_title: 'No artifacts in this chat yet', empty_hint: 'Ask for a document, slides, a spreadsheet, a diagram, a web page or an app, and it opens here.',
      no_chat: 'Open a chat to see its artifacts.', loading: 'Reading the chat…', failed: 'Couldn’t read this chat.', retry: 'Try again', denied: 'You don’t have access to this chat.',
      search: 'Search artifacts', no_match: 'No artifact matches.', new: 'New', new_title: 'Start an artifact', new_hint: 'Pick what to make. A ready request goes into the message box; describe what you want and send it.', blank: 'Write it yourself', blank_hint: 'An empty artifact only you can see, on this device.',
      t_document: 'Document', t_slides: 'Slides', t_sheet: 'Spreadsheet', t_diagram: 'Diagram', t_html: 'Web page', t_react: 'App', t_svg: 'Drawing', t_data: 'Data', t_code: 'Code', t_design: 'Design',
      d_document: 'A report, letter, guide or plan', d_slides: 'A presentation, one slide at a time', d_sheet: 'A table you can sort, filter, chart and calculate in', d_diagram: 'A flowchart, timeline, mind map or sequence', d_html: 'A page or interactive tool', d_react: 'An interactive app with React', d_svg: 'An illustration, icon or chart', d_data: 'Structured data in JSON', d_design: 'A screen or interface mock-up', d_code: 'A program or script',
      preview: 'Preview', code: 'Code', changes: 'Changes', version: 'Version', version_n: 'Version {n} of {total}', latest: 'latest', v_draft: 'Your edit', v_local: 'Only on this device', prev_version: 'Previous version', next_version: 'Next version',
      from_answer: 'From answer {n}', writing: 'Being written…', unfinished: 'This version was cut off before it finished.', edited_by_you: 'Your edit, not in the chat yet.', send_edit: 'Send my edit to the chat', discard_edit: 'Discard my edit', discard_confirm: 'Discard your edit?', discard_text: 'Your changes to this version are deleted from this device.', discard: 'Discard', cancel: 'Cancel', edits_kept: 'Edits are kept on this device.', edits_session: 'Edits last until you close this page.', edit_too_big: 'Too large to keep: this edit lasts until you close this page.',
      copy: 'Copy', copied: 'Copied', copy_failed: 'Couldn’t copy. Select the text and copy it.', download: 'Download', fullscreen: 'Full screen', exit_fullscreen: 'Exit full screen', more: 'More', reload: 'Reload', print: 'Print or save as PDF', save_files: 'Save to my files', save_note: 'Save as a note', saved_file: 'Saved to your files as {name}', saved_note: 'Saved as a note', save_failed: 'Couldn’t save. Try again.', note_denied: 'You aren’t allowed to create notes.', show_in_chat: 'Show in chat', delete: 'Delete', delete_confirm: 'Delete this artifact?', delete_text: 'It only exists on this device, so it can’t be brought back.',
      dl_as: 'Download as {format}',
      desktop: 'Desktop', tablet: 'Tablet', phone: 'Phone', width: 'Width',
      ask: 'Ask about this', ask_edit: 'Change this', ask_placeholder: 'What should change?', ask_send: 'Send', ask_hint: 'Sends to the chat with the selected part.', ask_busy: 'Wait for the current answer to finish.', no_composer: 'There’s no message box here.',
      error: 'This artifact hit an error', try_fix: 'Try fixing', fix_sent: 'Asked the model to fix it.', lib_failed: 'Couldn’t load {what} from {host}. Your network or administrator may block it; the code is still here.',
      ai_title: 'Let this artifact use AI?', ai_text: '“{title}” wants to send prompts to {model} on your account. It can’t see this chat; it only gets the answers to what it asks.', ai_allow: 'Allow until I reload', ai_deny: 'Don’t allow', ai_limit: 'Too many AI requests; wait a minute.', ai_off: 'AI isn’t available to artifacts here.', ai_no_model: 'No model to answer.',
     
     
      lines: '{n} lines', words: '{n} words', rows_cols: '{rows} rows × {cols} columns', slides_n: '{n} slides', items_n: '{n} items', chars: '{n} characters', read_min: '{n} min read',
      outline: 'Outline', slide: 'Slide {n} of {total}', prev_slide: 'Previous slide', next_slide: 'Next slide', present: 'Present', notes: 'Speaker notes', no_notes: 'No notes for this slide.',
      filter_rows: 'Filter rows', sort_asc: 'Sort ascending', sort_desc: 'Sort descending', add_row: 'Insert a row below', add_col: 'Insert a column', chart: 'Chart', table: 'Table', chart_bar: 'Bars', chart_line: 'Line', chart_of: 'Chart {col} by {by}', no_numbers: 'This table has no column of numbers to chart.', sum: 'Sum', avg: 'Average', min: 'Min', max: 'Max', count: 'Count', cell: 'Cell {ref}', formula_hint: 'Start with = for a formula: =SUM(B2:B9), =AVERAGE(C:C), =B2*1.2', rows_shown: 'Showing {n} of {total} rows',
      tree: 'Tree', as_table: 'As a table', expand: 'Expand all', collapse: 'Collapse all', bad_json: 'This isn’t valid JSON: {error}', json_items: '{n} items', json_keys: '{n} keys',
      wrap: 'Wrap lines', diagram_failed: 'This diagram has an error', no_changes: 'No changes from the previous version.', compare_with: 'Compared with {what}', added: 'added', removed: 'removed', diff_sum: '{added} added, {removed} removed', diff_big: 'These versions are too different to compare line by line.', unchanged_n: '{n} unchanged lines', previous: 'the previous version',
      untitled: 'Untitled', in_message: 'In this answer', versions_n: '{n} versions', auto_opened: 'Opened {title}',
      p_fix: 'The artifact “{title}” shows this error:\n\n{error}\n\nPlease fix it and send the whole corrected artifact.', p_edit: 'I edited the artifact “{title}”. Here is my version; please continue from it.', p_part: 'In the artifact “{title}”, about this part:', p_whole: 'About the artifact “{title}”:', p_reply: 'Reply with the whole updated artifact in one code block.',
      tp_document: 'Write a document about: \n\nPut the whole document in one code block opened with ```markdown artifact and starting with a # title. Use headings, short paragraphs, tables, ==highlights== and callouts (> [!TIP]), and add a ```mermaid diagram or a ```chart where they help.',
      tp_slides: 'Make a slide deck about: \n\nPut it in one code block opened with ```slides artifact. Separate slides with a line containing only ---. Start each slide with a # heading and keep bullets short. Make the first slide a title slide, ==highlights== for key words, a ```chart or a ```mermaid diagram where data or a process helps, ||| to split a slide into two columns, and speaker notes after a line starting with Note:.',
      tp_sheet: 'Make a spreadsheet of: \n\nPut it in one code block opened with ```csv artifact, with a header row. Use formulas such as =SUM(B2:B10) where totals help.',
      tp_diagram: 'Draw a diagram of: \n\nPut it in one ```mermaid code block (flowchart, sequence, timeline, mind map or Gantt), starting with a line %% artifact: Title.',
      tp_html: 'Build a web page that: \n\nPut the whole page in one code block opened with ```html artifact, with its CSS and JavaScript inside and a <title>.',
      tp_react: 'Build an interactive app that: \n\nWrite one React component in one ```jsx code block with a default export. You can use Tailwind classes, lucide-react icons, recharts and shadcn/ui components from @/components/ui/*. Start with // artifact: Title.',
      tp_svg: 'Draw an SVG of: \n\nPut it in one code block opened with ```svg artifact, with a viewBox and a <title>.',
      tp_data: 'Create data for: \n\nPut it in one code block opened with ```json artifact.',
      tp_design: 'Design a screen for: \n\nPut the whole design in one code block opened with ```html artifact, with a clean layout, realistic content and a <title>.',
      tp_code: 'Write a program that: \n\nPut the whole program in one code block opened with ``` followed by the language and artifact, for example ```python artifact, and give its file name in a first-line comment.',
      all_columns: 'All columns', chart_area: 'Area', chart_pie: 'Pie', callout_note: 'Note', callout_tip: 'Tip', callout_important: 'Important', callout_warning: 'Warning', drawing: 'Drawing the diagram…', preparing: 'Preparing the download…', show_code: 'Show the code in the chat', hide_code: 'Hide the code in the chat',
      del_row: 'Delete the selected rows', del_col: 'Delete the selected columns', undo: 'Undo', redo: 'Redo', sheet_keys: 'Spreadsheet. Arrow keys move, Shift selects, typing or Enter edits, Delete clears, Ctrl+Z undoes.', edit_slide: 'Edit this slide', add_slide: 'Add a slide after this one', del_slide: 'Delete this slide', save_slide: 'Save the slide', slide_saved: 'Slide saved as your edit', t_motion: 'Motion', d_motion: 'A short animated explainer you can download as a video',
      tp_motion: 'Make a short animated explainer (about 30 seconds) about: \n\nPut it in one code block opened with ```motion artifact. Start with settings: a size (16:9, 9:16 or 1:1), a style (minimal, bold, corporate, neon, paper, editorial, playful or chalk), "progress: on" and "captions: karaoke". Open each scene with a line like "--- 4s fade" (or slide, zoom, iris, flash, rise). Use # headings with kinetic type such as {words} or {letters}, "number: 12% | label", "chart: bar" or "chart: race" with CSV lines, "icons: book Library, users Clubs", "ring: 72% | label", "timeline: …", "compare: Before | After", "quote: … | who", a pattern, particles or a camera move where they help, and "say: …" for each caption. One idea per scene.',
      m_play: 'Play', m_pause: 'Pause', m_seek: 'Position in the video', m_scene: 'Scene {n} of {total}', scenes_n: '{n} scenes', m_full: 'Full screen',
      m_edit: 'Edit this scene', m_add: 'Add a scene after this one', m_del: 'Delete this scene', m_save: 'Save the scene', m_saved: 'Scene saved as your edit',
      m_duration: 'Length in seconds', m_auto: 'auto', m_transition: 'Transition', tr_fade: 'Fade', tr_slide: 'Slide', tr_zoom: 'Zoom', tr_wipe: 'Wipe', tr_cut: 'Cut',
      m_video: 'Video, {q} (.mp4)', m_frame: 'This frame (.png)', m_scenes: 'Each scene as a picture (.zip)', m_source: 'Motion file (.txt)',
      m_making: 'Making the video… {p}%', m_made: 'Video saved', m_webm: 'This browser can only make WebM videos, so the video is a .webm file', m_realtime: 'Recording in real time. Keep this tab open until it finishes.',
      m_failed: 'Couldn’t make the video', m_cancelled: 'Video cancelled', m_busy: 'A video is already being made', m_none: 'No scenes yet', m_problems: 'What was adjusted',
      mw_line: 'Line {line}: {what}', mw_duration: '“{value}” isn’t a usable length, so {n} s is used', mw_transition: '“{value}” isn’t a transition, so it fades', mw_effect: '“{value}” isn’t an effect',
      mw_chart: 'the chart has no numbers to draw', mw_chart_type: '“{value}” isn’t a chart type, so bars are drawn', mw_scenes: 'only the first {n} scenes are shown', mw_total: 'the video stops at {n} seconds',
      mw_items: 'only the first {n} are shown', mw_size: '“{value}” isn’t a size, so 16:9 is used', mw_color: '“{value}” isn’t a colour', mw_image: 'a picture from another site isn’t shown', mw_long: 'the line is cut at {n} characters',
      mw_value: '“{value}” isn’t a {what} it knows', mw_music: 'the music couldn’t be loaded', mw_music_site: 'music from another site isn’t played; add a file instead', mw_voice: 'narration isn’t available: text-to-speech isn’t set up or isn’t allowed for you',
      m_mute: 'Mute', m_unmute: 'Unmute', m_music: 'Add music from your device', m_music_remove: 'Remove the music', m_music_adding: 'Adding the music…', m_music_big: 'Music files can be up to 25 MB', m_music_failed: 'Couldn’t add the music',
      m_voice_on: 'Narrate the captions', m_voice_off: 'Stop narrating', m_voice_wait: 'Preparing the narration…', exporting: 'Making the presentation…',
      blank_type: 'Kind', blank_name: 'Title', blank_create: 'Create', blank_default: 'My artifact'
    },
    fr: {
      artifacts: 'Artefacts', artifact: 'Artefact', open_artifacts: 'Ouvrir les artefacts', close: 'Fermer', back: 'Tous les artefacts', open: 'Ouvrir',
      empty_title: 'Aucun artefact dans cette conversation', empty_hint: 'Demandez un document, des diapositives, un tableur, un diagramme, une page web ou une application : il s’ouvre ici.',
      no_chat: 'Ouvrez une conversation pour voir ses artefacts.', loading: 'Lecture de la conversation…', failed: 'Impossible de lire cette conversation.', retry: 'Réessayer', denied: 'Vous n’avez pas accès à cette conversation.',
      search: 'Rechercher', no_match: 'Aucun artefact ne correspond.', new: 'Nouveau', new_title: 'Créer un artefact', new_hint: 'Choisissez quoi créer. Une demande prête est placée dans la zone de message ; décrivez ce que vous voulez et envoyez-la.', blank: 'L’écrire vous-même', blank_hint: 'Un artefact vide, visible par vous seul, sur cet appareil.',
      t_document: 'Document', t_slides: 'Diapositives', t_sheet: 'Tableur', t_diagram: 'Diagramme', t_html: 'Page web', t_react: 'Application', t_svg: 'Dessin', t_data: 'Données', t_code: 'Code', t_design: 'Maquette',
      d_document: 'Un rapport, une lettre, un guide ou un plan', d_slides: 'Une présentation, diapositive par diapositive', d_sheet: 'Un tableau à trier, filtrer, représenter et calculer', d_diagram: 'Un organigramme, une frise, une carte mentale ou une séquence', d_html: 'Une page ou un outil interactif', d_react: 'Une application interactive en React', d_svg: 'Une illustration, une icône ou un graphique', d_data: 'Des données structurées en JSON', d_design: 'Une maquette d’écran ou d’interface', d_code: 'Un programme ou un script',
      preview: 'Aperçu', code: 'Code', changes: 'Modifications', version: 'Version', version_n: 'Version {n} sur {total}', latest: 'la plus récente', v_draft: 'Votre modification', v_local: 'Seulement sur cet appareil', prev_version: 'Version précédente', next_version: 'Version suivante',
      from_answer: 'Réponse {n}', writing: 'En cours d’écriture…', unfinished: 'Cette version a été interrompue avant la fin.', edited_by_you: 'Votre modification, pas encore dans la conversation.', send_edit: 'Envoyer ma modification', discard_edit: 'Annuler ma modification', discard_confirm: 'Annuler votre modification ?', discard_text: 'Vos changements sur cette version sont supprimés de cet appareil.', discard: 'Supprimer', cancel: 'Annuler', edits_kept: 'Les modifications sont gardées sur cet appareil.', edits_session: 'Les modifications durent jusqu’à la fermeture de la page.', edit_too_big: 'Trop volumineux pour être gardé : cette modification dure jusqu’à la fermeture de la page.',
      copy: 'Copier', copied: 'Copié', copy_failed: 'Impossible de copier. Sélectionnez le texte et copiez-le.', download: 'Télécharger', fullscreen: 'Plein écran', exit_fullscreen: 'Quitter le plein écran', more: 'Plus', reload: 'Recharger', print: 'Imprimer ou enregistrer en PDF', save_files: 'Enregistrer dans mes fichiers', save_note: 'Enregistrer comme note', saved_file: 'Enregistré dans vos fichiers sous {name}', saved_note: 'Enregistré comme note', save_failed: 'Échec de l’enregistrement. Réessayez.', note_denied: 'Vous n’êtes pas autorisé à créer des notes.', show_in_chat: 'Voir dans la conversation', delete: 'Supprimer', delete_confirm: 'Supprimer cet artefact ?', delete_text: 'Il n’existe que sur cet appareil : il ne pourra pas être récupéré.',
      dl_as: 'Télécharger en {format}',
      desktop: 'Ordinateur', tablet: 'Tablette', phone: 'Téléphone', width: 'Largeur',
      ask: 'Poser une question', ask_edit: 'Modifier ceci', ask_placeholder: 'Que faut-il changer ?', ask_send: 'Envoyer', ask_hint: 'Envoie à la conversation avec la partie sélectionnée.', ask_busy: 'Attendez la fin de la réponse en cours.', no_composer: 'Il n’y a pas de zone de message ici.',
      error: 'Cet artefact a rencontré une erreur', try_fix: 'Essayer de corriger', fix_sent: 'Correction demandée au modèle.', lib_failed: 'Impossible de charger {what} depuis {host}. Votre réseau ou votre administrateur le bloque peut-être ; le code reste disponible.',
      ai_title: 'Autoriser cet artefact à utiliser l’IA ?', ai_text: '« {title} » veut envoyer des requêtes à {model} avec votre compte. Il ne voit pas cette conversation ; il reçoit seulement les réponses à ses questions.', ai_allow: 'Autoriser jusqu’au rechargement', ai_deny: 'Refuser', ai_limit: 'Trop de requêtes IA ; patientez une minute.', ai_off: 'L’IA n’est pas disponible pour les artefacts ici.', ai_no_model: 'Aucun modèle pour répondre.',
     
     
      lines: '{n} lignes', words: '{n} mots', rows_cols: '{rows} lignes × {cols} colonnes', slides_n: '{n} diapositives', items_n: '{n} éléments', chars: '{n} caractères', read_min: '{n} min de lecture',
      outline: 'Plan', slide: 'Diapositive {n} sur {total}', prev_slide: 'Diapositive précédente', next_slide: 'Diapositive suivante', present: 'Présenter', notes: 'Notes de l’orateur', no_notes: 'Pas de notes pour cette diapositive.',
      filter_rows: 'Filtrer les lignes', sort_asc: 'Trier par ordre croissant', sort_desc: 'Trier par ordre décroissant', add_row: 'Insérer une ligne dessous', add_col: 'Insérer une colonne', chart: 'Graphique', table: 'Tableau', chart_bar: 'Barres', chart_line: 'Courbe', chart_of: '{col} selon {by}', no_numbers: 'Ce tableau n’a pas de colonne de nombres à représenter.', sum: 'Somme', avg: 'Moyenne', min: 'Min', max: 'Max', count: 'Nombre', cell: 'Cellule {ref}', formula_hint: 'Commencez par = pour une formule : =SUM(B2:B9), =AVERAGE(C:C), =B2*1,2', rows_shown: '{n} lignes affichées sur {total}',
      tree: 'Arborescence', as_table: 'En tableau', expand: 'Tout déplier', collapse: 'Tout replier', bad_json: 'Ce JSON n’est pas valide : {error}', json_items: '{n} éléments', json_keys: '{n} clés',
      wrap: 'Retour à la ligne', diagram_failed: 'Ce diagramme contient une erreur', no_changes: 'Aucun changement par rapport à la version précédente.', compare_with: 'Comparé à {what}', added: 'ajoutée', removed: 'supprimée', diff_sum: '{added} ajoutées, {removed} supprimées', diff_big: 'Ces versions sont trop différentes pour être comparées ligne à ligne.', unchanged_n: '{n} lignes inchangées', previous: 'la version précédente',
      untitled: 'Sans titre', in_message: 'Dans cette réponse', versions_n: '{n} versions', auto_opened: '{title} ouvert',
      p_fix: 'L’artefact « {title} » affiche cette erreur :\n\n{error}\n\nCorrigez-la et renvoyez l’artefact complet corrigé.', p_edit: 'J’ai modifié l’artefact « {title} ». Voici ma version ; continuez à partir d’elle.', p_part: 'Dans l’artefact « {title} », à propos de cette partie :', p_whole: 'À propos de l’artefact « {title} » :', p_reply: 'Répondez avec l’artefact complet mis à jour, dans un seul bloc de code.',
      tp_document: 'Rédige un document sur : \n\nMets tout le document dans un seul bloc ouvert par ```markdown artifact et commençant par un titre #. Utilise des titres, des paragraphes courts, des tableaux, des ==surlignages== et des encadrés (> [!TIP]), et ajoute un diagramme ```mermaid ou un ```chart quand c’est utile.',
      tp_slides: 'Crée une présentation sur : \n\nMets-la dans un seul bloc ouvert par ```slides artifact. Sépare les diapositives par une ligne contenant seulement ---. Commence chaque diapositive par un titre # et garde des puces courtes. Fais de la première une diapositive de titre, des ==surlignages== pour les mots clés, un ```chart ou un diagramme ```mermaid quand des données ou un processus aident, ||| pour couper une diapositive en deux colonnes, et des notes de l’orateur après une ligne commençant par Note:.',
      tp_sheet: 'Crée un tableur de : \n\nMets-le dans un seul bloc ouvert par ```csv artifact, avec une ligne d’en-tête. Utilise des formules comme =SUM(B2:B10) pour les totaux.',
      tp_diagram: 'Dessine un diagramme de : \n\nMets-le dans un seul bloc ```mermaid (organigramme, séquence, frise, carte mentale ou Gantt), commençant par une ligne %% artifact: Titre.',
      tp_html: 'Construis une page web qui : \n\nMets toute la page dans un seul bloc ouvert par ```html artifact, avec son CSS et son JavaScript et un <title>.',
      tp_react: 'Construis une application interactive qui : \n\nÉcris un composant React dans un seul bloc ```jsx avec un export par défaut. Tu peux utiliser les classes Tailwind, les icônes lucide-react, recharts et les composants shadcn/ui de @/components/ui/*. Commence par // artifact: Titre.',
      tp_svg: 'Dessine en SVG : \n\nMets-le dans un seul bloc ouvert par ```svg artifact, avec un viewBox et un <title>.',
      tp_data: 'Crée des données pour : \n\nMets-les dans un seul bloc ouvert par ```json artifact.',
      tp_design: 'Conçois un écran pour : \n\nMets toute la maquette dans un seul bloc ouvert par ```html artifact, avec une mise en page soignée, un contenu réaliste et un <title>.',
      tp_code: 'Écris un programme qui : \n\nMets tout le programme dans un seul bloc ouvert par ``` suivi du langage et de artifact, par exemple ```python artifact, avec son nom de fichier dans un commentaire en première ligne.',
      all_columns: 'Toutes les colonnes', chart_area: 'Aires', chart_pie: 'Secteurs', callout_note: 'Remarque', callout_tip: 'Astuce', callout_important: 'Important', callout_warning: 'Attention', drawing: 'Dessin du diagramme…', preparing: 'Préparation du téléchargement…', show_code: 'Afficher le code dans la conversation', hide_code: 'Masquer le code dans la conversation',
      del_row: 'Supprimer les lignes sélectionnées', del_col: 'Supprimer les colonnes sélectionnées', undo: 'Annuler', redo: 'Rétablir', sheet_keys: 'Tableur. Les flèches déplacent, Maj sélectionne, la saisie ou Entrée modifie, Suppr efface, Ctrl+Z annule.', edit_slide: 'Modifier cette diapositive', add_slide: 'Ajouter une diapositive après celle-ci', del_slide: 'Supprimer cette diapositive', save_slide: 'Enregistrer la diapositive', slide_saved: 'Diapositive enregistrée comme votre modification', t_motion: 'Animation', d_motion: 'Une courte vidéo explicative à télécharger',
      tp_motion: 'Crée une courte animation explicative (environ 30 secondes) sur : \n\nMets-la dans un seul bloc ouvert par ```motion artifact. Commence par des réglages : un format (16:9, 9:16 ou 1:1), un style (minimal, bold, corporate, neon, paper, editorial, playful ou chalk), « progress: on » et « captions: karaoke ». Ouvre chaque scène par une ligne comme « --- 4s fade » (ou slide, zoom, iris, flash, rise). Utilise des titres # avec un texte animé comme {words} ou {letters}, « number: 12% | légende », « chart: bar » ou « chart: race » suivi de lignes CSV, « icons: book Bibliothèque, users Clubs », « ring: 72% | légende », « timeline: … », « compare: Avant | Après », « quote: … | auteur », un motif, des particules ou un mouvement de caméra quand ils aident, et « say: … » pour chaque sous-titre. Une idée par scène.',
      m_play: 'Lire', m_pause: 'Pause', m_seek: 'Position dans la vidéo', m_scene: 'Scène {n} sur {total}', scenes_n: '{n} scènes', m_full: 'Plein écran',
      m_edit: 'Modifier cette scène', m_add: 'Ajouter une scène après celle-ci', m_del: 'Supprimer cette scène', m_save: 'Enregistrer la scène', m_saved: 'Scène enregistrée comme votre modification',
      m_duration: 'Durée en secondes', m_auto: 'auto', m_transition: 'Transition', tr_fade: 'Fondu', tr_slide: 'Glissement', tr_zoom: 'Zoom', tr_wipe: 'Volet', tr_cut: 'Coupe',
      m_video: 'Vidéo, {q} (.mp4)', m_frame: 'Cette image (.png)', m_scenes: 'Chaque scène en image (.zip)', m_source: 'Fichier d’animation (.txt)',
      m_making: 'Création de la vidéo… {p} %', m_made: 'Vidéo enregistrée', m_webm: 'Ce navigateur ne sait créer que des vidéos WebM : la vidéo est un fichier .webm', m_realtime: 'Enregistrement en temps réel. Gardez cet onglet ouvert jusqu’à la fin.',
      m_failed: 'Impossible de créer la vidéo', m_cancelled: 'Vidéo annulée', m_busy: 'Une vidéo est déjà en cours de création', m_none: 'Pas encore de scène', m_problems: 'Ce qui a été ajusté',
      mw_line: 'Ligne {line} : {what}', mw_duration: '« {value} » n’est pas une durée utilisable : {n} s est utilisé', mw_transition: '« {value} » n’est pas une transition : fondu', mw_effect: '« {value} » n’est pas un effet',
      mw_chart: 'le graphique n’a pas de nombres à tracer', mw_chart_type: '« {value} » n’est pas un type de graphique : barres', mw_scenes: 'seules les {n} premières scènes sont montrées', mw_total: 'la vidéo s’arrête à {n} secondes',
      mw_items: 'seuls les {n} premiers sont montrés', mw_size: '« {value} » n’est pas un format : 16:9 est utilisé', mw_color: '« {value} » n’est pas une couleur', mw_image: 'une image d’un autre site n’est pas montrée', mw_long: 'la ligne est coupée à {n} caractères',
      mw_value: '« {value} » n’est pas une valeur connue ({what})', mw_music: 'la musique n’a pas pu être chargée', mw_music_site: 'la musique d’un autre site n’est pas jouée ; ajoutez plutôt un fichier', mw_voice: 'la narration n’est pas disponible : la synthèse vocale n’est pas configurée ou pas autorisée pour vous',
      m_mute: 'Couper le son', m_unmute: 'Rétablir le son', m_music: 'Ajouter une musique depuis votre appareil', m_music_remove: 'Retirer la musique', m_music_adding: 'Ajout de la musique…', m_music_big: 'Les fichiers de musique peuvent faire jusqu’à 25 Mo', m_music_failed: 'Impossible d’ajouter la musique',
      m_voice_on: 'Lire les sous-titres à voix haute', m_voice_off: 'Arrêter la narration', m_voice_wait: 'Préparation de la narration…', exporting: 'Création de la présentation…',
      blank_type: 'Type', blank_name: 'Titre', blank_create: 'Créer', blank_default: 'Mon artefact'
    }
  };
  const language = () => {
    if (STRINGS[CONFIG.language]) return CONFIG.language;
    // Open WebUI sets <html lang> whenever the language changes, and keeps the choice in localStorage
    let locale = document.documentElement.getAttribute('lang') || '';
    if (!locale) { try { locale = localStorage.getItem('locale') || ''; } catch {} }
    locale = String(locale || navigator.language || 'en').toLowerCase();
    return STRINGS[locale.slice(0, 2)] ? locale.slice(0, 2) : 'en';
  };
  let lang = language();
  let appName = String(CONFIG.platformName || '').trim() || 'Open WebUI';
  const t = (key, vars) => {
    const text = String(STRINGS[lang][key] ?? STRINGS.en[key] ?? key).replace(/\{app\}/g, appName);
    return vars ? text.replace(/\{(\w+)\}/g, (whole, name) => name in vars ? vars[name] : whole) : text;
  };
  const n = value => Math.round(value).toLocaleString(lang);

  /* ---- small helpers ---- */
  const ICONS = {
    artifacts: 'M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23-.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5',
    close: 'M6 18 18 6M6 6l12 12', back: 'M15.75 19.5 8.25 12l7.5-7.5', left: 'M15.75 19.5 8.25 12l7.5-7.5', right: 'm8.25 4.5 7.5 7.5-7.5 7.5',
    copy: 'M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 0 1 1.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 0 0-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 0 1-1.125-1.125v-9.25m12 6.625v-1.875a3.375 3.375 0 0 0-3.375-3.375h-1.5a1.125 1.125 0 0 1-1.125-1.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H9.75',
    download: 'M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3',
    share: 'M7.217 10.907a2.25 2.25 0 1 0 0 2.186m0-2.186c.18.324.283.696.283 1.093s-.103.77-.283 1.093m0-2.186 9.566-5.314m-9.566 7.5 9.566 5.314m0 0a2.25 2.25 0 1 0 3.935 2.186 2.25 2.25 0 0 0-3.935-2.186Zm0-12.814a2.25 2.25 0 1 0 3.933-2.185 2.25 2.25 0 0 0-3.933 2.185Z',
    expand: 'M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15',
    more: 'M6.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM12.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM18.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Z',
    plus: 'M12 4.5v15m7.5-7.5h-15', refresh: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99',
    document: 'M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z',
    slides: 'M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6',
    sheet: 'M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h7.5c.621 0 1.125-.504 1.125-1.125m-9.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 3.75h-7.5A1.125 1.125 0 0 1 12 18.375m9.75-12.75c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125m19.5 0v1.5c0 .621-.504 1.125-1.125 1.125M2.25 5.625v1.5c0 .621.504 1.125 1.125 1.125m0 0h17.25m-17.25 0h7.5c.621 0 1.125.504 1.125 1.125M3.375 8.25c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125m17.25-3.75h-7.5c-.621 0-1.125.504-1.125 1.125m8.625-1.125c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h7.5m-7.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125M12 10.875v-1.5m0 1.5c0 .621-.504 1.125-1.125 1.125M12 10.875c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125M13.125 12h7.5m-7.5 0c-.621 0-1.125.504-1.125 1.125M20.625 12c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h7.5M12 14.625v-1.5m0 1.5c0 .621-.504 1.125-1.125 1.125M12 14.625c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125m0 1.5v-1.5m0 0c0-.621.504-1.125 1.125-1.125m0 0h7.5',
    diagram: 'M7.5 14.25v2.25m3-4.5v4.5m3-6.75v6.75m3-9v9M6 20.25h12A2.25 2.25 0 0 0 20.25 18V6A2.25 2.25 0 0 0 18 3.75H6A2.25 2.25 0 0 0 3.75 6v12A2.25 2.25 0 0 0 6 20.25Z',
    html: 'M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418',
    react: 'm3.75 13.5 10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75Z',
    svg: 'M9.53 16.122a3 3 0 0 0-5.78 1.128 2.25 2.25 0 0 1-2.4 2.245 4.5 4.5 0 0 0 8.4-2.245c0-.399-.078-.78-.22-1.128Zm0 0a15.998 15.998 0 0 0 3.388-1.62m-5.043-.025a15.994 15.994 0 0 1 1.622-3.395m3.42 3.42a15.995 15.995 0 0 0 4.764-4.648l3.876-5.814a1.151 1.151 0 0 0-1.597-1.597L14.146 6.32a15.996 15.996 0 0 0-4.649 4.763m3.42 3.42a6.776 6.776 0 0 0-3.42-3.42',
    data: 'M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125',
    code: 'M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5',
    design: 'M9.53 16.122a3 3 0 0 0-5.78 1.128 2.25 2.25 0 0 1-2.4 2.245 4.5 4.5 0 0 0 8.4-2.245c0-.399-.078-.78-.22-1.128Zm0 0a15.998 15.998 0 0 0 3.388-1.62m-5.043-.025a15.994 15.994 0 0 1 1.622-3.395m3.42 3.42a15.995 15.995 0 0 0 4.764-4.648l3.876-5.814a1.151 1.151 0 0 0-1.597-1.597L14.146 6.32a15.996 15.996 0 0 0-4.649 4.763m3.42 3.42a6.776 6.776 0 0 0-3.42-3.42',
    pencil: 'm16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125',
    sparkle: 'M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z',
    pause: 'M15.75 5.25v13.5m-7.5-13.5v13.5',
    sound: 'M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z',
    mute: 'M17.25 9.75 19.5 12m0 0 2.25 2.25M19.5 12l2.25-2.25M19.5 12l-2.25 2.25m-10.5-6 4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z',
    music: 'm9 9 10.5-3m0 6.553v3.75a2.25 2.25 0 0 1-1.632 2.163l-1.32.377a1.803 1.803 0 1 1-.99-3.467l2.31-.66a2.25 2.25 0 0 0 1.632-2.163Zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 0 1-1.632 2.163l-1.32.377a1.803 1.803 0 0 1-.99-3.467l2.31-.66A2.25 2.25 0 0 0 9 15.553Z',
    voice: 'M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z',
    motion: 'M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h1.5C5.496 19.5 6 18.996 6 18.375m-3.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 3.75h-1.5A1.125 1.125 0 0 1 18 18.375M20.625 4.5H3.375m17.25 0c.621 0 1.125.504 1.125 1.125M20.625 4.5h-1.5C18.504 4.5 18 5.004 18 5.625m3.75 0v1.5c0 .621-.504 1.125-1.125 1.125M3.375 4.5c-.621 0-1.125.504-1.125 1.125M3.375 4.5h1.5C5.496 4.5 6 5.004 6 5.625m-3.75 0v1.5c0 .621.504 1.125 1.125 1.125m0 0h1.5m-1.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125m1.5-3.75C5.496 8.25 6 7.746 6 7.125v-1.5M4.875 8.25C5.496 8.25 6 8.754 6 9.375v1.5m0-5.25v5.25m0-5.25C6 5.004 6.504 4.5 7.125 4.5h9.75c.621 0 1.125.504 1.125 1.125m1.125 2.625h1.5m-1.5 0A1.125 1.125 0 0 1 18 7.125v-1.5m1.125 2.625c-.621 0-1.125.504-1.125 1.125v1.5m2.625-2.625c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125M18 5.625v5.25M7.125 12h9.75m-9.75 0A1.125 1.125 0 0 1 6 10.875M7.125 12C6.504 12 6 12.504 6 13.125m0-2.25C6 11.496 5.496 12 4.875 12M18 10.875c0 .621-.504 1.125-1.125 1.125M18 10.875c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125m-12 5.25v-5.25m0 5.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125m-12 0v-1.5c0-.621-.504-1.125-1.125-1.125M18 18.375v-5.25m0 5.25v-1.5c0-.621.504-1.125 1.125-1.125M18 13.125v1.5c0 .621.504 1.125 1.125 1.125M18 13.125c0-.621.504-1.125 1.125-1.125M6 13.125v1.5c0 .621-.504 1.125-1.125 1.125M6 13.125C6 12.504 5.496 12 4.875 12m-1.5 0h1.5m-1.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125M19.125 12h1.5m0 0c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h1.5m14.25 0h1.5',
    play: 'M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z',
    warn: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
    undo: 'M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3',
    redo: 'm15 15 6-6m0 0-6-6m6 6H9a6 6 0 0 0 0 12h3',
    row_add: 'M3.75 6.75h16.5M3.75 12h16.5M12 15.75v6m3-3H9',
    row_del: 'M3.75 6.75h16.5M3.75 12h16.5M9 18.75h6',
    col_add: 'M6.75 3.75v16.5M12 3.75v16.5M15.75 12h6m-3-3v6',
    col_del: 'M6.75 3.75v16.5M12 3.75v16.5M15.75 12h6',
    chart: 'M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z'
  };
  const svgIcon = name => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false'})) node.setAttribute(key, value);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', ICONS[name] || ICONS.code); node.append(path);
    return node;
  };
  const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
  const setLabel = (node, label) => { node.setAttribute('aria-label', label); node.title = label; };
  const iconButton = (label, icon, action, cls = 'ar-icon') => { const node = el('button', cls); node.type = 'button'; setLabel(node, label); node.append(svgIcon(icon)); if (action) node.addEventListener('click', action); return node; };
  const textButton = (label, action, cls = 'ar-btn') => { const node = el('button', cls, label); node.type = 'button'; if (action) node.addEventListener('click', action); return node; };
  const nameSpan = (cls, text) => { const node = el('span', cls, text); node.dir = 'auto'; return node; };
  const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
  const randomId = () => { const bytes = new Uint8Array(12); crypto.getRandomValues(bytes); return [...bytes].map(b => b.toString(16).padStart(2, '0')).join(''); };
  const countLines = text => { if (!text) return 0; let k = 1; for (let i = text.indexOf('\n'); i >= 0; i = text.indexOf('\n', i + 1)) k++; return text.endsWith('\n') ? k - 1 : k; };
  const decodeEntities = text => text.replace(/&(quot|amp|lt|gt|#39|#x27|#x2F);/g, (_, name) => ({quot: '"', amp: '&', lt: '<', gt: '>', '#39': "'", '#x27': "'", '#x2F': '/'})[name]);
  const toMs = seconds => !seconds ? 0 : seconds > 1e14 ? seconds / 1e6 : seconds > 1e11 ? seconds : seconds * 1000;
  const relTime = seconds => {
    const ms = toMs(seconds); if (!ms) return '';
    const diff = (Date.now() - ms) / 1000;
    try {
      const rtf = new Intl.RelativeTimeFormat(lang, {numeric: 'auto'});
      if (diff < 60) return rtf.format(-Math.max(0, Math.round(diff)), 'second');
      if (diff < 3600) return rtf.format(-Math.round(diff / 60), 'minute');
      if (diff < 86400) return rtf.format(-Math.round(diff / 3600), 'hour');
      return new Date(ms).toLocaleDateString(lang, {dateStyle: 'medium'});
    } catch { return new Date(ms).toLocaleString(); }
  };

  /* ---- explicit state machines ---- */
  // Each piece of changing state is a machine: named states, the events each state accepts, and where they lead. An event
  // a state doesn't accept is ignored, so impossible combinations can't arise. Each machine's state is written on <html>
  // as data-ar-<name>, for inspection and for the browser tests.
  function machine(name, initial, table, onEnter) {
    let current = initial;
    const attr = 'ar' + name.charAt(0).toUpperCase() + name.slice(1);
    const write = () => { try { document.documentElement.dataset[attr] = current; } catch {} };
    write();
    return {
      get state() { return current; },
      is: (...states) => states.includes(current),
      send(event, data) {
        const rule = table[current]?.[event] ?? table['*']?.[event];
        if (rule === undefined) return false;
        const next = typeof rule === 'function' ? rule(data, current) : rule;
        if (!next) return false;
        const from = current; current = next; write();
        onEnter?.(next, from, event, data);
        return true;
      }
    };
  }

  /* ---- talking to Open WebUI ---- */
  const token = () => { try { return localStorage.token || ''; } catch { return ''; } };
  const sameOrigin = url => { try { return new URL(url, location.href).origin === location.origin; } catch { return false; } };
  const RETRY_STATUS = new Set([408, 429, 502, 503, 504]);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  // The sign-in token is only ever sent to this site; a failed read is tried once more
  async function request(url, options = {}, timeout = 30000) {
    const signal = options.signal, reading = !options.method || options.method === 'GET';
    const own = sameOrigin(url);
    if (!own) throw new Error('Refusing a request to another site');
    const headers = {...(options.headers || {}), ...(token() ? {Authorization: 'Bearer ' + token()} : {})};
    for (let attempt = 0; ; attempt++) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      const timer = new AbortController(), stop = () => timer.abort();
      signal?.addEventListener('abort', stop, {once: true});
      const clock = setTimeout(stop, timeout);
      try {
        const response = await fetch(url, {...options, headers, credentials: 'same-origin', signal: timer.signal});
        if (!(reading && attempt < 1 && RETRY_STATUS.has(response.status))) return response;
      } catch (error) {
        if (signal?.aborted || !reading || attempt >= 1) throw error;
      } finally { clearTimeout(clock); signal?.removeEventListener('abort', stop); }
      await pause(600);
    }
  }
  async function api(path, signal, options = {}, timeout) {
    const response = await request(path, {headers: {Accept: 'application/json', ...(options.body && typeof options.body === 'string' ? {'Content-Type': 'application/json'} : {})}, signal, ...options}, timeout);
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }
  const resolveAppName = async () => {
    if (String(CONFIG.platformName || '').trim()) return;
    try {
      const name = String((await api('/api/config'))?.name || '').replace(/\s*\(Open WebUI\)\s*$/, '').trim();
      if (name) appName = name.slice(0, 60);
    } catch {}
  };
  // Where we are: a chat of one's own, a shared chat, or elsewhere
  const route = () => {
    let match = /^\/c\/([^/?#]+)/.exec(location.pathname);
    if (match) return {kind: 'chat', id: decodeURIComponent(match[1])};
    match = /^\/s\/([^/?#]+)/.exec(location.pathname);
    if (match) return {kind: 'shared', id: decodeURIComponent(match[1])};
    return {kind: location.pathname === '/' ? 'new' : 'other', id: null};
  };
  let me = null;
  async function whoAmI() {
    if (me !== null || !token()) return me;
    try { const user = await api('/api/v1/auths/'); me = user?.id ? {id: String(user.id), role: user.role, name: user.name} : null; } catch { me = null; }
    return me;
  }

  /* ---- finding artifacts in a chat ---- */
  const ALIAS = {js: 'javascript', mjs: 'javascript', node: 'javascript', ts: 'typescript', py: 'python', python3: 'python', sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash', yml: 'yaml', md: 'markdown', htm: 'html', xhtml: 'html', 'c++': 'cpp', cs: 'csharp', 'c#': 'csharp', rb: 'ruby', rs: 'rust', kt: 'kotlin', golang: 'go', plaintext: 'text', plain: 'text', txt: 'text', tex: 'latex', ps1: 'powershell', mmd: 'mermaid', marp: 'slides', slide: 'slides', presentation: 'slides', animation: 'motion', anim: 'motion', video: 'motion', geojson: 'json', jsonc: 'json', json5: 'json'};
  const EXTS = {javascript: 'js', jsx: 'jsx', tsx: 'tsx', typescript: 'ts', python: 'py', bash: 'sh', yaml: 'yml', markdown: 'md', html: 'html', css: 'css', scss: 'scss', json: 'json', csv: 'csv', tsv: 'tsv', xml: 'xml', svg: 'svg', sql: 'sql', java: 'java', c: 'c', cpp: 'cpp', csharp: 'cs', go: 'go', rust: 'rs', ruby: 'rb', php: 'php', swift: 'swift', kotlin: 'kt', r: 'r', lua: 'lua', toml: 'toml', ini: 'ini', text: 'txt', latex: 'tex', diff: 'diff', powershell: 'ps1', mermaid: 'mmd', vue: 'vue', svelte: 'svelte', dart: 'dart', scala: 'scala', perl: 'pl', slides: 'md', motion: 'txt'};
  const BY_EXT = Object.fromEntries([...Object.entries(EXTS).map(([key, ext]) => [ext, key]), ...Object.entries(ALIAS)]);
  BY_EXT.jsx = 'jsx'; BY_EXT.tsx = 'tsx'; BY_EXT.md = 'markdown';
  const LABELS = {javascript: 'JavaScript', typescript: 'TypeScript', jsx: 'JSX', tsx: 'TSX', html: 'HTML', css: 'CSS', scss: 'SCSS', json: 'JSON', csv: 'CSV', tsv: 'TSV', xml: 'XML', svg: 'SVG', sql: 'SQL', yaml: 'YAML', toml: 'TOML', php: 'PHP', cpp: 'C++', csharp: 'C#', latex: 'LaTeX', markdown: 'Markdown', text: 'Text', bash: 'Shell', powershell: 'PowerShell', mermaid: 'Mermaid', slides: 'Slides', motion: 'Motion'};
  const labelOf = key => LABELS[key] || key.charAt(0).toUpperCase() + key.slice(1);
  const TYPE_ICON = {document: 'document', slides: 'slides', sheet: 'sheet', diagram: 'diagram', html: 'html', react: 'react', svg: 'svg', data: 'data', code: 'code', motion: 'motion'};
  // Reasoning and tool calls arrive wrapped in <details>; what is inside them isn't part of the answer
  function stripDetails(text) {
    let previous;
    do { previous = text; text = text.replace(/<details\b[^>]*>(?:(?!<details\b)[\s\S])*?<\/details>/gi, ''); } while (text !== previous);
    return text.replace(/<details\b[^>]*>[\s\S]*$/i, '');
  }
  const FENCE_RE = /^([ \t]*(?:>[ \t]?)*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?)(`{3,}|~{3,})[ \t]*(.*)$/;
  // Fenced blocks as CommonMark reads them: a longer fence holds a shorter one, and one left open runs to the end
  function parseBlocks(text) {
    const lines = text.replace(/\r\n?/g, '\n').split('\n'), blocks = [], prose = [];
    let open = null;
    for (const line of lines) {
      if (!open) {
        const match = FENCE_RE.exec(line);
        if (match && !(match[2][0] === '`' && match[3].includes('`'))) { open = {char: match[2][0], size: match[2].length, indent: match[1].length, info: match[3].trim(), lines: []}; prose.push('\u0000' + blocks.length); continue; }
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
    return {blocks, prose};
  }
  const cleanLang = value => { const key = String(value || '').toLowerCase(); const known = ALIAS[key] ?? key; return /^[a-z0-9+#_.-]{1,24}$/.test(known) ? known : 'text'; };
  // A first line like "<!-- artifact: Budget -->", "// artifact: Budget" or "%% artifact: Budget" names an artifact
  const MARKER_RE = /^\s*(?:<!--|\/\/|#|%%|--|\/\*|;|')\s*artifact\s*:\s*(.+?)\s*(?:-->|\*\/)?\s*$/i;
  const markerOf = text => { const first = text.replace(/^\s*\n/, '').split('\n', 1)[0] || ''; const match = MARKER_RE.exec(first); return match ? match[1].replace(/["<>]/g, '').trim().slice(0, 120) : ''; };
  const withoutMarker = text => { const lines = text.split('\n'); const i = lines.findIndex(line => line.trim()); if (i >= 0 && MARKER_RE.test(lines[i])) lines.splice(i, 1); return lines.join('\n'); };
  const humanize = name => name.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim();
  const firstHeading = text => { const match = /^ {0,3}#{1,3}\s+(.+?)\s*#*\s*$/m.exec(text); return match ? match[1].replace(/[*_`]/g, '').trim().slice(0, 120) : ''; };
  function readInfo(info, text) {
    let title = (/(?:title|filename|file|name)\s*=\s*["']([^"']{1,120})["']/i.exec(info) || /(?:title|filename|file|name)\s*=\s*([^\s"'}]{1,120})/i.exec(info) || [])[1] || '';
    let first = (info.split(/[\s,{}]+/).filter(Boolean)[0] || '').replace(/^\./, '');
    const colon = first.indexOf(':');
    if (colon > 0) { const after = first.slice(colon + 1); if (!title && /\.\w{1,8}$/.test(after)) title = after; first = first.slice(0, colon); }
    if (/=/.test(first)) first = '';
    let key = '';
    if (!title && /^[\w.\-/\\]+\.\w{1,8}$/.test(first) && !ALIAS[first.toLowerCase()] && !EXTS[first.toLowerCase()]) { title = first; first = ''; }
    if (first) key = cleanLang(first);
    if (!title) title = (/^\s*(?:#|\/\/|--|;|<!--|\/\*)\s*(?:file(?:name)?\s*:\s*)?([\w.\-/\\]+\.\w{1,8})\s*(?:-->|\*\/)?\s*$/i.exec(text.split('\n', 1)[0]) || [])[1] || '';
    if (!key && title) key = cleanLang(BY_EXT[(/\.(\w{1,8})$/.exec(title) || [])[1]?.toLowerCase()] || '');
    return {lang: key || 'text', title: title.split(/[\\/]/).pop().slice(0, 120), named: !!title};
  }
  const looksReact = text => /(^|\n)\s*(import\s+[^;]*from\s+['"]react['"]|export\s+default\s+(function|class|\(|[A-Z]))/.test(text) && /<[A-Za-z][\w.]*[\s/>]/.test(text);
  const slideBreaks = text => (text.match(/^\s*---\s*$/gm) || []).length;
  // What kind of artifact a block is, or null when it is too small to be one
  function typeOf(lang, text, info, marked) {
    const body = withoutMarker(text).trim();
    if (!body) return null;
    if (marked) {
      if (lang === 'markdown' || lang === 'md') return slideBreaks(body) >= 2 && /^\s*#/m.test(body) ? 'slides' : 'document';
      if (lang === 'text' && /^\s*#\s/.test(body)) return 'document';
    }
    // A chart is a small table of data, shown as its chart first
    if (lang === 'chart') return 'sheet';
    if (lang === 'slides') return 'slides';
    if (lang === 'motion') return 'motion';
    if (lang === 'mermaid') return 'diagram';
    if (lang === 'svg' || ((lang === 'xml' || lang === 'html' || lang === 'text') && /^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--[\s\S]*?-->\s*)?<svg[\s>]/i.test(body))) return 'svg';
    if (lang === 'html' || (lang === 'text' && /^\s*(<!doctype html|<html[\s>])/i.test(body))) return marked || countLines(body) >= Math.min(5, CONFIG.minCodeLines) ? 'html' : null;
    if (lang === 'jsx' || lang === 'tsx' || lang === 'react' || ((lang === 'javascript' || lang === 'typescript') && looksReact(body))) return 'react';
    if (lang === 'csv' || lang === 'tsv') return countLines(body) >= 2 ? 'sheet' : null;
    if (lang === 'json') return marked || countLines(body) >= 3 || body.length >= 80 ? 'data' : null;
    if (lang === 'markdown') return slideBreaks(body) >= 2 && /^\s*#/m.test(body) ? 'slides' : countLines(body) >= 5 ? 'document' : null;
    return marked || countLines(body) >= CONFIG.minCodeLines ? 'code' : null;
  }
  // A block is marked as an artifact by an "artifact:" first line, or "artifact" in its info string
  const isMarked = block => !!markerOf(block.text) || /(^|\s)artifact(\s|=|$)/i.test(block.info || '');
  // Which blocks are artifacts. 'marked': only those the model marked. 'rich' (the default): marked ones, and kinds that
  // need a viewer to be useful at all (a whole web page, an app, a drawing, a diagram, slides, a table of data).
  // 'all': also long answers, tables, JSON and any long code.
  function wanted(type, block, marked) {
    if (marked || CONFIG.detect === 'all') return true;
    if (CONFIG.detect === 'marked') return false;
    const body = block.text.trim();
    if (type === 'html') return /<!doctype html|<html[\s>]|<body[\s>]/i.test(body);
    if (type === 'react') return /export\s+default/.test(body);
    if (type === 'sheet') return countLines(body) >= 3;
    return type === 'svg' || type === 'diagram' || type === 'slides' || type === 'motion';
  }
  function titleOf(type, lang, text, info) {
    const marker = markerOf(text);
    if (marker) return {title: marker, named: true, strong: true};
    if (info.named) return {title: info.title, named: true, strong: true};
    let title = '';
    if (type === 'html' || type === 'svg') { const own = ((/<title[^>]*>([^<]{1,120})<\/title>/i.exec(text) || [])[1] || '').trim(); if (own) return {title: decodeEntities(own).slice(0, 120), named: true, strong: true}; }
    if (type === 'html') title = ((/<h1[^>]*>([^<]{1,120})<\/h1>/i.exec(text) || [])[1] || '').trim();
    else if (type === 'react') { const match = /export\s+default\s+(?:function\s+|class\s+)?([A-Z][A-Za-z0-9_]*)/.exec(text) || /function\s+([A-Z][A-Za-z0-9_]*)\s*\(/.exec(text); title = match && !/^(App|Component|Main|Index|Root)$/.test(match[1]) ? humanize(match[1]) : ''; }
    else if (type === 'diagram') title = ((/^\s*title\s*[: ]\s*(.+)$/im.exec(text) || [])[1] || '').trim();
    else if (type === 'document' || type === 'slides') title = firstHeading(text);
    else if (type === 'motion') title = ((/^\s*title\s*:\s*(.+)$/im.exec(text) || [])[1] || '').trim() || firstHeading(text);
    return {title: decodeEntities(title).slice(0, 120), named: !!title, strong: type === 'document' || type === 'slides' || type === 'motion' ? !!title : false};
  }
  // Markdown tables of three rows or more can be opened as a spreadsheet
  function tablesIn(lines) {
    const out = [];
    for (let i = 0; i + 1 < lines.length; i++) {
      if (!lines[i].includes('|') || !/^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(lines[i + 1])) continue;
      const start = i; let end = i + 2;
      while (end < lines.length && lines[end].includes('|') && lines[end].trim()) end++;
      if (end - start - 2 >= 3) {
        let heading = '';
        for (let k = start - 1; k >= 0 && k >= start - 6; k--) { const match = /^ {0,3}#{1,6}\s+(.+?)\s*#*$/.exec(lines[k]) || /^\s*\*\*(.+?)\*\*\s*:?\s*$/.exec(lines[k]); if (match) { heading = match[1].replace(/[*_`]/g, '').trim(); break; } }
        const cells = row => row.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|').replace(/\*\*|__|`/g, ''));
        const rows = [cells(lines[start]), ...lines.slice(start + 2, end).map(cells)];
        out.push({title: heading, text: rows.map(row => row.map(csvCell).join(',')).join('\n')});
      }
      i = end - 1;
    }
    return out;
  }
  const csvCell = value => /[",\n\r]/.test(value) || /^\s|\s$/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
  // The candidates in one answer, in the order they appear
  function candidatesOf(message) {
    const content = stripDetails(typeof message.content === 'string' ? message.content : '');
    if (!content.trim()) return [];
    const {blocks, prose} = parseBlocks(content);
    const out = [];
    blocks.forEach((block, index) => {
      const info = readInfo(block.info, block.text);
      const marked = isMarked(block);
      const type = typeOf(info.lang, block.text, info, marked);
      if (!type || !wanted(type, block, marked)) return;
      const name = titleOf(type, info.lang, block.text, info);
      out.push({type, lang: info.lang === 'react' ? 'jsx' : info.lang, title: name.title, named: name.named, strong: name.strong, text: block.text, unfinished: block.unfinished, index, info: block.info, source: 'block'});
    });
    const proseLines = prose.filter(line => !line.startsWith('\u0000'));
    if (CONFIG.detect !== 'all') return out;
    if (CONFIG.tablesAsSheets) tablesIn(proseLines).forEach((table, k) => out.push({type: 'sheet', lang: 'csv', title: table.title, named: !!table.title, text: table.text, unfinished: false, index: 1000 + k, info: '', source: 'table'}));
    // A long answer is offered as a document of its own, its code blocks kept in place
    const words = proseLines.join('\n');
    if (CONFIG.minDocumentChars > 0 && words.replace(/\s+/g, ' ').trim().length >= CONFIG.minDocumentChars && (/^ {0,3}#{1,3}\s/m.test(words) || words.split(/\n\s*\n/).length >= 4)) {
      const title = firstHeading(content);
      out.push({type: 'document', lang: 'markdown', title, named: !!title, text: content.trim(), unfinished: message.done === false, index: 2000, info: '', source: 'answer'});
    }
    return out;
  }
  // The messages on the branch being shown, oldest first
  function branchOf(chat) {
    const history = chat?.chat?.history, messages = history?.messages;
    if (!messages || typeof messages !== 'object') return [];
    let id = history.currentId;
    if (!(typeof id === 'string' && Object.prototype.hasOwnProperty.call(messages, id))) {
      id = Object.values(messages).filter(m => m && typeof m === 'object').sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))[0]?.id;
    }
    const out = [], seen = new Set();
    while (typeof id === 'string' && id && !seen.has(id) && seen.size < 20000) {
      seen.add(id);
      const message = Object.prototype.hasOwnProperty.call(messages, id) ? messages[id] : null;
      if (!message || typeof message !== 'object') break;
      out.push(message); id = message.parentId;
    }
    return out.reverse();
  }
  const normTitle = title => title.toLocaleLowerCase().replace(/\.[a-z0-9]{1,8}$/, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const sameText = (a, b) => a.replace(/\s+/g, ' ').trim() === b.replace(/\s+/g, ' ').trim();
  // Like Claude, one artifact carries versions: a later answer's block with the same name, or, when nothing is named,
  // the one block of its kind right after the previous answer's one block of that kind, is its next version
  function artifactsOf(chat) {
    const branch = branchOf(chat), list = [];
    let answerNo = 0, previous = null;
    for (const message of branch) {
      if (!message || message.role !== 'assistant' || typeof message.id !== 'string') continue;
      answerNo++;
      const found = candidatesOf(message);
      const perType = {};
      for (const c of found) perType[c.type] = (perType[c.type] || 0) + 1;
      const madeHere = [];
      for (const c of found) {
        const version = {text: c.text, messageId: message.id, index: c.index, at: message.timestamp || 0, unfinished: c.unfinished, answerNo, info: c.info, lang: c.lang, source: c.source, model: message.model || ''};
        let target = null;
        if (c.named) target = list.find(a => a.type === c.type && a.named && normTitle(a.title) === normTitle(c.title) && !madeHere.includes(a)) || null;
        if (!target && previous && perType[c.type] === 1 && c.type !== 'code' && c.source !== 'answer') {
          const before = list.filter(a => a.type === c.type && a.versions[a.versions.length - 1].messageId === previous);
          if (before.length === 1 && (!c.strong || !before[0].strong)) target = before[0];
        }
        if (target) {
          const last = target.versions[target.versions.length - 1];
          if (last.messageId === message.id || sameText(last.text, c.text)) continue;
          target.versions.push(version);
          if (c.named && (!target.named || (c.strong && !target.strong) || !target.strong)) { target.title = c.title; target.named = true; target.strong = !!c.strong; }
          target.lang = c.lang;
        } else {
          target = {key: 'a' + message.id + '-' + c.index, type: c.type, lang: c.lang, title: c.title, named: c.named, strong: !!c.strong, versions: [version], local: false};
          list.push(target);
        }
        madeHere.push(target);
      }
      // A reply without artifacts keeps the chain to the answer before it
      if (found.length) previous = message.id;
    }
    for (const artifact of list) artifact.autoTitle = artifact.title ? '' : autoTitle(artifact).slice(0, 80);
    return list;
  }
  // An untitled sheet or data set is named after its columns or keys
  function autoTitle(artifact) {
    const text = withoutMarker(artifact.versions[artifact.versions.length - 1]?.text || '').trim();
    if (artifact.type === 'sheet') { const head = parseDelimited(text.split('\n', 1)[0], text.split('\n', 1)[0].includes('\t') ? '\t' : text.split('\n', 1)[0].split(';').length > text.split('\n', 1)[0].split(',').length ? ';' : ',', 1)[0] || []; const cells = head.map(x => x.trim()).filter(x => x && !x.startsWith('=')).slice(0, 3); return cells.length ? cells.join(' · ') + (head.length > 3 ? ' …' : '') : ''; }
    if (artifact.type === 'data') { try { const v = JSON.parse(text); const keys = Array.isArray(v) ? Object.keys(v[0] && typeof v[0] === 'object' ? v[0] : {}) : v && typeof v === 'object' ? Object.keys(v) : []; return keys.length ? keys.slice(0, 3).join(' · ') + (keys.length > 3 ? ' …' : '') : ''; } catch { return ''; } }
    return '';
  }
  const displayTitle = artifact => artifact.title || artifact.autoTitle || t('t_' + artifact.type) + (artifact.type === 'code' ? ' · ' + labelOf(artifact.lang) : '');

  /* ---- what is kept in this browser: edits, blank artifacts, an artifact's own saved data ---- */
  const PREFIX = 'owui-chat-artifacts:';
  const BUDGET = 3 * 1024 * 1024, EDIT_LIMIT = 400000, STORE_LIMIT = 512 * 1024;
  const memory = new Map();
  const canStore = () => CONFIG.keepDrafts && !!me?.id;
  const storeKey = chatId => PREFIX + (me?.id || 'anon') + ':' + chatId;
  function readStore(chatId) {
    if (memory.has(chatId)) return memory.get(chatId);
    let data = null;
    if (canStore()) { try { data = JSON.parse(localStorage.getItem(storeKey(chatId)) || 'null'); } catch {} }
    if (!data || typeof data !== 'object') data = {};
    data = {edits: data.edits && typeof data.edits === 'object' ? data.edits : {}, locals: Array.isArray(data.locals) ? data.locals.filter(x => x && typeof x.id === 'string') : [], at: Date.now()};
    memory.set(chatId, data);
    return data;
  }
  const ourKeys = () => { const keys = []; try { for (let i = 0; i < localStorage.length; i++) { const key = localStorage.key(i); if (key && key.startsWith(PREFIX + (me?.id || 'anon') + ':')) keys.push(key); } } catch {} return keys; };
  // The oldest chats' edits make room for new ones once the budget is spent
  function writeStore(chatId) {
    if (!canStore()) return 'session';
    const data = memory.get(chatId); if (!data) return 'session';
    data.at = Date.now();
    const empty = !Object.keys(data.edits).length && !data.locals.length;
    try {
      if (empty) { localStorage.removeItem(storeKey(chatId)); return 'kept'; }
      const value = JSON.stringify(data);
      if (value.length > BUDGET) return 'big';
      let used = 0; const others = [];
      for (const key of ourKeys()) { if (key === storeKey(chatId)) continue; const raw = localStorage.getItem(key) || ''; used += raw.length; let at = 0; try { at = JSON.parse(raw).at || 0; } catch {} others.push({key, at, size: raw.length}); }
      others.sort((a, b) => a.at - b.at);
      while (used + value.length > BUDGET && others.length) { const old = others.shift(); localStorage.removeItem(old.key); used -= old.size; }
      localStorage.setItem(storeKey(chatId), value);
      return 'kept';
    } catch { return 'session'; }
  }
  const editOf = (chatId, artifact, index) => readStore(chatId).edits[artifact.key + '#' + index] || null;
  function setEdit(chatId, artifact, index, text) {
    const data = readStore(chatId), id = artifact.key + '#' + index;
    if (text == null) delete data.edits[id];
    else data.edits[id] = {text, at: Date.now()};
    if (text && text.length > EDIT_LIMIT) return 'big';
    return writeStore(chatId);
  }

  /* ---- running an artifact in a sandbox ---- */
  // Frames are given scripts but never this site's origin: they can't read the page, its storage or the sign-in token.
  // A content policy keeps them to the libraries listed in the settings, so a generated page can't send the chat anywhere.
  const SANDBOX = 'allow-scripts allow-modals allow-downloads allow-forms allow-popups';
  const originOf = url => { try { const u = new URL(url); return u.protocol === 'https:' || u.protocol === 'http:' ? u.origin : ''; } catch { return ''; } };
  const libraryHosts = () => [...new Set([CONFIG.esmCdn, CONFIG.babelUrl, CONFIG.tailwindUrl, CONFIG.mermaidUrl, ...String(CONFIG.extraHosts || '').split(/[\s,]+/)].map(originOf).filter(Boolean))].join(' ');
  function policy() {
    if (CONFIG.network === 'open') return "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; form-action 'none'; base-uri 'none'";
    if (CONFIG.network === 'off') return "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'";
    const hosts = libraryHosts();
    return `default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob: ${hosts}; style-src 'unsafe-inline' ${hosts}; img-src data: blob: ${hosts}; font-src data: ${hosts}; connect-src blob: data: ${hosts}; media-src data: blob:; worker-src blob:; form-action 'none'; base-uri 'none'`;
  }
  let adminPolicy = '';
  const attr = value => String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  // Inside every frame: errors and console errors are reported, and window.claude / window.ai / window.storage talk to the panel
  const BOOT = `(function(){var P=parent,N=__NONCE__,q={},s=0;function send(m){m.__ar=N;try{P.postMessage(m,'*')}catch(e){}}
function txt(v){try{return String(v&&(v.stack||v.message)||v).slice(0,4000)}catch(e){return 'Error'}}
addEventListener('error',function(e){if(e&&e.target&&e.target!==window&&(e.target.src||e.target.href)){if(e.target.getAttribute&&e.target.getAttribute('data-ar-optional'))return;var u=String(e.target.src||e.target.href).slice(0,300),h=u;try{h=new URL(u).host}catch(x){}send({type:'lib',what:h,url:u});return}send({type:'error',message:txt(e.error||e.message),line:e.lineno||0})},true);
addEventListener('unhandledrejection',function(e){send({type:'error',message:txt(e.reason)})});
var ce=console.error;console.error=function(){try{send({type:'console',message:Array.prototype.map.call(arguments,txt).join(' ')})}catch(e){}return ce.apply(console,arguments)};
function call(k,p){return new Promise(function(r,j){var i=++s;q[i]={r:r,j:j};send({type:'call',kind:k,id:i,payload:p})})}
addEventListener('message',function(e){if(e.source!==P)return;var d=e.data;if(!d||d.__ar!==N)return;if(d.type==='reply'&&q[d.id]){var x=q[d.id];delete q[d.id];d.error?x.j(new Error(d.error)):x.r(d.value)}else if(d.type==='print'){print()}});
var c=function(p){return call('ai',{prompt:String(p)})};window.claude={complete:c};window.ai={complete:c};
window.storage={get:function(k){return call('storage',{op:'get',key:String(k)})},set:function(k,v){return call('storage',{op:'set',key:String(k),value:String(v)})},delete:function(k){return call('storage',{op:'delete',key:String(k)})},list:function(p){return call('storage',{op:'list',prefix:p==null?'':String(p)})}};
window.__arSend=send;window.__arLoad=function(u,o){return new Promise(function(r,j){var t=document.createElement('script');t.setAttribute('data-ar-optional','1');t.src=u;t.onload=r;t.onerror=function(){j(new Error('load '+u))};document.head.appendChild(t)})};
addEventListener('DOMContentLoaded',function(){send({type:'ready'})})})();`;
  function head(nonce) {
    const metas = [`<meta charset="utf-8">`, `<meta http-equiv="Content-Security-Policy" content="${attr(policy())}">`];
    if (adminPolicy) metas.push(`<meta http-equiv="Content-Security-Policy" content="${attr(adminPolicy)}">`);
    metas.push(`<meta name="viewport" content="width=device-width, initial-scale=1">`, `<meta name="referrer" content="no-referrer">`, `<script>${BOOT.replace('__NONCE__', JSON.stringify(nonce))}<\/script>`);
    return metas.join('');
  }
  // JSON in a script: "</script" and line separators can't break out of it
  const scriptJson = value => JSON.stringify(value).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
  function htmlDoc(text, nonce) {
    const top = head(nonce);
    if (/<head[\s>]/i.test(text)) return text.replace(/<head([^>]*)>/i, (m, a) => `<head${a}>${top}`);
    if (/<html[\s>]/i.test(text)) return text.replace(/<html([^>]*)>/i, (m, a) => `<html${a}><head>${top}</head>`);
    return `<!doctype html><html><head>${top}</head><body>${text}</body></html>`;
  }
  const REACT_VERSION = '18.3.1';
  // shadcn/ui pieces that generated apps often import, drawn with Tailwind classes
  const SHIM = `import React from "__R__";const h=React.createElement;export const cn=(...a)=>a.flat().filter(Boolean).join(' ');
const mk=(tag,base)=>React.forwardRef(({className,...p},r)=>h(tag,{ref:r,className:cn(base,className),...p}));
export const Card=mk('div','rounded-xl border bg-white text-gray-900 shadow-sm');export const CardHeader=mk('div','flex flex-col gap-1.5 p-6');export const CardTitle=mk('h3','text-lg font-semibold leading-none tracking-tight');export const CardDescription=mk('p','text-sm text-gray-500');export const CardContent=mk('div','p-6 pt-0');export const CardFooter=mk('div','flex items-center p-6 pt-0');
const V={default:'bg-gray-900 text-white hover:bg-gray-800',destructive:'bg-red-600 text-white hover:bg-red-700',outline:'border border-gray-300 bg-white hover:bg-gray-50',secondary:'bg-gray-100 text-gray-900 hover:bg-gray-200',ghost:'hover:bg-gray-100',link:'underline-offset-4 hover:underline text-gray-900'};const S={default:'h-10 px-4 py-2',sm:'h-9 px-3',lg:'h-11 px-8',icon:'h-10 w-10'};
export const Button=React.forwardRef(({className,variant='default',size='default',asChild,...p},r)=>h('button',{ref:r,className:cn('inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none',V[variant]||V.default,S[size]||S.default,className),...p}));
export const Input=mk('input','flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm');export const Textarea=mk('textarea','flex min-h-[80px] w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm');export const Label=mk('label','text-sm font-medium leading-none');
export const Badge=({className,variant='default',...p})=>h('span',{className:cn('inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold',variant==='secondary'?'bg-gray-100 text-gray-900':variant==='outline'?'text-gray-900':variant==='destructive'?'bg-red-600 text-white':'bg-gray-900 text-white',className),...p});
export const Alert=mk('div','relative w-full rounded-lg border p-4');export const AlertTitle=mk('h5','mb-1 font-medium leading-none');export const AlertDescription=mk('div','text-sm');export const Separator=({className,orientation,...p})=>h('div',{className:cn(orientation==='vertical'?'h-full w-px':'h-px w-full','bg-gray-200',className),...p});
export const Progress=({value=0,className,...p})=>h('div',{className:cn('relative h-4 w-full overflow-hidden rounded-full bg-gray-100',className),...p},h('div',{className:'h-full bg-gray-900 transition-all',style:{width:Math.max(0,Math.min(100,value))+'%'}}));
export const Switch=({checked,onCheckedChange,className,...p})=>h('button',{role:'switch','aria-checked':!!checked,onClick:()=>onCheckedChange&&onCheckedChange(!checked),className:cn('inline-flex h-6 w-11 items-center rounded-full transition-colors',checked?'bg-gray-900':'bg-gray-200',className),...p},h('span',{className:cn('block h-5 w-5 rounded-full bg-white shadow transition-transform',checked?'translate-x-5':'translate-x-0.5')}));
export const Checkbox=({checked,onCheckedChange,className,...p})=>h('input',{type:'checkbox',checked:!!checked,onChange:e=>onCheckedChange&&onCheckedChange(e.target.checked),className:cn('h-4 w-4',className),...p});
export const Slider=({value=[0],onValueChange,min=0,max=100,step=1,className,...p})=>h('input',{type:'range',min,max,step,value:value[0],onChange:e=>onValueChange&&onValueChange([Number(e.target.value)]),className:cn('w-full',className),...p});
const T=React.createContext(null);export const Tabs=({defaultValue,value,onValueChange,className,children})=>{const[v,s]=React.useState(defaultValue);const cur=value!==undefined?value:v;return h(T.Provider,{value:{v:cur,s:x=>{s(x);onValueChange&&onValueChange(x)}}},h('div',{className},children))};
export const TabsList=mk('div','inline-flex h-10 items-center justify-center rounded-md bg-gray-100 p-1 text-gray-500');export const TabsTrigger=({value,className,...p})=>{const c=React.useContext(T);return h('button',{onClick:()=>c.s(value),className:cn('inline-flex items-center justify-center rounded-sm px-3 py-1.5 text-sm font-medium',c.v===value?'bg-white text-gray-900 shadow-sm':'',className),...p})};export const TabsContent=({value,className,...p})=>{const c=React.useContext(T);return c.v===value?h('div',{className:cn('mt-2',className),...p}):null};
export const Select=({value,onValueChange,children})=>h(SC.Provider,{value:{value,onValueChange}},children);const SC=React.createContext({});export const SelectTrigger=({children})=>null;export const SelectValue=()=>null;export const SelectContent=({children})=>{const c=React.useContext(SC);return h('select',{className:'h-10 rounded-md border border-gray-300 px-3 text-sm',value:c.value,onChange:e=>c.onValueChange&&c.onValueChange(e.target.value)},children)};export const SelectItem=({value,children})=>h('option',{value},children);`;
  function reactDoc(text, nonce) {
    const esm = String(CONFIG.esmCdn || '').replace(/\/+$/, '');
    const urls = {react: `${esm}/react@${REACT_VERSION}`, dom: `${esm}/react-dom@${REACT_VERSION}`, client: `${esm}/react-dom@${REACT_VERSION}/client`, esm, deps: `deps=react@${REACT_VERSION},react-dom@${REACT_VERSION}`, babel: CONFIG.babelUrl, tailwind: CONFIG.tailwindUrl};
    const run = `(async function(){var U=${scriptJson(urls)},SRC=${scriptJson(text)},SHIM=${scriptJson(SHIM)},send=window.__arSend,root=document.getElementById('root');
function fail(m,lib){root.innerHTML='';var p=document.createElement('pre');p.className='__ar_err';p.textContent=m;root.appendChild(p);send(lib?{type:'lib',what:lib.what,url:lib.url}:{type:'error',message:m})}
if(!U.esm){fail('No React runtime is set up.',{what:'React',url:''});return}
if(U.tailwind){try{await window.__arLoad(U.tailwind,1)}catch(e){}}
try{await window.__arLoad(U.babel)}catch(e){fail('Couldn\\'t load Babel from '+U.babel,{what:'Babel',url:U.babel});return}
var out;try{out=Babel.transform(SRC,{presets:[['react',{runtime:'classic'}],['typescript',{isTSX:true,allExtensions:true}]],filename:'artifact.tsx',sourceType:'module'}).code}catch(e){fail(String(e.message||e));return}
var shim=URL.createObjectURL(new Blob([SHIM.replace('__R__',U.react)],{type:'text/javascript'}));
function res(x){if(/^(https?:|blob:|data:)/.test(x))return x;if(x==='react')return U.react;if(x==='react-dom')return U.dom;if(x==='react-dom/client')return U.client;if(x.indexOf('react/')===0)return U.esm+'/react@${REACT_VERSION}/'+x.slice(6);if(x.indexOf('react-dom/')===0)return U.esm+'/react-dom@${REACT_VERSION}/'+x.slice(10);if(x.indexOf('@/components/ui/')===0||x==='@/lib/utils')return shim;if(x[0]==='.'||x[0]==='/'||x[0]==='@'&&x[1]==='/')return x;return U.esm+'/'+x+(x.indexOf('?')>=0?'&':'?')+U.deps}
out=out.replace(/(\\bfrom\\s*|\\bimport\\s*\\(\\s*|\\bimport\\s+)(['"])([^'"\\n]+)\\2/g,function(m,a,q,x){return a+q+res(x)+q});
if(!/\\bimport\\s+(\\*\\s+as\\s+)?React\\b/.test(out))out='import React from "'+U.react+'";\\n'+out;
var url=URL.createObjectURL(new Blob([out],{type:'text/javascript'}));
try{var mod=await import(url),R=(await import(U.react)).default,C=await import(U.client)}catch(e){var m=String(e&&e.message||e);if(/fetch|load|import/i.test(m)&&!/is not defined|Unexpected/.test(m)){fail(m,{what:'React',url:U.esm});return}fail(m);return}
var Comp=mod.default||Object.keys(mod).map(function(k){return mod[k]}).find(function(v){return typeof v==='function'});
if(!Comp){fail('Nothing to show: the code needs a component with export default.');return}
class B extends R.Component{constructor(p){super(p);this.state={e:null}}static getDerivedStateFromError(e){return{e:e}}componentDidCatch(e){send({type:'error',message:String(e&&e.stack||e).slice(0,4000)})}render(){return this.state.e?R.createElement('pre',{className:'__ar_err'},String(this.state.e&&this.state.e.message||this.state.e)):this.props.children}}
C.createRoot(root).render(R.createElement(B,null,R.createElement(Comp)));send({type:'rendered'})})();`;
    return `<!doctype html><html><head>${head(nonce)}<style>html,body{margin:0;min-height:100%;background:#fff;color:#111;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}.__ar_err{white-space:pre-wrap;color:#b91c1c;padding:16px;font:13px/1.5 ui-monospace,Menlo,monospace}</style></head><body><div id="root"></div><script>${run}<\/script></body></html>`;
  }
  function mermaidDoc(text, nonce, dark) {
    const run = `(async function(){var U=${scriptJson(CONFIG.mermaidUrl)},SRC=${scriptJson(text)},send=window.__arSend,out=document.getElementById('out');
try{await window.__arLoad(U)}catch(e){out.textContent='';send({type:'lib',what:'Mermaid',url:U});return}
try{mermaid.initialize({startOnLoad:false,securityLevel:'strict',htmlLabels:false,flowchart:{htmlLabels:false},theme:${dark ? "'dark'" : "'default'"}});var r=await mermaid.render('ar'+Date.now(),SRC);out.innerHTML=r.svg;send({type:'svg',svg:r.svg});send({type:'rendered'})}catch(e){out.textContent='';var p=document.createElement('pre');p.className='__ar_err';p.textContent=String(e&&e.message||e);out.appendChild(p);send({type:'error',message:String(e&&e.message||e).slice(0,4000)})}})();`;
    return `<!doctype html><html><head>${head(nonce)}<style>html,body{margin:0;background:${dark ? '#171717' : '#fff'};color:${dark ? '#eee' : '#111'}}#out{display:flex;justify-content:center;padding:24px}#out svg{max-width:100%;height:auto}.__ar_err{white-space:pre-wrap;color:${dark ? '#f87171' : '#b91c1c'};font:13px/1.5 ui-monospace,Menlo,monospace}</style></head><body><div id="out"></div><script>${run}<\/script></body></html>`;
  }
  const frames = new Map();
  function makeFrame(kind, text, artifact, onEvent, darkTheme) {
    const nonce = randomId();
    const frame = el('iframe', 'ar-frame');
    frame.setAttribute('sandbox', SANDBOX);
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.setAttribute('allow', 'clipboard-write; fullscreen');
    frame.title = displayTitle(artifact);
    const dark = darkTheme ?? document.documentElement.classList.contains('dark');
    frame.srcdoc = kind === 'react' ? reactDoc(text, nonce) : kind === 'diagram' ? mermaidDoc(text, nonce, dark) : htmlDoc(text, nonce);
    const record = {nonce, artifact, onEvent, frame};
    // Registered by the frame's own window: a message counts only from that window, with that frame's nonce
    const register = () => { if (frame.contentWindow) frames.set(frame.contentWindow, record); };
    frame.addEventListener('load', register);
    queueMicrotask(register);
    return frame;
  }
  // Diagrams inside documents and slides: drawn by Mermaid in a hidden sandboxed frame, then shown as a picture.
  // Each diagram is drawn once and kept, so slides, thumbnails and downloads share it.
  // Each entry is {status: 'pending' | 'ready' | 'failed', svg, promise}
  const diagrams = new Map();
  let diagramQueue = Promise.resolve();
  function diagramPromise(code) {
    const known = diagrams.get(code);
    if (known) return known.promise;
    const entry = {status: 'pending', svg: null, promise: null};
    diagrams.set(code, entry);
    entry.promise = new Promise(resolve => {
      diagramQueue = diagramQueue.then(() => new Promise(next => {
        let settled = false, frame = null, timer = 0;
        const finish = svg => { if (settled) return; settled = true; clearTimeout(timer); frame?.remove(); dropFrames(); entry.status = svg ? 'ready' : 'failed'; entry.svg = svg || null; resolve(entry.svg); next(); };
        frame = makeFrame('diagram', code, {key: 'figure', type: 'diagram', title: 'Diagram', versions: [{text: code}]}, event => { if (event.type === 'svg') finish(event.svg); else if (event.type === 'error' || event.type === 'lib') finish(null); }, false);
        frame.className = 'ar-hiddenframe'; frame.setAttribute('aria-hidden', 'true'); frame.tabIndex = -1;
        document.body.append(frame);
        timer = setTimeout(() => finish(null), 20000);
      }));
    });
    return entry.promise;
  }
  function diagramNode(code) {
    const box = el('figure', 'ar-figure ar-diagram');
    const show = svg => { box.dataset.state = svg ? 'ready' : 'failed'; box.replaceChildren(svg ? svgPicture(svg, t('t_diagram')) : Object.assign(el('pre', 'ar-pre'), {textContent: code})); };
    const known = diagrams.get(code);
    if (known && known.status !== 'pending') show(known.svg);
    else { box.dataset.state = 'loading'; box.append(el('span', 'ar-muted ar-small', t('drawing'))); diagramPromise(code).then(show); }
    return box;
  }
  const mermaidIn = text => [...withoutMarker(text).matchAll(/^[ \t]*(`{3,}|~{3,})[ \t]*mermaid[^\n]*\n([\s\S]*?)\n[ \t]*\1[ \t]*$/gm)].map(m => m[2]);
  const diagramsReady = text => Promise.all(mermaidIn(text).map(diagramPromise));
  const dropFrames = root => { for (const [win, record] of frames) if (!record.frame.isConnected || root?.contains(record.frame)) frames.delete(win); };
  addEventListener('message', event => {
    const record = frames.get(event.source);
    if (!record) return;
    const data = event.data;
    if (!data || typeof data !== 'object' || data.__ar !== record.nonce || typeof data.type !== 'string') return;
    const reply = (id, value, error) => { try { event.source.postMessage({__ar: record.nonce, type: 'reply', id, value, error}, '*'); } catch {} };
    if (data.type === 'call') {
      const id = Number(data.id) || 0;
      if (data.kind === 'ai') aiCall(record, data.payload).then(value => reply(id, value), error => reply(id, null, String(error?.message || error).slice(0, 300)));
      else if (data.kind === 'storage') storageCall(record, data.payload).then(value => reply(id, value), error => reply(id, null, String(error?.message || error).slice(0, 300)));
      else reply(id, null, 'Unknown call');
      return;
    }
    if (['error', 'console', 'lib', 'svg', 'rendered', 'ready'].includes(data.type)) record.onEvent?.({type: data.type, message: typeof data.message === 'string' ? data.message.slice(0, 4000) : '', what: String(data.what || '').slice(0, 40), url: String(data.url || '').slice(0, 300), svg: data.type === 'svg' && typeof data.svg === 'string' && data.svg.length < 5e6 ? data.svg : ''});
  });

  /* ---- what artifacts may ask for ---- */
  const aiConsent = new Map();
  const aiTimes = [];
  async function aiCall(record, payload) {
    if (!CONFIG.allowAi || !token()) throw new Error(t('ai_off'));
    const prompt = String(payload?.prompt || '').slice(0, 60000);
    if (!prompt.trim()) throw new Error('Empty prompt');
    const model = state.modelId;
    if (!model) throw new Error(t('ai_no_model'));
    // Per artifact: unknown -> asking -> allowed or denied, until the page is reloaded. Requests that arrive while the
    // person is being asked wait for that one answer instead of opening a second dialog.
    const key = record.artifact.key;
    let consent = aiConsent.get(key);
    if (!consent) {
      consent = {state: 'asking', answer: confirmBox(t('ai_title'), t('ai_text', {title: displayTitle(record.artifact), model}), t('ai_allow'), false, t('ai_deny'))};
      aiConsent.set(key, consent);
      consent.answer.then(ok => { consent.state = ok ? 'allowed' : 'denied'; });
    }
    if (consent.state === 'denied' || !(await consent.answer)) throw new Error('Not allowed');
    const now = Date.now();
    while (aiTimes.length && now - aiTimes[0] > 60000) aiTimes.shift();
    if (aiTimes.length >= CONFIG.aiPerMinute) throw new Error(t('ai_limit'));
    aiTimes.push(now);
    const data = await api('/api/chat/completions', null, {method: 'POST', body: JSON.stringify({model, stream: false, messages: [{role: 'user', content: prompt}]})}, 180000);
    const text = data?.choices?.[0]?.message?.content ?? data?.message?.content ?? data?.output?.find?.(item => item?.type === 'message')?.content?.map?.(part => part?.text || '').join('') ?? '';
    return String(text);
  }
  // window.storage: values kept in this browser for this person and this artifact, up to 512 KB per artifact
  async function storageCall(record, payload) {
    if (!CONFIG.allowStorage) throw new Error('Storage is turned off');
    const space = PREFIX + 'store:' + (me?.id || 'anon') + ':' + record.artifact.key;
    let data = {};
    try { data = JSON.parse(localStorage.getItem(space) || '{}') || {}; } catch {}
    const op = payload?.op, key = String(payload?.key ?? '').slice(0, 200);
    if (op === 'get') return Object.prototype.hasOwnProperty.call(data, key) ? {key, value: data[key]} : null;
    if (op === 'list') { const prefix = String(payload?.prefix || ''); return {keys: Object.keys(data).filter(k => k.startsWith(prefix))}; }
    if (op === 'delete') { const had = Object.prototype.hasOwnProperty.call(data, key); delete data[key]; localStorage.setItem(space, JSON.stringify(data)); return {key, deleted: had}; }
    if (op === 'set') {
      data[key] = String(payload?.value ?? '');
      const value = JSON.stringify(data);
      if (value.length > STORE_LIMIT) throw new Error('Storage is full (512 KB)');
      try { localStorage.setItem(space, value); } catch { throw new Error('The browser refused to store this'); }
      return {key, value: data[key]};
    }
    throw new Error('Unknown storage call');
  }

  /* ---- Markdown, read safely: only text and a fixed set of elements are ever made, so nothing in it can run ---- */
  const INLINE_RE = /(`+)(?!`)([\s\S]*?[^`])\1(?!`)|\*\*([^\n]+?)\*\*|__([^\n]+?)__|~~([^\n]+?)~~|\*([^*\s](?:[^*\n]*?[^*\s])?)\*|(?<![\w])_([^_\s](?:[^_\n]*?[^_\s])?)_(?![\w])|(!?)\[([^\]\n]*)\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))+)>?(?:\s+"[^"]*")?\s*\)|(https?:\/\/[^\s<>]*[^\s<>().,;:!?'"*_])|==([^=\n]+?)==/g;
  function inline(target, text, depth = 0) {
    let last = 0;
    const link = (label, href) => {
      if (!/^(https?:\/\/|mailto:)/i.test(href)) { target.append(label); return; }
      const a = el('a', null, label); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer nofollow'; target.append(a);
    };
    for (const match of text.matchAll(INLINE_RE)) {
      if (match.index > last) target.append(text.slice(last, match.index));
      last = match.index + match[0].length;
      const nest = (tag, value) => { const node = el(tag); depth < 4 ? inline(node, value, depth + 1) : node.append(value); target.append(node); };
      if (match[1]) target.append(el('code', null, match[2].replace(/^ (.*) $/, '$1')));
      else if (match[3] != null || match[4] != null) nest('strong', match[3] ?? match[4]);
      else if (match[5] != null) nest('del', match[5]);
      else if (match[6] != null || match[7] != null) nest('em', match[6] ?? match[7]);
      else if (match[10] != null) { if (match[8]) target.append(picture(match[10], match[9])); else link(match[9] || match[10], match[10]); }
      else if (match[11]) link(match[11], match[11]);
      else if (match[12] != null) nest('mark', match[12]);
    }
    if (last < text.length) target.append(text.slice(last));
  }
  // Pictures load only from this site (generated and uploaded images) or as data, unless the network valve is 'open':
  // a picture elsewhere could carry the chat's words out in its address
  function picture(src, alt) {
    let ok = /^data:image\/(png|jpe?g|gif|webp|svg\+xml);base64,/i.test(src);
    try { const u = new URL(src, location.href); ok = ok || u.origin === location.origin || (CONFIG.network === 'open' && u.protocol === 'https:'); } catch {}
    if (!ok) return el('span', 'ar-img', '🖼 ' + (alt || src));
    const img = el('img', 'ar-pic'); img.src = src; img.alt = alt || ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.decoding = 'async';
    img.addEventListener('error', () => img.replaceWith(el('span', 'ar-img', '🖼 ' + (alt || src))), {once: true});
    return img;
  }
  // Diagrams, charts and drawings inside documents and slides
  function figure(lang, body) {
    if (lang === 'mermaid') return diagramNode(body);
    if (lang === 'chart') { const chart = chartFromText(body); if (chart) { const box = el('figure', 'ar-figure'); box.append(chart); return box; } }
    if (lang === 'svg' || (lang === 'xml' && /<svg[\s>]/i.test(body))) { const box = el('figure', 'ar-figure'); box.append(svgPicture(body)); return box; }
    return null;
  }
  // An SVG shown as a picture, where its scripts can't run
  const svgData = text => 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(text)));
  function svgPicture(text, alt = '') { const img = el('img', 'ar-pic ar-svgpic'); img.alt = alt; img.src = svgData(text); return img; }
  const CALLOUTS = {note: 'note', info: 'note', tip: 'tip', important: 'important', warning: 'warning', caution: 'warning', danger: 'warning'};
  function renderMarkdown(text, depth = 0) {
    const out = document.createDocumentFragment();
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    let para = [];
    const endPara = () => { if (para.length) { const p = el('p'); p.dir = 'auto'; inline(p, para.join('\n')); out.append(p); para = []; } };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = FENCE_RE.exec(line);
      if (fence && !fence[1].trim() && !(fence[2][0] === '`' && fence[3].includes('`'))) {
        endPara();
        const body = [];
        for (i++; i < lines.length; i++) { const rest = lines[i].trim(); if (rest.length >= fence[2].length && rest === fence[2][0].repeat(rest.length)) break; body.push(lines[i]); }
        const special = depth < 3 ? figure(cleanLang((fence[3].split(/\s+/)[0] || '')), body.join('\n')) : null;
        if (special) { out.append(special); continue; }
        const pre = el('pre', 'ar-pre'); pre.append(el('code', null, body.join('\n'))); pre.tabIndex = 0; out.append(pre);
        continue;
      }
      if (!line.trim()) { endPara(); continue; }
      if (/^\s*<!--.*-->\s*$/.test(line)) continue;
      const heading = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
      if (heading) { endPara(); const node = el('h' + heading[1].length); node.dir = 'auto'; inline(node, heading[2]); out.append(node); continue; }
      if (/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) { endPara(); out.append(el('hr')); continue; }
      if (/^ {0,3}>/.test(line)) {
        endPara();
        const body = [];
        for (; i < lines.length && /^ {0,3}>/.test(lines[i]); i++) body.push(lines[i].replace(/^ {0,3}> ?/, ''));
        i--;
        // > [!NOTE], [!TIP], [!IMPORTANT], [!WARNING]: a callout
        const kind = /^\s*\[!(\w+)\]\s*$/.exec(body[0] || ''), callout = kind && CALLOUTS[kind[1].toLowerCase()];
        if (callout) body.shift();
        const quote = el(callout ? 'div' : 'blockquote', callout ? 'ar-callout' : null); if (callout) { quote.dataset.kind = callout; quote.append(el('strong', 'ar-callout-title', t('callout_' + callout))); }
        depth < 4 ? quote.append(renderMarkdown(body.join('\n'), depth + 1)) : quote.append(body.join('\n')); out.append(quote);
        continue;
      }
      if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        endPara();
        const cells = row => row.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
        const box = el('div', 'ar-tablebox'), table = el('table', 'ar-table');
        const headRow = el('tr'); for (const cell of cells(line)) { const th = el('th'); th.dir = 'auto'; inline(th, cell); headRow.append(th); }
        table.createTHead().append(headRow);
        const body = table.createTBody();
        for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) { const tr = el('tr'); for (const cell of cells(lines[i])) { const td = el('td'); td.dir = 'auto'; inline(td, cell); tr.append(td); } body.append(tr); }
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
            item = el('li'); item.dir = 'auto';
            const level = Math.min(4, Math.max(0, Math.floor((next[1].length - base) / 2)));
            if (level) item.style.marginInlineStart = (level * 1.25) + 'rem';
            const task = /^\[([ xX])\]\s+(.*)$/.exec(next[3]);
            if (task) { const box = el('input'); box.type = 'checkbox'; box.checked = task[1] !== ' '; box.disabled = true; item.classList.add('ar-task'); item.append(box, ' '); inline(item, task[2]); }
            else inline(item, next[3]);
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
  const wordCount = text => (text.replace(/```[\s\S]*?```/g, ' ').match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) || []).length;

  function documentView(text) {
    const wrap = el('div', 'ar-docwrap');
    const doc = el('article', 'ar-doc'); doc.append(renderMarkdown(withoutMarker(text)));
    const headings = [...doc.querySelectorAll('h1,h2,h3')];
    headings.forEach((node, i) => { node.id = 'ar-h-' + i; });
    if (headings.length >= 3) {
      const nav = el('nav', 'ar-outline'); nav.setAttribute('aria-label', t('outline'));
      nav.append(el('p', 'ar-outline-title', t('outline')));
      for (const node of headings) { const link = el('button', 'ar-outline-link', node.textContent); link.type = 'button'; link.dataset.level = node.tagName[1]; link.addEventListener('click', () => node.scrollIntoView({block: 'start', behavior: 'smooth'})); nav.append(link); }
      wrap.append(nav);
    }
    wrap.append(doc);
    return wrap;
  }

  /* ---- slides ---- */
  // A deck is its first lines (the artifact marker, a Marp front matter block), then slides separated by ---. Editing
  // one slide rewrites only its own part, so the rest of the deck stays exactly as the model wrote it.
  function deckParts(text) {
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    let prefix = '';
    const first = lines.findIndex(line => line.trim());
    if (first >= 0 && MARKER_RE.test(lines[first])) prefix = lines.splice(0, first + 1).join('\n') + '\n';
    let body = lines.join('\n');
    const front = /^\s*---\s*\n(?:[a-zA-Z_]+\s*:.*\n)+---\s*\n/.exec(body);
    if (front) { prefix += front[0]; body = body.slice(front[0].length); }
    return {prefix, chunks: body.split(/\n\s*---\s*\n/)};
  }
  const deckText = parts => parts.prefix + parts.chunks.map(chunk => chunk.trim()).join('\n\n---\n\n') + '\n';
  function slidesOf(text) {
    return deckParts(text).chunks.map((chunk, index) => {
      const split = chunk.split(/^\s*(?:Note|Notes|Speaker notes|Notes de l’orateur)\s*:\s*/im);
      return {body: split[0].trim(), notes: split.slice(1).join('\n').trim(), index, raw: chunk};
    }).filter(slide => slide.body || slide.notes);
  }
  function slideNode(slide, small) {
    const node = el('div', 'ar-slide' + (small ? ' ar-slide-small' : ''));
    const inner = el('div', 'ar-slide-inner');
    // <!-- layout: title | section | center --> picks a layout; a line of ||| splits a slide into columns
    const layout = (/<!--\s*layout\s*:\s*(title|section|center)\s*-->/i.exec(slide.body) || [])[1]?.toLowerCase();
    const parts = slide.body.split(/^\s*\|\|\|\s*$/m);
    if (parts.length > 1) {
      const first = parts[0].trim(), heading = /^(?:<!--.*-->\s*\n)*\s*#{1,3}\s.*$/m.exec(first);
      const lead = heading && first.indexOf(heading[0]) <= first.search(/\S/) + 40 ? heading[0] : '';
      if (lead) inner.append(renderMarkdown(lead));
      const cols = el('div', 'ar-cols'); cols.style.setProperty('--ar-cols', String(Math.min(4, parts.length)));
      parts.slice(0, 4).forEach((part, i) => { const col = el('div', 'ar-col'); col.append(renderMarkdown(i === 0 && lead ? part.replace(lead, '') : part)); cols.append(col); });
      inner.append(cols);
    } else inner.append(renderMarkdown(slide.body));
    const kids = [...inner.children];
    if (layout === 'title' || (!layout && kids.length && kids.every(k => /^H[1-6]$/.test(k.tagName) || k.tagName === 'P') && kids.length <= 3 && kids[0] && /^H[12]$/.test(kids[0].tagName))) node.classList.add('ar-slide-title');
    if (layout === 'section') node.classList.add('ar-slide-section');
    if (layout === 'center') node.classList.add('ar-slide-center');
    if (inner.querySelector('.ar-figure, .ar-diagram, .ar-pic, .ar-tablebox')) node.classList.add('ar-slide-visual');
    node.append(inner);
    return node;
  }
  function slidesView(text, ctx, editable = false, commit = null) {
    const slides = slidesOf(text);
    const wrap = el('div', 'ar-deck');
    if (!slides.length) { wrap.append(el('p', 'ar-muted', t('untitled'))); return wrap; }
    let at = clamp(ctx.slide || 0, 0, slides.length - 1), notesOpen = false;
    const stage = el('div', 'ar-stage'); stage.tabIndex = 0; stage.setAttribute('role', 'region'); stage.setAttribute('aria-roledescription', 'slide');
    const counter = el('span', 'ar-counter'); counter.setAttribute('aria-live', 'polite');
    const notes = el('div', 'ar-notes'); notes.hidden = true;
    const strip = el('div', 'ar-thumbs');
    const prev = iconButton(t('prev_slide'), 'left', () => go(at - 1));
    const next = iconButton(t('next_slide'), 'right', () => go(at + 1));
    const notesButton = textButton(t('notes'), () => { notesOpen = !notesOpen; notesButton.setAttribute('aria-pressed', String(notesOpen)); paint(); });
    notesButton.setAttribute('aria-pressed', 'false');
    const present = textButton(t('present'), () => { stage.requestFullscreen?.().catch(() => {}); stage.focus(); });
    present.prepend(svgIcon('play'));
    const bar = el('div', 'ar-subbar'); bar.append(prev, counter, next, el('span', 'ar-spacer'));
    // One slide can be edited, added after the current one, or deleted, as your edit
    const editor = el('div', 'ar-slideedit'); editor.hidden = true;
    const rewrite = apply => { const parts = deckParts(text); apply(parts.chunks); commit(deckText(parts)); };
    if (editable && commit) {
      const edit = iconButton(t('edit_slide'), 'pencil', () => {
        const area = el('textarea', 'ar-input ar-slidearea'); area.value = slides[at].raw.trim(); area.rows = Math.min(16, Math.max(6, countLines(area.value) + 1)); area.dir = 'auto'; setLabel(area, t('edit_slide'));
        const keep = textButton(t('save_slide'), () => { const index = slides[at].index; rewrite(chunks => { chunks[index] = area.value; }); toast(t('slide_saved')); }, 'ar-btn ar-primary');
        const cancel = textButton(t('cancel'), () => { editor.hidden = true; editor.replaceChildren(); });
        area.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); keep.click(); } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel.click(); } });
        editor.replaceChildren(area, actionsRow(cancel, keep)); editor.hidden = false; area.focus();
      });
      const add = iconButton(t('add_slide'), 'plus', () => { const index = slides[at].index; ctx.slide = at + 1; rewrite(chunks => chunks.splice(index + 1, 0, '# ' + t('untitled') + '\n\n- ')); });
      const remove = iconButton(t('del_slide'), 'row_del', () => { if (slides.length < 2) return; const index = slides[at].index; ctx.slide = Math.max(0, at - 1); rewrite(chunks => chunks.splice(index, 1)); });
      remove.disabled = slides.length < 2;
      bar.append(edit, add, remove);
    }
    bar.append(notesButton, present);
    const thumbs = slides.map((slide, i) => { const b = el('button', 'ar-thumb'); b.type = 'button'; setLabel(b, t('slide', {n: i + 1, total: slides.length})); b.append(slideNode(slide, true)); b.addEventListener('click', () => go(i)); strip.append(b); return b; });
    function go(i) { at = clamp(i, 0, slides.length - 1); ctx.slide = at; paint(); }
    function paint() {
      stage.replaceChildren(slideNode(slides[at]));
      stage.setAttribute('aria-label', t('slide', {n: at + 1, total: slides.length}));
      counter.textContent = (at + 1) + ' / ' + slides.length;
      prev.disabled = at === 0; next.disabled = at === slides.length - 1;
      thumbs.forEach((b, i) => b.setAttribute('aria-current', String(i === at)));
      thumbs[at]?.scrollIntoView({block: 'nearest', inline: 'nearest'});
      notes.hidden = !notesOpen;
      notes.textContent = slides[at].notes || t('no_notes');
    }
    stage.addEventListener('keydown', event => {
      if (['ArrowRight', 'PageDown', ' '].includes(event.key)) { event.preventDefault(); go(at + 1); }
      else if (['ArrowLeft', 'PageUp'].includes(event.key)) { event.preventDefault(); go(at - 1); }
      else if (event.key === 'Home') { event.preventDefault(); go(0); }
      else if (event.key === 'End') { event.preventDefault(); go(slides.length - 1); }
    });
    stage.addEventListener('click', event => { if (document.fullscreenElement === stage && !event.target.closest('a')) go(at + 1); });
    wrap.append(bar, stage, editor, notes, strip);
    paint();
    return wrap;
  }
  const actionsRow = (...nodes) => { const row = el('div', 'ar-actions'); row.append(...nodes); return row; };

  /* ---- spreadsheets ---- */
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
  const SHEET_ROWS = 2000, SHEET_COLS = 60;
  // A ```chart block starts with lines such as "type: bar" and "title: Sales"; the rest is CSV
  const CHART_KEYS = /^\s*(type|title|x|y|stacked)\s*:\s*(.*)$/i;
  function chartSpec(text) {
    const lines = withoutMarker(text).replace(/^\uFEFF/, '').split('\n'), spec = {};
    while (lines.length && (CHART_KEYS.test(lines[0]) || !lines[0].trim())) { const m = CHART_KEYS.exec(lines.shift()); if (m) spec[m[1].toLowerCase()] = m[2].trim(); }
    return {spec, body: lines.join('\n')};
  }
  function gridOf(text, lang) {
    const body = chartSpec(text).body;
    const first = body.split('\n', 1)[0];
    const delimiter = lang === 'tsv' || (first.includes('\t') && !first.includes(',')) ? '\t' : first.split(';').length > first.split(',').length ? ';' : ',';
    const rows = parseDelimited(body.replace(/\n+$/, ''), delimiter, SHEET_ROWS + 1);
    const cols = Math.min(SHEET_COLS, Math.max(1, ...rows.map(r => r.length)));
    return {rows: rows.slice(0, SHEET_ROWS).map(r => Array.from({length: cols}, (_, c) => r[c] ?? '')), cut: rows.length > SHEET_ROWS || rows.some(r => r.length > SHEET_COLS), delimiter};
  }
  const toCsv = (rows, delimiter = ',') => rows.map(r => r.map(v => { const s = String(v ?? ''); return s.includes(delimiter) || /["\n\r]/.test(s) || /^\s|\s$/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }).join(delimiter)).join('\n') + '\n';
  const colName = c => { let s = ''; c++; while (c > 0) { const m = (c - 1) % 26; s = String.fromCharCode(65 + m) + s; c = Math.floor((c - 1) / 26); } return s; };
  const colIndex = name => { let c = 0; for (const ch of name.toUpperCase()) c = c * 26 + ch.charCodeAt(0) - 64; return c - 1; };
  // "1,234.5", "12 %", "€5", "1 234,5": what a person would read as a number
  function toNumber(value) {
    if (typeof value === 'number') return value;
    if (typeof value === 'boolean') return value ? 1 : 0;
    let s = String(value ?? '').trim();
    if (!s) return null;
    let pct = false;
    if (s.endsWith('%')) { pct = true; s = s.slice(0, -1).trim(); }
    s = s.replace(/^[$€£¥₹]\s*|\s*[$€£¥₹]$/g, '').replace(/[\s  ]/g, '');
    if (/^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, '');
    else if (/^-?\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) s = s.replace(/\./g, '').replace(',', '.');
    else if (/^-?\d+,\d+$/.test(s)) s = s.replace(',', '.');
    if (!/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(s)) return null;
    const v = parseFloat(s);
    return Number.isFinite(v) ? (pct ? v / 100 : v) : null;
  }
  class FormulaError extends Error {}
  // A small formula engine: references, ranges, arithmetic, comparisons and common functions. Nothing is passed to eval.
  function sheetEngine(rows) {
    const cache = new Map(), busy = new Set();
    const raw = (r, c) => rows[r]?.[c] ?? '';
    function value(r, c) {
      const id = r + ':' + c;
      if (cache.has(id)) return cache.get(id);
      const text = String(raw(r, c));
      if (!text.startsWith('=') || text.length < 2) { const num = toNumber(text); const v = num != null && text.trim() ? num : text; cache.set(id, v); return v; }
      if (busy.has(id)) return '#CYCLE!';
      busy.add(id);
      let v;
      try { v = evaluate(text.slice(1)); if (typeof v === 'number' && !Number.isFinite(v)) v = '#NUM!'; }
      catch (error) { v = error instanceof FormulaError ? error.message : '#ERROR!'; }
      busy.delete(id); cache.set(id, v);
      return v;
    }
    function evaluate(src) {
      const tokens = src.match(/\s+|"(?:[^"]|"")*"|\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?|[A-Za-z]{1,3}:[A-Za-z]{1,3}|[A-Za-z_][A-Za-z0-9_.]*|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+|<>|<=|>=|[-+*/^&=<>(),;%:]/g)?.filter(tok => !/^\s+$/.test(tok)) || [];
      if (tokens.join('').replace(/\s/g, '') !== src.replace(/\s/g, '')) throw new FormulaError('#ERROR!');
      let p = 0;
      const peek = () => tokens[p], take = () => tokens[p++];
      const expect = tok => { if (take() !== tok) throw new FormulaError('#ERROR!'); };
      const num = v => { if (Array.isArray(v)) v = v[0]; if (typeof v === 'string' && v.startsWith('#')) throw new FormulaError(v); const x = v === '' ? 0 : toNumber(v); if (x == null) throw new FormulaError('#VALUE!'); return x; };
      const cmp = () => { let a = cat(); while (['=', '<>', '<', '>', '<=', '>='].includes(peek())) { const op = take(), b = cat(); const x = toNumber(a), y = toNumber(b); const [l, r] = x != null && y != null ? [x, y] : [String(a).toLowerCase(), String(b).toLowerCase()]; a = op === '=' ? l === r : op === '<>' ? l !== r : op === '<' ? l < r : op === '>' ? l > r : op === '<=' ? l <= r : l >= r; } return a; };
      const cat = () => { let a = add(); while (peek() === '&') { take(); a = String(scalar(a)) + String(scalar(add())); } return a; };
      const add = () => { let a = mul(); while (peek() === '+' || peek() === '-') { const op = take(), b = mul(); a = op === '+' ? num(a) + num(b) : num(a) - num(b); } return a; };
      const mul = () => { let a = pow(); while (peek() === '*' || peek() === '/') { const op = take(), b = pow(); if (op === '/' && num(b) === 0) throw new FormulaError('#DIV/0!'); a = op === '*' ? num(a) * num(b) : num(a) / num(b); } return a; };
      const pow = () => { let a = unary(); while (peek() === '^') { take(); a = Math.pow(num(a), num(unary())); } return a; };
      const unary = () => { if (peek() === '-') { take(); return -num(unary()); } if (peek() === '+') { take(); return num(unary()); } let v = primary(); if (peek() === '%') { take(); v = num(v) / 100; } return v; };
      const scalar = v => Array.isArray(v) ? (v[0] ?? '') : v;
      function range(tok) {
        const [a, b] = tok.replace(/\$/g, '').split(':');
        const pa = /^([A-Za-z]+)(\d*)$/.exec(a), pb = /^([A-Za-z]+)(\d*)$/.exec(b);
        if (!pa || !pb) throw new FormulaError('#REF!');
        const c1 = colIndex(pa[1]), c2 = colIndex(pb[1]);
        const r1 = pa[2] ? +pa[2] - 1 : 1, r2 = pb[2] ? +pb[2] - 1 : rows.length - 1;
        const out = [];
        for (let r = Math.min(r1, r2); r <= Math.max(r1, r2) && r < rows.length; r++) for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c++) { if (c >= (rows[0]?.length || 0)) throw new FormulaError('#REF!'); out.push(value(r, c)); }
        return out;
      }
      // Moves past one argument, minding brackets, to the separator or bracket that ends it
      function skipArg() { let depth = 0; while (p < tokens.length) { const tok = peek(); if (depth === 0 && (tok === ',' || tok === ';' || tok === ')')) return; if (tok === '(') depth++; if (tok === ')') depth--; p++; } }
      function primary() {
        const tok = take();
        if (tok == null) throw new FormulaError('#ERROR!');
        if (tok === '(') { const v = cmp(); expect(')'); return v; }
        if (tok[0] === '"') return tok.slice(1, -1).replace(/""/g, '"');
        if (/^(\d|\.)/.test(tok)) return parseFloat(tok);
        if (/:/.test(tok)) return range(tok);
        if (/^\$?[A-Za-z]{1,3}\$?\d+$/.test(tok)) { const m = /^\$?([A-Za-z]+)\$?(\d+)$/.exec(tok); const r = +m[2] - 1, c = colIndex(m[1]); if (r < 0 || r >= rows.length || c >= (rows[0]?.length || 0)) throw new FormulaError('#REF!'); return value(r, c); }
        const name = tok.toUpperCase();
        if (name === 'TRUE' || name === 'FALSE') return name === 'TRUE';
        if (peek() !== '(') throw new FormulaError('#NAME?');
        take();
        // IFERROR(value, fallback): an error in the first argument, raised or held in a cell, gives the fallback
        if (name === 'IFERROR') {
          const start = p;
          let v, failed = false;
          try { v = scalar(cmp()); failed = typeof v === 'string' && /^#[A-Z/0]+[!?]$/.test(v); } catch (error) { if (!(error instanceof FormulaError)) throw error; failed = true; p = start; skipArg(); }
          if (peek() !== ',' && peek() !== ';') throw new FormulaError('#ERROR!');
          take();
          const fallback = cmp(); expect(')');
          return failed ? scalar(fallback) : v;
        }
        const args = [];
        if (peek() !== ')') { do { args.push(cmp()); } while ((peek() === ',' || peek() === ';') && take()); }
        expect(')');
        return call(name, args);
      }
      function call(name, args) {
        const flat = () => args.flat();
        // As in Excel, an error in a range is the result of adding it up
        const nums = () => { const values = flat(); const bad = values.find(v => typeof v === 'string' && /^#[A-Z/0]+[!?]$/.test(v)); if (bad) throw new FormulaError(bad); return values.filter(v => v !== '' && typeof v !== 'boolean' && toNumber(v) != null).map(toNumber); };
        switch (name) {
          case 'SUM': return nums().reduce((a, b) => a + b, 0);
          case 'AVERAGE': case 'AVG': case 'MEAN': { const v = nums(); if (!v.length) throw new FormulaError('#DIV/0!'); return v.reduce((a, b) => a + b, 0) / v.length; }
          case 'MIN': { const v = nums(); return v.length ? Math.min(...v) : 0; }
          case 'MAX': { const v = nums(); return v.length ? Math.max(...v) : 0; }
          case 'MEDIAN': { const v = nums().sort((a, b) => a - b); if (!v.length) throw new FormulaError('#NUM!'); const m = v.length >> 1; return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2; }
          case 'COUNT': return flat().filter(v => v !== '' && typeof v !== 'boolean' && toNumber(v) != null).length;
          case 'COUNTA': return flat().filter(v => v !== '').length;
          case 'PRODUCT': return nums().reduce((a, b) => a * b, 1);
          case 'ROUND': { const d = args[1] == null ? 0 : num(args[1]); const f = Math.pow(10, d); return Math.round(num(args[0]) * f) / f; }
          case 'ABS': return Math.abs(num(args[0]));
          case 'SQRT': { const x = num(args[0]); if (x < 0) throw new FormulaError('#NUM!'); return Math.sqrt(x); }
          case 'POWER': return Math.pow(num(args[0]), num(args[1]));
          case 'INT': return Math.floor(num(args[0]));
          case 'MOD': { const d = num(args[1]); if (!d) throw new FormulaError('#DIV/0!'); return num(args[0]) - d * Math.floor(num(args[0]) / d); }
          case 'IF': { const c = scalar(args[0]); return (typeof c === 'boolean' ? c : num(c) !== 0) ? scalar(args[1] ?? true) : scalar(args[2] ?? false); }
          case 'AND': return flat().every(v => typeof v === 'boolean' ? v : num(v) !== 0);
          case 'OR': return flat().some(v => typeof v === 'boolean' ? v : num(v) !== 0);
          case 'NOT': { const c = scalar(args[0]); return !(typeof c === 'boolean' ? c : num(c) !== 0); }
          case 'CONCAT': case 'CONCATENATE': return flat().map(String).join('');
          case 'LEN': return String(scalar(args[0])).length;
          case 'UPPER': return String(scalar(args[0])).toUpperCase();
          case 'LOWER': return String(scalar(args[0])).toLowerCase();
          case 'ROUNDUP': case 'ROUNDDOWN': { const d = args[1] == null ? 0 : num(args[1]); const f = Math.pow(10, d), x = num(args[0]) * f; return (name === 'ROUNDUP' ? Math.sign(x) * Math.ceil(Math.abs(x) - 1e-9) : Math.trunc(x)) / f; }
          case 'COUNTIF': case 'SUMIF': case 'AVERAGEIF': {
            const range = [].concat(args[0]), test = criterion(scalar(args[1])), values = args[2] == null ? range : [].concat(args[2]);
            const hits = range.map((v, i) => test(v) ? values[i] : undefined).filter(v => v !== undefined);
            if (name === 'COUNTIF') return hits.length;
            const nums = hits.map(toNumber).filter(v => v != null);
            if (name === 'SUMIF') return nums.reduce((a, b) => a + b, 0);
            if (!nums.length) throw new FormulaError('#DIV/0!');
            return nums.reduce((a, b) => a + b, 0) / nums.length;
          }
          default: throw new FormulaError('#NAME?');
        }
      }
      // ">10", "<=5", "<>x", "=Paris" or a plain value, as COUNTIF and SUMIF read them
      function criterion(c) {
        const m = /^(<=|>=|<>|<|>|=)?(.*)$/s.exec(String(c ?? '')), op = m[1] || '=', want = m[2], wantN = toNumber(want);
        return v => { const n = toNumber(v); if (wantN != null && n != null) return op === '=' ? n === wantN : op === '<>' ? n !== wantN : op === '<' ? n < wantN : op === '>' ? n > wantN : op === '<=' ? n <= wantN : n >= wantN; const a = String(v ?? '').toLowerCase(), b = want.toLowerCase(); return op === '=' ? a === b : op === '<>' ? a !== b : false; };
      }
      const result = cmp();
      if (p < tokens.length) throw new FormulaError('#ERROR!');
      return scalar(result);
    }
    return {value, reset: () => cache.clear()};
  }
  const shown = v => typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString(lang) : v.toLocaleString(lang, {maximumFractionDigits: 6})) : typeof v === 'boolean' ? (v ? 'TRUE' : 'FALSE') : String(v ?? '');
  // A spreadsheet you work in as in Excel or Claude: click a cell and type to correct it, double-click or F2 to edit
  // what is there, Enter and Tab to move on, arrows to move, Shift to select a range, Delete to clear, Ctrl+C / Ctrl+V
  // to copy and paste blocks, Ctrl+Z / Ctrl+Y to undo and redo. Formulas recalculate at once.
  function sheetView(text, lang0, ctx, editable, commit, displayTitleText = '', onAsk = null) {
    const grid = gridOf(text, lang0);
    let rows = grid.rows.length ? grid.rows : [['']];
    let engine = sheetEngine(rows);
    let sortCol = -1, sortDir = 1, filter = '', chartCol = -1;
    let mode = ctx.sheetMode || (lang0 === 'chart' ? 'chart' : 'table');
    let chartKind = ['bar', 'line', 'area', 'pie'].includes(String(chartSpec(text).spec.type).toLowerCase()) ? String(chartSpec(text).spec.type).toLowerCase() : 'bar';
    // Selection in display order: an anchor and a focus cell; the active cell is the focus
    let anchor = {d: Math.min(1, rows.length - 1), c: 0}, focus = {...anchor};
    const undo = [], redo = [];
    // A cell is being looked at, or being edited
    const cell = machine('cell', 'select', {select: {edit: 'edit'}, edit: {commit: 'select', cancel: 'select'}});
    let editor = null;
    const wrap = el('div', 'ar-sheet');
    const bar = el('div', 'ar-subbar ar-sheetbar');
    const refBox = el('span', 'ar-ref');
    const formula = el('input', 'ar-formula'); formula.type = 'text'; formula.placeholder = t('formula_hint'); formula.readOnly = !editable; formula.dir = 'auto'; setLabel(formula, t('cell', {ref: 'A1'}));
    const search = el('input', 'ar-search ar-filter'); search.type = 'search'; search.placeholder = t('filter_rows'); search.setAttribute('aria-label', t('filter_rows'));
    const tableButton = textButton(t('table'), () => { mode = 'table'; ctx.sheetMode = mode; paint(); }, 'ar-btn ar-chip');
    const chartButton = textButton(t('chart'), () => { mode = 'chart'; ctx.sheetMode = mode; paint(); }, 'ar-btn ar-chip');
    chartButton.prepend(svgIcon('chart'));
    const tools = el('div', 'ar-sheettools');
    const tool = (label, icon, action) => { const b = iconButton(label, icon, action, 'ar-icon ar-tool'); tools.append(b); return b; };
    if (editable) {
      tool(t('add_row'), 'row_add', () => change(() => { const r = rowAt(focus.d) ?? rows.length - 1; rows.splice(r + 1, 0, rows[0].map(() => '')); }, {d: focus.d + 1, c: focus.c}));
      tool(t('del_row'), 'row_del', () => { const [d0, d1] = span('d'); if (d0 === 0 && d1 === 0) return; change(() => { const doomed = new Set(list().slice(Math.max(1, d0), d1 + 1)); rows = rows.filter((_, r) => !doomed.has(r)); if (rows.length < 2) rows.push(rows[0].map(() => '')); }); });
      tool(t('add_col'), 'col_add', () => change(() => { const c = focus.c + 1; rows.forEach((row, r) => row.splice(c, 0, r === 0 ? colName(c) : '')); }, {d: focus.d, c: focus.c + 1}));
      tool(t('del_col'), 'col_del', () => { const [c0, c1] = span('c'); if (rows[0].length <= c1 - c0 + 1) return; change(() => rows.forEach(row => row.splice(c0, c1 - c0 + 1))); });
      tool(t('undo'), 'undo', () => history(undo, redo));
      tool(t('redo'), 'redo', () => history(redo, undo));
    }
    const askButton = onAsk ? textButton(t('ask'), () => onAsk(rangeRef(), rangeText()), 'ar-btn ar-chip') : null;
    if (askButton) { askButton.prepend(svgIcon('sparkle')); tools.append(askButton); }
    bar.append(refBox, formula, tableButton, chartButton);
    const bar2 = el('div', 'ar-subbar ar-sheetbar2'); bar2.append(search, tools);
    const body = el('div', 'ar-sheetbody');
    const foot = el('div', 'ar-sheetfoot'); foot.setAttribute('aria-live', 'polite');
    wrap.append(bar, bar2, body, foot);
    let table = null, box = null, dragging = false;
    wrap.addEventListener('pointerup', () => { dragging = false; });
    wrap.addEventListener('pointerleave', () => { dragging = false; });

    // Display order: the header row, then the rows the filter keeps, sorted
    function list() {
      let idx = rows.map((_, i) => i).slice(1);
      if (filter) { const f = filter.toLocaleLowerCase(); idx = idx.filter(r => rows[r].some((_, c) => shown(engine.value(r, c)).toLocaleLowerCase().includes(f))); }
      if (sortCol >= 0) idx.sort((a, b) => { const x = engine.value(a, sortCol), y = engine.value(b, sortCol); const ex = x === '' || x == null, ey = y === '' || y == null; if (ex || ey) return ex === ey ? 0 : ex ? 1 : -1; const nx = typeof x === 'number', ny = typeof y === 'number'; return (nx && ny ? x - y : nx ? -1 : ny ? 1 : String(x).localeCompare(String(y), lang, {numeric: true})) * sortDir; });
      return [0, ...idx.slice(0, 500)];
    }
    let shownRows = list();
    const rowAt = d => shownRows[d];
    const span = axis => axis === 'd' ? [Math.min(anchor.d, focus.d), Math.max(anchor.d, focus.d)] : [Math.min(anchor.c, focus.c), Math.max(anchor.c, focus.c)];
    const refOf = (d, c) => colName(c) + (rowAt(d) + 1);
    const rangeRef = () => { const [d0, d1] = span('d'), [c0, c1] = span('c'); return d0 === d1 && c0 === c1 ? refOf(d0, c0) : refOf(d0, c0) + ':' + refOf(d1, c1); };
    const rangeCells = () => { const [d0, d1] = span('d'), [c0, c1] = span('c'), out = []; for (let d = d0; d <= d1; d++) { const row = []; for (let c = c0; c <= c1; c++) row.push(rowAt(d)); out.push(row.map((r, k) => ({r, c: c0 + k}))); } return out; };
    const rangeText = () => rangeCells().map(row => row.map(({r, c}) => shown(engine.value(r, c))).join('\t')).join('\n');

    // Every change goes through here: it can be undone, it recalculates, and it is kept as your edit
    function change(apply, moveTo) {
      undo.push(JSON.stringify(rows)); if (undo.length > 100) undo.shift(); redo.length = 0;
      apply();
      rows = rows.map(row => Array.from({length: Math.max(...rows.map(r => r.length))}, (_, c) => row[c] ?? ''));
      save();
      if (moveTo) { anchor = {...moveTo}; focus = {...moveTo}; }
      paint();
    }
    function history(from, to) {
      if (!from.length) return;
      to.push(JSON.stringify(rows)); rows = JSON.parse(from.pop()); save(); paint();
    }
    // A chart's own lines (type, title) are kept above its data
    function save() { engine = sheetEngine(rows); const head = Object.entries(chartSpec(text).spec).map(([k, v]) => k + ': ' + v).join('\n'); commit((head ? head + '\n' : '') + toCsv(rows, grid.delimiter === '\t' ? '\t' : ',')); }

    function paintSelection() {
      if (!table) return;
      const [d0, d1] = span('d'), [c0, c1] = span('c');
      for (const td of table.querySelectorAll('td.ar-sel, td.ar-cur')) td.classList.remove('ar-sel', 'ar-cur');
      for (const td of table.querySelectorAll('td[data-d]')) { const d = +td.dataset.d, c = +td.dataset.c; if (d >= d0 && d <= d1 && c >= c0 && c <= c1) td.classList.add('ar-sel'); if (d === focus.d && c === focus.c) { td.classList.add('ar-cur'); td.setAttribute('aria-selected', 'true'); } else td.setAttribute('aria-selected', String(td.classList.contains('ar-sel'))); }
      const r = rowAt(focus.d);
      refBox.textContent = rangeRef(); setLabel(formula, t('cell', {ref: refOf(focus.d, focus.c)}));
      if (!cell.is('edit')) formula.value = rows[r]?.[focus.c] ?? '';
      table.querySelector('td.ar-cur')?.scrollIntoView({block: 'nearest', inline: 'nearest'});
      stats();
    }
    function stats() {
      const [d0, d1] = span('d'), [c0, c1] = span('c'), single = d0 === d1 && c0 === c1;
      const values = [];
      if (single) rows.slice(1).forEach((_, i) => { const v = engine.value(i + 1, focus.c); if (typeof v === 'number') values.push(v); });
      else for (const row of rangeCells()) for (const {r, c} of row) { const v = engine.value(r, c); if (typeof v === 'number') values.push(v); }
      foot.replaceChildren();
      if (!single) foot.append(el('span', 'ar-stat ar-statref', rangeRef()));
      if (!values.length) return;
      const sum = values.reduce((a, b) => a + b, 0);
      for (const [label, v] of [[t('sum'), sum], [t('avg'), sum / values.length], [t('min'), Math.min(...values)], [t('max'), Math.max(...values)], [t('count'), values.length]]) { const item = el('span', 'ar-stat'); item.append(el('span', null, label + ' '), el('strong', null, shown(Math.round(v * 1e6) / 1e6))); foot.append(item); }
    }
    function select(d, c, extend) {
      d = clamp(d, 0, shownRows.length - 1); c = clamp(c, 0, rows[0].length - 1);
      focus = {d, c}; if (!extend) anchor = {d, c};
      paintSelection();
    }
    function startEdit(initial) {
      if (!editable || !cell.send('edit')) return;
      const td = table.querySelector('td.ar-cur'); if (!td) { cell.send('cancel'); return; }
      const r = rowAt(focus.d);
      editor = el('input', 'ar-celledit'); editor.type = 'text'; editor.dir = 'auto'; setLabel(editor, t('cell', {ref: refOf(focus.d, focus.c)}));
      editor.value = initial ?? rows[r]?.[focus.c] ?? '';
      td.classList.add('ar-editing'); td.append(editor);
      editor.focus(); if (initial == null) editor.select(); else editor.setSelectionRange(editor.value.length, editor.value.length);
      formula.value = editor.value;
      editor.addEventListener('input', () => { formula.value = editor.value; });
      editor.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); finishEdit(true, {d: focus.d + (event.shiftKey ? -1 : 1), c: focus.c}); }
        else if (event.key === 'Tab') { event.preventDefault(); finishEdit(true, {d: focus.d, c: focus.c + (event.shiftKey ? -1 : 1)}); }
        else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finishEdit(false); }
      });
      editor.addEventListener('blur', () => { if (cell.is('edit')) finishEdit(true); });
    }
    function finishEdit(keep, moveTo) {
      if (!cell.is('edit')) return;
      const value = editor?.value ?? '', r = rowAt(focus.d), c = focus.c;
      cell.send(keep ? 'commit' : 'cancel');
      editor?.remove(); editor = null;
      const target = moveTo ? {d: clamp(moveTo.d, 0, shownRows.length - 1), c: clamp(moveTo.c, 0, rows[0].length - 1)} : {...focus};
      if (keep && value !== (rows[r]?.[c] ?? '')) change(() => { rows[r][c] = value; }, target);
      else { paint(); select(target.d, target.c); }
      box?.focus({preventScroll: true});
    }
    // Pasting a block of cells, from a spreadsheet or tab- or comma-separated text, grows the sheet as needed
    function paste(textIn) {
      if (!editable || !textIn) return;
      const delim = textIn.includes('\t') ? '\t' : (textIn.includes(',') && textIn.includes('\n') ? ',' : '\t');
      const block = parseDelimited(textIn.replace(/\r\n?/g, '\n').replace(/\n$/, ''), delim, 2000);
      const startR = rowAt(focus.d), startC = focus.c;
      change(() => {
        block.forEach((line, i) => { const r = startR + i; while (r >= rows.length) rows.push(rows[0].map(() => '')); line.forEach((v, k) => { const c = startC + k; while (c >= rows[0].length) rows.forEach((row, j) => row.push(j === 0 ? colName(row.length) : '')); rows[r][c] = v; }); });
      });
    }
    function clearRange() { if (!editable) return; const cells = rangeCells().flat(); if (cells.every(({r, c}) => !rows[r][c])) return; change(() => cells.forEach(({r, c}) => { rows[r][c] = ''; })); }

    function paintTable() {
      shownRows = list();
      box = el('div', 'ar-tablebox ar-gridbox'); box.tabIndex = 0; box.setAttribute('role', 'application'); setLabel(box, t('sheet_keys'));
      table = el('table', 'ar-table ar-grid'); table.setAttribute('role', 'grid');
      const head = table.createTHead(), letters = el('tr');
      letters.append(el('th', 'ar-corner'));
      rows[0].forEach((_, c) => {
        const th = el('th', 'ar-colhead');
        const b = el('button', 'ar-sortbtn', colName(c) + (sortCol === c ? (sortDir > 0 ? ' ↑' : ' ↓') : '')); b.type = 'button'; b.tabIndex = -1;
        setLabel(b, (sortCol === c && sortDir > 0 ? t('sort_desc') : t('sort_asc')) + ' · ' + (shown(engine.value(0, c)) || colName(c)));
        b.addEventListener('click', () => { if (sortCol === c) sortDir = -sortDir; else { sortCol = c; sortDir = 1; } paint(); });
        if (sortCol === c) th.setAttribute('aria-sort', sortDir > 0 ? 'ascending' : 'descending');
        th.append(b); letters.append(th);
      });
      head.append(letters);
      const tbody = table.createTBody();
      shownRows.forEach((r, d) => {
        const tr = el('tr'); if (r === 0) tr.className = 'ar-headrow';
        tr.append(el('th', 'ar-rownum', String(r + 1)));
        rows[r].forEach((raw, c) => {
          const v = engine.value(r, c);
          const td = el('td', null, shown(v)); td.dir = 'auto'; td.dataset.d = d; td.dataset.c = c; td.dataset.r = r; td.setAttribute('role', 'gridcell');
          if (typeof v === 'number') td.classList.add('ar-num');
          if (typeof v === 'string' && /^#[A-Z/0]+[!?]$/.test(v)) td.classList.add('ar-errcell');
          if (String(raw).startsWith('=')) { td.classList.add('ar-fx'); td.title = raw; }
          tr.append(td);
        });
        tbody.append(tr);
      });
      // Pointer: click selects, Shift-click and dragging select a range, double-click edits
      table.addEventListener('pointerdown', event => {
        const td = event.target.closest('td[data-d]'); if (!td || event.button !== 0) return;
        if (cell.is('edit')) finishEdit(true);
        select(+td.dataset.d, +td.dataset.c, event.shiftKey); dragging = true; box.focus({preventScroll: true});
      });
      table.addEventListener('pointerover', event => { if (!dragging) return; const td = event.target.closest('td[data-d]'); if (td) select(+td.dataset.d, +td.dataset.c, true); });
      table.addEventListener('dblclick', event => { if (event.target.closest('td[data-d]')) startEdit(); });
      box.addEventListener('keydown', event => {
        if (cell.is('edit') || event.target !== box) return;
        const mod = event.ctrlKey || event.metaKey, k = event.key;
        const move = (dd, dc) => { event.preventDefault(); select(focus.d + dd, focus.c + dc, event.shiftKey); };
        if (k === 'ArrowUp') move(-1, 0); else if (k === 'ArrowDown') move(1, 0); else if (k === 'ArrowLeft') move(0, -1); else if (k === 'ArrowRight') move(0, 1);
        else if (k === 'Tab') move(0, event.shiftKey ? -1 : 1);
        else if (k === 'Enter') { event.preventDefault(); editable ? startEdit() : select(focus.d + 1, focus.c); }
        else if (k === 'F2') { event.preventDefault(); startEdit(); }
        else if (k === 'Home') { event.preventDefault(); select(mod ? 0 : focus.d, 0, event.shiftKey); }
        else if (k === 'End') { event.preventDefault(); select(mod ? shownRows.length - 1 : focus.d, rows[0].length - 1, event.shiftKey); }
        else if (k === 'Delete' || k === 'Backspace') { event.preventDefault(); clearRange(); }
        else if (mod && k.toLowerCase() === 'a') { event.preventDefault(); anchor = {d: 0, c: 0}; focus = {d: shownRows.length - 1, c: rows[0].length - 1}; paintSelection(); }
        else if (mod && k.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? history(redo, undo) : history(undo, redo); }
        else if (mod && k.toLowerCase() === 'y') { event.preventDefault(); history(redo, undo); }
        else if (k === 'Escape' && (anchor.d !== focus.d || anchor.c !== focus.c)) { event.preventDefault(); event.stopPropagation(); select(focus.d, focus.c); }
        else if (!mod && !event.altKey && k.length === 1 && editable) { event.preventDefault(); startEdit(k); }
      });
      box.addEventListener('copy', event => { if (cell.is('edit')) return; event.preventDefault(); event.clipboardData.setData('text/plain', rangeText()); toast(t('copied')); });
      box.addEventListener('cut', event => { if (cell.is('edit') || !editable) return; event.preventDefault(); event.clipboardData.setData('text/plain', rangeText()); clearRange(); });
      box.addEventListener('paste', event => { if (cell.is('edit')) return; event.preventDefault(); paste(event.clipboardData.getData('text/plain')); });
      box.append(table);
      body.replaceChildren(box);
      const total = rows.length - 1;
      if (shownRows.length - 1 < total) body.append(el('p', 'ar-muted ar-small', t('rows_shown', {n: n(shownRows.length - 1), total: n(total)})));
      if (grid.cut) body.append(el('p', 'ar-muted ar-small', t('rows_cols', {rows: n(SHEET_ROWS), cols: SHEET_COLS}) + ' max'));
    }
    function paintChart() {
      table = null; box = null;
      shownRows = list();
      const numeric = rows[0].map((_, c) => c).filter(c => { const vals = rows.slice(1).map((_, r) => engine.value(r + 1, c)).filter(v => v !== ''); return vals.length && vals.filter(v => typeof v === 'number').length / vals.length >= 0.8; });
      body.replaceChildren();
      if (!numeric.length) { body.append(el('p', 'ar-muted', t('no_numbers'))); return; }
      const labelCol = rows[0].map((_, c) => c).find(c => !numeric.includes(c)) ?? -1;
      if (chartCol !== 'all' && !numeric.includes(chartCol)) chartCol = lang0 === 'chart' || numeric.length <= 4 ? 'all' : numeric.find(c => c !== 0) ?? numeric[0];
      const controls = el('div', 'ar-chartbar');
      const pick = el('select', 'ar-select'); setLabel(pick, t('chart'));
      if (numeric.length > 1) { const o = el('option', null, t('all_columns')); o.value = 'all'; pick.append(o); }
      for (const c of numeric) { const o = el('option', null, shown(engine.value(0, c)) || colName(c)); o.value = c; pick.append(o); }
      if (numeric.length === 1) chartCol = numeric[0];
      pick.value = String(chartCol); pick.addEventListener('change', () => { chartCol = pick.value === 'all' ? 'all' : +pick.value; paintChart(); });
      const kind = el('select', 'ar-select'); setLabel(kind, t('chart'));
      for (const [v, label] of [['bar', t('chart_bar')], ['line', t('chart_line')], ['area', t('chart_area')], ['pie', t('chart_pie')]]) { const o = el('option', null, label); o.value = v; kind.append(o); }
      kind.value = chartKind; kind.addEventListener('change', () => { chartKind = kind.value; paintChart(); });
      controls.append(pick, kind);
      const cols = chartCol === 'all' ? numeric : [chartCol];
      const order = shownRows.slice(1, 81);
      const data = {labels: order.map(r => labelCol >= 0 ? shown(engine.value(r, labelCol)) : String(r + 1)), series: cols.map(c => ({name: shown(engine.value(0, c)) || colName(c), values: order.map(r => typeof engine.value(r, c) === 'number' ? engine.value(r, c) : 0)}))};
      const title = chartSpec(text).spec.title || (cols.length === 1 ? t('chart_of', {col: data.series[0].name, by: labelCol >= 0 ? shown(engine.value(0, labelCol)) : '#'}) : displayTitleText);
      const svgNode = chartSvg(data, chartKind, title);
      body.append(controls, svgNode);
      ctx.chartSvg = svgNode;
    }
    function paint() {
      // The grid is drawn again after a change; the keyboard stays with it
      const hadFocus = !!box && (document.activeElement === box || box.contains(document.activeElement));
      tableButton.setAttribute('aria-pressed', String(mode === 'table')); chartButton.setAttribute('aria-pressed', String(mode === 'chart'));
      bar2.hidden = mode !== 'table'; formula.disabled = mode !== 'table';
      if (mode === 'chart') { paintChart(); foot.replaceChildren(); return; }
      paintTable();
      focus = {d: clamp(focus.d, 0, shownRows.length - 1), c: clamp(focus.c, 0, rows[0].length - 1)};
      anchor = {d: clamp(anchor.d, 0, shownRows.length - 1), c: clamp(anchor.c, 0, rows[0].length - 1)};
      paintSelection();
      if (hadFocus) box?.focus({preventScroll: true});
    }
    search.addEventListener('input', () => { filter = search.value.trim(); anchor = focus = {d: Math.min(1, focus.d), c: focus.c}; paint(); });
    formula.addEventListener('keydown', event => {
      if (event.key === 'Enter' && editable) { event.preventDefault(); const r = rowAt(focus.d), c = focus.c; if (formula.value !== (rows[r]?.[c] ?? '')) change(() => { rows[r][c] = formula.value; }); box?.focus({preventScroll: true}); }
      else if (event.key === 'Escape') { formula.value = rows[rowAt(focus.d)]?.[focus.c] ?? ''; box?.focus({preventScroll: true}); }
    });
    formula.addEventListener('input', () => { if (cell.is('edit') && editor) editor.value = formula.value; });
    paint();
    ctx.rowsNow = () => rows; ctx.valueOf = (r, c) => engine.value(r, c);
    return wrap;
  }
  // Bar, line, area and pie charts, drawn as SVG elements: labels go in as text, and colours are written on the
  // elements themselves so a downloaded chart looks the same on its own
  const PALETTE = ['#2563eb', '#16a34a', '#d97706', '#db2777', '#7c3aed', '#0891b2', '#dc2626', '#65a30d'];
  function chartSvg(data, kind, caption) {
    const NS = 'http://www.w3.org/2000/svg', W = 720, H = 380, L = 64, R = 16, T = 34, legendH = data.series.length > 1 || kind === 'pie' ? 26 : 0, B = 70 + legendH;
    const mk = (tag, attrs, text) => { const node = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); if (text != null) node.textContent = text; return node; };
    const svgNode = mk('svg', {viewBox: `0 0 ${W} ${H}`, class: 'ar-chart', role: 'img', 'aria-label': caption, xmlns: NS});
    svgNode.append(mk('title', {}, caption), mk('text', {x: L, y: 18, class: 'ar-caption'}, caption));
    const labels = data.labels.map(x => String(x)), short = x => x.length > 14 ? x.slice(0, 13) + '…' : x;
    const legend = names => { let x = L; names.forEach((name, i) => { svgNode.append(mk('rect', {x, y: H - 20, width: 10, height: 10, rx: 2, fill: PALETTE[i % PALETTE.length]}), mk('text', {x: x + 14, y: H - 11, class: 'ar-axis'}, short(name))); x += 26 + Math.min(14, name.length) * 7; }); };
    if (kind === 'pie') {
      const values = data.series[0]?.values || [], total = values.reduce((a, b) => a + Math.max(0, b), 0) || 1;
      const cx = W / 2, cy = T + (H - T - legendH - 10) / 2, r = Math.min(cx - L, (H - T - legendH - 20) / 2);
      let angle = -Math.PI / 2;
      values.forEach((v, i) => {
        const part = Math.max(0, v) / total, next = angle + part * Math.PI * 2, color = PALETTE[i % PALETTE.length];
        if (part >= 0.9999) svgNode.append(mk('circle', {cx, cy, r, fill: color}));
        else if (part > 0) svgNode.append(mk('path', {d: `M${cx},${cy} L${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)} A${r},${r} 0 ${part > 0.5 ? 1 : 0} 1 ${cx + r * Math.cos(next)},${cy + r * Math.sin(next)} Z`, fill: color, stroke: '#fff', 'stroke-width': 1.5}));
        const mid = (angle + next) / 2;
        if (part >= 0.04) svgNode.append(mk('text', {x: cx + r * 0.65 * Math.cos(mid), y: cy + r * 0.65 * Math.sin(mid) + 4, 'text-anchor': 'middle', fill: '#fff', 'font-size': 12, 'font-weight': 600}, Math.round(part * 100) + '%'));
        angle = next;
      });
      legend(labels);
      return svgNode;
    }
    const all = data.series.flatMap(s => s.values);
    const max = Math.max(0, ...all), min = Math.min(0, ...all);
    const span = max - min || 1, y = v => T + (H - T - B) * (1 - (v - min) / span);
    for (let k = 0; k <= 4; k++) { const v = min + span * k / 4, yy = y(v); svgNode.append(mk('line', {x1: L, x2: W - R, y1: yy, y2: yy, class: 'ar-gridline', stroke: '#e5e5e5'}), mk('text', {x: L - 8, y: yy + 4, 'text-anchor': 'end', class: 'ar-axis'}, shown(Math.round(v * 100) / 100))); }
    const step = (W - L - R) / Math.max(1, labels.length), n = data.series.length;
    labels.forEach((label, i) => {
      const x = L + step * i;
      if (labels.length <= 30 || i % Math.ceil(labels.length / 30) === 0) svgNode.append(mk('text', {x: x + step / 2, y: H - B + 16, 'text-anchor': 'end', transform: `rotate(-35 ${x + step / 2} ${H - B + 16})`, class: 'ar-axis'}, short(label)));
    });
    data.series.forEach((series, k) => {
      const color = PALETTE[k % PALETTE.length];
      if (kind === 'bar') series.values.forEach((v, i) => { const w = step * 0.7 / n; svgNode.append(mk('rect', {x: L + step * i + step * 0.15 + w * k, width: Math.max(1, w - 1), y: Math.min(y(v), y(0)), height: Math.max(0.5, Math.abs(y(v) - y(0))), rx: 2, fill: color, class: 'ar-bar'})); });
      else {
        const points = series.values.map((v, i) => `${L + step * i + step / 2},${y(v)}`);
        if (kind === 'area' && points.length) svgNode.append(mk('polygon', {points: `${L + step / 2},${y(0)} ${points.join(' ')} ${L + step * (series.values.length - 1) + step / 2},${y(0)}`, fill: color, 'fill-opacity': n > 1 ? 0.25 : 0.35}));
        svgNode.append(mk('polyline', {points: points.join(' '), fill: 'none', stroke: color, 'stroke-width': 2.5, class: 'ar-line'}));
        series.values.forEach((v, i) => svgNode.append(mk('circle', {cx: L + step * i + step / 2, cy: y(v), r: 3, fill: color})));
      }
    });
    if (n > 1) legend(data.series.map(s => s.name));
    return svgNode;
  }
  // A ```chart block inside a document or a slide
  function chartFromText(text) {
    const {spec} = chartSpec(text), rows = gridOf(text, 'chart').rows, engine = sheetEngine(rows);
    const numeric = (rows[0] || []).map((_, c) => c).filter(c => c > 0 && rows.slice(1).some((_, r) => typeof engine.value(r + 1, c) === 'number'));
    if (rows.length < 2 || !numeric.length) return null;
    const data = {labels: rows.slice(1).map((_, r) => shown(engine.value(r + 1, 0))), series: numeric.map(c => ({name: shown(engine.value(0, c)), values: rows.slice(1).map((_, r) => typeof engine.value(r + 1, c) === 'number' ? engine.value(r + 1, c) : 0)}))};
    const kind = ['bar', 'line', 'area', 'pie'].includes(String(spec.type).toLowerCase()) ? String(spec.type).toLowerCase() : 'bar';
    return chartSvg(data, kind, spec.title || data.series.map(s => s.name).join(', '));
  }

  /* ---- data ---- */
  function jsonError(text, error) {
    const m = /position (\d+)/.exec(error.message || '');
    if (!m) return error.message;
    const pos = +m[1], before = text.slice(0, pos), line = before.split('\n').length, col = pos - before.lastIndexOf('\n');
    return `${error.message.replace(/\s*in JSON at position \d+.*/, '')} (${line}:${col})`;
  }
  function dataView(text, ctx) {
    const body = withoutMarker(text);
    const wrap = el('div', 'ar-data');
    let value;
    try { value = JSON.parse(body); } catch (error) { wrap.append(el('p', 'ar-alert', t('bad_json', {error: jsonError(body, error)}))); const pre = el('pre', 'ar-pre'); pre.textContent = body; wrap.append(pre); return wrap; }
    const tabular = Array.isArray(value) && value.length && value.every(row => row && typeof row === 'object' && !Array.isArray(row));
    let mode = ctx.dataMode && (ctx.dataMode !== 'table' || tabular) ? ctx.dataMode : 'tree';
    const bar = el('div', 'ar-subbar');
    const treeButton = textButton(t('tree'), () => { mode = 'tree'; ctx.dataMode = mode; paint(); });
    const tableButton = textButton(t('as_table'), () => { mode = 'table'; ctx.dataMode = mode; paint(); });
    const expand = textButton(t('expand'), () => holder.querySelectorAll('details').forEach(d => { d.open = true; fill(d); }));
    const collapse = textButton(t('collapse'), () => holder.querySelectorAll('details').forEach(d => { d.open = false; }));
    bar.append(treeButton); if (tabular) bar.append(tableButton); bar.append(el('span', 'ar-spacer'), expand, collapse);
    const holder = el('div', 'ar-tree');
    wrap.append(bar, holder);
    let budget = 0;
    const fill = details => { if (details.dataset.filled) return; details.dataset.filled = '1'; details._fill?.(); };
    function node(key, v, depth) {
      if (v && typeof v === 'object') {
        const details = el('details'); const summary = el('summary');
        const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
        if (key != null) summary.append(nameSpan('ar-key', String(key)), ': ');
        summary.append(el('span', 'ar-type', Array.isArray(v) ? '[ ] ' + t('json_items', {n: n(entries.length)}) : '{ } ' + t('json_keys', {n: n(entries.length)})));
        details.append(summary);
        details._fill = () => { const list = el('div', 'ar-children'); entries.slice(0, 500).forEach(([k, x]) => { if (budget++ < 20000) list.append(node(k, x, depth + 1)); }); if (entries.length > 500) list.append(el('p', 'ar-muted ar-small', '+ ' + n(entries.length - 500))); details.append(list); };
        details.addEventListener('toggle', () => { if (details.open) fill(details); });
        if (depth < 2) { details.open = true; fill(details); }
        return details;
      }
      const row = el('div', 'ar-leaf');
      if (key != null) row.append(nameSpan('ar-key', String(key)), ': ');
      const shownValue = v === null ? 'null' : typeof v === 'string' ? JSON.stringify(v) : String(v);
      const span = nameSpan('ar-val ar-' + (v === null ? 'null' : typeof v), shownValue.length > 2000 ? shownValue.slice(0, 2000) + '…' : shownValue);
      row.append(span);
      return row;
    }
    function paint() {
      treeButton.setAttribute('aria-pressed', String(mode === 'tree')); tableButton.setAttribute('aria-pressed', String(mode === 'table'));
      expand.hidden = collapse.hidden = mode !== 'tree';
      holder.replaceChildren();
      if (mode === 'table') {
        const keys = [...new Set(value.slice(0, 2000).flatMap(row => Object.keys(row)))].slice(0, SHEET_COLS);
        const csv = toCsv([keys, ...value.slice(0, SHEET_ROWS).map(row => keys.map(k => row[k] == null ? '' : typeof row[k] === 'object' ? JSON.stringify(row[k]) : String(row[k])))]);
        holder.append(sheetView(csv, 'csv', {}, false, () => {}));
      } else { budget = 0; holder.append(node(null, value, 0)); }
    }
    paint();
    ctx.tabular = tabular ? () => { const keys = [...new Set(value.flatMap(row => Object.keys(row)))]; return toCsv([keys, ...value.map(row => keys.map(k => row[k] == null ? '' : typeof row[k] === 'object' ? JSON.stringify(row[k]) : String(row[k])))]); } : null;
    return wrap;
  }

  /* ---- the code editor ---- */
  function codeView(text, editable, ctx, onChange) {
    const wrap = el('div', 'ar-editor');
    const gutter = el('div', 'ar-gutter'); gutter.setAttribute('aria-hidden', 'true');
    const area = el('textarea', 'ar-area'); area.value = text; area.spellcheck = false; area.readOnly = !editable;
    area.setAttribute('aria-label', t('code')); area.wrap = ctx.wrap ? 'soft' : 'off';
    area.setAttribute('autocapitalize', 'off'); area.setAttribute('autocomplete', 'off');
    if (/[֐-ࣿ]/.test(text.slice(0, 2000)) && !/[{};<>]/.test(text.slice(0, 2000))) area.dir = 'rtl';
    let lines = -1;
    const paintGutter = () => {
      const count = countLines(area.value) || 1;
      if (count !== lines) { lines = count; gutter.textContent = Array.from({length: Math.min(count, 100000)}, (_, i) => i + 1).join('\n'); }
      gutter.style.transform = `translateY(${-area.scrollTop}px)`;
    };
    area.addEventListener('scroll', paintGutter);
    area.addEventListener('input', () => { paintGutter(); onChange(area.value); });
    area.addEventListener('keydown', event => {
      if (event.key === 'Tab' && editable && !event.ctrlKey && !event.metaKey && !area.dataset.tabFree) {
        event.preventDefault();
        const {selectionStart: s, selectionEnd: e, value} = area;
        const from = value.lastIndexOf('\n', s - 1) + 1;
        if (event.shiftKey) {
          const block = value.slice(from, e), changed = block.replace(/^( {1,2}|\t)/gm, '');
          area.setRangeText(changed, from, e, 'select');
        } else if (s !== e && value.slice(s, e).includes('\n')) {
          const block = value.slice(from, e); area.setRangeText(block.replace(/^/gm, '  '), from, e, 'select');
        } else area.setRangeText('  ', s, e, 'end');
        area.dispatchEvent(new Event('input'));
      } else if (event.key === 'Escape') { area.dataset.tabFree = '1'; }
    });
    area.addEventListener('blur', () => { delete area.dataset.tabFree; });
    if (ctx.wrap) wrap.classList.add('ar-wrapped');
    wrap.append(gutter, area);
    requestAnimationFrame(paintGutter);
    ctx.area = area;
    return wrap;
  }

  /* ---- what changed between versions ---- */
  function diffLines(before, after) {
    const a = before.split('\n'), b = after.split('\n');
    let start = 0, endA = a.length, endB = b.length;
    while (start < endA && start < endB && a[start] === b[start]) start++;
    while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
    const rows = endA - start, cols = endB - start;
    if (rows * cols > 4e6) return null;
    const width = cols + 1, table = new Uint16Array((rows + 1) * width);
    for (let i = rows - 1; i >= 0; i--) for (let j = cols - 1; j >= 0; j--) table[i * width + j] = a[start + i] === b[start + j] ? table[(i + 1) * width + j + 1] + 1 : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    const ops = [];
    for (let k = 0; k < start; k++) ops.push([' ', a[k]]);
    let i = 0, j = 0;
    while (i < rows && j < cols) {
      if (a[start + i] === b[start + j]) { ops.push([' ', a[start + i]]); i++; j++; }
      else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) ops.push(['-', a[start + i++]]);
      else ops.push(['+', b[start + j++]]);
    }
    while (i < rows) ops.push(['-', a[start + i++]]);
    while (j < cols) ops.push(['+', b[start + j++]]);
    for (let k = endA; k < a.length; k++) ops.push([' ', a[k]]);
    return ops;
  }
  function diffView(before, after, label) {
    const wrap = el('div', 'ar-diff');
    const ops = diffLines(before, after);
    if (!ops) { wrap.append(el('p', 'ar-muted', t('diff_big'))); return wrap; }
    const added = ops.filter(([s]) => s === '+').length, removed = ops.filter(([s]) => s === '-').length;
    wrap.append(el('p', 'ar-muted ar-small', t('compare_with', {what: label}) + (added || removed ? ' · ' + t('diff_sum', {added, removed}) : '')));
    if (!added && !removed) { wrap.append(el('p', 'ar-muted', t('no_changes'))); return wrap; }
    const view = el('div', 'ar-difflines'); view.tabIndex = 0;
    const row = (sign, text) => { const line = el('div', 'ar-dline'); line.dataset.sign = sign === '+' ? 'add' : sign === '-' ? 'del' : 'same'; const mark = el('span', 'ar-dsign', sign === ' ' ? '' : sign); if (sign !== ' ') mark.setAttribute('aria-label', t(sign === '+' ? 'added' : 'removed')); line.append(mark, nameSpan('ar-dtext', text)); return line; };
    for (let k = 0; k < ops.length; k++) {
      if (ops[k][0] === ' ') {
        let run = k; while (run < ops.length && ops[run][0] === ' ') run++;
        const length = run - k, headN = k === 0 ? 0 : 3, tailN = run === ops.length ? 0 : 3;
        if (length > headN + tailN + 1) {
          for (let q = k; q < k + headN; q++) view.append(row(' ', ops[q][1]));
          view.append(el('div', 'ar-dskip', t('unchanged_n', {n: length - headN - tailN})));
          for (let q = run - tailN; q < run; q++) view.append(row(' ', ops[q][1]));
          k = run - 1; continue;
        }
      }
      view.append(row(ops[k][0], ops[k][1]));
    }
    wrap.append(view);
    return wrap;
  }

  /* ---- the panel ---- */
  const state = {route: route(), chat: null, artifacts: [], key: null, version: -1, mode: 'preview', modelId: '', query: '', loadedFor: null, loadedAt: 0, errors: new Map(), ctx: new Map()};
  // The panel: closed, showing the list, or showing one artifact. Leaving the viewer forgets which artifact it was.
  const view = machine('view', 'closed', {
    closed: {list: 'list', artifact: 'viewer'},
    list: {list: 'list', artifact: 'viewer', close: 'closed'},
    viewer: {artifact: 'viewer', back: 'list', list: 'list', gone: 'list', close: 'closed'}
  }, next => { if (next !== 'viewer') state.key = null; });
  // Reading the chat: a newer read always wins, and its outcome is one of these states
  const loader = machine('load', 'idle', {
    '*': {start: 'loading', none: 'idle'},
    loading: {done: 'ready', fail: 'failed', deny: 'denied'}
  });
  // Full screen is separate from what is shown
  const size = machine('size', 'normal', {normal: {toggle: 'full'}, full: {toggle: 'normal', reset: 'normal'}});
  const isLoading = () => loader.is('loading');
  let panel = null, toastEl = null, controller = null, bodyEl = null, headEl = null, footEl = null;
  const ctxOf = key => { if (!state.ctx.has(key)) state.ctx.set(key, {}); return state.ctx.get(key); };
  const current = () => state.artifacts.find(a => a.key === state.key) || null;
  const readOnly = () => state.route.kind !== 'chat';
  function toast(text) {
    if (!toastEl) return;
    toastEl.textContent = text; toastEl.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { toastEl.hidden = true; }, 3200);
  }
  function confirmBox(title, text, okLabel, danger, cancelLabel) {
    return new Promise(done => {
      const box = el('dialog', 'ar-dialog'); box.setAttribute('aria-labelledby', 'ar-confirm-title');
      const heading = el('h3', null, title); heading.id = 'ar-confirm-title';
      const cancel = textButton(cancelLabel || t('cancel'), () => box.close('cancel'));
      const ok = textButton(okLabel, () => box.close('ok'), 'ar-btn ' + (danger ? 'ar-danger' : 'ar-primary'));
      const actions = el('div', 'ar-actions'); actions.append(cancel, ok);
      box.append(heading, el('p', null, text), actions);
      box.addEventListener('close', () => { done(box.returnValue === 'ok'); box.remove(); }, {once: true});
      (panel || document.body).append(box); box.showModal(); cancel.focus();
    });
  }
  // A small menu under a button; Escape or a click elsewhere closes it
  function menu(anchor, items) {
    document.querySelector('.ar-menu')?.remove();
    const list = el('div', 'ar-menu'); list.setAttribute('role', 'menu');
    for (const item of items.filter(Boolean)) {
      if (item === '-') { list.append(el('div', 'ar-menusep')); continue; }
      const b = el('button', 'ar-menuitem' + (item.danger ? ' ar-danger-text' : ''), item.label); b.type = 'button'; b.setAttribute('role', 'menuitem');
      if (item.disabled) b.disabled = true;
      b.addEventListener('click', () => { close(); item.action(); });
      list.append(b);
    }
    const close = () => { list.remove(); document.removeEventListener('pointerdown', outside, true); anchor.setAttribute('aria-expanded', 'false'); };
    const outside = event => { if (!list.contains(event.target) && event.target !== anchor) close(); };
    list.addEventListener('keydown', event => {
      const buttons = [...list.querySelectorAll('button:not(:disabled)')], i = buttons.indexOf(document.activeElement);
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); anchor.focus(); }
      else if (event.key === 'ArrowDown') { event.preventDefault(); buttons[(i + 1) % buttons.length]?.focus(); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); buttons[(i - 1 + buttons.length) % buttons.length]?.focus(); }
    });
    panel.append(list);
    const a = anchor.getBoundingClientRect(), p = panel.getBoundingClientRect();
    list.style.top = (a.bottom - p.top + 4) + 'px';
    list.style.insetInlineEnd = Math.max(8, p.right - a.right) + 'px';
    anchor.setAttribute('aria-expanded', 'true');
    document.addEventListener('pointerdown', outside, true);
    list.querySelector('button:not(:disabled)')?.focus();
  }
  function build() {
    lang = language();
    panel = el('aside', 'ar-panel'); panel.id = 'owui-chat-artifacts'; panel.hidden = true; panel.lang = lang;
    panel.setAttribute('aria-label', t('artifacts'));
    const resizer = el('div', 'ar-resizer'); resizer.setAttribute('role', 'separator'); resizer.setAttribute('aria-orientation', 'vertical'); resizer.tabIndex = 0; setLabel(resizer, t('width'));
    headEl = el('div', 'ar-head'); bodyEl = el('div', 'ar-body'); footEl = el('div', 'ar-foot');
    toastEl = el('div', 'ar-toast'); toastEl.setAttribute('role', 'status'); toastEl.hidden = true;
    panel.append(resizer, headEl, bodyEl, footEl, toastEl);
    panel.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented || document.fullscreenElement) return;
      if (event.target.closest?.('textarea, input, .ar-menu, dialog')) return;
      event.preventDefault();
      if (view.is('viewer')) { view.send('back'); updateUrl(null); render(); } else closePanel();
    });
    // Drag the edge, or use the arrow keys on it, to change the width
    let drag = null;
    resizer.addEventListener('pointerdown', event => { drag = {x: event.clientX, w: panel.getBoundingClientRect().width}; resizer.setPointerCapture(event.pointerId); panel.classList.add('ar-dragging'); });
    resizer.addEventListener('pointermove', event => { if (drag) setWidth(drag.w + (drag.x - event.clientX) * (document.dir === 'rtl' ? -1 : 1)); });
    const end = () => { if (!drag) return; drag = null; panel.classList.remove('ar-dragging'); try { localStorage.setItem(PREFIX + 'width', String(Math.round(panelWidth))); } catch {} };
    resizer.addEventListener('pointerup', end); resizer.addEventListener('pointercancel', end);
    resizer.addEventListener('keydown', event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setWidth(panelWidth + (event.key === 'ArrowLeft' ? 40 : -40)); end(); } });
    document.body.append(panel);
  }

  /* ---- reading the chat ---- */
  async function load(quiet) {
    controller?.abort();
    const mine = controller = new AbortController(), signal = mine.signal;
    state.route = route();
    const r = state.route;
    if (state.loadedFor !== r.kind + ':' + r.id) { state.chat = null; state.artifacts = []; quiet = false; }
    if (!r.id || (r.kind !== 'chat' && r.kind !== 'shared')) { loader.send('none'); state.loadedFor = null; render(); return; }
    loader.send('start');
    if (!quiet) render();
    try {
      await whoAmI();
      const chat = await api(r.kind === 'chat' ? '/api/v1/chats/' + encodeURIComponent(r.id) : '/api/v1/chats/share/' + encodeURIComponent(r.id), signal);
      if (signal.aborted) return;
      state.chat = chat; state.loadedFor = r.kind + ':' + r.id; state.loadedAt = Date.now();
      state.shareId = chat?.share_id || null;
      const branch = branchOf(chat);
      state.modelId = [...branch].reverse().find(m => m.role === 'assistant' && typeof m.model === 'string')?.model || (Array.isArray(chat?.chat?.models) ? chat.chat.models[0] : '') || '';
      const found = artifactsOf(chat);
      const locals = r.kind === 'chat' ? readStore(r.id).locals.map(x => ({key: 'l' + x.id, type: x.type, lang: x.lang, title: x.title, named: true, local: true, versions: [{text: x.text, at: x.at / 1000, local: true, answerNo: 0, messageId: null}]})) : [];
      // Someone looking at the latest version follows a new one, as Claude does
      const open = current(), wasLatest = open && state.version === open.versions.length - 1;
      state.artifacts = [...found, ...locals];
      const now = current();
      if (now && wasLatest && now.versions.length > open.versions.length) state.version = now.versions.length - 1;
      if (controller === mine) loader.send('done');
    } catch (error) {
      if (signal.aborted || controller !== mine) return;
      loader.send(error.status === 401 || error.status === 403 ? 'deny' : 'fail');
      if (!quiet) state.artifacts = [];
    }
    if (controller === mine) { render(); syncChat(); }
  }

  /* ---- drawing ---- */
  function render() {
    if (!panel || view.is('closed')) return;
    panel.dataset.state = isLoading() ? 'loading' : 'ready';
    panel.dataset.chat = state.loadedFor || '';
    const artifact = view.is('viewer') ? current() : null;
    // An artifact that is no longer there (another branch, a deleted draft) gives way to the list
    if (view.is('viewer') && !artifact && !isLoading()) view.send('gone');
    artifact ? paintViewer(artifact) : paintList();
  }
  function paintList() {
    panel.dataset.view = 'list';
    dropFrames(bodyEl);
    const title = el('h2', 'ar-title', t('artifacts')); title.id = 'ar-heading';
    const newButton = state.route.kind === 'chat' && CONFIG.templates ? textButton(t('new'), () => openNew(newButton), 'ar-btn ar-primary') : null;
    if (newButton) { newButton.prepend(svgIcon('plus')); newButton.setAttribute('aria-haspopup', 'dialog'); }
    headEl.replaceChildren(svgIcon('artifacts'), title, el('span', 'ar-spacer'), ...(newButton ? [newButton] : []), iconButton(t('close'), 'close', closePanel));
    headEl.firstChild.classList.add('ar-headicon');
    footEl.replaceChildren(); footEl.hidden = true;
    bodyEl.replaceChildren();
    const r = state.route;
    if (!r.id || (r.kind !== 'chat' && r.kind !== 'shared')) { bodyEl.append(notice(t('no_chat'))); return; }
    if (isLoading() && !state.artifacts.length) { bodyEl.append(notice(t('loading'))); return; }
    if (loader.is('failed', 'denied') && !state.artifacts.length) { bodyEl.append(notice(loader.is('denied') ? t('denied') : t('failed'), null, loader.is('failed') ? textButton(t('retry'), () => load()) : null)); return; }
    if (!state.artifacts.length) { bodyEl.append(notice(t('empty_title'), t('empty_hint'), newButton ? textButton(t('new'), () => openNew(newButton)) : null)); return; }
    const search = el('input', 'ar-search'); search.type = 'search'; search.placeholder = t('search'); search.setAttribute('aria-label', t('search')); search.value = state.query;
    const list = el('ul', 'ar-list'); list.setAttribute('aria-labelledby', 'ar-heading');
    const paintItems = () => {
      const q = state.query.toLocaleLowerCase();
      const items = [...state.artifacts].sort((a, b) => (b.versions[b.versions.length - 1].at || 0) - (a.versions[a.versions.length - 1].at || 0)).filter(a => !q || displayTitle(a).toLocaleLowerCase().includes(q) || t('t_' + a.type).toLocaleLowerCase().includes(q) || a.versions[a.versions.length - 1].text.toLocaleLowerCase().includes(q));
      list.replaceChildren();
      if (!items.length) { list.append(el('li', 'ar-muted ar-pad', t('no_match'))); return; }
      for (const a of items) {
        const li = el('li'); const b = el('button', 'ar-item'); b.type = 'button'; b.dataset.key = a.key;
        const icon = el('span', 'ar-itemicon'); icon.dataset.type = a.type; icon.append(svgIcon(TYPE_ICON[a.type]));
        const text = el('span', 'ar-itemtext');
        const last = a.versions[a.versions.length - 1];
        const meta = [t('t_' + a.type) + (a.type === 'code' ? ' · ' + labelOf(a.lang) : ''), a.versions.length > 1 ? t('versions_n', {n: a.versions.length}) : '', a.local ? t('v_local') : last.answerNo ? t('from_answer', {n: last.answerNo}) : ''].filter(Boolean).join(' · ');
        text.append(nameSpan('ar-itemtitle', displayTitle(a)), el('span', 'ar-itemmeta', meta));
        if (last.unfinished && streaming()) text.append(el('span', 'ar-badge', t('writing')));
        b.append(icon, text);
        b.addEventListener('click', () => openArtifact(a.key));
        li.append(b); list.append(li);
      }
    };
    search.addEventListener('input', () => { state.query = search.value; paintItems(); });
    list.addEventListener('keydown', event => {
      const items = [...list.querySelectorAll('.ar-item')], i = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') { event.preventDefault(); items[Math.min(items.length - 1, i + 1)]?.focus(); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); i <= 0 ? search.focus() : items[i - 1]?.focus(); }
    });
    paintItems();
    bodyEl.append(search, list);
  }
  const notice = (title, hint, action) => { const box = el('div', 'ar-notice'); box.append(el('strong', null, title)); if (hint) box.append(el('span', null, hint)); if (action) box.append(action); return box; };
  function versionText(a, index) {
    const v = a.versions[index];
    if (!v) return '';
    if (a.local || readOnly()) return v.text;
    return editOf(state.route.id, a, index)?.text ?? v.text;
  }
  const isEdited = (a, index) => !a.local && !readOnly() && editOf(state.route.id, a, index) != null && editOf(state.route.id, a, index).text !== a.versions[index].text;
  function openArtifact(key, version, mode) {
    const a = state.artifacts.find(x => x.key === key);
    if (!a) return;
    const wasClosed = view.is('closed');
    view.send('artifact');
    state.key = key;
    state.version = a ? (version != null && version >= 0 && version < a.versions.length ? version : a.versions.length - 1) : -1;
    if (mode) state.mode = mode;
    else if (a && !hasPreview(a)) state.mode = 'code';
    else if (state.mode === 'changes' && (!a || a.versions.length < 2)) state.mode = 'preview';
    wasClosed ? openPanel() : render();
    updateUrl(a.key);
  }
  const hasPreview = a => a.type !== 'code';
  function paintViewer(a) {
    panel.dataset.view = 'viewer'; panel.dataset.type = a.type; panel.dataset.key = a.key;
    const index = clamp(state.version < 0 ? a.versions.length - 1 : state.version, 0, a.versions.length - 1);
    state.version = index;
    const v = a.versions[index], text = versionText(a, index), edited = isEdited(a, index);
    const ctx = ctxOf(a.key);
    // Header: back, title and version, the views, and what can be done with it
    const back = iconButton(t('back'), 'back', () => { view.send('back'); updateUrl(null); render(); });
    const titleBox = el('div', 'ar-titlebox');
    const title = nameSpan('ar-title', displayTitle(a)); title.id = 'ar-heading'; title.setAttribute('role', 'heading'); title.setAttribute('aria-level', '2');
    const meta = el('span', 'ar-meta', t('t_' + a.type) + (a.type === 'code' ? ' · ' + labelOf(a.lang) : ''));
    titleBox.append(title, meta);
    const versions = el('div', 'ar-versions');
    if (a.versions.length > 1) {
      const prev = iconButton(t('prev_version'), 'left', () => { state.version = index - 1; render(); });
      const next = iconButton(t('next_version'), 'right', () => { state.version = index + 1; render(); });
      prev.disabled = index === 0; next.disabled = index === a.versions.length - 1;
      const pick = el('select', 'ar-select ar-vselect'); setLabel(pick, t('version'));
      a.versions.forEach((ver, i) => { const o = el('option', null, 'v' + (i + 1) + (i === a.versions.length - 1 ? ' · ' + t('latest') : '') + (isEdited(a, i) ? ' · ' + t('v_draft') : '')); o.value = i; pick.append(o); });
      pick.value = index; pick.addEventListener('change', () => { state.version = +pick.value; render(); });
      versions.append(prev, pick, next);
    }
    const modes = el('div', 'ar-modes'); modes.setAttribute('role', 'tablist');
    const modeList = [hasPreview(a) && ['preview', t('preview')], ['code', t('code')], (a.versions.length > 1 || edited) && ['changes', t('changes')]].filter(Boolean);
    if (!modeList.some(([m]) => m === state.mode)) state.mode = modeList[0][0];
    for (const [m, label] of modeList) { const b = el('button', 'ar-mode', label); b.type = 'button'; b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(state.mode === m)); b.addEventListener('click', () => { state.mode = m; render(); }); modes.append(b); }
    // What is copied, downloaded or sent is the version as it is now, edits made since it was drawn included
    const copy = iconButton(t('copy'), 'copy', () => copyText(versionText(a, index)));
    const download = iconButton(t('download'), 'download', null); download.setAttribute('aria-haspopup', 'menu');
    download.addEventListener('click', () => menu(download, downloadItems(a, versionText(a, index), ctx)));
    const more = iconButton(t('more'), 'more', null); more.setAttribute('aria-haspopup', 'menu');
    more.addEventListener('click', () => menu(more, moreItems(a, index, versionText(a, index), isEdited(a, index))));
    const full = narrow() ? null : iconButton(size.is('full') ? t('exit_fullscreen') : t('fullscreen'), 'expand', toggleFull);
    // Two rows: what it is and what can be done with it, then its versions and views
    const top = el('div', 'ar-hrow'), tools = el('div', 'ar-hrow ar-headtools');
    const actions = el('div', 'ar-actionsrow'); actions.append(copy, download, more, ...(full ? [full] : []), iconButton(t('close'), 'close', closePanel));
    top.append(back, titleBox, actions);
    tools.append(versions, el('span', 'ar-spacer'), modes);
    headEl.replaceChildren(top, tools);
    // Body
    dropFrames(bodyEl);
    bodyEl.replaceChildren();
    const viewEl = el('div', 'ar-view'); viewEl.dataset.mode = state.mode;
    const commit = value => saveEdit(a, index, value);
    if (state.mode === 'code') viewEl.append(codeView(text, !readOnly(), ctx, commit));
    else if (state.mode === 'changes') {
      const before = edited ? a.versions[index].text : versionText(a, index - 1);
      viewEl.append(diffView(before, text, edited ? t('version') + ' ' + (index + 1) : t('previous')));
    } else viewEl.append(previewOf(a, text, ctx, commit));
    bodyEl.append(viewEl);
    paintFoot(a, index, text);
    selectionWatch(viewEl, a, text);
  }
  // Footer: where it comes from, whether it is edited, and any error
  function paintFoot(a, index, text) {
    const v = a.versions[index], edited = isEdited(a, index);
    footEl.hidden = false; footEl.replaceChildren();
    const info = el('span', 'ar-footinfo');
    const bits = [];
    if (a.local) bits.push(t('v_local'));
    else { bits.push(t('version_n', {n: index + 1, total: a.versions.length})); if (v.answerNo) bits.push(t('from_answer', {n: v.answerNo})); if (v.at) bits.push(relTime(v.at)); }
    bits.push(statsOf(a, text));
    info.textContent = bits.filter(Boolean).join(' · ');
    footEl.append(info);
    if (v.unfinished) footEl.append(el('span', 'ar-badge', streaming() ? t('writing') : t('unfinished')));
    if (edited) {
      const row = el('div', 'ar-draft');
      row.append(el('span', null, t('edited_by_you') + ' ' + t(canStore() ? 'edits_kept' : 'edits_session')));
      if (state.route.kind === 'chat') row.append(textButton(t('send_edit'), () => sendEdit(a, text)));
      row.append(textButton(t('discard_edit'), () => discardEdit(a, index)));
      footEl.append(row);
    }
    const problem = state.errors.get(a.key + '#' + index);
    if (problem) footEl.append(errorBar(a, problem));
  }
  function statsOf(a, text) {
    if (a.type === 'document') { const w = wordCount(text); return t('words', {n: n(w)}) + ' · ' + t('read_min', {n: Math.max(1, Math.round(w / 220))}); }
    if (a.type === 'slides') return t('slides_n', {n: slidesOf(text).length});
    if (a.type === 'motion') return motionStats(text);
    if (a.type === 'sheet') { const g = gridOf(text, a.lang); return t('rows_cols', {rows: n(Math.max(0, g.rows.length - 1)), cols: g.rows[0]?.length || 0}); }
    return t('lines', {n: n(countLines(text))});
  }
  function errorBar(a, problem) {
    const bar = el('div', 'ar-error'); bar.setAttribute('role', 'alert');
    bar.append(svgIcon('warn'));
    const text = el('div', 'ar-errortext');
    if (problem.lib) text.append(el('strong', null, t('lib_failed', {what: problem.lib.what, host: originOf(problem.lib.url) || problem.lib.url || '—'})));
    else { text.append(el('strong', null, t('error'))); const pre = el('code', null, problem.message.split('\n').slice(0, 3).join('\n')); pre.dir = 'ltr'; text.append(pre); }
    bar.append(text);
    if (!problem.lib && state.route.kind === 'chat' && !a.local) bar.append(textButton(t('try_fix'), () => tryFix(a, problem.message), 'ar-btn ar-primary'));
    return bar;
  }
  function previewOf(a, text, ctx, commit) {
    const index = state.version, errorKey = a.key + '#' + index;
    const onEvent = event => {
      if (event.type === 'error' || (event.type === 'lib')) {
        const problem = event.type === 'lib' ? {lib: {what: event.what, url: event.url}, message: ''} : {message: event.message};
        const had = state.errors.get(errorKey);
        // The first problem is kept, except that a library that didn't load explains everything after it
        if (had && !(problem.lib && !had.lib)) return;
        state.errors.set(errorKey, problem);
        if (current() === a && state.version === index) { footEl.querySelector('.ar-error')?.remove(); footEl.append(errorBar(a, problem)); }
      } else if (event.type === 'svg') ctx.svg = event.svg;
      else if (event.type === 'rendered') { ctx.rendered = true; panel.dataset.rendered = a.key; }
    };
    state.errors.delete(errorKey);
    if (a.type === 'document') return documentView(text);
    if (a.type === 'slides') return slidesView(text, ctx, !readOnly(), commit);
    if (a.type === 'motion') return motionView(text, ctx, !readOnly(), commit);
    if (a.type === 'sheet') return sheetView(text, a.lang, ctx, !readOnly(), commit, displayTitle(a), state.route.kind === 'chat' && !a.local ? (ref, cells) => askAbout(a, text, ref + '\n' + cells) : null);
    if (a.type === 'data') return dataView(text, ctx);
    if (a.type === 'svg') {
      const box = el('div', 'ar-svgbox');
      const img = el('img'); img.alt = displayTitle(a);
      const url = URL.createObjectURL(new Blob([text], {type: 'image/svg+xml'}));
      img.src = url; img.addEventListener('load', () => setTimeout(() => URL.revokeObjectURL(url), 1000)); img.addEventListener('error', () => onEvent({type: 'error', message: 'SVG'}));
      box.append(img); return box;
    }
    if (a.type === 'html' || a.type === 'react' || a.type === 'diagram') {
      const box = el('div', 'ar-framebox');
      if (a.type !== 'diagram') {
        const bar = el('div', 'ar-subbar');
        const devices = [['desktop', t('desktop'), ''], ['tablet', t('tablet'), '768px'], ['phone', t('phone'), '390px']];
        for (const [key, label] of devices) { const b = textButton(label, () => { ctx.device = key; render(); }, 'ar-btn ar-chip'); b.setAttribute('aria-pressed', String((ctx.device || 'desktop') === key)); bar.append(b); }
        bar.append(el('span', 'ar-spacer'), iconButton(t('reload'), 'refresh', () => render()));
        box.append(bar);
      }
      const stage = el('div', 'ar-framestage'); stage.dataset.device = ctx.device || 'desktop';
      stage.append(makeFrame(a.type, a.type === 'react' ? withoutMarker(text) : text, a, onEvent));
      box.append(stage);
      return box;
    }
    return codeView(text, !readOnly(), ctx, commit);
  }
  let saveTimer = 0;
  function saveEdit(a, index, value) {
    if (readOnly()) return;
    if (a.local) {
      const data = readStore(state.route.id), item = data.locals.find(x => 'l' + x.id === a.key);
      if (item) { item.text = value; item.at = Date.now(); a.versions[0].text = value; }
      clearTimeout(saveTimer); saveTimer = setTimeout(() => writeStore(state.route.id), 400);
      if (state.mode === 'preview' && a.type !== 'sheet') render();
      return;
    }
    const result = setEdit(state.route.id, a, index, value === a.versions[index].text ? null : value);
    if (result === 'big') toast(t('edit_too_big'));
    // The spreadsheet's own edits redraw at once; typing in the code view redraws the footer only
    // The spreadsheet redraws itself and keeps its selection; other previews are drawn again
    if (state.mode === 'preview' && a.type !== 'sheet') render();
    else if (state.mode === 'preview') { paintFoot(a, index, value); syncVersionLabels(a); }
    else { clearTimeout(saveTimer); saveTimer = setTimeout(() => { if (current() === a && state.version === index) { paintFoot(a, index, value); syncVersionLabels(a); } }, 500); }
  }
  function syncVersionLabels(a) {
    const pick = headEl.querySelector('.ar-vselect');
    if (pick) [...pick.options].forEach((o, i) => { o.textContent = 'v' + (i + 1) + (i === a.versions.length - 1 ? ' · ' + t('latest') : '') + (isEdited(a, i) ? ' · ' + t('v_draft') : ''); });
  }
  async function discardEdit(a, index) {
    if (!(await confirmBox(t('discard_confirm'), t('discard_text'), t('discard'), true))) return;
    setEdit(state.route.id, a, index, null); render();
  }

  /* ---- copying, downloading and keeping ---- */
  async function copyText(text) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; }
    catch { const box = el('textarea'); box.value = text; box.style.cssText = 'position:fixed;opacity:0'; panel.append(box); box.select(); try { ok = document.execCommand('copy'); } catch {} box.remove(); }
    toast(t(ok ? 'copied' : 'copy_failed'));
    return ok;
  }
  const safeName = name => String(name || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^\p{L}\p{N}._ -]+/gu, ' ').replace(/\s+/g, ' ').trim().slice(0, 80) || 'artifact';
  const baseName = a => { const title = displayTitle(a); return safeName(title.replace(/\.[A-Za-z0-9]{1,8}$/, '')); };
  function save(blob, name) {
    const link = el('a'); const url = URL.createObjectURL(blob);
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  const escapeHtml = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const FIGURE_CSS = 'mark{background:#fde68a;color:inherit;padding:0 .15em;border-radius:.2em}.ar-callout{margin:.75em 0;padding:.6em .9em;border-radius:8px;border-inline-start:4px solid #2563eb;background:#eff6ff}.ar-callout[data-kind=tip]{border-color:#16a34a;background:#f0fdf4}.ar-callout[data-kind=important]{border-color:#7c3aed;background:#f5f3ff}.ar-callout[data-kind=warning]{border-color:#d97706;background:#fffbeb}.ar-callout-title{display:block;font-weight:700;margin-bottom:.2em}.ar-callout>p{margin:.2em 0}.ar-figure{margin:.75em 0;text-align:center}.ar-figure img,.ar-figure svg,.ar-pic{max-width:100%;height:auto}.ar-diagram{background:#fff;border-radius:8px;padding:.5em}.ar-cols{display:grid;grid-template-columns:repeat(var(--ar-cols,2),minmax(0,1fr));gap:1.5em;align-items:start}.ar-chart{width:100%;height:auto}.ar-chart text{fill:#444;font:12px system-ui,sans-serif}.ar-chart .ar-caption{fill:#111;font-weight:600;font-size:14px}';
  const PRINT_CSS_BASE = 'body{font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111;max-width:46rem;margin:2rem auto;padding:0 1rem}h1,h2,h3{line-height:1.25}pre{background:#f5f5f5;padding:.75rem;border-radius:6px;overflow:auto;white-space:pre-wrap}code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.9em}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:.35rem .5rem;text-align:start}blockquote{border-inline-start:3px solid #ccc;margin:0;padding-inline-start:1rem;color:#444}img{max-width:100%}';
  const PRINT_CSS = PRINT_CSS_BASE + FIGURE_CSS;
  // Documents and slides are written from the same safe rendering the panel shows
  const rendered = text => { const box = el('div'); box.append(renderMarkdown(withoutMarker(text))); return box.innerHTML; };
  const standalone = (title, inner, css = PRINT_CSS) => `<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><style>${css}</style></head><body>${inner}</body></html>`;
  const SLIDE_CSS = 'html,body{margin:0;background:#111;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}.s{width:100vw;height:56.25vw;max-height:100vh;max-width:177.78vh;margin:auto;display:none;overflow:hidden}.s.on{display:block}.ar-slide{width:100%;height:100%;container-type:inline-size;background:#fff;color:#111;overflow:hidden}.ar-slide-inner{box-sizing:border-box;height:100%;padding:6cqw 8cqw;font-size:2.4cqw;line-height:1.45;display:flex;flex-direction:column;justify-content:center;overflow:hidden}.ar-slide h1{font-size:2.4em;line-height:1.1;margin:0 0 .4em}.ar-slide h2{font-size:1.8em;margin:0 0 .4em}.ar-slide h3{font-size:1.3em;margin:.2em 0 .3em}.ar-slide p{margin:.3em 0}.ar-slide li{margin:.25em 0}.ar-slide pre{background:#f3f3f3;padding:.6em;border-radius:6px;font-size:.75em}.ar-slide table{border-collapse:collapse;font-size:.8em}.ar-slide th,.ar-slide td{border:1px solid #ccc;padding:.3em .5em}.ar-slide-title .ar-slide-inner,.ar-slide-center .ar-slide-inner{align-items:center;text-align:center}.ar-slide-title h1{font-size:3.2em}.ar-slide-section{background:#1e293b;color:#fff}.ar-slide-section .ar-slide-inner{justify-content:flex-end}.ar-slide-visual .ar-slide-inner{justify-content:flex-start}.ar-slide .ar-figure{flex:1;min-height:0;display:flex;align-items:center;justify-content:center}.ar-slide .ar-figure img,.ar-slide .ar-figure svg{max-height:100%}.n{position:fixed;bottom:8px;right:12px;color:#aaa;font-size:12px}@media print{@page{size:landscape;margin:0}body{background:#fff}.s{display:block!important;width:100%;height:100vh;page-break-after:always}.n{display:none}}' + FIGURE_CSS;
  const slideHtml = slide => { const box = el('div'); box.append(slideNode(slide)); return box.innerHTML; };
  const slidesHtml = (a, text) => standalone(displayTitle(a), slidesOf(text).map((s, i) => `<section class="s${i ? '' : ' on'}">${slideHtml(s)}</section>`).join('') + '<div class="n"></div><script>var s=[].slice.call(document.querySelectorAll(".s")),i=0,n=document.querySelector(".n");function g(k){s[i].classList.remove("on");i=Math.max(0,Math.min(s.length-1,k));s[i].classList.add("on");n.textContent=(i+1)+" / "+s.length}g(0);addEventListener("keydown",function(e){if(["ArrowRight","PageDown"," "].indexOf(e.key)>=0)g(i+1);if(["ArrowLeft","PageUp"].indexOf(e.key)>=0)g(i-1)});addEventListener("click",function(){g(i+1)})<\/script>', SLIDE_CSS);
  const wordHtml = (a, text) => `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"><title>${escapeHtml(displayTitle(a))}</title><style>${PRINT_CSS}</style></head><body>${rendered(text)}</body></html>`;
  const xmlEscape = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '');
  // A minimal Excel workbook: values, and formulas with their last result
  function xlsxOf(text, langKey) {
    const rows = gridOf(text, langKey).rows, engine = sheetEngine(rows);
    const cells = rows.map((row, r) => '<row r="' + (r + 1) + '">' + row.map((raw, c) => {
      const ref = colName(c) + (r + 1), v = engine.value(r, c), source = String(raw);
      if (source.startsWith('=') && source.length > 1) return `<c r="${ref}"${typeof v === 'number' ? '' : ' t="str"'}><f>${xmlEscape(source.slice(1))}</f><v>${xmlEscape(typeof v === 'boolean' ? (v ? 1 : 0) : v)}</v></c>`;
      if (typeof v === 'number') return `<c r="${ref}"><v>${v}</v></c>`;
      if (source === '') return '';
      return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xmlEscape(source)}</t></is></c>`;
    }).join('') + '</row>').join('');
    const enc = new TextEncoder();
    const files = [
      ['[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'],
      ['_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'],
      ['xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'],
      ['xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'],
      ['xl/worksheets/sheet1.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + cells + '</sheetData></worksheet>']
    ];
    return makeZip(files.map(([name, body]) => ({name, data: enc.encode(body)})), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  }
  const CRC = (() => { const table = new Uint32Array(256); for (let k = 0; k < 256; k++) { let c = k; for (let j = 0; j < 8; j++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; table[k] = c >>> 0; } return table; })();
  const crc32 = bytes => { let c = 0xFFFFFFFF; for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
  function makeZip(files, type = 'application/zip') {
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
    return new Blob([...parts, ...central, end.buffer], {type});
  }
  // An SVG drawn into a PNG through an image, where its scripts can't run
  async function pngOf(svgText, name) {
    const url = URL.createObjectURL(new Blob([svgText], {type: 'image/svg+xml'}));
    try {
      const img = new Image(); img.src = url; await img.decode();
      const scale = Math.min(4, Math.max(1, 1600 / (img.naturalWidth || 800)));
      const canvas = document.createElement('canvas'); canvas.width = Math.round((img.naturalWidth || 800) * scale); canvas.height = Math.round((img.naturalHeight || 600) * scale);
      const g = canvas.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, canvas.width, canvas.height); g.drawImage(img, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('png');
      save(blob, name);
    } catch { toast(t('save_failed')); } finally { URL.revokeObjectURL(url); }
  }
  const chartText = ctx => ctx.chartSvg ? new XMLSerializer().serializeToString(ctx.chartSvg).replace('<svg', '<svg style="background:#fff;font-family:system-ui,sans-serif"') : '';
  function downloadItems(a, text, ctx) {
    const name = baseName(a), file = (ext, body, type) => () => save(new Blob([body], {type}), name + '.' + ext);
    const item = (format, action) => ({label: t('dl_as', {format}), action});
    const plain = 'text/plain;charset=utf-8';
    const later = action => () => { toast(t('preparing')); diagramsReady(text).then(action); };
    switch (a.type) {
      // Diagrams are drawn before a document or deck is written out, so they are in the file
      case 'document': return [item('Markdown (.md)', file('md', text, 'text/markdown;charset=utf-8')), item('HTML (.html)', later(() => file('html', standalone(displayTitle(a), rendered(text)), 'text/html;charset=utf-8')())), item('Word (.doc)', later(() => file('doc', wordHtml(a, text), 'application/msword')())), {label: t('print'), action: later(() => printHtml(standalone(displayTitle(a), rendered(text))))}];
      case 'slides': return [item('Markdown (.md)', file('md', text, 'text/markdown;charset=utf-8')), item('PowerPoint (.pptx)', () => { toast(t('exporting')); pptxOf(a, text).then(blob => save(blob, name + '.pptx'), () => toast(t('save_failed'))); }), item(t('t_slides') + ' (.html)', later(() => file('html', slidesHtml(a, text), 'text/html;charset=utf-8')())), {label: t('print'), action: later(() => printHtml(slidesHtml(a, text)))}];
      case 'motion': return [
        {label: t('m_video', {q: '1080p'}), action: () => motionVideo(a, text, 1080)},
        {label: t('m_video', {q: '720p'}), action: () => motionVideo(a, text, 720)},
        {label: t('m_frame'), action: () => motionFrame(a, text, ctx.motionTime).then(blob => save(blob, name + '.png'), () => toast(t('save_failed')))},
        {label: t('m_scenes'), action: () => { toast(t('preparing')); motionScenesZip(a, text).then(blob => save(blob, name + '-scenes.zip'), () => toast(t('save_failed'))); }},
        {label: t('m_source'), action: file('txt', text, plain)}];
      case 'sheet': return [item('CSV (.csv)', file('csv', withoutMarker(text), 'text/csv;charset=utf-8')), item('Excel (.xlsx)', () => save(xlsxOf(text, a.lang), name + '.xlsx')), ctx.chartSvg && state.mode === 'preview' && ctx.sheetMode === 'chart' ? item(t('chart') + ' (.svg)', () => save(new Blob([chartText(ctx)], {type: 'image/svg+xml'}), name + '-chart.svg')) : null, ctx.chartSvg && ctx.sheetMode === 'chart' ? item(t('chart') + ' (.png)', () => pngOf(chartText(ctx), name + '-chart.png')) : null, {label: t('print'), action: () => { const g = gridOf(text, a.lang), e = sheetEngine(g.rows); printHtml(standalone(displayTitle(a), '<table>' + g.rows.map((row, r) => '<tr>' + row.map((_, c) => (r ? '<td>' : '<th>') + escapeHtml(shown(e.value(r, c))) + (r ? '</td>' : '</th>')).join('') + '</tr>').join('') + '</table>')); }}].filter(Boolean);
      case 'diagram': return [item('Mermaid (.mmd)', file('mmd', text, plain)), ctx.svg ? item('SVG (.svg)', file('svg', ctx.svg, 'image/svg+xml')) : null, ctx.svg ? item('PNG (.png)', () => pngOf(ctx.svg, name + '.png')) : null].filter(Boolean);
      case 'svg': return [item('SVG (.svg)', file('svg', text, 'image/svg+xml')), item('PNG (.png)', () => pngOf(text, name + '.png'))];
      case 'html': return [item('HTML (.html)', file('html', text, 'text/html;charset=utf-8')), {label: t('print'), action: () => printFrame()}];
      case 'react': return [item((a.lang === 'tsx' ? 'TSX' : 'JSX') + ' (.' + (a.lang === 'tsx' ? 'tsx' : 'jsx') + ')', file(a.lang === 'tsx' ? 'tsx' : 'jsx', text, plain)), item('HTML (.html)', file('html', reactDoc(withoutMarker(text), 'export'), 'text/html;charset=utf-8'))];
      case 'data': return [item('JSON (.json)', file('json', withoutMarker(text), 'application/json')), ctx.tabular ? item('CSV (.csv)', () => save(new Blob([ctx.tabular()], {type: 'text/csv'}), name + '.csv')) : null].filter(Boolean);
      default: { const ext = (/\.([A-Za-z0-9]{1,8})$/.exec(a.title) || [])[1] || EXTS[a.lang] || 'txt'; return [item('.' + ext, () => save(new Blob([text], {type: plain}), (/\.[A-Za-z0-9]{1,8}$/.test(a.title) ? safeName(a.title) : name + '.' + ext)))]; }
    }
  }
  // Printing goes through a frame that runs no scripts; what it holds is the panel's own safe rendering
  function printHtml(html) {
    const frame = el('iframe', 'ar-printframe'); frame.setAttribute('sandbox', 'allow-modals allow-same-origin'); frame.srcdoc = html.replace(/<script[\s\S]*?<\/script>/gi, '');
    frame.addEventListener('load', () => { try { frame.contentWindow.focus(); frame.contentWindow.print(); } catch { toast(t('save_failed')); } setTimeout(() => frame.remove(), 60000); }, {once: true});
    document.body.append(frame);
  }
  function printFrame() {
    for (const [win, record] of frames) if (record.frame.isConnected && panel.contains(record.frame)) { try { win.postMessage({__ar: record.nonce, type: 'print'}, '*'); } catch {} return; }
  }
  function moreItems(a, index, text, edited) {
    const chat = state.route.kind === 'chat';
    const v = a.versions[index];
    return [
      chat && CONFIG.allowSave ? {label: t('save_files'), action: () => saveToFiles(a, text)} : null,
      chat && CONFIG.allowSave ? {label: t('save_note'), action: () => saveAsNote(a, text)} : null,
      v.messageId ? {label: t('show_in_chat'), action: () => showInChat(v.messageId)} : null,
      edited && chat ? '-' : null,
      edited && chat ? {label: t('send_edit'), action: () => sendEdit(a, text)} : null,
      edited ? {label: t('discard_edit'), action: () => discardEdit(a, index), danger: true} : null,
      a.local && chat ? '-' : null,
      a.local && chat ? {label: t('send_edit'), action: () => sendEdit(a, text)} : null,
      a.local && chat ? {label: t('delete'), action: () => deleteLocal(a), danger: true} : null
    ];
  }
  const fileExt = a => ({document: 'md', slides: 'md', sheet: 'csv', diagram: 'mmd', svg: 'svg', html: 'html', react: a.lang === 'tsx' ? 'tsx' : 'jsx', data: 'json'})[a.type] || (/\.([A-Za-z0-9]{1,8})$/.exec(a.title) || [])[1] || EXTS[a.lang] || 'txt';
  async function saveToFiles(a, text) {
    try {
      const name = baseName(a).replace(/\s+/g, '-') + '.' + fileExt(a), form = new FormData();
      form.append('file', new File([text], name, {type: 'text/plain'}));
      const response = await request('/api/v1/files/?process=false', {method: 'POST', body: form}, 60000);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      toast(t('saved_file', {name}));
    } catch { toast(t('save_failed')); }
  }
  const fenceFor = text => '`'.repeat(Math.max(3, ...[...text.matchAll(/`+/g)].map(match => match[0].length + 1)));
  const langTag = a => ({document: 'markdown', slides: 'slides', motion: 'motion', sheet: 'csv', diagram: 'mermaid', svg: 'svg', html: 'html', react: a.lang === 'tsx' ? 'tsx' : 'jsx', data: 'json'})[a.type] || (a.lang === 'text' ? '' : a.lang);
  const fenced = (a, text) => { const fence = fenceFor(text); return fence + langTag(a) + '\n' + text.replace(/\n$/, '') + '\n' + fence; };
  async function saveAsNote(a, text) {
    try {
      const md = a.type === 'document' ? text : fenced(a, text);
      const response = await request('/api/v1/notes/create', {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({title: displayTitle(a).slice(0, 120), data: {content: {json: null, html: '', md}}, meta: null, access_grants: []})});
      if (response.status === 401 || response.status === 403) toast(t('note_denied'));
      else if (!response.ok) throw new Error('HTTP ' + response.status);
      else toast(t('saved_note'));
    } catch { toast(t('save_failed')); }
  }
  const navigate = href => { const link = el('a'); link.href = href; document.body.append(link); link.click(); link.remove(); };
  function showInChat(messageId) {
    const node = document.getElementById('message-' + messageId);
    if (narrow()) closePanel();
    if (node) { node.scrollIntoView({block: 'center', behavior: 'smooth'}); node.classList.add('ar-flash'); setTimeout(() => node.classList.remove('ar-flash'), 1600); }
  }
  async function deleteLocal(a) {
    if (!(await confirmBox(t('delete_confirm'), t('delete_text'), t('delete'), true))) return;
    const data = readStore(state.route.id);
    data.locals = data.locals.filter(x => 'l' + x.id !== a.key);
    writeStore(state.route.id);
    state.artifacts = state.artifacts.filter(x => x !== a); view.send('back'); updateUrl(null); render();
  }

  /* ---- handing work to the chat ---- */
  const INLINE_LIMIT = 60000;
  const inlineSafe = text => text.length <= INLINE_LIMIT && !text.includes('{{');
  function composerFile() {
    const input = document.querySelector('#chat-input');
    let parent = input?.parentElement;
    while (parent && parent !== document.body) { const file = parent.querySelector('input[type="file"][multiple]'); if (file) return file; parent = parent.parentElement; }
    return null;
  }
  const busy = () => streaming() || !!document.querySelector('#messages-container [id^="message-"]:last-child .animate-pulse, button[aria-label="Stop"], #stop-response-button');
  // Fills the message box, or sends at once; Open WebUI fills {{...}} in text it is given, so such text travels as a file
  async function toChat(text, submit, attachment) {
    if (state.route.kind !== 'chat' || !document.getElementById('chat-input')) { toast(t('no_composer')); return false; }
    if (submit && busy()) { toast(t('ask_busy')); submit = false; }
    if (attachment) {
      const input = composerFile();
      if (!input) { await copyText(text + '\n\n' + attachment.text); return false; }
      const transfer = new DataTransfer();
      transfer.items.add(new File([attachment.text], attachment.name, {type: 'text/plain'}));
      input.files = transfer.files; input.dispatchEvent(new Event('change', {bubbles: true}));
      submit = false;
    }
    window.postMessage({type: submit ? 'input:prompt:submit' : 'input:prompt', text}, location.origin);
    if (narrow()) closePanel();
    if (!submit) setTimeout(() => document.getElementById('chat-input')?.focus({preventScroll: true}), 60);
    return true;
  }
  function sendEdit(a, text) {
    const title = displayTitle(a).replace(/["«»\n]/g, ' ');
    if (inlineSafe(text)) toChat(t('p_edit', {title}) + '\n\n' + fenced(a, text), false);
    else toChat(t('p_edit', {title}), false, {text, name: baseName(a).replace(/\s+/g, '-') + '.' + fileExt(a)});
  }
  async function tryFix(a, message) {
    const title = displayTitle(a).replace(/["«»\n]/g, ' ');
    const error = '```\n' + message.slice(0, 2000).replace(/```/g, "'''") + '\n```';
    if (await toChat(t('p_fix', {title, error}), true)) toast(t('fix_sent'));
  }
  // Selecting part of an artifact offers to ask about it
  let askButton = null;
  function selectionWatch(viewEl, a, text) {
    askButton?.remove(); askButton = null;
    if (state.route.kind !== 'chat' || a.local) return;
    const check = () => {
      let picked = '', rect = null;
      const area = viewEl.querySelector('textarea');
      if (area && document.activeElement === area && area.selectionEnd > area.selectionStart) { picked = area.value.slice(area.selectionStart, area.selectionEnd); rect = area.getBoundingClientRect(); rect = {left: rect.left + 24, top: rect.top + 8, bottom: rect.top + 8}; }
      else { const sel = getSelection(); if (sel && sel.rangeCount && !sel.isCollapsed && viewEl.contains(sel.anchorNode)) { picked = sel.toString(); rect = sel.getRangeAt(0).getBoundingClientRect(); } }
      askButton?.remove(); askButton = null;
      if (!picked.trim() || picked.length > 6000 || !rect) return;
      askButton = textButton(t('ask'), () => askAbout(a, text, picked), 'ar-btn ar-primary ar-askbtn');
      askButton.prepend(svgIcon('sparkle'));
      panel.append(askButton);
      const p = panel.getBoundingClientRect();
      askButton.style.top = clamp(rect.bottom - p.top + 6, 48, p.height - 48) + 'px';
      askButton.style.left = clamp(rect.left - p.left, 8, p.width - 180) + 'px';
    };
    viewEl.addEventListener('mouseup', () => setTimeout(check, 250));
    viewEl.addEventListener('keyup', event => { if (event.shiftKey || event.key === 'Shift') check(); });
    viewEl.addEventListener('scroll', hideAsk, true);
  }
  // Copying a selection is the usual reason to make one: the offer to ask steps aside
  const hideAsk = () => { askButton?.remove(); askButton = null; };
  document.addEventListener('copy', hideAsk);
  document.addEventListener('pointerdown', event => { if (askButton && event.target !== askButton && !askButton.contains(event.target)) hideAsk(); }, true);
  function askAbout(a, text, picked) {
    askButton?.remove(); askButton = null;
    const box = el('dialog', 'ar-dialog'); box.setAttribute('aria-labelledby', 'ar-ask-title');
    const form = el('form'); form.method = 'dialog';
    const heading = el('h3', null, t('ask_edit')); heading.id = 'ar-ask-title';
    const quote = el('pre', 'ar-quote', picked.length > 600 ? picked.slice(0, 600) + '…' : picked); quote.dir = 'auto';
    const input = el('textarea', 'ar-input'); input.rows = 3; input.placeholder = t('ask_placeholder'); input.maxLength = 2000; input.dir = 'auto'; setLabel(input, t('ask_placeholder'));
    const go = el('button', 'ar-btn ar-primary', t('ask_send')); go.type = 'submit';
    const actions = el('div', 'ar-actions'); actions.append(textButton(t('cancel'), () => box.close()), go);
    form.append(heading, quote, input, el('p', 'ar-muted ar-small', t('ask_hint')), actions);
    input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); } });
    form.addEventListener('submit', event => {
      event.preventDefault();
      const ask = input.value.trim(); if (!ask) { input.focus(); return; }
      box.close();
      const title = displayTitle(a).replace(/["«»\n]/g, ' ');
      const part = '```\n' + picked.slice(0, 4000).replace(/```/g, "'''") + '\n```';
      toChat([t('p_part', {title}), part, ask, t('p_reply')].join('\n\n'), true);
    });
    box.addEventListener('close', () => box.remove(), {once: true});
    box.append(form); panel.append(box); box.showModal(); input.focus();
  }

  /* ---- starting an artifact ---- */
  const NEW_TYPES = ['document', 'slides', 'motion', 'sheet', 'diagram', 'html', 'react', 'design', 'svg', 'data', 'code'];
  function openNew() {
    const box = el('dialog', 'ar-dialog ar-newdialog'); box.setAttribute('aria-labelledby', 'ar-new-title');
    const heading = el('h3', null, t('new_title')); heading.id = 'ar-new-title';
    const grid = el('div', 'ar-newgrid');
    for (const type of NEW_TYPES) {
      const b = el('button', 'ar-newitem'); b.type = 'button';
      const icon = el('span', 'ar-itemicon'); icon.dataset.type = type === 'design' ? 'html' : type; icon.append(svgIcon(type === 'design' ? 'design' : TYPE_ICON[type]));
      const words = el('span', 'ar-itemtext'); words.append(el('span', 'ar-itemtitle', t('t_' + type)), el('span', 'ar-itemmeta', t('d_' + type)));
      b.append(icon, words);
      b.addEventListener('click', () => { box.close(); toChat(t('tp_' + type), false); });
      grid.append(b);
    }
    const blank = textButton(t('blank'), () => { box.close(); blankDialog(); });
    blank.prepend(svgIcon('pencil')); blank.title = t('blank_hint');
    const actions = el('div', 'ar-actions'); actions.append(blank, el('span', 'ar-spacer'), textButton(t('cancel'), () => box.close()));
    box.append(heading, el('p', 'ar-muted ar-small', t('new_hint')), grid, actions);
    box.addEventListener('close', () => box.remove(), {once: true});
    panel.append(box); box.showModal(); grid.querySelector('button')?.focus();
  }
  function blankDialog() {
    const box = el('dialog', 'ar-dialog'); box.setAttribute('aria-labelledby', 'ar-blank-title');
    const form = el('form'); form.method = 'dialog';
    const heading = el('h3', null, t('blank')); heading.id = 'ar-blank-title';
    const kind = el('select', 'ar-select'); for (const type of ['document', 'slides', 'motion', 'sheet', 'diagram', 'html', 'react', 'svg', 'data', 'code']) { const o = el('option', null, t('t_' + type)); o.value = type; kind.append(o); }
    const name = el('input', 'ar-input'); name.type = 'text'; name.value = t('blank_default'); name.maxLength = 120;
    const l1 = el('label', 'ar-label', t('blank_type')); l1.append(kind); const l2 = el('label', 'ar-label', t('blank_name')); l2.append(name);
    const go = el('button', 'ar-btn ar-primary', t('blank_create')); go.type = 'submit';
    const actions = el('div', 'ar-actions'); actions.append(textButton(t('cancel'), () => box.close()), go);
    form.append(heading, el('p', 'ar-muted ar-small', t('blank_hint')), l1, l2, actions);
    form.addEventListener('submit', event => {
      event.preventDefault(); box.close();
      const type = kind.value, title = name.value.trim() || t('blank_default');
      const starter = {motion: 'size: 16:9\ntheme: light\n\n--- 3s\n# ' + title + '\n\n--- 4s fade\n## \n- \n', document: '# ' + title + '\n\n', slides: '# ' + title + '\n\n---\n\n# \n', sheet: 'Name,Value\n,\n', diagram: 'flowchart TD\n  A[Start] --> B[End]\n', html: '<!doctype html>\n<html>\n<head><title>' + escapeHtml(title) + '</title></head>\n<body>\n  <h1>' + escapeHtml(title) + '</h1>\n</body>\n</html>\n', react: 'export default function App() {\n  return <div className="p-8 text-2xl">Hello</div>;\n}\n', svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">\n  <circle cx="100" cy="100" r="80" fill="#6366f1"/>\n</svg>\n', data: '{\n  "name": ""\n}\n', code: ''}[type];
      const id = randomId(), data = readStore(state.route.id);
      data.locals.push({id, type, lang: {document: 'markdown', slides: 'slides', motion: 'motion', sheet: 'csv', diagram: 'mermaid', html: 'html', react: 'jsx', svg: 'svg', data: 'json', code: 'text'}[type], title, text: starter, at: Date.now()});
      writeStore(state.route.id);
      load(true).then(() => openArtifact('l' + id, 0, 'code'));
    });
    box.addEventListener('close', () => box.remove(), {once: true});
    box.append(form); panel.append(box); box.showModal(); name.select();
  }

  /* ---- PowerPoint ---- */
  // A real .pptx, written in the browser: each slide's title, text, bullets, highlights and columns as PowerPoint text,
  // and its diagrams, charts, drawings and pictures as images. Speaker notes are not carried over.
  const EMU = 914400, SLIDE_W = 12192000, SLIDE_H = 6858000;
  const NS_A = 'http://schemas.openxmlformats.org/drawingml/2006/main', NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships', NS_P = 'http://schemas.openxmlformats.org/presentationml/2006/main';
  const XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
  const GROUP = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>';
  // An image, SVG or not, drawn into a PNG through an <img>, where scripts can't run
  async function rasterize(src, maxWidth = 1600) {
    const img = new Image(); img.decoding = 'async'; img.src = src;
    await img.decode();
    const w0 = img.naturalWidth || 800, h0 = img.naturalHeight || 600, scale = Math.min(3, Math.max(1, maxWidth / w0));
    const canvas = document.createElement('canvas'); canvas.width = Math.round(w0 * scale); canvas.height = Math.round(h0 * scale);
    const g = canvas.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, canvas.width, canvas.height); g.drawImage(img, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('png');
    return {bytes: new Uint8Array(await blob.arrayBuffer()), w: canvas.width, h: canvas.height};
  }
  const svgElementData = node => svgData(new XMLSerializer().serializeToString(node).replace('<svg', '<svg style="background:#fff;font-family:Arial,sans-serif"'));
  // What a slide holds, read from the same rendering the panel shows
  function slideModel(slide) {
    const node = slideNode(slide), inner = node.querySelector('.ar-slide-inner');
    const model = {title: '', layout: node.classList.contains('ar-slide-section') ? 'section' : node.classList.contains('ar-slide-title') || node.classList.contains('ar-slide-center') ? 'title' : 'content', columns: [], figures: []};
    const runsOf = element => {
      const runs = [];
      const walk = (n, style) => {
        for (const child of n.childNodes) {
          if (child.nodeType === 3) { if (child.textContent) runs.push({text: child.textContent, ...style}); continue; }
          if (child.nodeType !== 1 || child.matches('ul,ol,figure,.ar-figure,img,svg,.ar-img')) continue;
          const tag = child.tagName;
          walk(child, {...style, bold: style.bold || tag === 'STRONG' || /^H\d$/.test(tag), italic: style.italic || tag === 'EM', mark: style.mark || tag === 'MARK', code: style.code || tag === 'CODE'});
        }
      };
      walk(element, {});
      return runs;
    };
    const blocksOf = container => {
      const blocks = [];
      for (const child of container.children) {
        if (/^H[1-6]$/.test(child.tagName) && !model.title) { model.title = child.textContent.trim(); continue; }
        if (child.matches('.ar-figure, figure')) { const img = child.querySelector('img'), svg = child.querySelector('svg'); if (img) model.figures.push({src: img.src, alt: img.alt}); else if (svg) model.figures.push({src: svgElementData(svg), alt: svg.getAttribute('aria-label') || ''}); continue; }
        if (child.matches('img.ar-pic')) { model.figures.push({src: child.src, alt: child.alt}); continue; }
        if (child.tagName === 'P') { for (const pic of child.querySelectorAll('img.ar-pic')) model.figures.push({src: pic.src, alt: pic.alt}); const runs = runsOf(child); if (runs.some(r => r.text.trim())) blocks.push({runs, bullet: false, level: 0}); continue; }
        if (child.tagName === 'UL' || child.tagName === 'OL') { [...child.children].forEach((li, k) => blocks.push({runs: runsOf(li), bullet: child.tagName === 'UL' ? '•' : (k + 1) + '.', level: Math.round(parseFloat(li.style.marginInlineStart || '0') / 1.25)})); continue; }
        if (child.matches('.ar-tablebox')) { for (const tr of child.querySelectorAll('tr')) blocks.push({runs: [{text: [...tr.children].map(cell => cell.textContent.trim()).join('   |   '), bold: !!tr.querySelector('th')}], bullet: false, level: 0}); continue; }
        if (child.matches('.ar-callout, blockquote')) { const title = child.querySelector('.ar-callout-title')?.textContent; const text = [...child.children].filter(x => !x.matches('.ar-callout-title')).map(x => x.textContent.trim()).join(' '); blocks.push({runs: [...(title ? [{text: title + ': ', bold: true}] : []), {text}], bullet: false, level: 0, accent: true}); continue; }
        if (child.tagName === 'PRE') { for (const line of child.textContent.split('\n').slice(0, 14)) blocks.push({runs: [{text: line || ' ', code: true}], bullet: false, level: 0}); continue; }
        if (child.matches('.ar-cols')) { for (const col of child.children) model.columns.push(blocksOf(col)); continue; }
        if (child.textContent.trim()) blocks.push({runs: [{text: child.textContent.trim()}], bullet: false, level: 0});
      }
      return blocks;
    };
    model.body = blocksOf(inner);
    return model;
  }
  const runXml = (run, size, color) => {
    const props = `lang="${xmlEscape(lang)}" sz="${size}"${run.bold ? ' b="1"' : ''}${run.italic ? ' i="1"' : ''} dirty="0"`;
    const font = run.code ? '<a:latin typeface="Consolas"/><a:cs typeface="Consolas"/>' : '';
    return `<a:r><a:rPr ${props}><a:solidFill><a:srgbClr val="${color}"/></a:solidFill>${run.mark ? '<a:highlight><a:srgbClr val="FDE68A"/></a:highlight>' : ''}${font}</a:rPr><a:t>${xmlEscape(run.text)}</a:t></a:r>`;
  };
  const paragraphXml = (block, size, color, align) => {
    const indent = 342900 + block.level * 342900;
    const ppr = block.bullet ? `<a:pPr marL="${indent}" indent="-342900"${align ? ` algn="${align}"` : ''}><a:buFont typeface="Arial"/><a:buChar char="${xmlEscape(block.bullet.length > 1 ? '•' : block.bullet)}"/></a:pPr>` : `<a:pPr${align ? ` algn="${align}"` : ''}><a:buNone/></a:pPr>`;
    const runs = block.runs.filter(r => r.text).map(r => runXml(r, size, block.accent ? '1D4ED8' : color)).join('');
    return `<a:p>${ppr}${runs || `<a:endParaRPr lang="${xmlEscape(lang)}" sz="${size}" dirty="0"/>`}</a:p>`;
  };
  const textBox = (id, x, y, w, h, paragraphs, anchor = 't') => `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="Text ${id}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="${Math.round(x)}" y="${Math.round(y)}"/><a:ext cx="${Math.round(w)}" cy="${Math.round(h)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720" anchor="${anchor}"><a:normAutofit/></a:bodyPr><a:lstStyle/>${paragraphs}</p:txBody></p:sp>`;
  const pictureXml = (id, rel, x, y, w, h, alt) => `<p:pic><p:nvPicPr><p:cNvPr id="${id}" name="Picture ${id}" descr="${xmlEscape(alt || '')}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${rel}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="${Math.round(x)}" y="${Math.round(y)}"/><a:ext cx="${Math.round(w)}" cy="${Math.round(h)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>`;
  // Fits a picture in its box without enlarging a small one past three times its own size
  const fit = (w, h, boxW, boxH) => { const s = Math.min(boxW / w, boxH / h, 3 * 9525); return [w * s, h * s]; };
  const bodySize = blocks => { const lines = blocks.reduce((sum, b) => sum + Math.max(1, Math.ceil(b.runs.reduce((n, r) => n + r.text.length, 0) / 70)), 0); return lines > 14 ? 1400 : lines > 10 ? 1600 : lines > 7 ? 1800 : 2000; };
  async function pptxOf(a, text) {
    await diagramsReady(text);
    const slides = slidesOf(withoutMarker(text).trim() ? text : '# ' + displayTitle(a));
    const files = [], media = [], enc = new TextEncoder();
    const slideXml = [];
    for (const [k, slide] of slides.entries()) {
      const m = slideModel(slide), rels = [`<Relationship Id="rId1" Type="${NS_R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>`];
      const dark = m.layout === 'section', ink = dark ? 'FFFFFF' : '111111';
      let id = 2, shapes = '';
      const margin = 0.6 * EMU, top = 0.45 * EMU, titleH = 1.0 * EMU, contentY = top + titleH + 0.15 * EMU, contentH = SLIDE_H - contentY - 0.45 * EMU, width = SLIDE_W - 2 * margin;
      const images = [];
      for (const figure of m.figures.slice(0, 4)) { try { images.push({...(await rasterize(figure.src)), alt: figure.alt}); } catch {} }
      if (m.layout === 'title' || m.layout === 'section') {
        const sub = m.body.map(b => paragraphXml(b, 2400, dark ? 'E2E8F0' : '444444', 'ctr')).join('');
        shapes += textBox(id++, margin, SLIDE_H * 0.3, width, 1.5 * EMU, paragraphXml({runs: [{text: m.title || displayTitle(a), bold: true}], bullet: false, level: 0}, 4800, ink, m.layout === 'section' ? 'l' : 'ctr'), m.layout === 'section' ? 'b' : 'ctr');
        if (sub) shapes += textBox(id++, margin, SLIDE_H * 0.3 + 1.6 * EMU, width, 1.6 * EMU, sub);
      } else {
        if (m.title) shapes += textBox(id++, margin, top, width, titleH, paragraphXml({runs: [{text: m.title, bold: true}], bullet: false, level: 0}, 3600, ink), 'b');
        const hasText = m.body.length || m.columns.length;
        const textW = images.length && hasText ? width * 0.5 : width;
        if (m.columns.length) {
          const gap = 0.3 * EMU, colW = (textW - gap * (m.columns.length - 1)) / m.columns.length;
          let y = contentY;
          if (m.body.length) { shapes += textBox(id++, margin, y, textW, 0.9 * EMU, m.body.map(b => paragraphXml(b, bodySize(m.body), ink)).join('')); y += 0.95 * EMU; }
          m.columns.forEach((col, i) => { shapes += textBox(id++, margin + i * (colW + gap), y, colW, contentY + contentH - y, col.map(b => paragraphXml(b, bodySize(col), ink)).join('')); });
        } else if (m.body.length) shapes += textBox(id++, margin, contentY, textW, contentH, m.body.map(b => paragraphXml(b, bodySize(m.body), ink)).join(''));
        if (images.length) {
          const areaX = hasText ? margin + textW + 0.3 * EMU : margin, areaW = hasText ? width - textW - 0.3 * EMU : width;
          const each = (contentH - 0.15 * EMU * (images.length - 1)) / images.length;
          images.forEach((img, i) => {
            const [w, h] = fit(img.w, img.h, areaW, each);
            const name = 'image' + (media.length + 1) + '.png';
            media.push({name, data: img.bytes});
            const rel = 'rId' + (rels.length + 1);
            rels.push(`<Relationship Id="${rel}" Type="${NS_R}/image" Target="../media/${name}"/>`);
            shapes += pictureXml(id++, rel, areaX + (areaW - w) / 2, contentY + i * (each + 0.15 * EMU) + (each - h) / 2, w, h, img.alt);
          });
        }
      }
      const bg = dark ? '<p:bg><p:bgPr><a:solidFill><a:srgbClr val="1E293B"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>' : '';
      slideXml.push(`${XML_HEAD}<p:sld xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}"><p:cSld>${bg}<p:spTree>${GROUP}${shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>`);
      files.push([`ppt/slides/_rels/slide${k + 1}.xml.rels`, `${XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${rels.join('')}</Relationships>`]);
    }
    const n2 = slideXml.length;
    const scheme = ['111111', 'FFFFFF', '1E293B', 'F3F4F6', '2563EB', '16A34A', 'D97706', 'DB2777', '7C3AED', '0891B2', '2563EB', '7C3AED'];
    const names = ['dk1', 'lt1', 'dk2', 'lt2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink'];
    const three = x => x + x + x;
    const theme = `${XML_HEAD}<a:theme xmlns:a="${NS_A}" name="Artifacts"><a:themeElements><a:clrScheme name="Artifacts">${names.map((nm, i) => `<a:${nm}><a:srgbClr val="${scheme[i]}"/></a:${nm}>`).join('')}</a:clrScheme><a:fontScheme name="Artifacts"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="Artifacts"><a:fillStyleLst>${three('<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>')}</a:fillStyleLst><a:lnStyleLst>${three('<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>')}</a:lnStyleLst><a:effectStyleLst>${three('<a:effectStyle><a:effectLst/></a:effectStyle>')}</a:effectStyleLst><a:bgFillStyleLst>${three('<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>')}</a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>`;
    const master = `${XML_HEAD}<p:sldMaster xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}"><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>${GROUP}</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="3600"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="2000"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles></p:sldMaster>`;
    const layout = `${XML_HEAD}<p:sldLayout xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}" type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>${GROUP}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>`;
    const now = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    files.push(
      ['[Content_Types].xml', `${XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>${slideXml.map((_, i) => `<Override PartName="/ppt/slides/slide${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>`).join('')}<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>`],
      ['_rels/.rels', `${XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${NS_R}/officeDocument" Target="ppt/presentation.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="${NS_R}/extended-properties" Target="docProps/app.xml"/></Relationships>`],
      ['docProps/core.xml', `${XML_HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>${xmlEscape(displayTitle(a))}</dc:title><dcterms:created xsi:type="dcterms:W3CDTF">${now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">${now}</dcterms:modified></cp:coreProperties>`],
      ['docProps/app.xml', `${XML_HEAD}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>${xmlEscape(appName)} Artifacts</Application><Slides>${n2}</Slides></Properties>`],
      ['ppt/presentation.xml', `${XML_HEAD}<p:presentation xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}" saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>${slideXml.map((_, i) => `<p:sldId id="${256 + i}" r:id="rId${i + 3}"/>`).join('')}</p:sldIdLst><p:sldSz cx="${SLIDE_W}" cy="${SLIDE_H}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>`],
      ['ppt/_rels/presentation.xml.rels', `${XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${NS_R}/slideMaster" Target="slideMasters/slideMaster1.xml"/><Relationship Id="rId2" Type="${NS_R}/theme" Target="theme/theme1.xml"/>${slideXml.map((_, i) => `<Relationship Id="rId${i + 3}" Type="${NS_R}/slide" Target="slides/slide${i + 1}.xml"/>`).join('')}</Relationships>`],
      ['ppt/slideMasters/slideMaster1.xml', master],
      ['ppt/slideMasters/_rels/slideMaster1.xml.rels', `${XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${NS_R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="${NS_R}/theme" Target="../theme/theme1.xml"/></Relationships>`],
      ['ppt/slideLayouts/slideLayout1.xml', layout],
      ['ppt/slideLayouts/_rels/slideLayout1.xml.rels', `${XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${NS_R}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>`],
      ['ppt/theme/theme1.xml', theme]
    );
    slideXml.forEach((xml, i) => files.push([`ppt/slides/slide${i + 1}.xml`, xml]));
    const entries = files.map(([name, body]) => ({name, data: enc.encode(body)}));
    for (const item of media) entries.push({name: 'ppt/media/' + item.name, data: item.data});
    // [Content_Types].xml first, as Office expects
    entries.sort((x, y) => (x.name === '[Content_Types].xml' ? -1 : y.name === '[Content_Types].xml' ? 1 : 0));
    return makeZip(entries, 'application/vnd.openxmlformats-officedocument.presentationml.presentation');
  }

  /* ---- motion: reading ---- */
  // A ```motion block is a short animated explainer described as scenes, never as code. Settings may come first
  // ("size: 16:9", "theme: dark", "accent: #e11d48", "background: #0f172a", "font: serif"); each scene opens with a
  // line such as "--- 4s fade" and holds headings, text, bullets, a big number, a chart, steps, a picture and a
  // spoken caption. A line may end with {effect} or {effect at 1.5s} to choose how and when it comes in.
  const MOTION_SIZES = {'16:9': [1280, 720], '9:16': [720, 1280], '1:1': [1080, 1080], '4:3': [960, 720], '4:5': [864, 1080]};
  const MOTION_TRANSITIONS = ['fade', 'slide', 'zoom', 'wipe', 'cut', 'flash', 'iris', 'rise', 'blur'];
  // How a line comes in. The last six move its words or letters: kinetic type.
  const MOTION_EFFECTS = ['fade', 'up', 'down', 'left', 'right', 'zoom', 'pop', 'type', 'none', 'words', 'letters', 'scramble', 'wave', 'blur', 'drop'];
  const MOTION_CHARTS = ['bar', 'line', 'area', 'pie', 'donut', 'hbar', 'race'];
  const MOTION_STYLES_LIST = ['minimal', 'bold', 'corporate', 'neon', 'paper', 'editorial', 'playful', 'chalk'];
  const MOTION_CAMERAS = ['still', 'zoom-in', 'zoom-out', 'pan-left', 'pan-right', 'pan-up', 'pan-down', 'drift'];
  const MOTION_PATTERNS = ['none', 'grid', 'dots', 'lines', 'waves', 'rings', 'blobs'];
  const MOTION_PARTICLES = ['none', 'confetti', 'sparkles', 'snow', 'bokeh', 'bubbles'];
  const MOTION_SHAPES = ['circle', 'square', 'ring', 'triangle', 'star', 'blob', 'line', 'dots', 'arrow', 'plus'];
  const MOTION_PLACES = ['top-left', 'top', 'top-right', 'left', 'center', 'right', 'bottom-left', 'bottom', 'bottom-right'];
  const MOTION_LIMITS = {scenes: 40, items: 12, bullets: 8, sceneMin: 0.5, sceneMax: 30, total: 180, chars: 300, rows: 24, series: 6, steps: 6, shapes: 6, frames: 12};
  const SCENE_RE = /^\s*-{3,}(?:\s+(.*?))?\s*$/;
  const SETTING_RE = /^\s*(size|theme|style|accent|background|font|title|logo|progress|captions|music|voice)\s*:\s*(.*?)\s*$/i;
  const COMMENT_RE = /^\s*(?:\/\/|%%|<!--.*-->\s*$)/;
  const colorOk = value => {
    if (!value || value.length > 60) return false;
    try { if (typeof window !== 'undefined' && window.CSS?.supports) return window.CSS.supports('color', value); } catch {}
    // Without a browser to ask (the tests), hex, rgb(), hsl() and the common colour names
    return /^(#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})|(?:rgb|hsl)a?\([\d\s.,%/+-]*\)|black|white|red|green|blue|yellow|orange|purple|pink|gray|grey|navy|teal|maroon|olive|lime|aqua|fuchsia|silver|gold|indigo|violet|brown|cyan|magenta|crimson|coral|salmon|beige|ivory|khaki|lavender|tomato|turquoise|transparent)$/i.test(value);
  };
  // The settings and scenes as written, so one scene can be changed and the rest kept exactly as they were
  function motionParts(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const prefix = [], scenes = [];
    let i = 0;
    for (; i < lines.length; i++) {
      const line = lines[i];
      if (SCENE_RE.test(line)) break;
      if (!line.trim() || MARKER_RE.test(line) || SETTING_RE.test(line) || COMMENT_RE.test(line)) { prefix.push(line); continue; }
      break;
    }
    // Content before the first "---" is a scene of its own
    let current = null;
    for (; i < lines.length; i++) {
      const line = lines[i];
      if (SCENE_RE.test(line)) { current = {head: line.trim(), lines: []}; scenes.push(current); continue; }
      if (!current) { current = {head: '', lines: []}; scenes.push(current); }
      current.lines.push(line);
    }
    return {prefix, scenes};
  }
  const motionText = parts => [...parts.prefix, ...parts.scenes.flatMap(scene => [scene.head || '---', ...scene.lines])].join('\n').replace(/\n*$/, '\n');
  // "€1,350.5M" counts up as €0.0M … €1,350.5M, keeping its signs, separators and decimals
  function countOf(raw) {
    const m = /^(\D*?)(-?\d(?:[\d,.']|\s(?=\d))*)(.*)$/.exec(String(raw || '').trim());
    // "€12" and "$3.2B" count; "Q3" or "Room 101" are names, not amounts
    if (!m || /\p{L}/u.test(m[1])) return null;
    let digits = m[2].trim(), mark = '.', group = '';
    const lastDot = digits.lastIndexOf('.'), lastComma = digits.lastIndexOf(',');
    if (lastComma > lastDot && digits.length - lastComma - 1 !== 3) { mark = ','; group = lastDot >= 0 ? '.' : /\s/.test(digits) ? ' ' : ''; }
    else if (lastDot > lastComma && (lastComma >= 0 || digits.length - lastDot - 1 !== 3 || (digits.match(/\./g) || []).length === 1)) { mark = '.'; group = lastComma >= 0 ? ',' : /\s/.test(digits) ? ' ' : /'/.test(digits) ? "'" : ''; }
    else { group = (/[,.\s']/.exec(digits) || [''])[0]; mark = group === '.' ? ',' : '.'; }
    const clean = digits.split(group || '\u0000').join('').replace(/\s/g, '').replace(mark, '.');
    const value = parseFloat(clean);
    if (!Number.isFinite(value)) return null;
    const decimals = clean.includes('.') ? clean.split('.')[1].length : 0;
    return {prefix: m[1], value, decimals: Math.min(decimals, 4), mark, group, suffix: m[3]};
  }
  function countText(spec, value) {
    const fixed = Math.abs(value).toFixed(spec.decimals);
    let [whole, part] = fixed.split('.');
    if (spec.group) whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, spec.group);
    return spec.prefix + (value < 0 ? '-' : '') + whole + (part ? spec.mark + part : '') + spec.suffix;
  }
  function readSeconds(token) {
    const m = /^(\d+(?:[.,]\d+)?)\s*(ms|s|sec|secs|seconds?)?$/i.exec(String(token || '').trim());
    if (!m) return null;
    const value = parseFloat(m[1].replace(',', '.'));
    return /^ms$/i.test(m[2] || '') ? value / 1000 : value;
  }
  // What a scene line says, and how it comes in
  function motionLine(raw, warn, line) {
    let text = raw.trim(), effect = '', at = null;
    const tail = /\s*\{([^{}]*)\}\s*$/.exec(text);
    if (tail) {
      text = text.slice(0, tail.index).trim();
      for (const word of tail[1].toLowerCase().split(/[\s,]+/).filter(Boolean)) {
        if (MOTION_EFFECTS.includes(word)) effect = word;
        else if (/^(?:at|@|\+)?\d/.test(word) || word === 'at') { const s = readSeconds(word.replace(/^(at|@|\+)/, '')); if (s != null) at = clamp(s, 0, MOTION_LIMITS.sceneMax); }
        else warn(line, 'mw_effect', {value: word});
      }
    }
    if (text.length > MOTION_LIMITS.chars) { text = text.slice(0, MOTION_LIMITS.chars); warn(line, 'mw_long', {n: MOTION_LIMITS.chars}); }
    return {text, effect, at};
  }
  function motionScene(part, index, warn, base) {
    const scene = {part: index, duration: null, transition: 'fade', items: [], say: '', background: '', layout: '', camera: '', pattern: '', particles: '', shapes: [], lower: null, line: base};
    const choose = (list, value, line, what) => { const word = String(value || '').trim().toLowerCase(); if (list.includes(word)) return word; warn(line, 'mw_value', {what, value: String(value || '').trim()}); return ''; };
    // "--- 4s fade" sets the scene's length and how it arrives
    const head = SCENE_RE.exec(part.head || '');
    for (const word of (head?.[1] || '').split(/\s+/).filter(Boolean)) {
      const seconds = readSeconds(word);
      if (seconds != null) scene.duration = seconds;
      else if (MOTION_TRANSITIONS.includes(word.toLowerCase())) scene.transition = word.toLowerCase();
      else if (/^\d/.test(word)) warn(base, 'mw_duration', {value: word, n: 3});
      else warn(base, 'mw_transition', {value: word});
    }
    const lines = part.lines;
    let bullets = 0;
    for (let i = 0; i < lines.length; i++) {
      const line = base + 1 + i, raw = lines[i];
      if (!raw.trim() || COMMENT_RE.test(raw) || MARKER_RE.test(raw)) continue;
      const directive = /^\s*([a-z]+)\s*:\s*(.*)$/i.exec(raw);
      const key = directive?.[1].toLowerCase();
      const add = item => { if (scene.items.length >= MOTION_LIMITS.items) { if (!scene.full) { scene.full = true; warn(line, 'mw_items', {n: MOTION_LIMITS.items}); } return; } scene.items.push({line, ...item}); };
      if (key === 'duration' || key === 'length') { const s = readSeconds(directive[2]); if (s != null) scene.duration = s; else warn(line, 'mw_duration', {value: directive[2], n: 3}); continue; }
      if (key === 'transition') { const word = directive[2].trim().toLowerCase(); if (MOTION_TRANSITIONS.includes(word)) scene.transition = word; else warn(line, 'mw_transition', {value: directive[2]}); continue; }
      if (key === 'background') { const value = directive[2].trim(); if (value.split(',').every(each => colorOk(each.trim()))) scene.background = value; else warn(line, 'mw_color', {value}); continue; }
      if (key === 'layout') { const word = directive[2].trim().toLowerCase(); if (['center', 'left', 'split'].includes(word)) scene.layout = word; continue; }
      if (key === 'say' || key === 'caption' || key === 'voice') { scene.say = motionLine(directive[2], warn, line).text; continue; }
      if (key === 'camera') { scene.camera = choose(MOTION_CAMERAS, directive[2], line, 'camera'); continue; }
      if (key === 'pattern') { scene.pattern = choose(MOTION_PATTERNS, directive[2], line, 'pattern'); continue; }
      if (key === 'particles' || key === 'fx') { scene.particles = choose(MOTION_PARTICLES, directive[2], line, 'particles'); continue; }
      if (key === 'lower' || key === 'lowerthird') { const [name, ...role] = motionLine(directive[2], warn, line).text.split('|'); scene.lower = {name: name.trim(), role: role.join('|').trim()}; continue; }
      // "shape: circle #f59e0b top-right large {spin}": something drawn behind the scene, not part of its flow
      if (key === 'shape') {
        let text = directive[2].trim(), effect = '', motionWord = '';
        const tail = /\s*\{([^{}]*)\}\s*$/.exec(text);
        if (tail) { text = text.slice(0, tail.index); for (const w of tail[1].toLowerCase().split(/[\s,]+/).filter(Boolean)) { if (['spin', 'float', 'pulse', 'draw'].includes(w)) motionWord = w; else if (MOTION_EFFECTS.includes(w)) effect = w; else warn(line, 'mw_effect', {value: w}); } }
        const shape = {kind: '', color: '', place: 'center', size: 'medium', motion: motionWord, effect, line};
        for (const word of text.split(/\s+/).filter(Boolean)) {
          const w = word.toLowerCase();
          if (MOTION_SHAPES.includes(w)) shape.kind = w;
          else if (MOTION_PLACES.includes(w)) shape.place = w;
          else if (['small', 'medium', 'large', 'huge'].includes(w)) shape.size = w;
          else if (colorOk(word)) shape.color = word;
          else warn(line, 'mw_value', {what: 'shape', value: word});
        }
        if (!shape.kind) { if (!text.split(/\s+/).some(w => w && !MOTION_SHAPES.includes(w.toLowerCase()) && !MOTION_PLACES.includes(w.toLowerCase()) && !['small', 'medium', 'large', 'huge'].includes(w.toLowerCase()) && !colorOk(w))) warn(line, 'mw_value', {what: 'shape', value: text}); continue; }
        if (scene.shapes.length >= MOTION_LIMITS.shapes) { warn(line, 'mw_items', {n: MOTION_LIMITS.shapes}); continue; }
        scene.shapes.push(shape);
        continue;
      }
      if (key === 'icon') { const {text, effect, at} = motionLine(directive[2], warn, line); const [name, ...label] = text.split('|'); add({kind: 'icon', name: name.trim(), label: label.join('|').trim(), effect, at}); continue; }
      if (key === 'icons') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const list = text.split(/\s*[,;]\s*/).filter(Boolean).map(each => { const [name, ...label] = each.trim().split(/\s+/); return {name, label: label.join(' ')}; });
        if (list.length > MOTION_LIMITS.steps) warn(line, 'mw_items', {n: MOTION_LIMITS.steps});
        if (list.length) add({kind: 'icons', list: list.slice(0, MOTION_LIMITS.steps), effect, at});
        continue;
      }
      // "progress: 72% | Course completed" fills a bar; "ring:" a circle. "3/5" works as well as a percentage.
      if (key === 'progress' || key === 'ring') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const [value, ...label] = text.split('|');
        const fraction = /^\s*(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)/.exec(value), count = countOf(value);
        let ratio = fraction ? parseFloat(fraction[1].replace(',', '.')) / (parseFloat(fraction[2].replace(',', '.')) || 1) : count ? count.value / 100 : null;
        if (ratio == null) { warn(line, 'mw_value', {what: key, value: value.trim()}); continue; }
        if (ratio < 0 || ratio > 1) { warn(line, 'mw_value', {what: key, value: value.trim()}); ratio = clamp(ratio, 0, 1); }
        add({kind: key, value: value.trim(), label: label.join('|').trim(), ratio, count: fraction ? null : count, effect, at});
        continue;
      }
      if (key === 'timeline') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const marks = text.split(/\s*[;|]\s*/).filter(Boolean).map(each => { const [when, ...what] = each.trim().split(/\s+/); return {when, what: what.join(' ')}; });
        if (marks.length > MOTION_LIMITS.steps) warn(line, 'mw_items', {n: MOTION_LIMITS.steps});
        if (marks.length) add({kind: 'timeline', marks: marks.slice(0, MOTION_LIMITS.steps), effect, at});
        continue;
      }
      // "compare: Before | After", then lines "- old way | new way"
      if (key === 'compare' || key === 'versus' || key === 'vs') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const [left, ...right] = text.split('|'); const rows = [];
        while (i + 1 < lines.length && /^\s*[-*+•]\s+/.test(lines[i + 1]) && rows.length < MOTION_LIMITS.bullets) { const [a, ...b] = lines[++i].replace(/^\s*[-*+•]\s+/, '').split('|'); rows.push([a.trim(), b.join('|').trim()]); }
        add({kind: 'compare', left: left.trim(), right: right.join('|').trim(), rows, effect, at});
        continue;
      }
      if (key === 'quote') { const {text, effect, at} = motionLine(directive[2], warn, line); const [body, ...who] = text.split('|'); add({kind: 'quote', text: body.trim().replace(/^["“«]|["”»]$/g, ''), author: who.join('|').trim(), effect, at}); continue; }
      if (key === 'badge' || key === 'tag') { const {text, effect, at} = motionLine(directive[2], warn, line); add({kind: 'badge', text, effect, at}); continue; }
      if (key === 'number' || key === 'big' || key === 'stat') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const [value, ...label] = text.split('|');
        add({kind: 'number', value: value.trim(), label: label.join('|').trim(), count: countOf(value), effect, at});
        continue;
      }
      if (key === 'steps' || key === 'step' || key === 'flow') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const steps = text.split(/\s*(?:->|→|=>|➜|\|)\s*/).map(s => s.trim()).filter(Boolean);
        if (steps.length > MOTION_LIMITS.steps) warn(line, 'mw_items', {n: MOTION_LIMITS.steps});
        if (steps.length) add({kind: 'steps', steps: steps.slice(0, MOTION_LIMITS.steps), effect, at});
        continue;
      }
      if (key === 'image' || key === 'picture' || /^\s*!\[/.test(raw)) {
        let src = '', caption = '';
        const md = /^\s*!\[([^\]]*)\]\(\s*([^)\s]+)[^)]*\)\s*(\{[^{}]*\})?\s*$/.exec(raw);
        const read = motionLine(md ? (md[3] || '') : directive[2], warn, line), {effect, at} = read;
        if (md) { caption = md[1]; src = md[2]; } else { const [a, ...b] = read.text.split('|'); src = a.trim(); caption = b.join('|').trim(); }
        add({kind: 'image', src, caption, effect, at});
        continue;
      }
      if (key === 'chart') {
        const {text, effect, at} = motionLine(directive[2], warn, line);
        const words = text.split(/\s+/).filter(Boolean);
        let type = 'bar';
        if (words.length && MOTION_CHARTS.includes(words[0].toLowerCase())) type = words.shift().toLowerCase();
        else if (words.length && /^[a-z]+$/i.test(words[0]) && words.length === 1) { warn(line, 'mw_chart_type', {value: words[0]}); words.shift(); }
        // Its data are the CSV lines that follow, up to a blank line or the next instruction
        const rows = [];
        while (i + 1 < lines.length && lines[i + 1].trim() && !/^\s*([a-z]+\s*:|#|-\s|\*\s|>|!\[)/i.test(lines[i + 1])) rows.push(lines[++i]);
        const grid = gridOf(rows.join('\n'), 'csv').rows.slice(0, MOTION_LIMITS.rows + 1), engine = sheetEngine(grid);
        const numeric = (grid[0] || []).map((_, c) => c).filter(c => c > 0 && grid.slice(1).some((_, r) => typeof engine.value(r + 1, c) === 'number')).slice(0, MOTION_LIMITS.series);
        if (grid.length < 2 || !numeric.length) { warn(line, 'mw_chart'); continue; }
        // Names and labels are read as written ("2025" stays 2025), unless they are formulas
        const asWritten = (r, c) => { const raw = String(grid[r][c] ?? '').trim(); return raw.startsWith('=') ? shown(engine.value(r, c)) : raw; };
        const data = {labels: grid.slice(1).map((_, r) => asWritten(r + 1, 0)), series: numeric.map(c => ({name: asWritten(0, c), values: grid.slice(1).map((_, r) => { const v = engine.value(r + 1, c); return typeof v === 'number' && Number.isFinite(v) ? v : 0; })}))};
        add({kind: 'chart', type, title: words.join(' '), data, effect, at});
        continue;
      }
      const {text, effect, at} = motionLine(raw, warn, line);
      const heading = /^(#{1,6})\s+(.*)$/.exec(text);
      if (heading) { add({kind: heading[1].length === 1 ? 'title' : 'subtitle', text: heading[2].replace(/\s+#+\s*$/, ''), effect, at}); continue; }
      const bullet = /^(?:[-*+•]|\d{1,3}[.)])\s+(.*)$/.exec(text);
      if (bullet) { if (++bullets > MOTION_LIMITS.bullets) { if (bullets === MOTION_LIMITS.bullets + 1) warn(line, 'mw_items', {n: MOTION_LIMITS.bullets}); continue; } add({kind: 'bullet', text: bullet[1], effect, at}); continue; }
      const quote = /^>\s?(.*)$/.exec(text);
      if (quote) { const body = quote[1].replace(/^\[!\w+\]\s*/, '').trim(); if (body) { const last = scene.items[scene.items.length - 1]; if (last?.kind === 'callout' && last.line === line - 1) { last.text += ' ' + body; last.line = line; } else add({kind: 'callout', text: body, effect, at}); } continue; }
      add({kind: 'text', text, effect, at});
    }
    return scene;
  }
  const wordsIn = text => (String(text || '').match(/\S+/g) || []).length;
  // A scene without a length gets one from what it holds, and long enough to read its caption
  function sceneLength(scene) {
    let need = 1.2;
    for (const item of scene.items) need += {title: 0.9, subtitle: 0.6, bullet: 0.9, number: 1.8, chart: item.type === 'race' ? 1.2 * (item.data?.series.length || 1) : 2.6, steps: 0.7 * (item.steps?.length || 1), image: 1.2, callout: 0.4 + wordsIn(item.text) / 4,
      icon: 1, icons: 0.5 * (item.list?.length || 1), progress: 1.6, ring: 1.8, timeline: 0.7 * (item.marks?.length || 1), compare: 0.8 * (item.rows?.length || 1) + 0.6, quote: 1 + wordsIn(item.text) / 3.5, badge: 0.3}[item.kind] ?? 0.3 + wordsIn(item.text) / 4;
    need = Math.max(need, scene.say ? 1 + wordsIn(scene.say) / 2.6 : 0);
    return clamp(Math.round(need * 2) / 2, 2, 12);
  }
  function motionOf(text) {
    const warnings = [];
    const warn = (line, key, vars = {}) => { if (warnings.length < 30) warnings.push({line, key, vars}); };
    const parts = motionParts(text);
    const settings = {size: '16:9', w: 1280, h: 720, theme: 'light', style: '', accent: '', background: '', font: '', title: '', logo: '', progress: false, captions: 'karaoke', music: '', volume: 0.35, voice: false};
    const yes = value => /^(on|yes|true|1)$/i.test(value);
    parts.prefix.forEach((line, i) => {
      const m = SETTING_RE.exec(line);
      if (!m) return;
      const key = m[1].toLowerCase(), value = m[2].trim();
      if (key === 'size') { const size = value.replace(/\s+/g, '').replace(/[x×/]/, ':'); const named = {landscape: '16:9', wide: '16:9', portrait: '9:16', vertical: '9:16', story: '9:16', square: '1:1'}[value.toLowerCase()] || size; if (MOTION_SIZES[named]) { settings.size = named; [settings.w, settings.h] = MOTION_SIZES[named]; } else warn(i + 1, 'mw_size', {value}); }
      else if (key === 'theme') { const word = value.toLowerCase(); if (word === 'light' || word === 'dark') settings.theme = word; else if (colorOk(value)) settings.background = value; else warn(i + 1, 'mw_color', {value}); }
      else if (key === 'accent') { if (colorOk(value)) settings.accent = value; else warn(i + 1, 'mw_color', {value}); }
      else if (key === 'background') { if (value.split(',').every(each => colorOk(each.trim()))) settings.background = value; else warn(i + 1, 'mw_color', {value}); }
      else if (key === 'font') { const word = value.toLowerCase(); settings.font = ['serif', 'mono', 'rounded', 'sans'].includes(word) ? word : (warn(i + 1, 'mw_value', {what: 'font', value}), ''); }
      else if (key === 'title') settings.title = value.slice(0, 120);
      else if (key === 'style') { const word = value.toLowerCase(); if (MOTION_STYLES_LIST.includes(word)) settings.style = word; else warn(i + 1, 'mw_value', {what: 'style', value}); }
      else if (key === 'logo') settings.logo = value.split('|')[0].trim();
      else if (key === 'progress') settings.progress = yes(value);
      else if (key === 'captions') { const word = value.toLowerCase(); if (['karaoke', 'plain', 'off'].includes(word)) settings.captions = word; else warn(i + 1, 'mw_value', {what: 'captions', value}); }
      // "music: /api/v1/files/…/content | 0.3": a track from this site under the whole video, at that volume
      else if (key === 'music') { const [src, level] = value.split('|').map(v => v.trim()); settings.music = src || ''; const v = parseFloat(String(level || '').replace('%', '')); if (Number.isFinite(v)) settings.volume = clamp(/%/.test(level) || v > 1 ? v / 100 : v, 0, 1); }
      else if (key === 'voice') settings.voice = yes(value);
    });
    let line = parts.prefix.length + 1;
    const scenes = [];
    parts.scenes.forEach((part, index) => {
      const base = part.head ? line : line - 1;
      const scene = motionScene(part, index, warn, base);
      line += (part.head ? 1 : 0) + part.lines.length;
      if (!scene.items.length && !scene.say && !scene.lower) return;
      scenes.push(scene);
    });
    if (scenes.length > MOTION_LIMITS.scenes) { warn(scenes[MOTION_LIMITS.scenes].line, 'mw_scenes', {n: MOTION_LIMITS.scenes}); scenes.length = MOTION_LIMITS.scenes; }
    for (const scene of scenes) {
      if (scene.duration != null && (scene.duration < MOTION_LIMITS.sceneMin || scene.duration > MOTION_LIMITS.sceneMax)) { warn(scene.line, 'mw_duration', {value: scene.duration + 's', n: clamp(scene.duration, MOTION_LIMITS.sceneMin, MOTION_LIMITS.sceneMax)}); }
      scene.given = scene.duration == null ? sceneLength(scene) : clamp(scene.duration, MOTION_LIMITS.sceneMin, MOTION_LIMITS.sceneMax);
    }
    if (scenes[0]) scenes[0].transition = 'fade';
    if (!settings.title) settings.title = scenes.flatMap(s => s.items).find(item => item.kind === 'title')?.text || '';
    const model = {settings, w: settings.w, h: settings.h, scenes, total: 0, warnings};
    timeScenes(model, warn);
    return model;
  }
  // Where each scene starts. A scene lasts as long as it was given, or as long as its narration if that is longer,
  // and the whole video stops at the limit.
  function timeScenes(model, warn) {
    let start = 0;
    const scenes = model.scenes;
    for (let i = 0; i < scenes.length; i++) {
      const scene = scenes[i];
      scene.duration = Math.max(scene.given, scene.spoken ? Math.min(MOTION_LIMITS.sceneMax, scene.spoken + 0.6) : 0);
      if (start + scene.duration > MOTION_LIMITS.total + 1e-9) {
        if (warn && !model.warnings.some(w => w.key === 'mw_total')) warn(scene.line, 'mw_total', {n: MOTION_LIMITS.total});
        scene.duration = MOTION_LIMITS.total - start;
        if (scene.duration < MOTION_LIMITS.sceneMin) { scenes.length = i; break; }
      }
      scene.start = start; start += scene.duration;
    }
    model.total = start;
    model.layouts?.clear();
  }

  /* ---- motion: drawing ---- */
  const MOTION_THEMES = {
    light: {bg: '#ffffff', text: '#111827', muted: '#4b5563', accent: '#2563eb', soft: 'rgba(17,24,39,0.06)', line: 'rgba(17,24,39,0.14)'},
    dark: {bg: '#0b1020', text: '#f3f4f6', muted: '#a3acb9', accent: '#60a5fa', soft: 'rgba(255,255,255,0.08)', line: 'rgba(255,255,255,0.18)'}
  };
  // Ready-made looks, in the spirit of design templates: colours, type, a background pattern and how titles arrive
  const MOTION_STYLES = {
    minimal: {...MOTION_THEMES.light},
    bold: {bg: '#facc15', text: '#111111', muted: '#3f3f46', accent: '#111111', soft: 'rgba(0,0,0,0.08)', line: 'rgba(0,0,0,0.22)', titleWeight: 900, upper: true, effects: {title: 'drop'}},
    corporate: {bg: '#ffffff', text: '#0f172a', muted: '#475569', accent: '#2563eb', soft: 'rgba(37,99,235,0.07)', line: 'rgba(15,23,42,0.1)', pattern: 'grid', bar: true},
    neon: {bg: '#05010f, #1e0b3a', text: '#f5f3ff', muted: '#c4b5fd', accent: '#22d3ee', soft: 'rgba(34,211,238,0.12)', line: 'rgba(196,181,253,0.22)', glow: true, pattern: 'lines', titleWeight: 800, effects: {title: 'blur'}},
    paper: {bg: '#f6f1e7', text: '#2b2118', muted: '#6b5b4b', accent: '#c2410c', soft: 'rgba(43,33,24,0.06)', line: 'rgba(43,33,24,0.14)', font: 'serif', pattern: 'dots'},
    editorial: {bg: '#fafaf9', text: '#0c0a09', muted: '#57534e', accent: '#dc2626', soft: 'rgba(12,10,9,0.05)', line: 'rgba(12,10,9,0.15)', font: 'serif', bar: true, effects: {title: 'words'}},
    playful: {bg: '#fff7ed', text: '#1e1b4b', muted: '#4c4980', accent: '#8b5cf6', soft: 'rgba(139,92,246,0.1)', line: 'rgba(30,27,75,0.14)', font: 'rounded', pattern: 'blobs', titleWeight: 800, effects: {title: 'pop', bullet: 'pop', icon: 'pop'}},
    chalk: {bg: '#1d3b2f', text: '#f0fdf4', muted: '#bbf7d0', accent: '#fde047', soft: 'rgba(255,255,255,0.08)', line: 'rgba(255,255,255,0.18)', font: 'rounded', effects: {title: 'letters'}}
  };
  const MOTION_FONTS = {serif: 'Georgia, "Times New Roman", serif', mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', rounded: 'ui-rounded, "SF Pro Rounded", "Nunito", system-ui, sans-serif'};
  const easeOut = p => 1 - Math.pow(1 - clamp(p, 0, 1), 3);
  const easeBack = p => { p = clamp(p, 0, 1); const c = 1.6; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); };
  let probe = null;
  // A colour as red, green and blue, read back from a one-pixel canvas so any CSS colour works
  function rgbOf(color) {
    try {
      probe = probe || document.createElement('canvas').getContext('2d', {willReadFrequently: true});
      probe.clearRect(0, 0, 1, 1); probe.fillStyle = '#000'; probe.fillStyle = color; probe.fillRect(0, 0, 1, 1);
      const [r, g, b] = probe.getImageData(0, 0, 1, 1).data; return {r, g, b};
    } catch { return {r: 255, g: 255, b: 255}; }
  }
  const lightness = ({r, g, b}) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  // The colours of a scene: its theme, or text that reads on the background it was given
  function lookOf(model, scene) {
    const s = model.settings, background = scene?.background || s.background;
    const style = MOTION_STYLES[s.style] || null;
    let look = {...(style || MOTION_THEMES[s.theme] || MOTION_THEMES.light)};
    // A background of its own: text and lines that read on it, keeping the style's type and pattern
    if (background) { const first = background.split(',')[0].trim(); const base = lightness(rgbOf(first)) < 0.35 ? MOTION_THEMES.dark : MOTION_THEMES.light; look = {...look, text: base.text, muted: base.muted, soft: base.soft, line: base.line, accent: style ? look.accent : base.accent}; look.bg = background; }
    if (s.accent) look.accent = s.accent;
    look.family = MOTION_FONTS[s.font || look.font] || model.family;
    look.pattern = scene?.pattern || look.pattern || 'none';
    look.onAccent = lightness(rgbOf(look.accent)) > 0.4 ? '#111111' : '#ffffff';
    return look;
  }
  const RTL_RE = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
  // **bold**, ==highlight== and *italic* inside a line; links and code show as their text
  function runsOf(text) {
    const runs = [], re = /(\*\*([^*]+)\*\*|==([^=]+)==|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_|`([^`]+)`|\[([^\]]+)\]\([^)]*\))/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) runs.push({text: text.slice(last, m.index)});
      if (m[2] != null) runs.push({text: m[2], bold: true});
      else if (m[3] != null) runs.push({text: m[3], mark: true});
      else if (m[4] != null || m[5] != null) runs.push({text: m[4] ?? m[5], italic: true});
      else runs.push({text: m[6] ?? m[7]});
      last = re.lastIndex;
    }
    if (last < text.length) runs.push({text: text.slice(last)});
    return runs.filter(run => run.text);
  }
  const fontOf = (look, size, weight = 400, italic = false) => (italic ? 'italic ' : '') + weight + ' ' + Math.round(size) + 'px ' + look.family;
  // Text broken into lines that fit, word by word (letter by letter for words longer than the line)
  function wrapText(g, text, look, size, weight, width) {
    const rtl = RTL_RE.test(text);
    if (rtl) {
      g.font = fontOf(look, size, weight);
      const words = text.replace(/[*=_`]/g, '').split(/\s+/), lines = []; let line = '';
      for (const word of words) { const next = line ? line + ' ' + word : word; if (line && g.measureText(next).width > width) { lines.push([{text: line, w: g.measureText(line).width}]); line = word; } else line = next; }
      if (line) lines.push([{text: line, w: g.measureText(line).width}]);
      return {lines, rtl: true};
    }
    const pieces = [];
    for (const run of runsOf(text)) for (const part of run.text.split(/(\s+)/)) if (part) pieces.push({...run, text: part});
    const measure = piece => { g.font = fontOf(look, size, piece.bold ? Math.max(weight, 700) : weight, piece.italic); return g.measureText(piece.text).width; };
    const lines = []; let line = [], used = 0;
    const push = () => { while (line.length && /^\s+$/.test(line[line.length - 1].text)) used -= line.pop().w; if (line.length) lines.push(line); line = []; used = 0; };
    for (const piece of pieces) {
      const w = measure(piece);
      if (/^\s+$/.test(piece.text)) { if (line.length) { line.push({...piece, w}); used += w; } continue; }
      if (used + w <= width || !line.length) {
        if (w > width) { // A word wider than the line is broken, as CJK text and long links need
          let chunk = '';
          for (const ch of piece.text) { const test = chunk + ch; if (measure({...piece, text: test}) > width - used && chunk) { line.push({...piece, text: chunk, w: measure({...piece, text: chunk})}); push(); chunk = ch; } else chunk = test; }
          if (chunk) { const cw = measure({...piece, text: chunk}); line.push({...piece, text: chunk, w: cw}); used += cw; }
        } else { line.push({...piece, w}); used += w; }
      } else { push(); line.push({...piece, w}); used = w; }
    }
    push();
    return {lines, rtl: false};
  }
  const STYLE = {title: [64, 700, 1.15], subtitle: [40, 600, 1.2], text: [30, 400, 1.35], bullet: [30, 400, 1.35], callout: [28, 500, 1.35], label: [28, 500, 1.3], say: [26, 500, 1.3], quote: [44, 500, 1.3], small: [22, 500, 1.3], head: [32, 700, 1.2]};
  // Where everything in a scene goes, worked out once for a size and kept
  function layoutScene(g, model, scene) {
    const key = scene.part + ':' + model.w + 'x' + model.h;
    model.layouts = model.layouts || new Map();
    if (model.layouts.has(key)) return model.layouts.get(key);
    const W = model.w, H = model.h, look = lookOf(model, scene), base = Math.min(W, H) / 720 * (W < H ? 1.05 : 1);
    const captions = model.settings.captions !== 'off' && scene.say;
    const marginX = W * 0.075, marginY = H * (captions ? 0.07 : 0.085), sayRoom = (captions ? H * 0.14 : 0) + (scene.lower ? H * 0.13 : 0);
    const pictures = scene.items.filter(item => item.kind === 'chart' || item.kind === 'image');
    const visual = pictures.length === 1 ? pictures[0] : null;
    const words = scene.items.filter(item => !pictures.includes(item) && item.kind !== 'title' && item.kind !== 'subtitle');
    const split = W > H * 1.2 && visual && words.length && scene.layout !== 'center' && scene.layout !== 'left' || scene.layout === 'split' && visual;
    const center = scene.layout === 'center' || (!scene.layout && !pictures.length && scene.items.every(item => ['title', 'subtitle', 'number', 'text', 'steps', 'icon', 'icons', 'ring', 'quote', 'badge', 'timeline', 'progress', 'compare'].includes(item.kind)) && scene.items.filter(item => item.kind === 'text').length <= 1);
    const textBlock = (item, width, scale) => {
      const [size0, weight0, lead] = STYLE[item.kind] || STYLE.text, size = size0 * base * scale;
      const weight = item.kind === 'title' ? look.titleWeight || weight0 : weight0;
      const text = item.kind === 'title' && look.upper ? item.text.toLocaleUpperCase(lang) : item.text;
      const indent = item.kind === 'bullet' ? size * 1.1 : item.kind === 'callout' ? size * 1.2 : 0;
      const wrapped = wrapText(g, text, look, size, weight, width - indent - (item.kind === 'callout' ? size * 0.9 : 0));
      const pad = item.kind === 'callout' ? size * 0.6 : 0;
      return {item, size, weight, lead, indent, pad, lines: wrapped.lines, rtl: wrapped.rtl, w: width, h: wrapped.lines.length * size * lead + pad * 2};
    };
    const blockFor = (item, width, scale, height) => {
      if (item.kind === 'number') { const size = 128 * base * scale; const label = item.label ? textBlock({kind: 'label', text: item.label}, width, scale) : null; return {item, size, label, w: width, h: size * 1.05 + (label ? label.h + size * 0.1 : 0)}; }
      if (item.kind === 'steps') { const vertical = W < H; const n = item.steps.length, gap = 28 * base; const boxW = vertical ? width * 0.8 : (width - gap * (n - 1)) / n; const size = clamp(28 * base * scale, 14, 40); const wraps = item.steps.map(s => wrapText(g, s, look, size, 600, boxW - size * 1.2)); const lines = Math.max(...wraps.map(w => w.lines.length)); const boxH = lines * size * 1.3 + size * 1.4; return {item, size, wraps, boxW, boxH, gap, vertical, w: width, h: vertical ? n * boxH + (n - 1) * gap * 1.4 : boxH}; }
      if (item.kind === 'chart' || item.kind === 'image') return {item, w: width, h: height || Math.min(H * 0.5, width * 0.62), caption: item.kind === 'image' && item.caption ? textBlock({kind: 'say', text: item.caption}, width, scale) : null};
      if (item.kind === 'icon') { const size = 150 * base * scale; const label = item.label ? textBlock({kind: 'label', text: item.label}, width, scale) : null; return {item, size, label, w: width, h: size + (label ? label.h + 12 * base : 0)}; }
      if (item.kind === 'icons') { const n = item.list.length, cell = Math.min(width / n, 240 * base), size = Math.min(96 * base * scale, cell * 0.62); const labels = item.list.map(each => each.label ? textBlock({kind: 'small', text: each.label}, cell - 8, scale) : null); return {item, size, cell, labels, w: cell * n, h: size + 14 * base + Math.max(0, ...labels.map(l => l?.h || 0))}; }
      if (item.kind === 'progress') { const label = textBlock({kind: 'label', text: item.label || ' '}, width * 0.75, scale), bar = 26 * base * scale; return {item, label, bar, w: Math.min(width, 900 * base), h: label.h + 12 * base + bar}; }
      if (item.kind === 'ring') { const size = Math.min(width, 250 * base * scale); const label = item.label ? textBlock({kind: 'label', text: item.label}, Math.max(size, width * 0.5), scale) : null; return {item, size, label, w: Math.max(size, label?.w || 0), h: size + (label ? label.h + 12 * base : 0)}; }
      if (item.kind === 'timeline') {
        const n = item.marks.length, vertical = W < H, cell = vertical ? width : width / n;
        const whats = item.marks.map(m => textBlock({kind: 'small', text: m.what || ' '}, vertical ? width * 0.7 : cell - 12 * base, scale));
        const dateSize = 30 * base * scale;
        return {item, vertical, cell, whats, dateSize, w: width, h: vertical ? whats.reduce((sum, b) => sum + Math.max(b.h, dateSize * 1.2) + 18 * base, 0) : dateSize * 1.4 + 40 * base + Math.max(...whats.map(b => b.h))};
      }
      if (item.kind === 'compare') {
        const col = (width - 40 * base) / 2, heads = [textBlock({kind: 'head', text: item.left || ' '}, col, scale), textBlock({kind: 'head', text: item.right || ' '}, col, scale)];
        const rows = item.rows.map(([a, b2]) => [textBlock({kind: 'small', text: a || ' '}, col - 30 * base, scale), textBlock({kind: 'small', text: b2 || ' '}, col - 30 * base, scale)]);
        const rowH = rows.map(([a, b2]) => Math.max(a.h, b2.h) + 12 * base);
        return {item, col, heads, rows, rowH, w: width, h: Math.max(heads[0].h, heads[1].h) + 22 * base + rowH.reduce((sum, h) => sum + h, 0)};
      }
      if (item.kind === 'quote') { const body = textBlock({kind: 'quote', text: item.text}, width * 0.9, scale); const who = item.author ? textBlock({kind: 'label', text: '— ' + item.author}, width * 0.9, scale) : null; return {...body, item, who, w: width, h: body.h + 50 * base * scale + (who ? who.h + 10 * base : 0)}; }
      if (item.kind === 'badge') { const size = 22 * base * scale; g.font = fontOf(look, size, 700); const w = g.measureText(item.text.toLocaleUpperCase(lang)).width + size * 1.6; return {item, size, w, h: size * 1.9}; }
      return textBlock(item, width, scale);
    };
    const gap = H * 0.035;
    let placed = null, warnTight = false;
    for (const scale of [1, 0.9, 0.8, 0.7, 0.6, 0.52]) {
      const blocks = [], top = marginY, bottom = H - marginY - sayRoom, room = bottom - top, full = W - marginX * 2;
      const heads = scene.items.filter(item => item.kind === 'title' || item.kind === 'subtitle');
      if (split) {
        const headBlocks = heads.map(item => blockFor(item, full, scale));
        const headH = headBlocks.reduce((sum, b) => sum + b.h + gap, 0);
        const colW = full * 0.44, visW = full * 0.52, bodyTop = top + headH, bodyH = bottom - bodyTop;
        const left = words.map(item => blockFor(item, colW, scale)), leftH = left.reduce((sum, b) => sum + b.h, 0) + gap * Math.max(0, left.length - 1);
        const vis = blockFor(visual, visW, scale, Math.max(H * 0.2, bodyH - (visual.caption ? 40 * base : 0)));
        if (leftH > bodyH || bodyH < H * 0.25) continue;
        let y = top; for (const b of headBlocks) { b.x = marginX; b.y = y; y += b.h + gap; blocks.push(b); }
        let ly = bodyTop + (bodyH - leftH) / 2; for (const b of left) { b.x = marginX; b.y = ly; ly += b.h + gap; blocks.push(b); }
        vis.x = marginX + full - visW; vis.y = bodyTop + Math.max(0, (bodyH - vis.h - (vis.caption?.h || 0)) / 2); blocks.push(vis);
        placed = blocks; break;
      }
      const all = scene.items.map(item => blockFor(item, (item.kind === 'chart' || item.kind === 'image') ? full * (W < H ? 1 : 0.8) : full * (center ? 0.86 : 1), scale));
      const fixedH = all.filter(b => b.item.kind !== 'chart' && b.item.kind !== 'image').reduce((sum, b) => sum + b.h, 0) + gap * Math.max(0, all.length - 1);
      const visuals = all.filter(b => b.item.kind === 'chart' || b.item.kind === 'image');
      // Charts and pictures take the room the text leaves
      if (visuals.length) { const each = (room - fixedH - gap * visuals.length) / visuals.length - (visuals.some(v => v.caption) ? 40 * base : 0); for (const v of visuals) v.h = clamp(each, H * 0.18, Math.min(room, H * 0.66)); }
      const totalH = all.reduce((sum, b) => sum + b.h + (b.caption ? b.caption.h + 8 : 0), 0) + gap * Math.max(0, all.length - 1);
      if (totalH > room && scale > 0.52) continue;
      if (totalH > room) warnTight = true;
      let y = top + Math.max(0, (room - totalH) / 2);
      for (const b of all) { b.x = center || b.item.kind === 'chart' || b.item.kind === 'image' || b.item.kind === 'icons' ? (W - b.w) / 2 : marginX; b.y = y; y += b.h + (b.caption ? b.caption.h + 8 : 0) + gap; blocks.push(b); }
      placed = blocks; break;
    }
    if (!placed) placed = [];
    // When each thing comes in: in order, a beat apart, unless a line says when
    let at = 0.25;
    for (const b of placed) {
      const item = b.item, explicit = item.at != null;
      b.effect = item.effect || look.effects?.[item.kind] || {title: 'up', subtitle: 'up', text: 'up', bullet: 'left', callout: 'fade', number: 'zoom', chart: 'fade', steps: 'pop', image: 'zoom', icon: 'pop', icons: 'pop', progress: 'fade', ring: 'zoom', timeline: 'fade', compare: 'fade', quote: 'words', badge: 'pop'}[item.kind] || 'fade';
      const chars = [...(item.text || '')].length, words = wordsIn(item.text);
      const enter = item.kind === 'number' ? 1.3 : item.kind === 'chart' ? (item.type === 'race' ? Math.max(1.5, scene.duration - 0.8) : 1.4) : item.kind === 'steps' ? 0.45 * item.steps.length + 0.3
        : item.kind === 'icons' ? 0.35 * item.list.length + 0.4 : item.kind === 'progress' ? 1.3 : item.kind === 'ring' ? 1.5 : item.kind === 'timeline' ? 0.45 * item.marks.length + 0.5 : item.kind === 'compare' ? 0.35 * item.rows.length + 0.7
        : b.effect === 'type' ? clamp(chars * 0.035, 0.5, 3) : b.effect === 'words' ? clamp(words * 0.16, 0.6, 3) : b.effect === 'letters' ? clamp(chars * 0.03, 0.6, 2.5) : b.effect === 'scramble' ? clamp(chars * 0.03, 0.8, 2) : b.effect === 'wave' ? 1.1 : 0.7;
      b.start = explicit ? item.at : at; b.enter = enter;
      if (!explicit) at += item.kind === 'title' ? 0.45 : item.kind === 'bullet' ? 0.4 : Math.min(enter, 0.9) * 0.6 + 0.2;
      b.center = center && !split;
    }
    // A scene too short for what it holds still shows everything: the entrances are squeezed into it
    const last = Math.max(0, ...placed.map(b => b.start + b.enter));
    const room = Math.max(0.4, scene.duration - 0.35);
    if (last > room) { const f = room / last; for (const b of placed) { b.start *= f; b.enter *= f; } }
    const captionRoom = captions ? H * 0.14 : 0;
    const layout = {blocks: placed, look, sayRoom, base, tight: warnTight, say: captions ? {block: textBlock({kind: 'say', text: scene.say}, W * 0.8, 1), y: H - marginY * 0.6 - captionRoom * 0.5} : null,
      lower: scene.lower ? {name: textBlock({kind: 'head', text: scene.lower.name || ' '}, W * 0.5, 1), role: scene.lower.role ? textBlock({kind: 'small', text: scene.lower.role}, W * 0.5, 1) : null, y: H - marginY * 0.5 - captionRoom - H * 0.12} : null};
    model.layouts.set(key, layout);
    return layout;
  }
  function paintBackground(g, look, W, H) {
    const colors = String(look.bg).split(',').map(c => c.trim()).filter(Boolean);
    if (colors.length > 1) { const grad = g.createLinearGradient(0, 0, W, H); colors.forEach((c, i) => grad.addColorStop(i / (colors.length - 1), c)); g.fillStyle = grad; }
    else g.fillStyle = colors[0] || '#fff';
    g.fillRect(0, 0, W, H);
  }
  function roundRect(g, x, y, w, h, r) { g.beginPath(); if (g.roundRect) g.roundRect(x, y, w, h, r); else g.rect(x, y, w, h); }
  // Lines of text, with bold and highlighted words, right to left where the script is
  // Kinetic type: words or letters arrive one by one, scramble into place, or ride a wave
  const SCRAMBLE = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&*+';
  function paintKinetic(g, b, look, x, y, width, align, color, kind, p) {
    const pieces = b.lines.flat(), total = kind === 'words' ? pieces.filter(pc => /\S/.test(pc.text)).length : pieces.reduce((n, pc) => n + [...pc.text].length, 0);
    let k = 0;
    b.lines.forEach((line, i) => {
      const lineW = line.reduce((sum, piece) => sum + piece.w, 0);
      const baseY = y + i * b.size * b.lead + b.size * (b.lead - 1) / 2 + b.size * 0.82;
      let cx = align === 'center' ? x + (width - lineW) / 2 : x;
      for (const piece of line) {
        g.font = fontOf(look, b.size, piece.bold ? Math.max(b.weight, 700) : b.weight, piece.italic);
        g.fillStyle = piece.mark ? look.accent : color;
        if (kind === 'words') {
          if (/\S/.test(piece.text)) {
            const q = easeOut(clamp((p - 0.75 * k / Math.max(1, total)) / 0.25, 0, 1)); k++;
            if (q > 0) { g.save(); g.globalAlpha *= q; g.fillText(piece.text, cx, baseY + (1 - q) * b.size * 0.45); g.restore(); }
          }
        } else {
          let ox = cx;
          for (const ch of piece.text) {
            const w = g.measureText(ch).width, order = k / Math.max(1, total); k++;
            if (kind === 'letters') { const q = clamp((p - 0.8 * order) / 0.2, 0, 1); if (q > 0) { g.save(); g.globalAlpha *= easeOut(q); g.fillText(ch, ox, baseY - (1 - easeBack(q)) * b.size * 0.6); g.restore(); } }
            else if (kind === 'scramble') { const done = order < p * 1.15 - 0.15; const glyph = done || !/\S/.test(ch) ? ch : SCRAMBLE[(k * 7 + Math.floor(p * 40) * 13) % SCRAMBLE.length]; g.save(); if (!done) g.globalAlpha *= 0.55; g.fillText(glyph, ox, baseY); g.restore(); }
            else { const lift = Math.sin(k * 0.55 - p * 12) * b.size * 0.22 * (1 - p); g.save(); g.globalAlpha *= clamp(p * 2 - order, 0, 1); g.fillText(ch, ox, baseY + lift); g.restore(); }
            ox += w;
          }
        }
        cx += piece.w;
      }
    });
  }
  function paintLines(g, b, look, x, y, width, align, reveal = Infinity, color = look.text, kinetic = null) {
    if (kinetic && !b.rtl && kinetic.p < 1) { paintKinetic(g, b, look, x, y, width, align, color, kinetic.kind, kinetic.p); return; }
    let shownChars = 0;
    b.lines.forEach((line, i) => {
      const lineW = line.reduce((sum, piece) => sum + piece.w, 0);
      const baseY = y + i * b.size * b.lead + b.size * (b.lead - 1) / 2 + b.size * 0.82;
      if (b.rtl) {
        g.direction = 'rtl'; g.textAlign = 'right'; g.font = fontOf(look, b.size, b.weight); g.fillStyle = color;
        const right = align === 'center' ? x + (width + lineW) / 2 : x + width;
        let text = line[0].text; if (shownChars + text.length > reveal) text = text.slice(0, Math.max(0, reveal - shownChars)); shownChars += line[0].text.length;
        g.fillText(text, right, baseY); g.direction = 'ltr'; g.textAlign = 'left';
        return;
      }
      let cx = align === 'center' ? x + (width - lineW) / 2 : x;
      for (const piece of line) {
        if (shownChars >= reveal) return;
        let text = piece.text; if (shownChars + text.length > reveal) text = text.slice(0, reveal - shownChars); shownChars += piece.text.length;
        g.font = fontOf(look, b.size, piece.bold ? Math.max(b.weight, 700) : b.weight, piece.italic);
        if (piece.mark && text.trim()) { g.fillStyle = look.accent; g.globalAlpha *= 0.2; roundRect(g, cx - 2, baseY - b.size * 0.82, g.measureText(text).width + 4, b.size * 1.05, 4); g.fill(); g.globalAlpha /= 0.2; }
        g.fillStyle = piece.mark ? look.accent : color; g.fillText(text, cx, baseY);
        cx += piece.w;
      }
    });
  }
  function paintChart(g, b, look, p) {
    const item = b.item, data = item.data, x = b.x, y = b.y, w = b.w, h = b.h;
    const colors = [look.accent, ...PALETTE.filter(c => c.toLowerCase() !== String(look.accent).toLowerCase())];
    const size = clamp(h / 18, 11, 22);
    g.font = fontOf(look, size, 500); g.textBaseline = 'alphabetic';
    let top = y;
    if (item.title) { g.fillStyle = look.text; g.font = fontOf(look, size * 1.25, 700); g.fillText(item.title, x, y + size * 1.2); top += size * 2; }
    const legendH = data.series.length > 1 ? size * 1.8 : 0;
    if (item.type === 'race') { paintRace(g, b, look, p, top, size, colors); return; }
    if (item.type === 'pie' || item.type === 'donut') {
      const values = data.series[0].values.map(v => Math.max(0, v)), sum = values.reduce((a, v) => a + v, 0) || 1;
      const r = Math.min(w * 0.3, (h - (top - y)) / 2 - size), cx = x + r + size, cy = top + (h - (top - y)) / 2;
      let angle = -Math.PI / 2; const end = -Math.PI / 2 + Math.PI * 2 * easeOut(p);
      values.forEach((v, i) => { const a = (v / sum) * Math.PI * 2, stop = Math.min(angle + a, end); if (stop > angle) { g.beginPath(); g.moveTo(cx, cy); g.arc(cx, cy, r, angle, stop); g.closePath(); g.fillStyle = colors[i % colors.length]; g.fill(); } angle += a; });
      if (item.type === 'donut') { g.save(); g.globalCompositeOperation = 'destination-out'; g.beginPath(); g.arc(cx, cy, r * 0.58, 0, Math.PI * 2); g.fill(); g.restore(); g.fillStyle = look.text; g.font = fontOf(look, r * 0.3, 800); g.textAlign = 'center'; g.fillText(Math.round(sum).toLocaleString(lang), cx, cy + r * 0.1); g.textAlign = 'left'; }
      g.font = fontOf(look, size, 500);
      data.labels.slice(0, 10).forEach((label, i) => { const ly = cy - r + i * size * 1.6; g.globalAlpha = clamp(p * 2 - i * 0.1, 0, 1); g.fillStyle = colors[i % colors.length]; g.fillRect(cx + r + size * 2, ly, size, size); g.fillStyle = look.text; g.fillText(label + '  ' + Math.round(values[i] / sum * 100) + '%', cx + r + size * 3.5, ly + size * 0.9); });
      g.globalAlpha = 1;
      return;
    }
    // Round steps for the axis: 0, 250, 500… rather than 0, 337.5, 675
    const all = data.series.flatMap(s => s.values), rawMax = Math.max(0, ...all), rawMin = Math.min(0, ...all);
    const rough = (rawMax - rawMin || 1) / 4, power = Math.pow(10, Math.floor(Math.log10(rough))), unit = [1, 2, 2.5, 5, 10].map(f => f * power).find(f => f >= rough) || rough;
    const max = Math.ceil(rawMax / unit) * unit, min = Math.floor(rawMin / unit) * unit, span = max - min || 1, ticks = Math.round(span / unit);
    const L = x + size * 3.2, R = x + w, T = top + size * 0.5, B = y + h - size * 2 - legendH, plotH = B - T, plotW = R - L;
    const yOf = v => B - ((v - min) / span) * plotH;
    g.strokeStyle = look.line; g.lineWidth = 1; g.fillStyle = look.muted; g.font = fontOf(look, size * 0.9, 400);
    for (let i = 0; i <= ticks; i++) { const v = min + unit * i, yy = yOf(v); g.beginPath(); g.moveTo(L, yy); g.lineTo(R, yy); g.stroke(); g.textAlign = 'right'; g.fillText((Math.round(v * 1000) / 1000).toLocaleString(lang), L - size * 0.4, yy + size * 0.3); }
    g.textAlign = 'center';
    const n = data.labels.length, step = plotW / Math.max(1, n);
    data.labels.forEach((label, i) => { const text = label.length > 14 ? label.slice(0, 13) + '…' : label; g.fillText(text, L + step * i + step / 2, B + size * 1.3); });
    g.textAlign = 'left';
    if (item.type === 'hbar') {
      g.clearRect(0, 0, 0, 0);
      const rowsN = data.labels.length, rowH = (h - (top - y) - legendH) / Math.max(1, rowsN), labelW = Math.min(w * 0.3, 220), max2 = Math.max(1e-9, ...data.series[0].values);
      data.labels.forEach((label, i) => { const v = data.series[0].values[i], q = easeOut(clamp(p * 1.6 - i * 0.1, 0, 1)), yy = top + i * rowH, bw = (w - labelW - 70) * (v / max2) * q; g.fillStyle = look.text; g.font = fontOf(look, Math.min(size * 1.1, rowH * 0.45), 600); g.textAlign = 'right'; g.fillText(label.length > 18 ? label.slice(0, 17) + '…' : label, x + labelW - 10, yy + rowH * 0.62); g.textAlign = 'left'; g.fillStyle = colors[i % colors.length]; roundRect(g, x + labelW, yy + rowH * 0.18, Math.max(2, bw), rowH * 0.64, Math.min(8, rowH * 0.2)); g.fill(); g.fillStyle = look.muted; g.fillText(Math.round(v * q).toLocaleString(lang), x + labelW + bw + 10, yy + rowH * 0.62); });
      return;
    }
    if (item.type === 'bar') {
      const k = data.series.length, barW = step * 0.7 / k;
      data.series.forEach((s, si) => s.values.forEach((v, i) => {
        const q = easeOut(clamp(p * 1.6 - i * 0.08 - si * 0.05, 0, 1)), yy = yOf(v * q), y0 = yOf(0);
        g.fillStyle = colors[si % colors.length]; roundRect(g, L + step * i + step * 0.15 + barW * si, Math.min(yy, y0), Math.max(1, barW - 3), Math.abs(y0 - yy), Math.min(6, barW / 4)); g.fill();
      }));
    } else {
      data.series.forEach((s, si) => {
        const pts = s.values.map((v, i) => [L + step * i + step / 2, yOf(v)]), upto = easeOut(p) * (pts.length - 1);
        const drawn = []; for (let i = 0; i < pts.length; i++) { if (i <= upto) drawn.push(pts[i]); else { const f = upto - Math.floor(upto); if (i === Math.floor(upto) + 1 && f > 0) drawn.push([pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f]); break; } }
        const color = colors[si % colors.length];
        if (drawn.length > 1 && item.type === 'area') { g.beginPath(); g.moveTo(drawn[0][0], yOf(0)); drawn.forEach(([px, py]) => g.lineTo(px, py)); g.lineTo(drawn[drawn.length - 1][0], yOf(0)); g.closePath(); g.fillStyle = color; g.globalAlpha *= 0.25; g.fill(); g.globalAlpha /= 0.25; }
        g.beginPath(); drawn.forEach(([px, py], i) => i ? g.lineTo(px, py) : g.moveTo(px, py)); g.strokeStyle = color; g.lineWidth = Math.max(2, size / 5); g.lineJoin = 'round'; g.stroke();
        g.fillStyle = color; drawn.forEach(([px, py], i) => { if (i < drawn.length - 1 || upto >= pts.length - 1) { g.beginPath(); g.arc(px, py, Math.max(3, size / 4), 0, Math.PI * 2); g.fill(); } });
      });
    }
    if (legendH) { let lx = L; g.font = fontOf(look, size * 0.95, 500); data.series.forEach((s, si) => { g.fillStyle = colors[si % colors.length]; g.fillRect(lx, y + h - legendH * 0.55, size * 0.8, size * 0.8); g.fillStyle = look.text; g.fillText(s.name, lx + size * 1.2, y + h - legendH * 0.55 + size * 0.75); lx += g.measureText(s.name).width + size * 2.6; }); }
  }
  // A bar chart race: each column is a moment (a year, a month); the bars grow, shrink and change places
  function paintRace(g, b, look, p, top, size, colors) {
    const data = b.item.data, frames = data.series, n = data.labels.length, x = b.x, w = b.w, h = b.h - (top - b.y);
    const f = clamp(p, 0, 1) * Math.max(0, frames.length - 1), i0 = Math.floor(f), i1 = Math.min(frames.length - 1, i0 + 1), mix = f - i0, ease = mix * mix * (3 - 2 * mix);
    const value = r => frames[i0].values[r] + (frames[i1].values[r] - frames[i0].values[r]) * ease;
    const rankOf = frame => { const order = frames[frame].values.map((v, r) => [v, r]).sort((a, c) => c[0] - a[0]); const ranks = []; order.forEach(([, r], k) => { ranks[r] = k; }); return ranks; };
    const r0 = rankOf(i0), r1 = rankOf(i1), shown = Math.min(n, 8), rowH = h / (shown + 0.6), max = Math.max(1e-9, ...Array.from({length: n}, (_, r) => value(r)));
    const labelW = Math.min(w * 0.28, 200);
    // The moment shown, large and faint behind the bars
    const moment = frames[mix < 0.5 ? i0 : i1].name;
    g.save(); g.fillStyle = look.text; g.globalAlpha *= 0.22; g.font = fontOf(look, Math.min(h * 0.3, 150), 800); g.textAlign = 'right'; g.fillText(moment, x + w, b.y + b.h - size * 0.5); g.restore();
    for (let r = 0; r < n; r++) {
      const rank = r0[r] + (r1[r] - r0[r]) * ease; if (rank > shown - 0.5) continue;
      const yy = top + rank * rowH, v = value(r), bw = (w - labelW - 90) * (v / max);
      g.fillStyle = look.text; g.font = fontOf(look, Math.min(size * 1.15, rowH * 0.42), 600); g.textAlign = 'right'; g.fillText(data.labels[r], x + labelW - 10, yy + rowH * 0.6); g.textAlign = 'left';
      g.fillStyle = colors[r % colors.length]; roundRect(g, x + labelW, yy + rowH * 0.14, Math.max(2, bw), rowH * 0.7, Math.min(8, rowH * 0.2)); g.fill();
      g.fillStyle = look.muted; g.fillText(Math.round(v).toLocaleString(lang), x + labelW + bw + 10, yy + rowH * 0.6);
    }
  }
  function paintSteps(g, b, look, p) {
    const n = b.item.steps.length;
    b.item.steps.forEach((step, i) => {
      const q = clamp((p * (n * 0.45 + 0.3) - i * 0.45) / 0.45, 0, 1); if (q <= 0) return;
      const s = b.item.effect === 'fade' ? 1 : easeBack(q);
      const bx = b.vertical ? b.x + (b.w - b.boxW) / 2 : b.x + i * (b.boxW + b.gap), by = b.vertical ? b.y + i * (b.boxH + b.gap * 1.4) : b.y;
      g.save(); g.globalAlpha *= easeOut(q); g.translate(bx + b.boxW / 2, by + b.boxH / 2); g.scale(s, s); g.translate(-(bx + b.boxW / 2), -(by + b.boxH / 2));
      g.fillStyle = look.soft; roundRect(g, bx, by, b.boxW, b.boxH, b.size * 0.5); g.fill();
      g.strokeStyle = look.accent; g.lineWidth = 2; roundRect(g, bx, by, b.boxW, b.boxH, b.size * 0.5); g.stroke();
      const wrap = b.wraps[i], textH = wrap.lines.length * b.size * 1.3;
      paintLines(g, {lines: wrap.lines, rtl: wrap.rtl, size: b.size, lead: 1.3, weight: 600}, look, bx + b.size * 0.6, by + (b.boxH - textH) / 2, b.boxW - b.size * 1.2, 'center');
      g.restore();
      if (i < n - 1) {
        const r = clamp((p * (n * 0.45 + 0.3) - i * 0.45 - 0.3) / 0.3, 0, 1); if (r <= 0) return;
        g.strokeStyle = look.accent; g.fillStyle = look.accent; g.lineWidth = 3; g.beginPath();
        if (b.vertical) { const ax = bx + b.boxW / 2, ay = by + b.boxH + 4, len = b.gap * 1.4 - 8; g.moveTo(ax, ay); g.lineTo(ax, ay + len * r); g.stroke(); if (r === 1) { g.beginPath(); g.moveTo(ax - 7, ay + len - 8); g.lineTo(ax, ay + len); g.lineTo(ax + 7, ay + len - 8); g.stroke(); } }
        else { const ax = bx + b.boxW + 4, ay = by + b.boxH / 2, len = b.gap - 8; g.moveTo(ax, ay); g.lineTo(ax + len * r, ay); g.stroke(); if (r === 1) { g.beginPath(); g.moveTo(ax + len - 8, ay - 7); g.lineTo(ax + len, ay); g.lineTo(ax + len - 8, ay + 7); g.stroke(); } }
      }
    });
  }
  function paintImage(g, b, look, model) {
    const image = model.images?.get(b.item.src);
    const x = b.x, y = b.y, w = b.w, h = b.h;
    g.save(); roundRect(g, x, y, w, h, 14); g.clip();
    if (image && image.naturalWidth) {
      const f = Math.min(w / image.naturalWidth, h / image.naturalHeight), iw = image.naturalWidth * f, ih = image.naturalHeight * f;
      g.drawImage(image, x + (w - iw) / 2, y + (h - ih) / 2, iw, ih);
    } else {
      g.fillStyle = look.soft; g.fillRect(x, y, w, h);
      g.fillStyle = look.muted; g.font = fontOf(look, 22, 500); g.textAlign = 'center'; g.fillText('🖼 ' + (b.item.caption || ''), x + w / 2, y + h / 2); g.textAlign = 'left';
    }
    g.restore();
    if (b.caption) paintLines(g, b.caption, look, x, y + h + 8, w, 'center', Infinity, look.muted);
  }

  // Line icons drawn by the panel itself, and emoji for everything else, so any name gives a picture
  const LINE_ICONS = {
    check: 'M5 12.5l4.5 4.5L19 7', cross: 'M6 6l12 12M18 6L6 18', plus: 'M12 5v14M5 12h14', minus: 'M5 12h14',
    arrow: 'M4 12h15M13 6l6 6-6 6', 'arrow-right': 'M4 12h15M13 6l6 6-6 6', 'arrow-left': 'M20 12H5M11 6l-6 6 6 6', 'arrow-up': 'M12 20V5M6 11l6-6 6 6', 'arrow-down': 'M12 4v15M6 13l6 6 6-6',
    star: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z',
    heart: 'M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.1a4.3 4.3 0 0 1 7.5 2.7C19.5 15.4 12 20 12 20z',
    bolt: 'M13 2 4.5 13.5H11L10 22l8.5-11.5H12z', clock: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7v5l3.5 2',
    calendar: 'M4 6h16v14H4zM4 10h16M8 3v5M16 3v5', user: 'M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8M4.5 20c.8-3.6 3.9-6 7.5-6s6.7 2.4 7.5 6',
    users: 'M9 5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7M2.5 19.5c.7-3 3.4-5 6.5-5s5.8 2 6.5 5M16 5.2a3.3 3.3 0 0 1 0 6.4M17.5 14.6c2.1.5 3.6 2.3 4 4.9',
    globe: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9M12 3C9.5 5.5 8.2 8.5 8.2 12s1.3 6.5 3.8 9',
    idea: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2h5c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3z',
    lock: 'M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11', shield: 'M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z',
    chart: 'M4 20h16M7 16v-6M12 16V5M17 16v-4', pie: 'M12 3a9 9 0 1 0 9 9h-9zM14.5 2.5v7h7a7 7 0 0 0-7-7z',
    target: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7a5 5 0 1 0 0 10a5 5 0 1 0 0-10M12 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2',
    flag: 'M5 21V4M5 4h11l-2 4 2 4H5', mail: 'M3.5 6h17v12h-17zM4 7l8 6 8-6', home: 'M4 11l8-7 8 7M6 9.5V20h12V9.5M10 20v-5h4v5',
    pin: 'M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11zM12 7.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5',
    book: 'M4 5.5C6.5 4 9.5 4 12 5.5v14c-2.5-1.5-5.5-1.5-8 0zM20 5.5c-2.5-1.5-5.5-1.5-8 0v14c2.5-1.5 5.5-1.5 8 0z',
    graduation: 'M2.5 9.5 12 5l9.5 4.5L12 14zM6.5 11.5v4.5c3.5 2.5 7.5 2.5 11 0v-4.5M21.5 9.5v5',
    leaf: 'M5 19c0-8 5-13 15-14-1 10-6 15-14 15M5 19c3-4 6-6 9-7',
    sun: 'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
    moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z', chat: 'M4 5h16v11H9l-5 4z',
    search: 'M10.5 4a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13M15.5 15.5 20 20', info: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 11v6M12 7.5v.5',
    warning: 'M12 3.5 2.5 20h19zM12 10v4.5M12 17.5v.5',
    money: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M15 9c-.5-1.2-1.7-2-3-2-1.7 0-3 1-3 2.5S10.3 11.5 12 12s3 1 3 2.5-1.3 2.5-3 2.5c-1.3 0-2.5-.8-3-2M12 5.5V7M12 17v1.5',
    trophy: 'M8 4h8v5a4 4 0 0 1-8 0zM8 6H5c0 3 1.5 4.5 3 4.5M16 6h3c0 3-1.5 4.5-3 4.5M12 13v4M8.5 20h7M10 17h4v3h-4z',
    play: 'M8 5l11 7-11 7z', music: 'M9 18V6l11-2v12M9 18a2.5 2.5 0 1 1-5 0a2.5 2.5 0 1 1 5 0M20 16a2.5 2.5 0 1 1-5 0a2.5 2.5 0 1 1 5 0',
    laptop: 'M5 6h14v9H5zM3 18h18', phone: 'M8 3h8v18H8zM11 18h2',
    gear: 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1',
    rocket: 'M12 3c3 2 4.5 5.5 4.5 9.5L14 16h-4l-2.5-3.5C7.5 8.5 9 5 12 3zM12 8.5a1.5 1.5 0 1 0 0 3a1.5 1.5 0 1 0 0-3M8 14l-3 3 1 2 3-1M16 14l3 3-1 2-3-1M10.5 18.5 12 21l1.5-2.5',
    flask: 'M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3M7.5 14h9', cloud: 'M7 18h10a4 4 0 0 0 .5-8 5.5 5.5 0 0 0-10.6 1.3A3.4 3.4 0 0 0 7 18z',
    download: 'M12 4v11M7 10l5 5 5-5M5 20h14', upload: 'M12 20V9M7 14l5-5 5 5M5 4h14'
  };
  const ICON_ALIASES = {tick: 'check', done: 'check', yes: 'check', no: 'cross', close: 'cross', x: 'cross', next: 'arrow', lightbulb: 'idea', bulb: 'idea', person: 'user', people: 'users', team: 'users', world: 'globe', earth: 'globe', web: 'globe', security: 'shield', safe: 'shield', graph: 'chart', stats: 'chart', bars: 'chart', goal: 'target', email: 'mail', house: 'home', location: 'pin', map: 'pin', cap: 'graduation', school: 'graduation', study: 'book', learn: 'book', nature: 'leaf', eco: 'leaf', time: 'clock', date: 'calendar', message: 'chat', talk: 'chat', settings: 'gear', launch: 'rocket', science: 'flask', lab: 'flask', price: 'money', cost: 'money', coin: 'money', award: 'trophy', win: 'trophy', video: 'play', song: 'music', computer: 'laptop', mobile: 'phone', alert: 'warning', energy: 'bolt', fast: 'bolt', love: 'heart', like: 'heart', favourite: 'star', favorite: 'star'};
  const EMOJI = {fire: '🔥', party: '🎉', brain: '🧠', robot: '🤖', ai: '🤖', books: '📚', pencil: '✏️', microscope: '🔬', dna: '🧬', coffee: '☕', handshake: '🤝', medal: '🏅', crown: '👑', gift: '🎁', camera: '📷', film: '🎬', palette: '🎨', art: '🎨', plant: '🌱', tree: '🌳', water: '💧', recycle: '♻️', car: '🚗', bus: '🚌', plane: '✈️', train: '🚆', bike: '🚲', apple: '🍎', food: '🍽️', pizza: '🍕', sparkles: '✨', thumbsup: '👍', clap: '👏', wave: '👋', smile: '😊', question: '❓', hourglass: '⏳', battery: '🔋', wifi: '📶', key: '🔑', bell: '🔔', megaphone: '📣', news: '📰', hospital: '🏥', pill: '💊', health: '🩺', atom: '⚛️', teacher: '🧑‍🏫', student: '🧑‍🎓', building: '🏢', city: '🏙️', growth: '📈', decline: '📉', puzzle: '🧩', dart: '🎯', calculator: '🧮', ruler: '📐', magnet: '🧲', telescope: '🔭', satellite: '🛰️', mountain: '⛰️', ocean: '🌊', rain: '🌧️', snow: '❄️', thermometer: '🌡️', clipboard: '📋', folder: '📁', paperclip: '📎', scissors: '✂️', hammer: '🔨', wrench: '🔧', link: '🔗', eye: '👁️', ear: '👂', hand: '✋', muscle: '💪', baby: '👶', family: '👪', dog: '🐶', cat: '🐱', bird: '🐦', fish: '🐟', bee: '🐝', butterfly: '🦋', flower: '🌸', rainbow: '🌈', soccer: '⚽', basketball: '🏀', guitar: '🎸', piano: '🎹', ticket: '🎟️', newspaper: '🗞️', bank: '🏦', cart: '🛒', bag: '🛍️', package: '📦', truck: '🚚', factory: '🏭', solar: '☀️', wind: '🌬️', ok: '👌'};
  const iconPaths = new Map();
  function paintIcon(g, name, cx, cy, size, look) {
    const raw = String(name || '').trim(), key = raw.toLowerCase(), line = LINE_ICONS[key] || LINE_ICONS[ICON_ALIASES[key]];
    if (line) {
      if (!iconPaths.has(line)) { try { iconPaths.set(line, new Path2D(line)); } catch { iconPaths.set(line, null); } }
      const path = iconPaths.get(line);
      if (path) { g.save(); g.translate(cx - size / 2, cy - size / 2); g.scale(size / 24, size / 24); g.strokeStyle = look.accent; g.lineWidth = 1.7; g.lineCap = 'round'; g.lineJoin = 'round'; g.stroke(path); g.restore(); return; }
    }
    const glyph = EMOJI[key] || (/\p{Extended_Pictographic}/u.test(raw) ? raw : '');
    g.save(); g.textAlign = 'center'; g.textBaseline = 'middle';
    if (glyph) { g.font = Math.round(size * 0.86) + 'px "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif'; g.fillText(glyph, cx, cy + size * 0.04); }
    else { g.fillStyle = look.soft; g.beginPath(); g.arc(cx, cy, size / 2, 0, Math.PI * 2); g.fill(); g.fillStyle = look.accent; g.font = fontOf(look, size * 0.46, 800); g.fillText((raw[0] || '?').toLocaleUpperCase(lang), cx, cy + size * 0.02); }
    g.restore();
  }

  // ---- the new blocks
  function paintIconBlock(g, b, look, p) {
    if (b.item.kind === 'icon') {
      paintIcon(g, b.item.name, b.center ? b.x + b.w / 2 : b.x + b.size / 2, b.y + b.size / 2, b.size, look);
      if (b.label) paintLines(g, b.label, look, b.center ? b.x : b.x, b.y + b.size + 12 * (b.label.size / 28), b.center ? b.w : b.label.w, b.center ? 'center' : 'left', Infinity, look.text);
      return;
    }
    const n = b.item.list.length;
    b.item.list.forEach((each, i) => {
      const q = clamp((p * (n * 0.35 + 0.4) - i * 0.35) / 0.4, 0, 1); if (q <= 0) return;
      const cx = b.x + b.cell * i + b.cell / 2, s = b.item.effect === 'fade' ? 1 : easeBack(q);
      g.save(); g.globalAlpha *= easeOut(q); g.translate(cx, b.y + b.size / 2); g.scale(s, s); g.translate(-cx, -(b.y + b.size / 2));
      paintIcon(g, each.name, cx, b.y + b.size / 2, b.size, look); g.restore();
      if (b.labels[i]) { g.save(); g.globalAlpha *= easeOut(q); paintLines(g, b.labels[i], look, b.x + b.cell * i + 4, b.y + b.size + 14 * (b.labels[i].size / 22), b.cell - 8, 'center', Infinity, look.text); g.restore(); }
    });
  }
  function paintProgress(g, b, look, p) {
    const item = b.item, e = easeOut(p), x = b.x, w = b.w, y = b.y;
    paintLines(g, b.label, look, x, y, w * 0.75, 'left', Infinity, look.text);
    g.font = fontOf(look, b.label.size, 700); g.fillStyle = look.accent; g.textAlign = 'right';
    g.fillText(item.count ? countText(item.count, item.count.value * e) : item.value, x + w, y + b.label.size * 1.05); g.textAlign = 'left';
    const by = y + b.label.h + 12 * (b.bar / 26);
    g.fillStyle = look.soft; roundRect(g, x, by, w, b.bar, b.bar / 2); g.fill();
    if (item.ratio * e > 0) { g.fillStyle = look.accent; roundRect(g, x, by, Math.max(b.bar, w * item.ratio * e), b.bar, b.bar / 2); g.fill(); }
  }
  function paintRing(g, b, look, p) {
    const item = b.item, e = easeOut(p), cx = b.x + b.w / 2, r = b.size / 2 - b.size * 0.06, cy = b.y + b.size / 2, thick = b.size * 0.09;
    g.lineWidth = thick; g.lineCap = 'round';
    g.strokeStyle = look.soft; g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.stroke();
    g.strokeStyle = look.accent; g.beginPath(); g.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * item.ratio * e); if (item.ratio * e > 0.001) g.stroke();
    g.font = fontOf(look, b.size * 0.24, 800); g.fillStyle = look.text; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(item.count ? countText(item.count, item.count.value * e) : item.value, cx, cy); g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    if (b.label) paintLines(g, b.label, look, b.x + (b.w - b.label.w) / 2, b.y + b.size + 12 * (b.label.size / 28), b.label.w, 'center', Infinity, look.muted);
  }
  function paintTimeline(g, b, look, p) {
    const marks = b.item.marks, n = marks.length, total = n * 0.45 + 0.5;
    g.strokeStyle = look.line; g.lineWidth = 3; g.lineCap = 'round';
    if (b.vertical) {
      let y = b.y; const x = b.x + b.dateSize * 2.6;
      g.beginPath(); g.moveTo(x, b.y); g.lineTo(x, b.y + (b.h) * easeOut(clamp(p * total / (total - 0.2), 0, 1))); g.stroke();
      marks.forEach((m, i) => {
        const q = clamp((p * total - 0.3 - i * 0.45) / 0.4, 0, 1), rowH = Math.max(b.whats[i].h, b.dateSize * 1.2) + 18 * (b.dateSize / 30);
        if (q > 0) { g.save(); g.globalAlpha *= easeOut(q); g.fillStyle = look.accent; g.beginPath(); g.arc(x, y + b.dateSize * 0.6, b.dateSize * 0.3 * easeBack(q), 0, Math.PI * 2); g.fill(); g.font = fontOf(look, b.dateSize, 800); g.textAlign = 'right'; g.fillText(m.when, x - b.dateSize * 0.7, y + b.dateSize * 0.9); g.textAlign = 'left'; paintLines(g, b.whats[i], look, x + b.dateSize * 0.8, y, b.w * 0.6, 'left', Infinity, look.text); g.restore(); }
        y += rowH;
      });
      return;
    }
    const lineY = b.y + b.dateSize * 1.4 + 12;
    g.beginPath(); g.moveTo(b.x, lineY); g.lineTo(b.x + b.w * easeOut(clamp(p * total / (total - 0.2), 0, 1)), lineY); g.stroke();
    marks.forEach((m, i) => {
      const q = clamp((p * total - 0.3 - i * 0.45) / 0.4, 0, 1); if (q <= 0) return;
      const cx = b.x + b.cell * i + b.cell / 2;
      g.save(); g.globalAlpha *= easeOut(q);
      g.fillStyle = look.accent; g.beginPath(); g.arc(cx, lineY, b.dateSize * 0.32 * easeBack(q), 0, Math.PI * 2); g.fill();
      g.font = fontOf(look, b.dateSize, 800); g.textAlign = 'center'; g.fillText(m.when, cx, b.y + b.dateSize); g.textAlign = 'left';
      paintLines(g, b.whats[i], look, b.x + b.cell * i + 6, lineY + 22, b.cell - 12, 'center', Infinity, look.text);
      g.restore();
    });
  }
  function paintCompare(g, b, look, p) {
    const total = b.rows.length * 0.35 + 0.7, gapX = (b.w - b.col * 2), headH = Math.max(b.heads[0].h, b.heads[1].h);
    [0, 1].forEach(side => {
      const q = easeOut(clamp(p * total / 0.6, 0, 1)), x = b.x + side * (b.col + gapX), shift = (1 - q) * 40 * (side ? 1 : -1);
      g.save(); g.globalAlpha *= q; g.translate(shift, 0);
      g.fillStyle = side ? look.accent : look.soft; g.globalAlpha *= side ? 0.12 : 1; roundRect(g, x, b.y, b.col, b.h, 18); g.fill(); g.globalAlpha /= side ? 0.12 : 1;
      paintLines(g, b.heads[side], look, x + 20, b.y + 14, b.col - 40, 'left', Infinity, side ? look.accent : look.text);
      let y = b.y + headH + 22;
      b.rows.forEach((row, r) => {
        const rq = clamp((p * total - 0.6 - r * 0.35) / 0.35, 0, 1);
        if (rq > 0) { g.save(); g.globalAlpha *= easeOut(rq); g.strokeStyle = side ? look.accent : look.muted; g.lineWidth = 2.5; g.lineCap = 'round'; const iy = y + row[side].size * 0.75, ix = x + 26; g.beginPath(); if (side) { g.moveTo(ix - 7, iy); g.lineTo(ix - 2, iy + 5); g.lineTo(ix + 7, iy - 6); } else { g.moveTo(ix - 6, iy - 6); g.lineTo(ix + 6, iy + 6); g.moveTo(ix + 6, iy - 6); g.lineTo(ix - 6, iy + 6); } g.stroke(); paintLines(g, row[side], look, x + 46, y, b.col - 66, 'left', Infinity, look.text); g.restore(); }
        y += b.rowH[r];
      });
      g.restore();
    });
  }
  function paintQuote(g, b, look, p, kinetic) {
    const markSize = 90 * (b.size / 44);
    g.save(); g.globalAlpha *= easeOut(clamp(p * 3, 0, 1)); g.fillStyle = look.accent; g.font = fontOf(look, markSize, 800); g.textAlign = b.center ? 'center' : 'left'; g.fillText('“', b.center ? b.x + b.w / 2 : b.x, b.y + markSize * 0.7); g.restore();
    const top = b.y + markSize * 0.55;
    paintLines(g, b, look, b.x + (b.center ? b.w * 0.05 : 0), top, b.w * 0.9, b.center ? 'center' : 'left', Infinity, look.text, kinetic);
    if (b.who) { g.save(); g.globalAlpha *= clamp(p * 2 - 1, 0, 1); paintLines(g, b.who, look, b.x + (b.center ? b.w * 0.05 : 0), top + b.lines.length * b.size * b.lead + 10, b.w * 0.9, b.center ? 'center' : 'left', Infinity, look.muted); g.restore(); }
  }
  function paintBadge(g, b, look) {
    const x = b.center ? b.x + (b.w0 ? 0 : 0) : b.x;
    g.fillStyle = look.accent; roundRect(g, x, b.y, b.w, b.h, b.h / 2); g.fill();
    g.fillStyle = look.onAccent; g.font = fontOf(look, b.size, 700); g.textAlign = 'center'; g.fillText(b.item.text.toLocaleUpperCase(lang), x + b.w / 2, b.y + b.h * 0.66); g.textAlign = 'left';
  }

  // ---- what sits behind and over a scene: patterns, shapes, particles
  function seeded(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function paintPattern(g, look, kind, W, H, t, base) {
    if (!kind || kind === 'none') return;
    g.save(); g.strokeStyle = look.line; g.fillStyle = look.line; g.lineWidth = 1;
    const step = 64 * base, drift = (t * 8 * base) % step;
    if (kind === 'grid') { g.globalAlpha *= 0.7; g.beginPath(); for (let x = -step + drift; x < W + step; x += step) { g.moveTo(x, 0); g.lineTo(x, H); } for (let y = -step + drift; y < H + step; y += step) { g.moveTo(0, y); g.lineTo(W, y); } g.stroke(); }
    else if (kind === 'dots') { g.globalAlpha *= 0.9; const d = 36 * base; for (let x = d / 2 + drift % d; x < W; x += d) for (let y = d / 2; y < H; y += d) { g.beginPath(); g.arc(x, y, 1.6 * base, 0, Math.PI * 2); g.fill(); } }
    else if (kind === 'lines') { g.globalAlpha *= 0.6; const d = 40 * base; g.beginPath(); for (let x = -H + (t * 20 * base) % d; x < W; x += d) { g.moveTo(x, H); g.lineTo(x + H, 0); } g.stroke(); }
    else if (kind === 'waves') { g.globalAlpha *= 0.8; g.lineWidth = 2; for (let k = 0; k < 5; k++) { g.beginPath(); for (let x = 0; x <= W; x += 12) { const y = H * (0.55 + k * 0.09) + Math.sin(x / (W / 4) + t * 0.9 + k) * 18 * base; x ? g.lineTo(x, y) : g.moveTo(x, y); } g.stroke(); } }
    else if (kind === 'rings') { g.globalAlpha *= 0.7; g.lineWidth = 1.5; const max = Math.hypot(W, H); for (let r = (t * 30 * base) % (80 * base); r < max; r += 80 * base) { g.beginPath(); g.arc(W, H, r, 0, Math.PI * 2); g.stroke(); } }
    else if (kind === 'blobs') { g.fillStyle = look.accent; for (let k = 0; k < 3; k++) { const x = W * (0.2 + 0.3 * k) + Math.sin(t * 0.4 + k * 2) * W * 0.05, y = H * (k % 2 ? 0.8 : 0.2) + Math.cos(t * 0.35 + k) * H * 0.05, r = Math.min(W, H) * (0.22 + 0.06 * k); const grad = g.createRadialGradient(x, y, 0, x, y, r); grad.addColorStop(0, look.accent); grad.addColorStop(1, 'rgba(0,0,0,0)'); g.globalAlpha = 0.16; g.fillStyle = grad; g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill(); } }
    g.restore();
  }
  const PLACE = {'top-left': [0.13, 0.17], top: [0.5, 0.12], 'top-right': [0.87, 0.17], left: [0.1, 0.5], center: [0.5, 0.5], right: [0.9, 0.5], 'bottom-left': [0.13, 0.83], bottom: [0.5, 0.88], 'bottom-right': [0.87, 0.83]};
  function paintShapes(g, scene, look, W, H, local, base) {
    scene.shapes.forEach((shape, i) => {
      const q = easeOut(clamp((local - 0.05 - i * 0.12) / 0.6, 0, 1)); if (q <= 0) return;
      const [fx, fy] = PLACE[shape.place] || PLACE.center, size = {small: 60, medium: 120, large: 230, huge: 400}[shape.size] * base;
      const big = shape.size === 'large' || shape.size === 'huge';
      let x = W * fx, y = H * fy, s = shape.effect === 'pop' || !shape.effect ? easeBack(q) : 1, angle = 0;
      if (shape.motion === 'spin') angle = local * 0.7; if (shape.motion === 'float') y += Math.sin(local * 1.6 + i) * 12 * base; if (shape.motion === 'pulse') s *= 1 + Math.sin(local * 3) * 0.06;
      const draw = shape.motion === 'draw' ? q : 1;
      g.save(); g.globalAlpha *= (big ? 0.22 : 0.85) * (shape.effect === 'fade' ? q : Math.min(1, q * 1.5)); g.translate(x, y); g.rotate(angle); g.scale(s, s);
      const color = shape.color || look.accent, r = size / 2; g.fillStyle = color; g.strokeStyle = color; g.lineWidth = Math.max(3, size * 0.06); g.lineCap = 'round';
      g.beginPath();
      if (shape.kind === 'circle') { g.arc(0, 0, r, 0, Math.PI * 2 * draw); draw < 1 ? g.stroke() : g.fill(); }
      else if (shape.kind === 'ring') { g.arc(0, 0, r * 0.85, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * draw); g.stroke(); }
      else if (shape.kind === 'square') { roundRect(g, -r, -r, size, size, size * 0.12); g.fill(); }
      else if (shape.kind === 'triangle') { g.moveTo(0, -r); g.lineTo(r * 0.95, r * 0.75); g.lineTo(-r * 0.95, r * 0.75); g.closePath(); g.fill(); }
      else if (shape.kind === 'star') { for (let k = 0; k < 10; k++) { const a = -Math.PI / 2 + k * Math.PI / 5, rr = k % 2 ? r * 0.45 : r; g.lineTo(Math.cos(a) * rr, Math.sin(a) * rr); } g.closePath(); g.fill(); }
      else if (shape.kind === 'blob') { for (let k = 0; k <= 24; k++) { const a = k / 24 * Math.PI * 2, rr = r * (0.82 + 0.18 * Math.sin(a * 3 + local * 1.2 + i)); g.lineTo(Math.cos(a) * rr, Math.sin(a) * rr); } g.closePath(); g.fill(); }
      else if (shape.kind === 'line') { g.moveTo(-r, 0); g.lineTo(-r + size * draw, 0); g.stroke(); }
      else if (shape.kind === 'arrow') { g.moveTo(-r, 0); g.lineTo(-r + size * draw, 0); g.stroke(); if (draw >= 1) { g.beginPath(); g.moveTo(r - size * 0.2, -size * 0.18); g.lineTo(r, 0); g.lineTo(r - size * 0.2, size * 0.18); g.stroke(); } }
      else if (shape.kind === 'plus') { g.moveTo(-r * 0.7, 0); g.lineTo(r * 0.7, 0); g.moveTo(0, -r * 0.7); g.lineTo(0, r * 0.7); g.stroke(); }
      else if (shape.kind === 'dots') { for (let a = 0; a < 4; a++) for (let c = 0; c < 4; c++) { g.moveTo(-r + c * size / 3 + 4, -r + a * size / 3); g.arc(-r + c * size / 3, -r + a * size / 3, size * 0.035, 0, Math.PI * 2); } g.fill(); }
      g.restore();
    });
  }
  const PARTICLE_COLORS = ['#f43f5e', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7', '#ec4899', '#14b8a6'];
  function paintParticles(g, kind, look, W, H, local, seed, base) {
    if (!kind || kind === 'none') return;
    const rand = seeded(seed * 7919 + 17), count = {confetti: 70, sparkles: 36, snow: 80, bokeh: 16, bubbles: 24}[kind] || 0;
    g.save();
    for (let i = 0; i < count; i++) {
      const x0 = rand() * W, y0 = rand() * H, speed = 0.6 + rand() * 0.8, phase = rand() * Math.PI * 2, size = rand();
      if (kind === 'confetti') { const y = (y0 + local * H * 0.35 * speed) % (H + 40) - 20, x = x0 + Math.sin(local * 2 + phase) * 30 * base; g.save(); g.translate(x, y); g.rotate(local * 3 * speed + phase); g.fillStyle = PARTICLE_COLORS[i % PARTICLE_COLORS.length]; g.globalAlpha = clamp(local * 2, 0, 1) * 0.9; g.fillRect(-5 * base, -3 * base, 10 * base, 6 * base); g.restore(); }
      else if (kind === 'sparkles') { const a = Math.max(0, Math.sin(local * 3 * speed + phase)); if (a <= 0.05) continue; const r = (4 + size * 8) * base * a; g.save(); g.translate(x0, y0); g.globalAlpha = a * 0.9; g.fillStyle = look.accent; g.beginPath(); for (let k = 0; k < 8; k++) { const ang = k * Math.PI / 4, rr = k % 2 ? r * 0.25 : r; g.lineTo(Math.cos(ang) * rr, Math.sin(ang) * rr); } g.closePath(); g.fill(); g.restore(); }
      else if (kind === 'snow') { const y = (y0 + local * H * 0.12 * speed) % (H + 10), x = x0 + Math.sin(local + phase) * 20 * base; g.globalAlpha = 0.85; g.fillStyle = '#ffffff'; g.beginPath(); g.arc(x, y, (1.5 + size * 3) * base, 0, Math.PI * 2); g.fill(); }
      else if (kind === 'bokeh') { const y = (y0 - local * 25 * speed * base + H * 2) % H, r = (40 + size * 90) * base; const grad = g.createRadialGradient(x0, y, 0, x0, y, r); grad.addColorStop(0, look.accent); grad.addColorStop(1, 'rgba(0,0,0,0)'); g.globalAlpha = 0.14 + 0.08 * Math.sin(local + phase); g.fillStyle = grad; g.beginPath(); g.arc(x0, y, r, 0, Math.PI * 2); g.fill(); }
      else if (kind === 'bubbles') { const y = H - ((local * H * 0.18 * speed + (H - y0)) % (H + 60)) + 30, r = (8 + size * 22) * base; g.globalAlpha = 0.5; g.strokeStyle = look.accent; g.lineWidth = 2; g.beginPath(); g.arc(x0 + Math.sin(local * 1.5 + phase) * 14 * base, y, r, 0, Math.PI * 2); g.stroke(); }
    }
    g.restore();
  }
  // A slow camera move over the whole scene
  function cameraOf(scene, local, W, H) {
    const q = clamp(local / Math.max(0.1, scene.duration), 0, 1), e = q * q * (3 - 2 * q);
    switch (scene.camera) {
      case 'zoom-in': return {s: 1 + 0.08 * e, x: 0, y: 0};
      case 'zoom-out': return {s: 1.08 - 0.08 * e, x: 0, y: 0};
      case 'pan-left': return {s: 1.08, x: (0.5 - e) * W * 0.05, y: 0};
      case 'pan-right': return {s: 1.08, x: (e - 0.5) * W * 0.05, y: 0};
      case 'pan-up': return {s: 1.08, x: 0, y: (0.5 - e) * H * 0.05};
      case 'pan-down': return {s: 1.08, x: 0, y: (e - 0.5) * H * 0.05};
      case 'drift': return {s: 1.04, x: Math.sin(q * Math.PI * 2) * W * 0.01, y: Math.cos(q * Math.PI * 2) * H * 0.01};
      default: return null;
    }
  }
  function paintBlock(g, b, look, model, local) {
    const p = b.enter > 0 ? clamp((local - b.start) / b.enter, 0, 1) : (local >= b.start ? 1 : 0);
    if (p <= 0) return;
    const item = b.item, e = easeOut(p), d = Math.min(model.w, model.h) * 0.06;
    g.save();
    const effect = b.effect, perPart = ['words', 'letters', 'scramble', 'wave'].includes(effect);
    if (effect !== 'type' && effect !== 'none' && !perPart && !(item.kind === 'chart' && effect === 'fade') && item.kind !== 'steps' && item.kind !== 'icons') g.globalAlpha *= e;
    if (effect === 'blur' && p < 1 && 'filter' in g) g.filter = 'blur(' + ((1 - e) * 14).toFixed(1) + 'px)';
    if (effect === 'drop') g.translate(0, -(1 - easeBack(p)) * d * 2.5);
    if (look.glow && (item.kind === 'title' || item.kind === 'number')) { g.shadowColor = look.accent; g.shadowBlur = (b.size || 40) * 0.45; }
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    if (effect === 'up') g.translate(0, (1 - e) * d);
    else if (effect === 'down') g.translate(0, -(1 - e) * d);
    else if (effect === 'left') g.translate(-(1 - e) * d, 0);
    else if (effect === 'right') g.translate((1 - e) * d, 0);
    else if ((effect === 'zoom' || effect === 'pop') && item.kind !== 'steps') { const s = effect === 'pop' ? 0.6 + 0.4 * easeBack(p) : 0.85 + 0.15 * e; g.translate(cx, cy); g.scale(s, s); g.translate(-cx, -cy); }
    const align = b.center ? 'center' : 'left';
    if (item.kind === 'number') {
      g.font = fontOf(look, b.size, 800); g.fillStyle = look.accent; g.textAlign = b.center ? 'center' : 'left';
      const value = item.count ? countText(item.count, item.count.value * easeOut(clamp(p * 1.15, 0, 1))) : item.value;
      g.fillText(value, b.center ? b.x + b.w / 2 : b.x, b.y + b.size * 0.88); g.textAlign = 'left';
      if (b.label) paintLines(g, b.label, look, b.x, b.y + b.size * 1.15, b.w, align, Infinity, look.muted);
    } else if (item.kind === 'chart') paintChart(g, b, look, p);
    else if (item.kind === 'steps') paintSteps(g, b, look, p);
    else if (item.kind === 'image') paintImage(g, b, look, model);
    else if (item.kind === 'icon' || item.kind === 'icons') paintIconBlock(g, b, look, p);
    else if (item.kind === 'progress') paintProgress(g, b, look, p);
    else if (item.kind === 'ring') paintRing(g, b, look, p);
    else if (item.kind === 'timeline') paintTimeline(g, b, look, p);
    else if (item.kind === 'compare') paintCompare(g, b, look, p);
    else if (item.kind === 'quote') paintQuote(g, b, look, p, perPart ? {kind: effect, p} : null);
    else if (item.kind === 'badge') paintBadge(g, b, look);
    else {
      const reveal = effect === 'type' ? Math.floor([...item.text].length * p) : Infinity;
      if (item.kind === 'bullet') { g.fillStyle = look.accent; g.beginPath(); g.arc(b.x + b.size * 0.3, b.y + b.size * b.lead / 2 + b.size * 0.05, b.size * 0.17, 0, Math.PI * 2); g.fill(); }
      if (item.kind === 'callout') { g.fillStyle = look.soft; roundRect(g, b.x, b.y, b.w, b.h, b.size * 0.4); g.fill(); g.fillStyle = look.accent; g.fillRect(b.x, b.y + b.size * 0.3, Math.max(4, b.size * 0.16), b.h - b.size * 0.6); }
      const color = item.kind === 'subtitle' || item.kind === 'callout' ? look.text : item.kind === 'text' && !b.center ? look.muted : look.text;
      paintLines(g, b, look, b.x + b.indent + (item.kind === 'callout' ? b.size * 0.3 : 0), b.y + b.pad, b.w - b.indent - (item.kind === 'callout' ? b.size * 0.6 : 0), align, reveal, item.kind === 'text' && b.center ? look.muted : color, perPart ? {kind: effect, p} : null);
      // Corporate and editorial titles get a rule drawn under them
      if (item.kind === 'title' && look.bar) { g.shadowBlur = 0; g.fillStyle = look.accent; const w = 90 * (b.size / 64) * easeOut(clamp(p * 1.4 - 0.2, 0, 1)), bx = b.center ? b.x + b.w / 2 - 45 * (b.size / 64) : b.x; g.fillRect(bx, b.y + b.h + b.size * 0.12, w, Math.max(4, b.size * 0.08)); }
    }
    g.restore();
  }
  // Captions: plain, or karaoke, where each word lights up as it is said (in time with the narration when there is one)
  function paintCaption(g, model, scene, layout, local) {
    const look = layout.look, q = easeOut(clamp((local - 0.2) / 0.5, 0, 1)), sb = layout.say.block, lineW = Math.max(...sb.lines.map(l => l.reduce((sum, p) => sum + p.w, 0)), 0);
    if (q <= 0 || !sb.lines.length) return;
    g.save(); g.globalAlpha *= q;
    const W = model.w, bw = Math.min(W * 0.86, lineW + sb.size * 1.6), bh = sb.h + sb.size * 0.8, bx = (W - bw) / 2, by = layout.say.y - bh / 2;
    g.fillStyle = 'rgba(0,0,0,0.66)'; roundRect(g, bx, by, bw, bh, sb.size * 0.5); g.fill();
    if (model.settings.captions !== 'karaoke' || sb.rtl) { paintLines(g, sb, look, (W - W * 0.8) / 2, by + sb.size * 0.4, W * 0.8, 'center', Infinity, '#ffffff'); g.restore(); return; }
    const words = sb.lines.flat().filter(pc => /\S/.test(pc.text)).length, speaking = scene.spoken || Math.max(0.6, scene.duration - 0.6);
    const said = clamp((local - 0.25) / speaking, 0, 1) * words;
    let k = 0;
    sb.lines.forEach((line, i) => {
      const lw = line.reduce((sum, piece) => sum + piece.w, 0), baseY = by + sb.size * 0.4 + i * sb.size * sb.lead + sb.size * (sb.lead - 1) / 2 + sb.size * 0.82;
      let cx = (W - lw) / 2;
      for (const piece of line) {
        g.font = fontOf(look, sb.size, piece.bold ? 800 : 600, piece.italic);
        if (/\S/.test(piece.text)) { g.fillStyle = k < said - 1 ? '#ffffff' : k < said ? '#facc15' : 'rgba(255,255,255,0.55)'; k++; }
        g.fillText(piece.text, cx, baseY); cx += piece.w;
      }
    });
    g.restore();
  }
  // A lower third: a name and a role sliding in from the left
  function paintLower(g, model, layout, local) {
    const lower = layout.lower, look = layout.look, q = easeOut(clamp((local - 0.35) / 0.6, 0, 1)); if (q <= 0) return;
    const base = layout.base, pad = 18 * base, x = model.w * 0.06, nameW = Math.max(...lower.name.lines.map(l => l.reduce((sum, p) => sum + p.w, 0)), 0), roleW = lower.role ? Math.max(...lower.role.lines.map(l => l.reduce((sum, p) => sum + p.w, 0)), 0) : 0;
    const w = Math.max(nameW, roleW) + pad * 2.5, h = lower.name.h + (lower.role ? lower.role.h : 0) + pad * 1.4;
    g.save(); g.globalAlpha *= q; g.translate(-(1 - q) * 60 * base, 0);
    g.fillStyle = look.text; g.globalAlpha *= 0.92; roundRect(g, x, lower.y, w * q, h, 10 * base); g.fill(); g.globalAlpha /= 0.92;
    g.fillStyle = look.accent; g.fillRect(x, lower.y, 8 * base, h);
    g.beginPath(); g.rect(x, lower.y, w * q, h); g.clip();
    paintLines(g, lower.name, look, x + pad * 1.4, lower.y + pad * 0.7, w, 'left', Infinity, look.bg.split(',')[0].trim());
    if (lower.role) paintLines(g, lower.role, look, x + pad * 1.4, lower.y + pad * 0.7 + lower.name.h, w, 'left', Infinity, look.accent);
    g.restore();
  }
  function paintScene(g, model, scene, local) {
    const layout = layoutScene(g, model, scene), look = layout.look, W = model.w, H = model.h;
    paintBackground(g, look, W, H);
    g.textBaseline = 'alphabetic';
    const camera = cameraOf(scene, local, W, H);
    g.save();
    if (camera) { g.translate(W / 2 + camera.x, H / 2 + camera.y); g.scale(camera.s, camera.s); g.translate(-W / 2, -H / 2); }
    paintPattern(g, look, look.pattern, W, H, scene.start + local, layout.base);
    if (scene.shapes.length) paintShapes(g, scene, look, W, H, local, layout.base);
    for (const b of layout.blocks) paintBlock(g, b, look, model, local);
    g.restore();
    paintParticles(g, scene.particles, look, W, H, local, scene.part + 1, layout.base);
    if (layout.lower) paintLower(g, model, layout, local);
    if (layout.say) paintCaption(g, model, scene, layout, local);
  }
  // One frame of the video at time t, drawn at any scale; the same function makes the preview and the export
  function paintMotion(g, model, t, scale = 1) {
    g.save(); g.setTransform(scale, 0, 0, scale, 0, 0);
    g.imageSmoothingQuality = 'high';
    const W = model.w, H = model.h, scenes = model.scenes;
    if (!scenes.length) { paintBackground(g, lookOf(model, null), W, H); g.restore(); return; }
    t = clamp(t, 0, model.total);
    let i = scenes.findIndex(scene => t < scene.start + scene.duration); if (i < 0) i = scenes.length - 1;
    const scene = scenes[i], local = t - scene.start, kind = scene.transition, span = Math.min(0.6, scene.duration / 3);
    if (i > 0 && kind !== 'cut' && local < span) {
      const prev = scenes[i - 1], q = easeOut(local / span);
      if (kind === 'slide') { g.save(); g.translate(-q * W * 0.35, 0); paintScene(g, model, prev, prev.duration); g.restore(); g.save(); g.translate((1 - q) * W, 0); paintScene(g, model, scene, local); g.restore(); }
      else if (kind === 'wipe') { paintScene(g, model, prev, prev.duration); g.save(); g.beginPath(); g.rect(0, 0, W * q, H); g.clip(); paintScene(g, model, scene, local); g.restore(); }
      else if (kind === 'iris') { paintScene(g, model, prev, prev.duration); g.save(); g.beginPath(); g.arc(W / 2, H / 2, Math.hypot(W, H) / 2 * q, 0, Math.PI * 2); g.clip(); paintScene(g, model, scene, local); g.restore(); }
      else if (kind === 'rise') { g.save(); g.translate(0, -q * H * 0.3); paintScene(g, model, prev, prev.duration); g.restore(); g.save(); g.translate(0, (1 - q) * H); paintScene(g, model, scene, local); g.restore(); }
      else if (kind === 'flash') { const linear = local / span; if (linear < 0.5) paintScene(g, model, prev, prev.duration); else paintScene(g, model, scene, local); g.save(); g.fillStyle = '#ffffff'; g.globalAlpha = 1 - Math.abs(linear * 2 - 1); g.fillRect(0, 0, W, H); g.restore(); }
      else if (kind === 'blur' && 'filter' in g) { g.save(); g.filter = 'blur(' + (q * 16).toFixed(1) + 'px)'; paintScene(g, model, prev, prev.duration); g.restore(); g.save(); g.globalAlpha = q; g.filter = 'blur(' + ((1 - q) * 16).toFixed(1) + 'px)'; paintScene(g, model, scene, local); g.restore(); }
      else { paintScene(g, model, prev, prev.duration); g.save(); g.globalAlpha = q; if (kind === 'zoom') { const s = 1.12 - 0.12 * q; g.translate(W / 2, H / 2); g.scale(s, s); g.translate(-W / 2, -H / 2); } paintScene(g, model, scene, local); g.restore(); }
    } else paintScene(g, model, scene, local);
    // Over every scene: the logo in a corner, and a bar showing how far the video has got
    const s = model.settings, logo = s.logo ? model.images?.get(s.logo) : null;
    if (logo && logo.naturalWidth) { const h = H * 0.07, w = Math.min(W * 0.22, h * logo.naturalWidth / logo.naturalHeight); g.save(); g.globalAlpha = 0.92; g.drawImage(logo, W - w - W * 0.035, H * 0.04, w, w * logo.naturalHeight / logo.naturalWidth); g.restore(); }
    if (s.progress) { const look = lookOf(model, scene); g.fillStyle = look.accent; g.fillRect(0, H - Math.max(4, H * 0.007), W * (t / Math.max(0.001, model.total)), Math.max(4, H * 0.007)); }
    g.restore();
  }
  // Pictures: from this site or as data only (or any https site when the network valve is open, asked for without
  // credentials so the canvas stays exportable). Anything else isn't loaded and is said so.
  function motionImages(model) {
    model.images = model.images || new Map();
    const waits = [];
    const logo = model.settings.logo ? [{src: model.settings.logo, line: 1, kind: 'image'}] : [];
    for (const item of [...logo, ...model.scenes.flatMap(scene => scene.items).filter(item => item.kind === 'image')]) {
      if (model.images.has(item.src)) continue;
      let ok = /^data:image\/(png|jpe?g|gif|webp);base64,/i.test(item.src), cross = false;
      try { const u = new URL(item.src, location.href); if (u.origin === location.origin && /^https?:$/.test(u.protocol)) ok = true; else if (CONFIG.network === 'open' && u.protocol === 'https:') { ok = true; cross = true; } } catch {}
      if (!ok) { model.images.set(item.src, null); if (!model.warnings.some(w => w.key === 'mw_image' && w.line === item.line)) model.warnings.push({line: item.line, key: 'mw_image', vars: {}}); continue; }
      const img = new Image(); if (cross) img.crossOrigin = 'anonymous'; img.referrerPolicy = 'no-referrer'; img.decoding = 'async';
      model.images.set(item.src, img);
      waits.push(new Promise(resolve => { img.onload = resolve; img.onerror = () => { model.images.set(item.src, null); resolve(); }; setTimeout(resolve, 8000); }));
      img.src = item.src;
    }
    return Promise.all(waits);
  }
  /* ---- motion: sound ---- */
  // Music and narration are decoded once per page and kept: a spoken line is only asked for once (Open WebUI also caches it)
  const soundCache = new Map();
  async function pool(list, size, worker) { let next = 0; const run = async () => { while (next < list.length) await worker(list[next++]); }; await Promise.all(Array.from({length: Math.min(size, list.length)}, run)); }
  let soundDecoder = null;
  const decodeSound = bytes => { soundDecoder = soundDecoder || new OfflineAudioContext(2, 1, 48000); return soundDecoder.decodeAudioData(bytes); };
  const note = (model, line, key, vars = {}) => { if (!model.warnings.some(w => w.key === key && w.line === line)) model.warnings.push({line, key, vars}); };
  const fromResponse = async response => { if (!response.ok) { const e = new Error('HTTP ' + response.status); e.status = response.status; throw e; } return response.arrayBuffer(); };
  // A data: address is decoded here; anything else goes through request(), to this site only
  const dataBytes = url => { const bin = atob(url.slice(url.indexOf(',') + 1)), out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out.buffer; };
  function loadSound(key, bytes) {
    if (!soundCache.has(key)) { const promise = Promise.resolve().then(bytes).then(decodeSound); soundCache.set(key, promise); promise.catch(() => soundCache.delete(key)); }
    return soundCache.get(key);
  }
  async function motionSound(model, onStatus) {
    const s = model.settings, music = s.music;
    if (music) {
      let own = false, ok = /^data:audio\/[\w.+-]+;base64,/i.test(music);
      try { const u = new URL(music, location.href); if (u.origin === location.origin && /^https?:$/.test(u.protocol)) { ok = true; own = true; } } catch {}
      if (!ok) note(model, 1, 'mw_music_site');
      else { try { model.music = await loadSound('music:' + music, () => own ? request(music).then(fromResponse) : dataBytes(music)); } catch { note(model, 1, 'mw_music'); } }
    }
    if (s.voice) {
      const spoken = model.scenes.filter(scene => scene.say);
      let done = 0, refused = false;
      onStatus?.(spoken.length ? 0 : 1);
      await pool(spoken, 3, async scene => {
        if (refused) return;
        try {
          scene.voice = await loadSound('say:' + scene.say, () => request('/api/v1/audio/speech', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({input: scene.say})}, 60000).then(fromResponse));
          scene.spoken = scene.voice.duration;
        } catch (error) { refused = true; if (!model.warnings.some(w => w.key === 'mw_voice')) note(model, scene.line, 'mw_voice'); }
        onStatus?.(++done / spoken.length);
      });
      timeScenes(model);
    }
    model.sounding = !!(model.music || model.scenes.some(scene => scene.voice));
  }
  // What plays when: the music under everything (quieter while someone speaks, fading out at the end), each scene's voice
  function soundPlan(model) {
    const voices = model.scenes.filter(scene => scene.voice).map(scene => ({buffer: scene.voice, at: scene.start + 0.25}));
    return {music: model.music ? {buffer: model.music, volume: model.settings.volume} : null, voices, total: model.total};
  }
  function scheduleSound(audio, destination, plan, from, now) {
    const sources = [];
    if (plan.music) {
      const src = audio.createBufferSource(), gain = audio.createGain();
      src.buffer = plan.music.buffer; src.loop = true; src.connect(gain); gain.connect(destination);
      const v = plan.music.volume, rel = t => now + Math.max(0, t - from);
      gain.gain.setValueAtTime(from < 0.8 ? 0 : v, now); if (from < 0.8) gain.gain.linearRampToValueAtTime(v, rel(0.8));
      for (const voice of plan.voices) { const a = voice.at, b = voice.at + voice.buffer.duration; if (b <= from) continue; gain.gain.setTargetAtTime(v * 0.35, rel(a - 0.15), 0.08); gain.gain.setTargetAtTime(v, rel(b), 0.25); }
      const end = plan.total - from; if (end > 1.6) { gain.gain.setValueAtTime(v, now + end - 1.5); gain.gain.linearRampToValueAtTime(0, now + end); }
      src.start(now, from % plan.music.buffer.duration); src.stop(now + Math.max(0, plan.total - from)); sources.push(src);
    }
    for (const voice of plan.voices) {
      const end = voice.at + voice.buffer.duration; if (end <= from) continue;
      const src = audio.createBufferSource(); src.buffer = voice.buffer; src.connect(destination);
      if (voice.at >= from) src.start(now + voice.at - from); else src.start(now, from - voice.at);
      sources.push(src);
    }
    return sources;
  }
  // The whole soundtrack as one stereo buffer, for the video file
  async function mixSound(model) {
    if (!model.sounding || model.total <= 0) return null;
    const audio = new OfflineAudioContext(2, Math.ceil(model.total * 48000), 48000);
    scheduleSound(audio, audio.destination, soundPlan(model), 0, 0);
    return audio.startRendering();
  }
  // Everything a frame needs: its fonts and its pictures, and the sound when it has any
  async function motionReady(model, onStatus) {
    try { await Promise.race([Promise.all([400, 500, 600, 700, 800].map(w => document.fonts.load(w + ' 32px ' + model.family))), pause(3000)]); } catch {}
    await motionImages(model);
    await motionSound(model, onStatus);
    model.layouts?.clear();
  }
  const motionModel = text => { const model = motionOf(text); model.family = getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif'; return model; };
  const clock = seconds => { const s = Math.max(0, Math.floor(seconds + 1e-6)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  const sceneAt = (model, t) => { const i = model.scenes.findIndex(scene => t < scene.start + scene.duration - 1e-6); return i < 0 ? Math.max(0, model.scenes.length - 1) : i; };

  /* ---- motion: the player ---- */
  function motionView(text, ctx, editable = false, commit = null) {
    const model = motionModel(text);
    const wrap = el('div', 'ar-motion');
    if (!model.scenes.length) { wrap.append(el('p', 'ar-muted', t('m_none'))); return wrap; }
    // Playing, paused or at the end; the state also shows on <html> as data-ar-motion
    const player = machine('motion', 'paused', {paused: {play: 'playing'}, playing: {pause: 'paused', end: 'ended'}, ended: {play: 'playing', seek: 'paused'}});
    // It plays by itself the first time it is shown; after an edit it stays where it was
    const first = ctx.motionTime == null;
    let time = clamp(ctx.motionTime ?? 0, 0, model.total), last = 0, frame = 0, ready = false;
    const stage = el('div', 'ar-motionstage'); stage.tabIndex = 0; stage.setAttribute('role', 'region'); setLabel(stage, displayTitleOf(model));
    // Wide or tall, the whole frame fits the panel
    stage.style.aspectRatio = model.w + ' / ' + model.h;
    stage.style.width = 'min(100% - 2rem, 64rem, calc(62vh * ' + (model.w / model.h).toFixed(4) + '))';
    const canvas = el('canvas', 'ar-motioncanvas');
    const dpr = clamp(window.devicePixelRatio || 1, 1, 2), scale = Math.min(dpr, 1920 / Math.max(model.w, model.h));
    canvas.width = Math.round(model.w * scale); canvas.height = Math.round(model.h * scale);
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', displayTitleOf(model));
    const g = canvas.getContext('2d');
    stage.append(canvas);
    const playButton = iconButton(t('m_play'), 'play', () => toggle());
    const timeLabel = el('span', 'ar-counter'), sceneLabel = el('span', 'ar-muted ar-small');
    const seek = el('input', 'ar-motionseek'); seek.type = 'range'; seek.min = '0'; seek.max = String(Math.round(model.total * 1000)); seek.step = String(Math.round(1000 / 30)); setLabel(seek, t('m_seek'));
    seek.addEventListener('input', () => { setTime(+seek.value / 1000); if (player.is('ended')) player.send('seek'); });
    const full = iconButton(t('m_full'), 'expand', () => stage.requestFullscreen?.().catch(() => {}));
    // Sound plays in time with the frames; it starts only once someone presses play, as browsers require
    let audio = null, sources = [], muted = false;
    const muteButton = iconButton(t('m_mute'), 'sound', () => { muted = !muted; paintMute(); if (player.is('playing')) { stopSound(); startSound(); } });
    muteButton.hidden = true;
    const paintMute = () => { muteButton.replaceChildren(svgIcon(muted ? 'mute' : 'sound')); setLabel(muteButton, t(muted ? 'm_unmute' : 'm_mute')); muteButton.setAttribute('aria-pressed', String(muted)); };
    function startSound() {
      if (!model.sounding || muted) return;
      try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume?.(); } catch { return; }
      sources = scheduleSound(audio, audio.destination, soundPlan(model), time, audio.currentTime + 0.03);
    }
    function stopSound() { for (const src of sources) { try { src.stop(); } catch {} } sources = []; }
    const status = el('span', 'ar-muted ar-small ar-motionstatus');
    const bar = el('div', 'ar-subbar'); bar.append(playButton, timeLabel, sceneLabel, status, el('span', 'ar-spacer'), muteButton);
    const editor = el('div', 'ar-slideedit'); editor.hidden = true;
    // Each change says where the player should be afterwards (ctx.motionTime) before it is saved
    const rewrite = apply => { const parts = motionParts(text); apply(parts.scenes); commit(motionText(parts)); };
    if (editable && commit) {
      const edit = iconButton(t('m_edit'), 'pencil', () => openEditor());
      const add = iconButton(t('m_add'), 'plus', () => { const scene = model.scenes[sceneAt(model, time)]; ctx.motionTime = scene.start + scene.duration + 0.01; rewrite(scenes => scenes.splice(scene.part + 1, 0, {head: '--- 3s fade', lines: ['# ' + t('untitled'), '']})); });
      const remove = iconButton(t('m_del'), 'row_del', () => { if (model.scenes.length < 2) return; const scene = model.scenes[sceneAt(model, time)]; ctx.motionTime = scene.start; rewrite(scenes => scenes.splice(scene.part, 1)); });
      remove.disabled = model.scenes.length < 2;
      // The soundtrack: a music file from your device (kept in your Open WebUI files), and narration of the captions
      const setting = (key, value) => { const parts = motionParts(text), re = new RegExp('^\\s*' + key + '\\s*:', 'i'), at = parts.prefix.findIndex(l => re.test(l)); if (value == null) { if (at >= 0) parts.prefix.splice(at, 1); } else if (at >= 0) parts.prefix[at] = key + ': ' + value; else { let last = -1; parts.prefix.forEach((l, k) => { if (SETTING_RE.test(l) || MARKER_RE.test(l)) last = k; }); parts.prefix.splice(last + 1, 0, key + ': ' + value); } ctx.motionTime = time; commit(motionText(parts)); };
      const picker = el('input'); picker.type = 'file'; picker.accept = 'audio/*'; picker.hidden = true;
      picker.addEventListener('change', async () => {
        const file = picker.files?.[0]; picker.value = ''; if (!file) return;
        if (file.size > 25 * 1048576) { toast(t('m_music_big')); return; }
        toast(t('m_music_adding'));
        try { const form = new FormData(); form.append('file', file); const made = await api('/api/v1/files/?process=false', null, {method: 'POST', body: form}, 120000); if (!made?.id) throw new Error('upload'); setting('music', '/api/v1/files/' + made.id + '/content | ' + model.settings.volume); }
        catch { toast(t('m_music_failed')); }
      });
      const musicButton = iconButton(model.settings.music ? t('m_music_remove') : t('m_music'), 'music', () => { if (model.settings.music) setting('music', null); else picker.click(); });
      musicButton.setAttribute('aria-pressed', String(!!model.settings.music));
      const voiceButton = iconButton(model.settings.voice ? t('m_voice_off') : t('m_voice_on'), 'voice', () => setting('voice', model.settings.voice ? null : 'on'));
      voiceButton.setAttribute('aria-pressed', String(!!model.settings.voice)); voiceButton.disabled = !model.scenes.some(scene => scene.say) && !model.settings.voice;
      bar.append(edit, add, remove, musicButton, voiceButton, picker);
    }
    bar.append(full);
    function openEditor() {
      pauseIt();
      const scene = model.scenes[sceneAt(model, time)], part = motionParts(text).scenes[scene.part];
      const head = SCENE_RE.exec(part.head || '')?.[1] || '';
      const given = head.split(/\s+/).map(readSeconds).find(v => v != null);
      const length = el('input', 'ar-input ar-motionlen'); length.type = 'number'; length.min = String(MOTION_LIMITS.sceneMin); length.max = String(MOTION_LIMITS.sceneMax); length.step = '0.5'; length.placeholder = t('m_auto'); length.value = given != null ? String(given) : '';
      const kind = el('select', 'ar-select');
      for (const tr of MOTION_TRANSITIONS) { const o = el('option', null, t('tr_' + tr)); o.value = tr; kind.append(o); }
      kind.value = scene.transition;
      const l1 = el('label', 'ar-label ar-inline', t('m_duration')); l1.append(length);
      const l2 = el('label', 'ar-label ar-inline', t('m_transition')); l2.append(kind);
      const area = el('textarea', 'ar-input ar-slidearea'); area.value = part.lines.join('\n').trim(); area.rows = Math.min(16, Math.max(6, countLines(area.value) + 1)); area.dir = 'auto'; setLabel(area, t('m_edit'));
      const keep = textButton(t('m_save'), () => {
        const seconds = readSeconds(length.value);
        const words = head.split(/\s+/).filter(w => w && readSeconds(w) == null && !MOTION_TRANSITIONS.includes(w.toLowerCase()));
        const newHead = ['---', seconds != null ? clamp(seconds, MOTION_LIMITS.sceneMin, MOTION_LIMITS.sceneMax) + 's' : '', kind.value, ...words].filter(Boolean).join(' ');
        ctx.motionTime = scene.start;
        rewrite(scenes => { scenes[scene.part] = {head: newHead, lines: [...area.value.replace(/\r\n?/g, '\n').split('\n').map(l => SCENE_RE.test(l) ? l.replace(/-{3,}/, '—') : l), '']}; });
        toast(t('m_saved'));
      }, 'ar-btn ar-primary');
      const cancel = textButton(t('cancel'), () => { editor.hidden = true; editor.replaceChildren(); stage.focus(); });
      area.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); keep.click(); } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel.click(); } });
      const fields = el('div', 'ar-motionfields'); fields.append(l1, l2);
      editor.replaceChildren(fields, area, actionsRow(cancel, keep)); editor.hidden = false; area.focus();
    }
    // Each scene as a picture of how it ends; choosing one goes to its start
    const strip = el('div', 'ar-thumbs ar-motionthumbs');
    const thumbs = model.scenes.map((scene, i) => {
      const b = el('button', 'ar-thumb'); b.type = 'button'; setLabel(b, t('m_scene', {n: i + 1, total: model.scenes.length}));
      const c = el('canvas'); c.width = 192; c.height = Math.round(192 * model.h / model.w); b.append(c);
      b.addEventListener('click', () => { setTime(scene.start + Math.min(0.01, scene.duration / 2)); if (player.is('ended')) player.send('seek'); stage.focus(); });
      strip.append(b); return {b, c, scene};
    });
    const paintThumbs = () => { for (const th of thumbs) paintMotion(th.c.getContext('2d'), model, th.scene.start + th.scene.duration - 0.001, th.c.width / model.w); };
    // What was adjusted while reading: a bad length, an unknown transition, a picture from elsewhere
    const notes = el('ul', 'ar-motionwarn');
    const paintNotes = () => {
      notes.replaceChildren(...model.warnings.map(w => el('li', null, t('mw_line', {line: w.line, what: t(w.key, w.vars)}))));
      notes.hidden = !model.warnings.length; notes.setAttribute('aria-label', t('m_problems'));
    };
    function draw() {
      paintMotion(g, model, time, scale);
      seek.value = String(Math.round(time * 1000)); seek.setAttribute('aria-valuetext', clock(time) + ' / ' + clock(model.total));
      timeLabel.textContent = clock(time) + ' / ' + clock(model.total);
      const i = sceneAt(model, time); sceneLabel.textContent = t('m_scene', {n: i + 1, total: model.scenes.length});
      thumbs.forEach((th, j) => th.b.setAttribute('aria-current', String(j === i)));
      stage.dataset.time = time.toFixed(3);
      ctx.motionTime = time;
    }
    function setTime(value) { time = clamp(value, 0, model.total); draw(); if (player.is('playing')) { stopSound(); startSound(); } }
    function tick(now) {
      frame = 0;
      if (!canvas.isConnected) { player.send('pause'); stopSound(); return; }
      if (!player.is('playing')) return;
      // A hidden tab or a slow frame doesn't make the video jump ahead
      const delta = Math.min(0.1, (now - last) / 1000); last = now;
      time = Math.min(model.total, time + delta);
      draw();
      if (time >= model.total) { player.send('end'); stopSound(); paintButton(); return; }
      frame = requestAnimationFrame(tick);
    }
    function paintButton() { const playing = player.is('playing'); playButton.replaceChildren(svgIcon(playing ? 'pause' : 'play')); setLabel(playButton, t(playing ? 'm_pause' : 'm_play')); }
    function play() { if (!ready) return; if (player.is('ended') || time >= model.total - 1e-3) time = 0; if (player.send('play')) { last = performance.now(); frame = requestAnimationFrame(tick); startSound(); } paintButton(); }
    function pauseIt() { if (player.send('pause')) { cancelAnimationFrame(frame); stopSound(); } paintButton(); }
    function toggle() { player.is('playing') ? pauseIt() : play(); }
    stage.addEventListener('keydown', event => {
      if (event.target !== stage) return;
      const step = event.shiftKey ? 5 : 1;
      if (event.key === ' ' || event.key === 'k' || event.key === 'K') { event.preventDefault(); toggle(); }
      else if (event.key === 'ArrowRight') { event.preventDefault(); setTime(time + step); if (player.is('ended')) player.send('seek'); }
      else if (event.key === 'ArrowLeft') { event.preventDefault(); setTime(time - step); if (player.is('ended')) player.send('seek'); }
      else if (event.key === '.') { event.preventDefault(); pauseIt(); setTime(time + 1 / 30); }
      else if (event.key === ',') { event.preventDefault(); pauseIt(); setTime(time - 1 / 30); }
      else if (event.key === 'Home') { event.preventDefault(); setTime(0); }
      else if (event.key === 'End') { event.preventDefault(); setTime(model.total); }
    });
    stage.addEventListener('click', () => { toggle(); stage.focus(); });
    const seekRow = el('div', 'ar-motionseekrow'); seekRow.append(seek);
    wrap.append(bar, stage, seekRow, editor, strip, notes);
    paintButton(); paintNotes(); draw();
    // Fonts and pictures first; then it plays by itself, unless the person prefers less motion or came back to it
    if (model.settings.voice) status.textContent = t('m_voice_wait');
    motionReady(model, value => { if (model.settings.voice) status.textContent = t('m_voice_wait') + ' ' + Math.round(value * 100) + '%'; }).then(() => {
      status.textContent = '';
      // Narration may have made scenes longer
      seek.max = String(Math.round(model.total * 1000)); thumbs.forEach(th => th.b.dataset.start = th.scene.start);
      muteButton.hidden = !model.sounding; paintMute(); stage.dataset.sound = model.sounding ? 'yes' : 'no';
      ready = true; paintNotes(); draw(); paintThumbs();
      const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      if (!still && first && canvas.isConnected) play();
    });
    ctx.motionModel = model;
    return wrap;
  }
  const displayTitleOf = model => model.settings.title || t('t_motion');
  function motionStats(text) { const model = motionOf(text); return t('scenes_n', {n: model.scenes.length}) + ' · ' + clock(model.total); }

  /* ---- motion: making the video ---- */
  // An MP4 file written by hand around the H.264 samples the browser's own encoder made: one track, one chunk,
  // its index (moov) first so the video starts playing before it has fully loaded.
  function mp4Of(samples, avcC, W, H, fps, sound = null) {
    const timescale = fps * 1000, delta = 1000, count = samples.length, msDuration = Math.round(count * 1000 / fps);
    const u32 = v => [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255], u16 = v => [(v >>> 8) & 255, v & 255];
    const ascii = s => [...s].map(c => c.charCodeAt(0));
    const join = parts => { let size = 0; for (const p of parts) size += p.length; const out = new Uint8Array(size); let at = 0; for (const p of parts) { out.set(p, at); at += p.length; } return out; };
    const box = (type, ...parts) => { const body = join(parts.map(p => p instanceof Uint8Array ? p : Uint8Array.from(p))); return join([Uint8Array.from([...u32(body.length + 8), ...ascii(type)]), body]); };
    const full = (type, version, flags, ...parts) => box(type, [version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255], ...parts);
    const matrix = [...u32(0x10000), ...u32(0), ...u32(0), ...u32(0), ...u32(0x10000), ...u32(0), ...u32(0), ...u32(0), ...u32(0x40000000)];
    const keys = samples.map((s, i) => s.key ? i + 1 : 0).filter(Boolean);
    // The sound track: AAC (with its esds descriptor) or Opus (with dOps), each packet's length in samples
    const rate = sound?.rate || 48000, packets = sound?.chunks || [];
    const lengths = packets.map(c => Math.max(1, Math.round((c.dur || 0) * rate / 1e6)) || (sound?.codec === 'aac' ? 1024 : 960));
    const soundSamples = lengths.reduce((sum, n) => sum + n, 0), soundMs = Math.round(soundSamples * 1000 / rate);
    const runs = []; for (const n of lengths) { const last = runs[runs.length - 1]; if (last && last[1] === n) last[0]++; else runs.push([1, n]); }
    const descriptor = (tag, bytes) => [tag, bytes.length, ...bytes];
    const entry = () => {
      const head = [new Array(6).fill(0), u16(1), u32(0), u32(0), u16(2), u16(16), u16(0), u16(0), u32(rate << 16 >>> 0)];
      if (sound.codec === 'aac') return box('mp4a', ...head, full('esds', 0, 0, descriptor(0x03, [...u16(0), 0, ...descriptor(0x04, [0x40, 0x15, 0, 0, 0, ...u32(128000), ...u32(128000), ...descriptor(0x05, [...sound.description])]), ...descriptor(0x06, [0x02])])));
      return box('Opus', ...head, box('dOps', [0, 2, ...u16(sound.preskip ?? 312), ...u32(rate), ...u16(0), 0]));
    };
    const soundTrak = offset => box('trak',
      full('tkhd', 0, 3, u32(0), u32(0), u32(2), u32(0), u32(soundMs), u32(0), u32(0), u16(0), u16(1), u16(0x0100), u16(0), matrix, u32(0), u32(0)),
      box('mdia',
        full('mdhd', 0, 0, u32(0), u32(0), u32(rate), u32(soundSamples), u16(0x55c4), u16(0)),
        full('hdlr', 0, 0, u32(0), ascii('soun'), u32(0), u32(0), u32(0), ascii('SoundHandler'), [0]),
        box('minf',
          full('smhd', 0, 0, u16(0), u16(0)),
          box('dinf', full('dref', 0, 0, u32(1), full('url ', 0, 1))),
          box('stbl',
            full('stsd', 0, 0, u32(1), entry()),
            full('stts', 0, 0, u32(runs.length), ...runs.map(([n, d]) => [...u32(n), ...u32(d)])),
            full('stsc', 0, 0, u32(1), u32(1), u32(packets.length), u32(1)),
            full('stsz', 0, 0, u32(0), u32(packets.length), ...packets.map(c => u32(c.data.length))),
            full('stco', 0, 0, u32(1), u32(offset))))));
    const ftyp = box('ftyp', ascii('isom'), u32(512), ascii('isom'), ascii('iso2'), ascii('avc1'), ascii('mp41'));
    const videoBytes = samples.reduce((sum, s) => sum + s.data.length, 0);
    const moovWith = offset => box('moov',
      full('mvhd', 0, 0, u32(0), u32(0), u32(1000), u32(Math.max(msDuration, soundMs)), u32(0x00010000), u16(0x0100), u16(0), u32(0), u32(0), matrix, new Array(24).fill(0), u32(sound ? 3 : 2)),
      box('trak',
        full('tkhd', 0, 3, u32(0), u32(0), u32(1), u32(0), u32(msDuration), u32(0), u32(0), u16(0), u16(0), u16(0), u16(0), matrix, u32(W << 16), u32(H << 16)),
        box('mdia',
          full('mdhd', 0, 0, u32(0), u32(0), u32(timescale), u32(count * delta), u16(0x55c4), u16(0)),
          full('hdlr', 0, 0, u32(0), ascii('vide'), u32(0), u32(0), u32(0), ascii('VideoHandler'), [0]),
          box('minf',
            full('vmhd', 0, 1, u16(0), u16(0), u16(0), u16(0)),
            box('dinf', full('dref', 0, 0, u32(1), full('url ', 0, 1))),
            box('stbl',
              full('stsd', 0, 0, u32(1), box('avc1', new Array(6).fill(0), u16(1), u16(0), u16(0), u32(0), u32(0), u32(0), u16(W), u16(H), u32(0x00480000), u32(0x00480000), u32(0), u16(1), new Array(32).fill(0), u16(0x0018), u16(0xffff), box('avcC', avcC))),
              full('stts', 0, 0, u32(1), u32(count), u32(delta)),
              full('stss', 0, 0, u32(keys.length), ...keys.map(u32)),
              full('stsc', 0, 0, u32(1), u32(1), u32(count), u32(1)),
              full('stsz', 0, 0, u32(0), u32(count), ...samples.map(s => u32(s.data.length))),
              full('stco', 0, 0, u32(1), u32(offset)))))),
      ...(sound ? [soundTrak(offset + videoBytes)] : []));
    const probeMoov = moovWith(0);
    const dataSize = videoBytes + packets.reduce((sum, c) => sum + c.data.length, 0);
    const moov = moovWith(ftyp.length + probeMoov.length + 8);
    return new Blob([ftyp, moov, Uint8Array.from([...u32(dataSize + 8), ...ascii('mdat')]), ...samples.map(s => s.data), ...packets.map(c => c.data)], {type: 'video/mp4'});
  }
  const abortError = () => new DOMException('Cancelled', 'AbortError');
  const bytesOf = d => new Uint8Array(ArrayBuffer.isView(d) ? d.buffer.slice(d.byteOffset, d.byteOffset + d.byteLength) : d.slice(0));
  async function encodeSound(buffer, signal) {
    if (!buffer || typeof AudioEncoder !== 'function' || typeof AudioData !== 'function') return null;
    for (const [codec, kind] of [['mp4a.40.2', 'aac'], ['opus', 'opus']]) {
      const config = {codec, sampleRate: 48000, numberOfChannels: 2, bitrate: 128000};
      try { if (!(await AudioEncoder.isConfigSupported(config)).supported) continue; } catch { continue; }
      const chunks = []; let description = null, failure = null;
      const encoder = new AudioEncoder({output: (chunk, meta) => { const data = new Uint8Array(chunk.byteLength); chunk.copyTo(data); chunks.push({data, ts: chunk.timestamp, dur: chunk.duration}); const d = meta?.decoderConfig?.description; if (d && !description) description = bytesOf(d); }, error: error => { failure = error; }});
      encoder.configure(config);
      const L = buffer.getChannelData(0), R = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : L, step = 48000;
      try {
        for (let at = 0; at < buffer.length; at += step) {
          if (signal.aborted) throw abortError();
          const n = Math.min(step, buffer.length - at), planar = new Float32Array(n * 2); planar.set(L.subarray(at, at + n), 0); planar.set(R.subarray(at, at + n), n);
          const data = new AudioData({format: 'f32-planar', sampleRate: 48000, numberOfFrames: n, numberOfChannels: 2, timestamp: Math.round(at / 48 * 1000), data: planar});
          encoder.encode(data); data.close();
          while (encoder.encodeQueueSize > 4 && !failure) await pause(2);
        }
        await encoder.flush();
      } finally { try { encoder.close(); } catch {} }
      if (failure || !chunks.length) continue;
      if (kind === 'aac' && !description) continue;
      // Opus' own header (OpusHead) says how many samples to skip at the start
      let preskip = 312; if (kind === 'opus' && description?.length >= 12 && String.fromCharCode(...description.slice(0, 8)) === 'OpusHead') preskip = description[10] | (description[11] << 8);
      return {codec: kind, chunks, description, preskip, rate: 48000};
    }
    return null;
  }
  // Faster than real time and frame-exact: every frame is drawn, encoded to H.264 by the browser, and written into an MP4.
  // Needs WebCodecs, which browsers offer only on secure (https or localhost) pages.
  async function encodeMotion(model, W, H, fps, signal, progress, mix = null) {
    if (typeof VideoEncoder !== 'function' || typeof VideoFrame !== 'function' || !window.isSecureContext) return null;
    // With a soundtrack, the sound is encoded first; a browser that can't encode sound records in real time instead
    const sound = mix ? await encodeSound(mix, signal) : null;
    if (mix && !sound) return null;
    let config = null;
    for (const codec of ['avc1.640028', 'avc1.4d0028', 'avc1.42e028', 'avc1.42001f']) {
      const want = {codec, width: W, height: H, bitrate: W * H > 1e6 ? 8e6 : 4e6, framerate: fps, avc: {format: 'avc'}};
      try { const answer = await VideoEncoder.isConfigSupported(want); if (answer.supported) { config = answer.config || want; break; } } catch {}
    }
    if (!config) return null;
    const samples = []; let description = null, failure = null;
    const encoder = new VideoEncoder({
      output: (chunk, meta) => {
        const data = new Uint8Array(chunk.byteLength); chunk.copyTo(data);
        samples.push({data, ts: chunk.timestamp, key: chunk.type === 'key'});
        const d = meta?.decoderConfig?.description;
        if (d && !description) description = new Uint8Array(ArrayBuffer.isView(d) ? d.buffer.slice(d.byteOffset, d.byteOffset + d.byteLength) : d.slice(0));
      },
      error: error => { failure = error; }
    });
    encoder.configure(config);
    const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
    const g = canvas.getContext('2d', {alpha: false});
    const frames = Math.max(1, Math.round(model.total * fps)), scale = W / model.w;
    try {
      for (let i = 0; i < frames; i++) {
        if (signal.aborted) throw abortError();
        if (failure) throw failure;
        paintMotion(g, model, i / fps, scale);
        const frame = new VideoFrame(canvas, {timestamp: Math.round(i * 1e6 / fps), duration: Math.round(1e6 / fps)});
        encoder.encode(frame, {keyFrame: i % (fps * 2) === 0}); frame.close();
        while (encoder.encodeQueueSize > 6 && !failure) await pause(3);
        if (i % 6 === 0) { progress(i / frames); await pause(0); }
      }
      await encoder.flush();
    } finally { try { encoder.close(); } catch {} }
    if (failure) throw failure;
    // This writer keeps samples in the order they come; a stream that reorders frames goes to the other way
    if (!description || samples.length !== frames || samples.some((s, i) => i && s.ts <= samples[i - 1].ts)) return null;
    progress(1);
    return mp4Of(samples, description, W, H, fps, sound);
  }
  // The other way: the browser records the canvas while the video plays once, in real time (MP4 where it can, else WebM)
  async function recordMotion(model, W, H, fps, signal, progress, mix = null) {
    if (typeof MediaRecorder !== 'function') throw new Error('recorder');
    const types = mix ? ['video/mp4;codecs=avc1,mp4a.40.2', 'video/mp4;codecs=avc1,opus', 'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'] : ['video/mp4;codecs=avc1', 'video/mp4', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
    const type = types.find(each => { try { return MediaRecorder.isTypeSupported(each); } catch { return false; } });
    if (!type) throw new Error('recorder');
    const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
    const g = canvas.getContext('2d', {alpha: false}), scale = W / model.w;
    paintMotion(g, model, 0, scale);
    const stream = canvas.captureStream(0), track = stream.getVideoTracks()[0];
    const manual = typeof track?.requestFrame === 'function';
    let live = manual ? stream : canvas.captureStream(fps);
    // The soundtrack plays into the recording, not out of the speakers
    let audio = null, soundSource = null;
    if (mix) { try { audio = new (window.AudioContext || window.webkitAudioContext)(); await audio.resume?.(); const out = audio.createMediaStreamDestination(); soundSource = audio.createBufferSource(); soundSource.buffer = mix; soundSource.connect(out); live = new MediaStream([...live.getVideoTracks(), ...out.stream.getAudioTracks()]); } catch { audio = null; } }
    const recorder = new MediaRecorder(live, {mimeType: type, videoBitsPerSecond: W * H > 1e6 ? 8e6 : 4e6, audioBitsPerSecond: 128000});
    const parts = [];
    recorder.ondataavailable = event => { if (event.data?.size) parts.push(event.data); };
    const stopped = new Promise((resolve, reject) => { recorder.onstop = resolve; recorder.onerror = event => reject(event.error || new Error('recorder')); });
    recorder.start(1000);
    soundSource?.start();
    const began = performance.now();
    try {
      for (;;) {
        if (signal.aborted) throw abortError();
        const now = (performance.now() - began) / 1000;
        if (now >= model.total) break;
        paintMotion(g, model, now, scale); if (manual) track.requestFrame();
        progress(now / model.total);
        await pause(1000 / fps);
      }
      paintMotion(g, model, model.total, scale); if (manual) track.requestFrame();
      await pause(150);
    } finally {
      if (recorder.state !== 'inactive') recorder.stop();
      await stopped.catch(() => {});
      for (const each of [stream, live]) each.getTracks().forEach(tr => tr.stop());
      try { soundSource?.stop(); } catch {} audio?.close?.();
    }
    if (signal.aborted) throw abortError();
    return new Blob(parts, {type: type.split(';')[0]});
  }
  // One video at a time; the dialog shows how far it has got and can stop it
  const making = machine('export', 'idle', {idle: {start: 'running'}, running: {done: 'idle', fail: 'idle', cancel: 'idle'}});
  async function motionVideo(a, text, height) {
    if (!making.send('start')) { toast(t('m_busy')); return; }
    const model = motionModel(text);
    // "1080p" is the short side, so a portrait video is 1080 wide; H.264 needs even sizes
    const ratio = model.w / model.h, short = Math.round(height / 2) * 2;
    const W = ratio >= 1 ? Math.round(short * ratio / 2) * 2 : short, Hh = ratio >= 1 ? short : Math.round(short / ratio / 2) * 2;
    const controller = new AbortController();
    const box = el('dialog', 'ar-dialog ar-progressdialog'); box.setAttribute('aria-labelledby', 'ar-video-title');
    const heading = el('h3', null, t('m_making', {p: 0})); heading.id = 'ar-video-title';
    const meter = el('progress', 'ar-progress'); meter.max = 1; meter.value = 0;
    const note = el('p', 'ar-muted ar-small', '');
    const stop = textButton(t('cancel'), () => controller.abort());
    box.append(heading, meter, note, actionsRow(el('span', 'ar-spacer'), stop));
    box.addEventListener('cancel', event => { event.preventDefault(); controller.abort(); });
    panel.append(box); box.showModal();
    const progress = value => { const p = Math.round(clamp(value, 0, 1) * 100); meter.value = clamp(value, 0, 1); heading.textContent = t('m_making', {p}); };
    try {
      await motionReady(model, value => { if (model.settings.voice) heading.textContent = t('m_voice_wait') + ' ' + Math.round(value * 100) + '%'; });
      const mix = model.sounding ? await mixSound(model) : null;
      let blob = await encodeMotion(model, W, Hh, 30, controller.signal, progress, mix);
      if (!blob) { note.textContent = t('m_realtime'); blob = await recordMotion(model, W, Hh, 30, controller.signal, progress, mix); }
      const ext = /mp4/.test(blob.type) ? 'mp4' : 'webm';
      save(blob, baseName(a) + '.' + ext);
      toast(ext === 'mp4' ? t('m_made') : t('m_webm'));
      making.send('done');
    } catch (error) {
      making.send(error?.name === 'AbortError' ? 'cancel' : 'fail');
      toast(error?.name === 'AbortError' ? t('m_cancelled') : t('m_failed'));
    } finally { box.close(); box.remove(); }
  }
  async function motionFrame(a, text, at) {
    const model = motionModel(text); await motionReady(model);
    const canvas = document.createElement('canvas'), f = 1920 / Math.max(model.w, model.h);
    canvas.width = Math.round(model.w * f); canvas.height = Math.round(model.h * f);
    paintMotion(canvas.getContext('2d'), model, clamp(at ?? model.total, 0, model.total), f);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('png');
    return blob;
  }
  async function motionScenesZip(a, text) {
    const model = motionModel(text); await motionReady(model);
    const canvas = document.createElement('canvas'), f = 1280 / Math.max(model.w, model.h);
    canvas.width = Math.round(model.w * f); canvas.height = Math.round(model.h * f);
    const g = canvas.getContext('2d'), files = [];
    for (const [i, scene] of model.scenes.entries()) {
      paintMotion(g, model, scene.start + scene.duration - 0.001, f);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      files.push({name: 'scene-' + String(i + 1).padStart(2, '0') + '.png', data: new Uint8Array(await blob.arrayBuffer())});
    }
    return makeZip(files);
  }

  /* ---- the panel beside the chat ---- */
  const streaming = () => stream.is('writing', 'finishing');
  // Open WebUI lays its chat out itself; room is made by padding the chat's container, so everything inside it shrinks
  const narrow = () => innerWidth < 900;
  let panelWidth = (() => { try { return Number(localStorage.getItem(PREFIX + 'width')) || 0; } catch { return 0; } })();
  let host = null, opener = null;
  const hostOf = () => document.getElementById('chat-container') || document.querySelector('[id^="message-"]')?.closest('.h-screen, [class*="h-screen"], [class*="h-full"].w-full') || null;
  function setWidth(w) {
    panelWidth = clamp(Math.round(w || Math.min(680, innerWidth * 0.46)), 360, Math.max(360, Math.round(innerWidth * 0.75)));
    document.documentElement.style.setProperty('--ar-w', panelWidth + 'px');
    place();
  }
  function place() {
    if (!panel || view.is('closed')) return;
    const next = hostOf();
    if (host && host !== next) host.classList.remove('ar-docked');
    host = next;
    const overlay = narrow() || size.is('full') || !host;
    panel.classList.toggle('ar-full', size.is('full'));
    panel.classList.toggle('ar-overlay', overlay);
    host?.classList.toggle('ar-docked', !overlay);
    if (!overlay) { const r = host.getBoundingClientRect(); panel.style.top = Math.max(0, r.top) + 'px'; panel.style.height = Math.min(innerHeight, r.height || innerHeight) + 'px'; }
    else { panel.style.top = ''; panel.style.height = ''; }
  }
  addEventListener('resize', () => { if (!view.is('closed')) setWidth(panelWidth); });
  function openPanel(from) {
    if (!panel) build();
    opener = from || (document.activeElement instanceof HTMLElement && !panel.contains(document.activeElement) ? document.activeElement : null);
    const wasHidden = panel.hidden;
    if (view.is('closed')) view.send('list');
    panel.hidden = false; panel.lang = lang = language();
    setWidth(panelWidth);
    syncHeaderButton();
    if (wasHidden && (state.loadedFor !== route().kind + ':' + route().id || Date.now() - state.loadedAt > 1500)) load(state.loadedFor === route().kind + ':' + route().id);
    else render();
    requestAnimationFrame(() => headEl.querySelector('button')?.focus({preventScroll: true}));
  }
  function closePanel() {
    if (!panel || view.is('closed')) return;
    if (document.fullscreenElement && panel.contains(document.fullscreenElement)) document.exitFullscreen?.().catch(() => {});
    view.send('close'); size.send('reset');
    panel.hidden = true; panel.classList.remove('ar-full');
    dropFrames(panel); bodyEl.replaceChildren(); askButton?.remove();
    host?.classList.remove('ar-docked');
    updateUrl(null);
    syncHeaderButton();
    if (opener?.isConnected) opener.focus({preventScroll: true});
  }
  function toggleFull() { size.send('toggle'); place(); render(); }
  // ?artifact=<key>&v=<n> opens an artifact, also from a shared link
  function updateUrl(key) {
    try {
      const url = new URL(location.href);
      if (key) url.searchParams.set('artifact', key); else { url.searchParams.delete('artifact'); url.searchParams.delete('v'); }
      if (url.href !== location.href) history.replaceState(history.state, '', url.href);
    } catch {}
  }
  function deepLink() {
    let params; try { params = new URLSearchParams(location.search); } catch { return; }
    const key = params.get('artifact');
    if (!key) return;
    const a = state.artifacts.find(x => x.key === key);
    if (!a) return;
    const v = parseInt(params.get('v') || '', 10);
    openArtifact(key, Number.isFinite(v) ? v - 1 : undefined);
  }

  /* ---- ways in ---- */
  const MENU_ITEM = 'owui-chat-artifacts-menu-item', HEADER = 'owui-chat-artifacts-header';
  let menuItem = null, headerButton = null;
  const visible = node => !!node && node.getClientRects().length > 0;
  function syncMenu() {
    const trigger = CONFIG.chatMenu ? [...document.querySelectorAll('#chat-context-menu-button[aria-expanded="true"]')].find(visible) : null;
    if (!trigger || route().kind !== 'chat') { menuItem?.remove(); return; }
    const menus = [...document.querySelectorAll('body > [role="menu"] .app-dropdown-menu')];
    const target = menus.length === 1 ? menus[0] : menus.find(m => m.querySelector(':scope > button[aria-haspopup="dialog"]'));
    if (!target) return;
    if (!menuItem) {
      menuItem = el('button', 'flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] cursor-pointer select-none hover:bg-gray-50/40 dark:hover:bg-gray-800/40');
      menuItem.type = 'button'; menuItem.id = MENU_ITEM;
      const icon = svgIcon('artifacts'); icon.setAttribute('class', 'size-4');
      menuItem.append(icon, el('div', 'flex items-center', t('artifacts')));
      menuItem.addEventListener('click', () => { view.send('list'); openPanel(trigger); render(); });
    }
    if (menuItem.parentElement !== target) {
      const after = target.querySelector(':scope > #owui-chat-inspector-menu-item') || target.querySelector(':scope > button[aria-haspopup="dialog"]:not(#' + MENU_ITEM + ')');
      after ? after.after(menuItem) : target.prepend(menuItem);
    }
  }
  function syncHeaderButton() {
    const r = route();
    const trigger = [...document.querySelectorAll('#chat-context-menu-button')].find(visible);
    const want = CONFIG.headerButton && (r.kind === 'chat' || (r.kind === 'shared' && state.artifacts.length));
    if (!want) { headerButton?.remove(); return; }
    if (!headerButton) {
      headerButton = el('button'); headerButton.type = 'button'; headerButton.id = HEADER;
      setLabel(headerButton, t('open_artifacts')); headerButton.append(svgIcon('artifacts'));
      headerButton.addEventListener('click', () => { if (!view.is('closed')) closePanel(); else openPanel(headerButton); });
    }
    headerButton.setAttribute('aria-pressed', String(!view.is('closed')));
    headerButton.setAttribute('aria-controls', 'owui-chat-artifacts');
    const count = state.artifacts.length;
    headerButton.dataset.count = count > 0 ? String(Math.min(99, count)) : '';
    if (trigger) {
      // Beside the chat's ⋯ button, styled like it
      const wrap = trigger.parentElement?.children.length === 1 ? trigger.parentElement : trigger;
      headerButton.className = trigger.className + ' ar-headerbtn';
      headerButton.classList.remove('ar-floating');
      // Other plugins' buttons may sit there too; anywhere in that group, next to the ⋯ button, is right
      let node = wrap.previousElementSibling, placed = false;
      while (node && /^owui-chat-[a-z-]+-header$/.test(node.id)) { if (node === headerButton) { placed = true; break; } node = node.previousElementSibling; }
      if (!placed) wrap.before(headerButton);
    } else if (r.kind === 'shared') {
      headerButton.className = 'ar-floating';
      if (headerButton.parentElement !== document.body) document.body.append(headerButton);
    } else headerButton.remove();
  }
  // In the chat: an Open button on each code block that is an artifact, and a card under each answer that made one
  function messageIdOf(blockId) {
    const prefix = 'code-textarea-' + (state.route.id || '') + '-';
    if (!blockId.startsWith(prefix)) return null;
    const rest = blockId.slice(prefix.length);
    const ids = Object.keys(state.chat?.chat?.history?.messages || {}).filter(id => rest.startsWith(id + '-')).sort((a, b) => b.length - a.length);
    return ids[0] || null;
  }
  const firstLine = text => (text.split('\n').find(line => line.trim()) || '').trim();
  function versionsIn(messageId) {
    const out = [];
    for (const a of state.artifacts) a.versions.forEach((v, i) => { if (v.messageId === messageId) out.push({a, i, v}); });
    return out.sort((x, y) => x.v.index - y.v.index);
  }
  function syncChat() {
    const r = route();
    if ((r.kind !== 'chat' && r.kind !== 'shared') || state.loadedFor !== r.kind + ':' + r.id) return;
    syncHeaderButton();
    if (!CONFIG.cardsInChat) return;
    const seen = new Map();
    for (const block of document.querySelectorAll('#messages-container [id^="code-textarea-"], [id^="message-"] [id^="code-textarea-"]')) {
      const messageId = messageIdOf(block.id);
      if (!messageId) continue;
      const frame = block.closest('.relative.flex.flex-col') || block.parentElement?.parentElement;
      const toolbar = frame?.querySelector('.copy-code-button')?.parentElement;
      if (!toolbar) continue;
      const label = (frame.querySelector('.truncate span, span.truncate')?.textContent || '').trim();
      const lineNodes = block.querySelectorAll('.cm-line');
      const head = lineNodes.length ? [...lineNodes].map(x => x.textContent).find(x => x.trim())?.trim() || '' : '';
      const list = versionsIn(messageId).filter(x => x.v.source === 'block');
      const k = (seen.get(messageId + '|' + label) || 0);
      seen.set(messageId + '|' + label, k + 1);
      const sameInfo = list.filter(x => x.v.info.trim() === label);
      const match = sameInfo.find(x => head && firstLine(x.v.text) === head) || sameInfo[k] || null;
      let button = toolbar.querySelector('.ar-openbtn');
      if (!match) { button?.remove(); continue; }
      if (!button) {
        const copy = toolbar.querySelector('.copy-code-button');
        button = el('button', (copy?.className || '').replace('copy-code-button', '') + ' ar-openbtn'); button.type = 'button';
        button.append(svgIcon('artifacts'), el('span', null, t('open')));
        copy ? copy.before(button) : toolbar.append(button);
      }
      button.dataset.key = match.a.key; button.dataset.v = match.i;
      setLabel(button, t('open') + ' · ' + displayTitle(match.a));
      button.onclick = () => openArtifact(match.a.key, match.i, hasPreview(match.a) ? 'preview' : 'code');
      const native = toolbar.querySelector('.run-code-button');
      if (CONFIG.replaceBuiltin && native && (match.a.type === 'html' || match.a.type === 'svg') && !native.dataset.ar) {
        native.dataset.ar = '1';
        native.addEventListener('click', event => { if (!CONFIG.replaceBuiltin) return; event.preventDefault(); event.stopImmediatePropagation(); button.click(); }, true);
      }
      frame.dataset.arKey = match.a.key + '#' + match.i;
      if (CONFIG.collapseCode) frame.classList.add('ar-collapsed');
      frame.nextElementSibling?.classList.contains('ar-writing') && frame.nextElementSibling.remove();
    }
    for (const message of document.querySelectorAll('[id^="message-"]')) {
      const id = message.id.slice(8);
      // Once the answer is finished, its "being written" cards give way to the real ones
      if (!streaming()) message.querySelectorAll('.ar-writing').forEach(node => node.remove());
      const found = versionsIn(id);
      const content = message.querySelector('#response-content-container') || message.querySelector('.chat-assistant');
      let cards = message.querySelector(':scope .ar-cards');
      if (!found.length || !content) { cards?.remove(); continue; }
      const signature = found.map(x => x.a.key + ':' + x.i + ':' + displayTitle(x.a)).join('|') + '|' + lang + '|' + message.querySelectorAll('.ar-collapsed[data-ar-key]').length;
      if (cards?.dataset.sig === signature && cards.parentElement === content) continue;
      if (!cards) { cards = el('div', 'ar-cards'); cards.setAttribute('role', 'group'); setLabel(cards, t('in_message')); }
      cards.dataset.sig = signature;
      cards.replaceChildren(...found.map(({a, i}) => {
        const card = el('div', 'ar-card'); card.dataset.key = a.key;
        const main = el('button', 'ar-cardmain'); main.type = 'button';
        const icon = el('span', 'ar-itemicon'); icon.dataset.type = a.type; icon.append(svgIcon(TYPE_ICON[a.type]));
        const words = el('span', 'ar-itemtext');
        words.append(nameSpan('ar-itemtitle', displayTitle(a)), el('span', 'ar-itemmeta', t('t_' + a.type) + (a.versions.length > 1 ? ' · v' + (i + 1) : '')));
        main.append(icon, words, el('span', 'ar-cardopen', t('open')));
        main.addEventListener('click', () => openArtifact(a.key, i));
        card.append(main);
        // The folded code can be shown in the chat again
        const block = [...message.querySelectorAll('[data-ar-key]')].find(node => node.dataset.arKey === a.key + '#' + i);
        if (block?.classList.contains('ar-collapsed')) {
          const toggle = iconButton(t('show_code'), 'code', () => { const shown = block.classList.toggle('ar-shown'); setLabel(toggle, t(shown ? 'hide_code' : 'show_code')); toggle.setAttribute('aria-pressed', String(shown)); }, 'ar-icon ar-cardcode');
          toggle.setAttribute('aria-pressed', String(block.classList.contains('ar-shown')));
          card.append(toggle);
        }
        return card;
      }));
      if (cards.parentElement !== content) content.append(cards);
    }
  }

  /* ---- keeping up with the chat ---- */
  // Watches the page, not the server: once the newest answer has stopped changing, the chat is read once
  let lastPath = null, watchSig = '', quietTicks = 0, chatWatch = 0, settleTimer = 0;
  // Following the newest answer. 'settling': a chat was just opened and is being drawn, which isn't an answer being
  // written. 'writing': the last answer keeps changing. 'finishing': it has been still, and the chat is being read.
  const stream = machine('stream', 'idle', {
    '*': {navigate: 'settling'},
    settling: {settled: 'idle'},
    idle: {change: 'writing'},
    writing: {change: 'writing', quiet: 'finishing'},
    finishing: {change: 'writing', loaded: 'idle'}
  }, next => { quietTicks = 0; if (next === 'settling') { clearTimeout(settleTimer); settleTimer = setTimeout(() => stream.send('settled'), 2500); } });
  const seenVersions = new Set();
  let seenFor = null;
  // While an answer is being written, each code block that will be an artifact is folded away behind a small card
  // saying it is being written; the panel opens once it is finished
  function markWriting() {
    const messages = document.querySelectorAll('#messages-container [id^="message-"]');
    const last = messages[messages.length - 1];
    if (!last || last.classList.contains('user-message')) return;
    for (const block of last.querySelectorAll('[id^="code-textarea-"]')) {
      const frame = block.closest('.relative.flex.flex-col');
      if (!frame || frame.dataset.arKey) continue;
      const label = (frame.querySelector('.truncate span, span.truncate')?.textContent || '').trim();
      const text = [...block.querySelectorAll('.cm-line')].map(x => x.textContent).join('\n');
      const info = readInfo(label, text), probe = {text, info: label}, marked = isMarked(probe);
      const type = typeOf(info.lang, text, info, marked);
      if (!type || !wanted(type, probe, marked)) continue;
      if (CONFIG.collapseCode) frame.classList.add('ar-collapsed');
      let card = frame.nextElementSibling?.classList.contains('ar-writing') ? frame.nextElementSibling : null;
      if (!card) {
        card = el('div', 'ar-cards ar-writing'); card.setAttribute('role', 'status');
        const row = el('div', 'ar-card ar-card-writing');
        const icon = el('span', 'ar-itemicon'); icon.dataset.type = type; icon.append(svgIcon(TYPE_ICON[type]));
        const words = el('span', 'ar-itemtext'); words.append(nameSpan('ar-itemtitle', ''), el('span', 'ar-itemmeta', ''));
        row.append(icon, words, el('span', 'ar-spinner'));
        card.append(row); frame.after(card);
      }
      const title = markerOf(text) || info.title || titleOf(type, info.lang, text, info).title || t('t_' + type);
      card.querySelector('.ar-itemtitle').textContent = title;
      card.querySelector('.ar-itemmeta').textContent = t('writing') + ' · ' + t('lines', {n: n(countLines(text))});
    }
  }
  // When an answer is done: open what it made, as Claude does
  function openMade() {
    const fresh = [];
    for (const a of state.artifacts) a.versions.forEach((v, i) => { const id = a.key + '#' + i; if (!seenVersions.has(id)) { seenVersions.add(id); if (v.messageId && !v.unfinished) fresh.push({a, i}); } });
    const made = fresh.pop();
    if (made && CONFIG.autoOpen && !narrow()) openArtifact(made.a.key, made.i, hasPreview(made.a) ? 'preview' : 'code');
  }
  const rememberAll = () => { seenVersions.clear(); for (const a of state.artifacts) a.versions.forEach((_, i) => seenVersions.add(a.key + '#' + i)); seenFor = state.loadedFor; };
  setInterval(() => {
    const r = route();
    const path = location.pathname;
    if (path !== lastPath) {
      const moved = lastPath !== null;
      lastPath = path; watchSig = ''; stream.send('navigate');
      if (moved) { state.errors.clear(); if (!view.is('closed') && r.kind !== 'chat' && r.kind !== 'shared') closePanel(); else if (view.is('viewer')) { view.send('list'); render(); } }
      if (r.kind === 'chat' || r.kind === 'shared') load(true).then(() => { rememberAll(); deepLink(); }); else { state.artifacts = []; state.loadedFor = null; syncHeaderButton(); }
      return;
    }
    syncMenu();
    if (r.kind !== 'chat' && r.kind !== 'shared') { if (headerButton?.isConnected) headerButton.remove(); return; }
    if (r.kind === 'chat') syncHeaderButton();
    if (state.loadedFor === r.kind + ':' + r.id && ++chatWatch % 2 === 0) syncChat();
    if (CONFIG.replaceBuiltin) document.querySelector('[data-panel-tab="artifacts"] button[aria-label^="Close"], [data-panel-tab="artifacts"] button[title^="Close"]')?.click();
    if (!view.is('closed')) place();
    if (document.hidden || isLoading()) return;
    const messages = document.querySelectorAll('#messages-container [id^="message-"]');
    const last = messages[messages.length - 1];
    // Our own cards and buttons don't count as the answer changing
    const ours = last ? [...last.querySelectorAll('.ar-cards, .ar-openbtn')].reduce((sum, node) => sum + node.textContent.length, 0) : 0;
    const sig = messages.length + ':' + (last ? last.id + ':' + (last.textContent.length - ours) : '');
    if (sig !== watchSig) {
      const first = !watchSig; watchSig = sig; quietTicks = 0;
      // An answer the chat already has, finished, isn't being written: it is only being drawn
      const known = last && state.chat?.chat?.history?.messages?.[last.id.slice(8)];
      const finished = known && known.done !== false && state.loadedFor === r.kind + ':' + r.id;
      if (!first && !finished && r.kind === 'chat' && stream.send('change')) markWriting();
      return;
    }
    if (stream.is('writing') && ++quietTicks >= 2 && stream.send('quiet')) {
      load(true).then(() => { stream.send('loaded'); if (seenFor !== state.loadedFor) rememberAll(); else openMade(); syncChat(); });
    }
  }, 700);
  /* ---- start ---- */
  async function start() {
    if (!document.getElementById('owui-chat-artifacts-css')) { const style = el('style'); style.id = 'owui-chat-artifacts-css'; style.textContent = CSS; document.head.append(style); }
    try { const config = await api('/api/config'); adminPolicy = String(config?.ui?.iframe_csp || '').slice(0, 4000); if (!String(CONFIG.platformName || '').trim()) { const name = String(config?.name || '').replace(/\s*\(Open WebUI\)\s*$/, '').trim(); if (name) appName = name.slice(0, 60); } } catch {}
    document.documentElement.dataset.chatArtifacts = '1';
    if (CONFIG.replaceBuiltin) document.documentElement.classList.add('ar-replace');
  }
  // Escape closes the panel when nothing of Open WebUI's is open and nobody is typing
  addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || !panel || view.is('closed') || document.fullscreenElement) return;
    if (panel.contains(event.target)) return;
    if (document.querySelector('body > [role="menu"], dialog[open], .modal') || event.target.closest?.('textarea, input, [contenteditable="true"]')) return;
    event.preventDefault(); closePanel();
  });
  // The button and the menu entry follow the page as it changes, once per frame at most
  let framePending = false;
  new MutationObserver(() => {
    if (framePending) return;
    framePending = true;
    requestAnimationFrame(() => { framePending = false; const kind = route().kind; if (kind === 'chat' || kind === 'shared') syncHeaderButton(); syncMenu(); });
  }).observe(document.documentElement, {childList: true, subtree: true});
  // Switching Open WebUI's language switches this one too, without a reload
  new MutationObserver(() => {
    const next = language();
    if (next === lang) return;
    lang = next;
    if (menuItem) { menuItem.remove(); menuItem = null; }
    if (headerButton) setLabel(headerButton, t('open_artifacts'));
    if (panel) { panel.lang = lang; panel.setAttribute('aria-label', t('artifacts')); render(); }
    syncChat();
  }).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  document.addEventListener('fullscreenchange', () => { if (!view.is('closed') && !document.fullscreenElement) render(); });
  start();
})();
"""


def _url(value: str) -> str:
    # Only a plain http(s) address can reach the browser's content policy and frames
    value = str(value or "").strip()
    return value if _re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[^\s\"'<>`]*)?", value) and len(value) <= 300 else ""


class Event:
    class Valves(BaseModel):
        detect: Literal["rich", "marked", "all"] = Field(
            default="rich",
            description="Which code blocks become artifacts. 'rich': blocks the model marked as artifacts (as Artifacts mode asks it to), and whole web pages, React apps, drawings, diagrams, slides and CSV data. 'marked': only marked blocks. 'all': also long answers, Markdown tables, JSON and any long code. Greetings and ordinary answers never make artifacts in 'rich' or 'marked'.",
        )
        auto_open: bool = Field(
            default=True,
            description="Open the Artifacts panel when an answer finishes making a new artifact or version. Never on a phone. The panel never shows an artifact before it is complete.",
        )
        cards_in_chat: bool = Field(
            default=True,
            description="Show a card under each answer that made an artifact, and an Open button on each code block that is one.",
        )
        collapse_code: bool = Field(
            default=True,
            description="Fold an artifact's code away in the chat, leaving a card to open it (and to show the code), as Claude does. While it is being written, the card says so.",
        )
        min_code_lines: int = Field(
            default=12,
            ge=1,
            le=500,
            description="With detect = 'all': the shortest code block, in lines, that becomes a code artifact.",
        )
        min_document_chars: int = Field(
            default=1500,
            ge=0,
            le=100000,
            description="With detect = 'all': an answer with at least this much text, and headings or several paragraphs, is also offered as a document. 0 turns this off.",
        )
        tables_as_sheets: bool = Field(
            default=True,
            description="With detect = 'all': Markdown tables of three rows or more in answers become spreadsheets.",
        )
        network: Literal["libraries", "open", "off"] = Field(
            default="libraries",
            description="What artifacts may load. 'libraries': only the addresses below (React, Babel, Tailwind, Mermaid and extra_hosts), so a generated page can't send anything elsewhere. 'open': anything on the web. 'off': nothing; React apps and diagrams then can't run.",
        )
        esm_cdn: str = Field(
            default="https://esm.sh",
            description="Where React apps load React and the npm packages they import. esm.sh, or your own copy of it.",
        )
        babel_url: str = Field(
            default="https://cdn.jsdelivr.net/npm/@babel/standalone@7.29.10/babel.min.js",
            description="Babel standalone, which turns an app's JSX and TypeScript into JavaScript in the browser.",
        )
        tailwind_url: str = Field(
            default="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4.3.3/dist/index.global.js",
            description="Tailwind for React apps. Empty leaves it out.",
        )
        mermaid_url: str = Field(
            default="https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js",
            description="Mermaid, which draws diagrams.",
        )
        extra_hosts: str = Field(
            default="https://cdn.jsdelivr.net, https://cdnjs.cloudflare.com, https://unpkg.com, https://fonts.googleapis.com, https://fonts.gstatic.com",
            description="Other addresses web pages and apps may load scripts, styles, fonts and images from, comma separated, when network is 'libraries'.",
        )
        allow_ai: bool = Field(
            default=True,
            description="Let artifacts ask the chat's model questions through window.claude.complete(), as Claude's AI-powered artifacts do. Each artifact asks the person first, and only gets the answers.",
        )
        ai_calls_per_minute: int = Field(default=10, ge=1, le=120, description="The most AI requests all artifacts in a browser tab may make in a minute.")
        allow_storage: bool = Field(
            default=True,
            description="Let artifacts keep data with window.storage, in the person's browser, up to 512 KB each.",
        )
        keep_drafts: bool = Field(
            default=True,
            description="Keep each person's edits and blank artifacts in their browser. Turn off on shared computers: edits then last until the page is closed.",
        )
        allow_save: bool = Field(default=True, description="Offer Save to my files and Save as a note.")
        replace_builtin: bool = Field(
            default=True,
            description="Stand in for Open WebUI's own Artifacts, which only shows HTML and SVG: its ⋯ menu entry is hidden, its Preview button opens this panel, and its tab is closed when it opens by itself. Off keeps both.",
        )
        templates: bool = Field(default=True, description="Offer New, with ready requests for documents, slides, spreadsheets, diagrams, pages, apps, designs, drawings, data and code.")
        header_button: bool = Field(default=True, description="Add an Artifacts button beside the chat's ⋯ button.")
        show_in_chat_menu: bool = Field(default=True, description="Add Artifacts to the chat's ⋯ menu.")
        platform_name: str = Field(default="", description="The platform's name in the wording, such as 'EveGPT'. Empty uses WEBUI_NAME.")
        language: Literal["auto", "en", "fr"] = Field(default="auto", description="'auto' follows each person's Open WebUI language, and uses English where there is no translation.")

    LOADER_BLOCK_START = "// owui-chat-artifacts:start"
    LOADER_BLOCK_END = "// owui-chat-artifacts:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment_key = None
        self._fragment = ""
        reload_bootstrap()

    def _config(self) -> dict:
        v = self.valves
        hosts = [h for h in (_url(x) for x in str(v.extra_hosts).split(",")) if h][:30]
        return {
            "detect": v.detect,
            "autoOpen": bool(v.auto_open),
            "cardsInChat": bool(v.cards_in_chat),
            "collapseCode": bool(v.collapse_code),
            "minCodeLines": int(v.min_code_lines),
            "minDocumentChars": int(v.min_document_chars),
            "tablesAsSheets": bool(v.tables_as_sheets),
            "network": v.network,
            "esmCdn": _url(v.esm_cdn).rstrip("/"),
            "babelUrl": _url(v.babel_url),
            "tailwindUrl": _url(v.tailwind_url),
            "mermaidUrl": _url(v.mermaid_url),
            "extraHosts": ", ".join(hosts),
            "allowAi": bool(v.allow_ai),
            "aiPerMinute": int(v.ai_calls_per_minute),
            "allowStorage": bool(v.allow_storage),
            "keepDrafts": bool(v.keep_drafts),
            "allowSave": bool(v.allow_save),
            "templates": bool(v.templates),
            "replaceBuiltin": bool(v.replace_builtin),
            "headerButton": bool(v.header_button),
            "chatMenu": bool(v.show_in_chat_menu),
            "platformName": str(v.platform_name).strip()[:60],
            "language": v.language,
        }

    def _loader_fragment(self) -> str:
        # Runs for every page load: rebuilt only when a valve changed
        key = _json.dumps(self._config(), sort_keys=True)
        if key != self._fragment_key:
            # JSON inside a script: "</" is escaped so no value can end the script early
            config = key.replace("</", "<\\/")
            script = LOADER_SCRIPT.replace("__CSS__", _json.dumps(ARTIFACTS_CSS).replace("</", "<\\/")).replace("__CONFIG__", config)
            self._fragment = self.LOADER_BLOCK_START + "\n" + script + "\n" + self.LOADER_BLOCK_END
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
