"""
title: Chat Files
description: A Files tab, chosen from the Tabs panel's + menu, for every document, spreadsheet and other file in a chat and in your account, as a list or a grid, with previews, details, search, filters, rename, delete, zip and a jump back to the message.
author: Muhammad Sohail
version: 2.0.0
license: MIT
required_open_webui_version: 0.13.0
"""

# A companion to Chat Gallery, built the same way. The two protocol blocks
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


FILES_CSS = r"""
#owui-chat-files-button svg,#owui-chat-files-header svg{pointer-events:none}
#owui-chat-files-header svg{width:1.125rem;height:1.125rem}
#owui-chat-files-header.w-7 svg{width:.875rem;height:.875rem}
#owui-chat-files-header[aria-pressed=true]{color:var(--color-gray-700,#4e4e4e)}
.dark #owui-chat-files-header[aria-pressed=true]{color:var(--color-gray-200,#e3e3e3)}
.cf-dialog{--cf-surface:#fff;--cf-text:var(--color-gray-800,#262626);--cf-muted:var(--color-gray-600,#5d5d5d);--cf-soft:var(--color-gray-50,#f9f9f9);--cf-hover:var(--color-gray-100,#ececec);--cf-line:var(--color-gray-100,#ececec);--cf-strong-line:var(--color-gray-200,#e3e3e3);--cf-picked:var(--color-gray-100,#ececec);--cf-primary:var(--color-gray-900,#171717);--cf-on-primary:#fff;--cf-modal:#fff;--cf-danger:#dc2626;--cf-danger-text:#b91c1c;--cf-warn:#92400e;--cf-shadow:0 10px 30px -10px rgb(0 0 0/.25);
--cf-k-pdf:#b91c1c;--cf-k-doc:#1d4ed8;--cf-k-sheet:#166534;--cf-k-slide:#9a3412;--cf-k-code:#6d28d9;--cf-k-audio:#9d174d;--cf-k-video:#115e59;--cf-k-image:#854d0e;--cf-k-archive:#92400e;--cf-k-ref:#075985;--cf-k-other:#4b5563}
.dark .cf-dialog{--cf-surface:var(--color-gray-900,#171717);--cf-text:var(--color-gray-100,#ececec);--cf-muted:var(--color-gray-400,#b4b4b4);--cf-soft:var(--color-gray-850,#262626);--cf-hover:var(--color-gray-800,#333);--cf-line:var(--color-gray-800,#333);--cf-strong-line:var(--color-gray-700,#4e4e4e);--cf-picked:var(--color-gray-800,#333);--cf-primary:var(--color-gray-100,#ececec);--cf-on-primary:var(--color-gray-900,#171717);--cf-modal:var(--color-gray-950,#0d0d0d);--cf-danger:#dc2626;--cf-danger-text:#f87171;--cf-warn:#fbbf24;--cf-shadow:0 10px 30px -10px rgb(0 0 0/.6);color-scheme:dark;
--cf-k-pdf:#f87171;--cf-k-doc:#60a5fa;--cf-k-sheet:#4ade80;--cf-k-slide:#fb923c;--cf-k-code:#a78bfa;--cf-k-audio:#f472b6;--cf-k-video:#2dd4bf;--cf-k-image:#facc15;--cf-k-archive:#fbbf24;--cf-k-ref:#38bdf8;--cf-k-other:#9ca3af}
.cf-dialog{position:fixed;left:0;top:0;z-index:45;box-sizing:border-box;display:flex;flex-direction:column;container:cf/inline-size;margin:0;padding:0;border:0;background:transparent;color:var(--cf-text);overflow:hidden;overscroll-behavior:contain;font-family:inherit;font-size:.875rem;line-height:1.4}
.cf-dialog[hidden]{display:none}
.cf-dialog.cf-solo{background:var(--cf-surface)}
[data-cf-covered]{opacity:0!important;pointer-events:none!important}
.cf-viewing>.cf-head,.cf-viewing>.cf-tools,.cf-viewing>.cf-kinds{display:none}
.cf-viewing .cf-scroll,.cf-viewing .cf-foot{visibility:hidden}
.panel-tab-strip.cf-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){background:transparent!important;color:var(--color-gray-500,#6b7280)!important}
.dark .panel-tab-strip.cf-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){color:var(--color-gray-400,#9ca3af)!important}
#owui-chat-files-tabs{scrollbar-width:none}
.cf-dialog *{box-sizing:border-box}
.cf-dialog button,.cf-dialog select,.cf-dialog input{font:inherit;color:inherit}
.cf-dialog :focus-visible{outline:2px solid var(--cf-text)!important;outline-offset:2px!important}
.cf-dialog :is(.cf-search,.cf-main,.cf-stage,.cf-menuitem):focus-visible{outline:none!important}
.cf-head{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;padding:1rem 1.25rem .5rem}
.cf-tools{display:flex;align-items:center;gap:.375rem;padding:0 1.25rem .5rem}
.cf-kinds{display:flex;gap:.375rem;padding:0 1.25rem .625rem;overflow-x:auto;scrollbar-width:none}
.cf-kinds[hidden]{display:none}
.cf-tabs,.cf-seg{display:flex;gap:2px;padding:2px;border-radius:.75rem;background:var(--cf-soft)}
.cf-tab{border:0;background:transparent;padding:.25rem .75rem;border-radius:.625rem;cursor:pointer;color:var(--cf-muted);white-space:nowrap;transition:background .12s,color .12s}
.cf-tab:hover{color:var(--cf-text)}
.cf-tab[aria-selected=true],.cf-segbtn[aria-pressed=true]{background:var(--cf-surface);color:var(--cf-text);box-shadow:0 1px 2px rgb(0 0 0/.08)}
.dark .cf-tab[aria-selected=true],.dark .cf-segbtn[aria-pressed=true]{background:var(--cf-hover)}
.cf-tab[hidden]{display:none}
.cf-segbtn{width:1.75rem;height:1.75rem;border-radius:.625rem}
.cf-segbtn svg{width:1rem!important;height:1rem!important}
.cf-spacer{flex:1}
.cf-searchbox{position:relative;display:flex;align-items:center;flex:1;min-width:8rem;max-width:28rem;margin:0}
.cf-searchbox svg{position:absolute;inset-inline-start:.625rem;width:1rem;height:1rem;color:var(--cf-muted);pointer-events:none}
.cf-search{width:100%;height:2rem;padding:0 .75rem 0 2rem;border:1px solid transparent;border-radius:.75rem;background:var(--cf-soft);color:inherit;font-size:.875rem;outline:none;transition:border-color .12s}
[dir=rtl] .cf-search{padding:0 2rem 0 .75rem}
.cf-search:focus{border-color:var(--cf-strong-line)}
.cf-search::placeholder{color:var(--cf-muted);opacity:1}
.cf-select{appearance:auto;height:2rem;max-width:11rem;padding:0 .5rem;border:0;border-radius:.75rem;background:var(--cf-soft);cursor:pointer;margin-inline-start:auto}
.dark .cf-select,.dark .cf-search{border-color:var(--cf-line)}
.cf-select option{background:var(--cf-surface);color:var(--cf-text)}
.cf-chip{display:inline-flex;align-items:center;gap:.375rem;flex:none;height:1.75rem;padding:0 .625rem;border:1px solid var(--cf-line);border-radius:999px;background:transparent;color:var(--cf-muted);font-size:.8125rem;cursor:pointer;white-space:nowrap;transition:background .12s,color .12s,border-color .12s}
.cf-chip:hover{background:var(--cf-soft);color:var(--cf-text)}
.cf-chip[aria-pressed=true]{background:var(--cf-primary);border-color:var(--cf-primary);color:var(--cf-on-primary)}
.cf-chipcount{font-variant-numeric:tabular-nums;opacity:.8}
.cf-btn{display:inline-flex;align-items:center;justify-content:center;gap:.375rem;height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--cf-soft);cursor:pointer;white-space:nowrap;transition:background .12s}
.cf-btn:hover{background:var(--cf-hover)}
.cf-btn:disabled{opacity:.5;cursor:default}
.cf-btn[aria-pressed=true],.cf-btn.cf-primary{background:var(--cf-primary);color:var(--cf-on-primary)}
.cf-btn.cf-primary:hover,.cf-btn[aria-pressed=true]:hover{filter:brightness(.9)}
.dark .cf-btn.cf-primary:hover,.dark .cf-btn[aria-pressed=true]:hover{filter:brightness(1.1)}
.cf-btn.cf-danger{background:var(--cf-danger);color:#fff}
.cf-btn.cf-danger:hover{filter:brightness(1.1)}
.cf-btn[hidden],.cf-icon[hidden]{display:none}
.cf-icon{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;border:0;border-radius:.75rem;background:transparent;cursor:pointer;color:var(--cf-muted);transition:background .12s,color .12s}
.cf-icon:hover{background:var(--cf-hover);color:var(--cf-text)}
.cf-icon:disabled{opacity:.4;cursor:default}
.cf-icon svg,.cf-btn svg{width:1.125rem;height:1.125rem}
.cf-mini{width:1.5rem;height:1.5rem;border-radius:.5rem}
.cf-mini svg{width:.875rem;height:.875rem}
.cf-modal{position:fixed;inset:0;box-sizing:border-box;width:min(28rem,calc(100vw - 2rem));height:fit-content;max-height:calc(100vh - 2rem);margin:auto;padding:1.5rem;border:1px solid var(--cf-line);border-radius:1.5rem;background:var(--cf-modal);color:var(--cf-text);box-shadow:var(--cf-shadow);font-size:.875rem;line-height:1.45;overflow:auto}
.cf-modal::backdrop{background:rgb(0 0 0/.6)}
.cf-modal h3{margin:0 0 .75rem;font-size:1.0625rem;font-weight:600}
.cf-modal p{margin:0 0 1rem;color:var(--cf-muted)}
.cf-modal .cf-bad{color:var(--cf-danger-text)}
.cf-names{margin:0 0 .75rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--cf-soft);list-style:none;font-size:.8125rem}
.cf-names li{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cf-namesmore{color:var(--cf-muted)}
.cf-label{display:flex;flex-direction:column;gap:.375rem;margin:0 0 .5rem;color:var(--cf-muted)}
.cf-input{height:2.25rem;padding:0 .75rem;border:1px solid var(--cf-line);border-radius:.75rem;background:var(--cf-soft);color:var(--cf-text);outline:none}
.cf-input:focus{border-color:var(--cf-strong-line)}
.cf-actions{display:flex;justify-content:flex-end;gap:.5rem}
.cf-body{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
.cf-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:0 1.25rem 1rem}
.cf-colhead{display:grid;grid-template-columns:minmax(0,1fr) 8.5rem 5.5rem 8rem 2rem;align-items:center;gap:.75rem;padding:.25rem .5rem;border-bottom:1px solid var(--cf-line);color:var(--cf-muted);font-size:.75rem}
.cf-colhead .cf-colname{padding-inline-start:3.5rem}
.cf-colhead:has(~.cf-selecting) .cf-colname{padding-inline-start:5.5rem}
.cf-colhead[hidden]{display:none}
.cf-colbtn{display:inline-flex;align-items:center;gap:.25rem;min-width:0;height:1.75rem;padding:0 .25rem;border:0;border-radius:.5rem;background:transparent;color:inherit;text-align:start;cursor:pointer;white-space:nowrap}
span.cf-colbtn{cursor:default}
button.cf-colbtn:hover{color:var(--cf-text)}
.cf-colbtn[aria-pressed=true]{color:var(--cf-text);font-weight:600}
.cf-colsize{justify-content:flex-end}
.cf-list{display:flex;flex-direction:column;gap:1px;margin:0;padding:.25rem 0 0;list-style:none}
.cf-list li{margin:0;min-width:0}
.cf-item{content-visibility:auto;contain-intrinsic-size:auto 3.25rem}
.cf-group{padding:.875rem .5rem .375rem;color:var(--cf-muted);font-size:.75rem;font-weight:600;letter-spacing:.01em}
.cf-group:first-child{padding-top:.25rem}
.cf-row{position:relative;display:grid;grid-template-columns:minmax(0,1fr) 8.5rem 5.5rem 8rem 2rem;align-items:center;gap:.75rem;min-height:3.25rem;padding:0 .5rem;border-radius:.75rem;transition:background .12s}
.cf-row:hover{background:var(--cf-soft)}
.cf-row.cf-picked{background:var(--cf-picked)}
.cf-row:has(.cf-main:focus-visible){outline:2px solid var(--cf-text);outline-offset:-2px}
.cf-main{display:flex;align-items:center;gap:.75rem;min-width:0;padding:.5rem 0;border:0;background:transparent;text-align:start;cursor:pointer;color:inherit}
.cf-main::after{content:'';position:absolute;inset:0;border-radius:inherit}
.cf-row.cf-gone .cf-name{text-decoration:line-through;color:var(--cf-muted)}
.cf-thumb{position:relative;flex:none;display:flex;align-items:center;justify-content:center;width:2.5rem;height:2.25rem;border-radius:.5rem;overflow:hidden;background:color-mix(in srgb,var(--cf-tone) 13%,transparent)}
.cf-thumb[data-tone=pdf]{--cf-tone:var(--cf-k-pdf)}.cf-thumb[data-tone=doc]{--cf-tone:var(--cf-k-doc)}.cf-thumb[data-tone=sheet]{--cf-tone:var(--cf-k-sheet)}.cf-thumb[data-tone=slide]{--cf-tone:var(--cf-k-slide)}
.cf-thumb[data-tone=code]{--cf-tone:var(--cf-k-code)}.cf-thumb[data-tone=audio]{--cf-tone:var(--cf-k-audio)}.cf-thumb[data-tone=video]{--cf-tone:var(--cf-k-video)}.cf-thumb[data-tone=image]{--cf-tone:var(--cf-k-image)}
.cf-thumb[data-tone=archive]{--cf-tone:var(--cf-k-archive)}.cf-thumb[data-tone=ref]{--cf-tone:var(--cf-k-ref)}.cf-thumb[data-tone=other]{--cf-tone:var(--cf-k-other)}
.cf-badge{font-size:.625rem;font-weight:700;letter-spacing:.02em;color:var(--cf-tone)}
.cf-thumbimg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:var(--cf-soft)}
.cf-thumb.cf-hasimg .cf-badge{visibility:hidden}
.cf-text{display:flex;flex-direction:column;min-width:0;flex:1}
.cf-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;unicode-bidi:plaintext}
.cf-sub{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;color:var(--cf-muted);font-size:.75rem;font-variant-numeric:tabular-nums}
.cf-sub:empty{display:none}
.cf-cell{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--cf-muted);font-size:.8125rem;font-variant-numeric:tabular-nums;pointer-events:none}
.cf-sizecell{text-align:end}
.cf-list[data-view=list] .cf-subtext{display:none}
.cf-flag{flex:none;white-space:nowrap;padding:0 .375rem;border-radius:.375rem;font-size:.6875rem;font-weight:600;color:var(--cf-warn);background:color-mix(in srgb,var(--cf-warn) 14%,transparent)}
.cf-flag[data-state=failed],.cf-flag[data-state=missing]{color:var(--cf-danger-text);background:color-mix(in srgb,var(--cf-danger-text) 12%,transparent)}
.cf-more{position:relative;z-index:1;width:1.75rem;height:1.75rem;opacity:0}
.cf-row:hover .cf-more,.cf-row:focus-within .cf-more,.cf-more[aria-expanded=true]{opacity:1}
@media(hover:none){.cf-more{opacity:1}}
.cf-check{display:none;flex:none;align-items:center;justify-content:center;width:1.25rem;height:1.25rem;border:1.5px solid var(--cf-muted);border-radius:.375rem;background:var(--cf-surface)}
.cf-check svg{width:.875rem;height:.875rem;opacity:0;color:var(--cf-on-primary)}
.cf-selecting .cf-row:not(.cf-gone) .cf-check{display:flex}
.cf-row.cf-picked .cf-check{background:var(--cf-primary);border-color:var(--cf-primary)}
.cf-row.cf-picked .cf-check svg{opacity:1}
.cf-list[data-view=grid]{display:grid;grid-template-columns:repeat(auto-fill,minmax(10rem,1fr));gap:.75rem;padding-top:.5rem}
.cf-list[data-view=grid] .cf-item{contain-intrinsic-size:auto 13rem}
.cf-list[data-view=grid] .cf-group,.cf-list[data-view=grid] .cf-empty{grid-column:1/-1}
.cf-list[data-view=grid] .cf-row{display:block;min-height:0;padding:.5rem;border:1px solid var(--cf-line)}
.cf-list[data-view=grid] .cf-row:hover{border-color:var(--cf-strong-line)}
.cf-list[data-view=grid] .cf-main{flex-direction:column;align-items:stretch;gap:.5rem;width:100%;padding:0}
.cf-list[data-view=grid] .cf-thumb{width:100%;height:auto;aspect-ratio:16/10;border-radius:.5rem}
.cf-list[data-view=grid] .cf-sub{min-width:0}
.cf-list[data-view=grid] .cf-subtext{min-width:0;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cf-list[data-view=grid] .cf-badge{font-size:1rem}
.cf-list[data-view=grid] .cf-text{padding:0 .125rem .125rem}
.cf-list[data-view=grid] .cf-name{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;white-space:normal;overflow-wrap:anywhere;line-height:1.3;min-height:2.6em}
.cf-list[data-view=grid] .cf-cell{display:none}
.cf-list[data-view=grid] .cf-check{position:absolute;top:1rem;inset-inline-start:1rem;z-index:1}
.cf-list[data-view=grid] .cf-more{position:absolute;top:1rem;inset-inline-end:1rem;background:var(--cf-surface);box-shadow:0 1px 3px rgb(0 0 0/.15)}
.cf-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.25rem;min-height:12rem;padding:2rem;color:var(--cf-muted);text-align:center}
.cf-empty strong{color:var(--cf-text);font-weight:500}
.cf-empty .cf-btn{margin-top:.5rem;color:var(--cf-text)}
.cf-sentinel{height:1px}
.cf-foot{display:flex;align-items:center;gap:.5rem;min-height:2.5rem;padding:.25rem 1.25rem .75rem;color:var(--cf-muted);font-size:.8125rem;font-variant-numeric:tabular-nums;border-top:1px solid var(--cf-line)}
.cf-foot .cf-btn{height:1.625rem;padding:0 .5rem;font-size:.75rem;color:var(--cf-text)}
.cf-menu{position:absolute;z-index:6;min-width:12rem;max-width:18rem;padding:.25rem;border:1px solid var(--cf-line);border-radius:1rem;background:var(--cf-modal);color:var(--cf-text);box-shadow:var(--cf-shadow)}
.cf-menuitem{display:flex;align-items:center;gap:.625rem;width:100%;height:2rem;padding:0 .625rem;border:0;border-radius:.625rem;background:transparent;font-size:.8125rem;text-align:start;cursor:pointer;white-space:nowrap}
.cf-menuitem svg{flex:none;width:1rem;height:1rem;color:var(--cf-muted)}
.cf-menuitem:hover,.cf-menuitem:focus-visible{background:var(--cf-hover)}
.cf-menudanger,.cf-menudanger svg{color:var(--cf-danger-text)}
.cf-viewer{position:absolute;inset:0;z-index:2;display:none;grid-template-columns:minmax(0,1fr) 19rem;grid-template-rows:auto minmax(0,1fr)}
.cf-solo .cf-viewer{background:var(--cf-surface)}
.cf-viewer.cf-open{display:grid}
.cf-vbar{grid-column:1/-1;display:flex;align-items:center;gap:.125rem;min-width:0;padding:.75rem 1rem;border-bottom:1px solid var(--cf-line);overflow-x:auto;scrollbar-width:none}
.cf-count{margin:0 .25rem;color:var(--cf-muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.cf-stage{position:relative;min-width:0;min-height:0;overflow:auto;display:flex;flex-direction:column}
.cf-stage.cf-loading>*{opacity:.6}
.cf-pdf{flex:1;width:100%;min-height:20rem;border:0;background:var(--cf-soft)}
.cf-media{max-width:100%;max-height:100%;margin:auto;padding:1rem}
audio.cf-media{width:min(32rem,100%)}
img.cf-media{object-fit:contain}
.cf-textwrap{display:flex;flex-direction:column;min-height:0;flex:1;padding:1rem 1.25rem}
.cf-cut{margin:0 0 .5rem;color:var(--cf-muted);font-size:.75rem}
.cf-pre{flex:1;margin:0;padding:1rem;border-radius:.75rem;background:var(--cf-soft);white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8125rem;line-height:1.55;tab-size:4;unicode-bidi:plaintext}
.cf-pre.cf-code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;overflow:auto;overflow-wrap:normal}
.cf-tablebox{width:fit-content;max-width:100%;overflow:auto;max-height:100%;border:1px solid var(--cf-line);border-radius:.75rem}
.cf-table{border-collapse:collapse;font-size:.8125rem;font-variant-numeric:tabular-nums}
.cf-table th,.cf-table td{padding:.375rem .625rem;border-bottom:1px solid var(--cf-line);text-align:start;white-space:nowrap;max-width:24rem;overflow:hidden;text-overflow:ellipsis}
.cf-table th{position:sticky;top:0;background:var(--cf-soft);font-weight:600}
.cf-notice{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.375rem;flex:1;min-height:12rem;padding:2rem;color:var(--cf-muted);text-align:center;overflow-wrap:anywhere}
.cf-notice strong{color:var(--cf-text);font-weight:500}
.cf-notice .cf-btn{margin-top:.5rem;color:var(--cf-text)}
.cf-info{min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:1rem 1.25rem;border-inline-start:1px solid var(--cf-line);display:flex;flex-direction:column;gap:1rem}
.cf-title{margin:0;font-size:.9375rem;font-weight:600;overflow-wrap:anywhere}
.cf-facts{display:grid;grid-template-columns:auto 1fr;gap:.375rem .75rem;margin:0}
.cf-facts dt{color:var(--cf-muted)}
.cf-facts dd{margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.cf-iddd{display:flex;align-items:center;gap:.25rem;min-width:0}
.cf-id{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.75rem}
.cf-error{margin:0;padding:.5rem .75rem;border-radius:.5rem;color:var(--cf-danger-text);background:color-mix(in srgb,var(--cf-danger-text) 10%,transparent);font-size:.8125rem;overflow-wrap:anywhere}
.cf-toast{position:absolute;left:50%;bottom:1rem;translate:-50% 0;z-index:7;max-width:calc(100% - 2rem);padding:.5rem .875rem;border-radius:999px;background:var(--cf-primary);color:var(--cf-on-primary);font-size:.8125rem;pointer-events:none;opacity:0;transition:opacity .15s}
.cf-toast.cf-show{opacity:1}
.cf-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media(prefers-reduced-motion:reduce){.cf-toast,.cf-row,.cf-btn,.cf-icon,.cf-chip,.cf-tab{transition:none}}
@media(forced-colors:active){.cf-row,.cf-btn,.cf-tab,.cf-select,.cf-search,.cf-chip{border:1px solid CanvasText}.cf-row.cf-picked,.cf-chip[aria-pressed=true]{outline:2px solid Highlight}}
@container cf (max-width:760px){
.cf-head{padding:.75rem .75rem .5rem}
.cf-tools{padding:0 .75rem .5rem}
.cf-kinds{padding:0 .75rem .5rem}
.cf-scroll{padding:0 .5rem .75rem}
.cf-colhead{display:none}
.cf-row{grid-template-columns:minmax(0,1fr) 2rem;padding:0 .375rem}
.cf-list[data-view=list] .cf-cell{display:none}
.cf-list[data-view=list] .cf-subtext{display:inline}
.cf-list[data-view=grid]{grid-template-columns:repeat(auto-fill,minmax(8.5rem,1fr));gap:.5rem}
.cf-foot{padding:.25rem .75rem .75rem}
.cf-viewer{grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(10rem,1fr) auto}
.cf-vbar{padding:.5rem}
.cf-info{border-inline-start:0;border-top:1px solid var(--cf-line);padding:.75rem;max-height:16rem;gap:.75rem}
.cf-tabs{order:0}.cf-close{order:1;margin-inline-start:auto}.cf-head>:not(.cf-tabs,.cf-close,.cf-spacer){order:2}.cf-spacer{display:none}
.cf-textwrap{padding:.75rem}
}
"""

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiChatFiles) return;
  window.__owuiChatFiles = true;
  const CONFIG = __CONFIG__;
  const PAGE_SIZE = 200;
  const TEXT_LIMIT = 524288;
  const PDF_LIMIT = 104857600;
  const BLOB_DOWNLOAD_LIMIT = 209715200;
  const FILE_RE = /\/api\/v1\/files\/([0-9a-fA-F-]{36})\/content/g;
  const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

  /* ---- words, in the language Open WebUI is set to ---- */
  const STRINGS = {
    en: {
      files: 'Files', close_files: 'Close Files', files_to_show: 'Files to show', this_chat: 'This chat', my_files: 'My files',
      search: 'Search files', search_label: 'Search file names and types', select: 'Select', cancel: 'Cancel', select_all: 'Select all', select_none: 'Select none',
      download_all: 'Download all', download: 'Download', download_n: 'Download {n}', delete: 'Delete', delete_n: 'Delete {n}', list: 'Files', refresh: 'Refresh',
      kind: 'Kind of file', kind_all: 'All kinds', kind_documents: 'Documents', kind_sheets: 'Spreadsheets', kind_slides: 'Presentations', kind_code: 'Code and data',
      kind_audio: 'Audio', kind_video: 'Video', kind_images: 'Images', kind_archives: 'Archives', kind_other: 'Other', kind_references: 'Knowledge and links',
      sort: 'Order', sort_newest: 'Newest first', sort_oldest: 'Oldest first', sort_name: 'Name A to Z', sort_largest: 'Largest first',
      ref_collection: 'Knowledge base', ref_note: 'Note', ref_chat: 'Chat', ref_folder: 'Folder', ref_web: 'Web page', ref_filesystem: 'Terminal file',
      none_match: 'No matching files', none_match_hint: 'Try other words, another kind, or clear the search.',
      no_chat: 'No chat open', no_chat_hint: 'Open a chat to see its files, or look through all of your files.',
      chat_failed: 'Couldn’t show this chat’s files', chat_empty: 'No files in this chat yet', chat_empty_hint: 'Documents, spreadsheets and other files you attach appear here.',
      library_failed: 'Couldn’t load your files', library_failed_hint: 'Check your connection, then try again.', retry: 'Try again',
      library_empty: 'No files yet', library_empty_hint: 'Files you upload in any chat appear here.',
      err_access: 'You no longer have access to this chat.', err_unsaved: 'This chat isn’t saved yet.', err_generic: 'Couldn’t load this chat’s files. Try again in a moment.',
      collecting: 'Collecting files…', loading: 'Loading…', loading_more: '{n} files so far, loading more…', so_far: ' so far', of: '{shown} of {total}',
      count_one: '{n} file', count_other: '{n} files', refs_one: '{n} reference', refs_other: '{n} references',
      viewer: 'File preview', back: 'Back to files', show_in_chat: 'Show in chat', open_in_chat: 'Open in chat', open_tab: 'Open in a new tab', open_ref: 'Open',
      attach: 'Attach to message', copy_text: 'Copy text', text_copied: 'Text copied', rename: 'Rename', previous: 'Previous file', next: 'Next file',
      details: 'Details', f_name: 'Name', f_type: 'Type', f_size: 'Size', f_created: 'Uploaded', f_status: 'Status', f_owner: 'Owner', f_sent: 'Sent in this chat',
      owner_you: 'You', owner_other: 'Someone else', st_ready: 'Ready', st_processing: 'Processing', st_failed: 'Processing failed', st_missing: 'Deleted',
      missing: 'This file was deleted', missing_hint: 'It is still mentioned in the chat, but Open WebUI no longer has it.',
      empty_file: 'This file is empty', preview_loading: 'Loading preview…', preview_failed: 'Couldn’t load the preview',
      no_preview: 'No preview for this kind of file', no_preview_hint: 'Download it to open it on your device.',
      binary: 'This file isn’t text', too_large: 'This file is too large to preview here',
      extracted: 'Text Open WebUI extracted from this file', truncated: 'Showing the first {size}. Download the file to see all of it.',
      table_cut: 'Showing the first {rows} rows and {cols} columns.', not_pdf: 'This file is named as a PDF but isn’t one',
      ref_hint: 'This chat refers to it; it is not a file.', ref_unavailable: 'This item can’t be opened from here',
      rename_title: 'Rename file', rename_label: 'New name', save: 'Save', rename_empty: 'Enter a name.', rename_bad: 'A name can’t contain / or \\, or be longer than 255 characters.',
      renamed: 'Renamed', rename_failed: 'Couldn’t rename the file', rename_note: 'Chats that already show this file keep the old name.',
      confirm_one: 'Delete this file?', confirm_many: 'Delete {n} files?',
      confirm_text_one: 'It is removed from Open WebUI, from every chat that shows it, and from any knowledge base it was added to. This can’t be undone.',
      confirm_text_many: 'They are removed from Open WebUI, from every chat that shows them, and from any knowledge base they were added to. This can’t be undone.',
      deleted_one: 'File deleted', deleted_many: '{n} files deleted', deleted_some: '{n} deleted, {failed} couldn’t be deleted', delete_failed_one: 'Couldn’t delete the file', delete_failed_many: 'Couldn’t delete the files',
      download_failed: 'Couldn’t download the file', copy_failed: 'Couldn’t copy', open_chat_to_attach: 'Open a chat to attach files', attach_failed: 'Couldn’t attach the file',
      preparing: 'Preparing {i} of {n}…', zip_stopped: 'Stopped at {mb} MB. Select fewer files.', zip_none: 'None of these files could be downloaded', zip_cancelled: 'Download cancelled',
      saved_one: '{n} file saved', saved_other: '{n} files saved', skipped: ', {n} skipped',
      all: 'All', view: 'View', view_list: 'List', view_grid: 'Grid', col_name: 'Name', col_kind: 'Kind', col_size: 'Size', col_date: 'Date',
      kinds: 'Kinds of file', kind_count: '{label}, {n}', more_for: 'More for {name}', open: 'Open', copy_name: 'Copy name', name_copied: 'Name copied',
      selected_n: '{n} selected', g_today: 'Today', g_yesterday: 'Yesterday', g_week: 'Previous 7 days', g_month: 'Previous 30 days', g_undated: 'No date',
      f_kind: 'Kind', f_mime: 'Media type', f_ext: 'Extension', f_bytes: '{n} bytes', f_updated: 'Changed', f_from: 'Sent by', from_you: 'You', from_answer: 'An answer',
      f_lines: 'Lines', f_words: 'Words', f_chars: 'Characters', f_rows: 'Rows', f_cols: 'Columns', f_dims: 'Dimensions', f_duration: 'Duration', f_id: 'File ID',
      copy_id: 'Copy file ID', id_copied: 'File ID copied', chat_missing: 'That chat is no longer available',
      rename_dots: 'A name needs more than dots.', delete_more: 'and {n} more', zip_many: 'A zip holds at most {n} files. Select fewer.',
      keys: 'Arrow keys move, Enter opens, Space selects, Delete deletes, slash searches.'
    },
    fr: {
      files: 'Fichiers', close_files: 'Fermer les fichiers', files_to_show: 'Fichiers à afficher', this_chat: 'Cette conversation', my_files: 'Mes fichiers',
      search: 'Rechercher', search_label: 'Rechercher dans les noms et les types de fichier', select: 'Sélectionner', cancel: 'Annuler', select_all: 'Tout sélectionner', select_none: 'Tout désélectionner',
      download_all: 'Tout télécharger', download: 'Télécharger', download_n: 'Télécharger {n}', delete: 'Supprimer', delete_n: 'Supprimer {n}', list: 'Fichiers', refresh: 'Actualiser',
      kind: 'Type de fichier', kind_all: 'Tous les types', kind_documents: 'Documents', kind_sheets: 'Tableurs', kind_slides: 'Présentations', kind_code: 'Code et données',
      kind_audio: 'Audio', kind_video: 'Vidéo', kind_images: 'Images', kind_archives: 'Archives', kind_other: 'Autres', kind_references: 'Connaissances et liens',
      sort: 'Ordre', sort_newest: 'Plus récents d’abord', sort_oldest: 'Plus anciens d’abord', sort_name: 'Nom de A à Z', sort_largest: 'Plus lourds d’abord',
      ref_collection: 'Base de connaissances', ref_note: 'Note', ref_chat: 'Conversation', ref_folder: 'Dossier', ref_web: 'Page web', ref_filesystem: 'Fichier du terminal',
      none_match: 'Aucun fichier correspondant', none_match_hint: 'Essayez d’autres mots, un autre type, ou effacez la recherche.',
      no_chat: 'Aucune conversation ouverte', no_chat_hint: 'Ouvrez une conversation pour voir ses fichiers, ou parcourez tous vos fichiers.',
      chat_failed: 'Impossible d’afficher les fichiers de cette conversation', chat_empty: 'Pas encore de fichier dans cette conversation', chat_empty_hint: 'Les documents, tableurs et autres fichiers que vous joignez apparaissent ici.',
      library_failed: 'Impossible de charger vos fichiers', library_failed_hint: 'Vérifiez votre connexion, puis réessayez.', retry: 'Réessayer',
      library_empty: 'Pas encore de fichier', library_empty_hint: 'Les fichiers que vous importez dans vos conversations apparaissent ici.',
      err_access: 'Vous n’avez plus accès à cette conversation.', err_unsaved: 'Cette conversation n’est pas encore enregistrée.', err_generic: 'Impossible de charger les fichiers de cette conversation. Réessayez dans un instant.',
      collecting: 'Recherche des fichiers…', loading: 'Chargement…', loading_more: '{n} fichiers pour l’instant, chargement…', so_far: ' pour l’instant', of: '{shown} sur {total}',
      count_one: '{n} fichier', count_other: '{n} fichiers', refs_one: '{n} référence', refs_other: '{n} références',
      viewer: 'Aperçu du fichier', back: 'Retour aux fichiers', show_in_chat: 'Afficher dans la conversation', open_in_chat: 'Ouvrir dans la conversation', open_tab: 'Ouvrir dans un nouvel onglet', open_ref: 'Ouvrir',
      attach: 'Joindre au message', copy_text: 'Copier le texte', text_copied: 'Texte copié', rename: 'Renommer', previous: 'Fichier précédent', next: 'Fichier suivant',
      details: 'Détails', f_name: 'Nom', f_type: 'Type', f_size: 'Poids', f_created: 'Importé le', f_status: 'État', f_owner: 'Propriétaire', f_sent: 'Envoyé dans cette conversation le',
      owner_you: 'Vous', owner_other: 'Quelqu’un d’autre', st_ready: 'Prêt', st_processing: 'Traitement en cours', st_failed: 'Échec du traitement', st_missing: 'Supprimé',
      missing: 'Ce fichier a été supprimé', missing_hint: 'La conversation le mentionne encore, mais Open WebUI ne l’a plus.',
      empty_file: 'Ce fichier est vide', preview_loading: 'Chargement de l’aperçu…', preview_failed: 'Impossible de charger l’aperçu',
      no_preview: 'Pas d’aperçu pour ce type de fichier', no_preview_hint: 'Téléchargez-le pour l’ouvrir sur votre appareil.',
      binary: 'Ce fichier n’est pas du texte', too_large: 'Ce fichier est trop lourd pour être prévisualisé ici',
      extracted: 'Texte extrait de ce fichier par Open WebUI', truncated: 'Affichage des {size} premiers. Téléchargez le fichier pour tout voir.',
      table_cut: 'Affichage des {rows} premières lignes et {cols} premières colonnes.', not_pdf: 'Ce fichier porte le nom d’un PDF mais n’en est pas un',
      ref_hint: 'Cette conversation y fait référence ; ce n’est pas un fichier.', ref_unavailable: 'Cet élément ne peut pas être ouvert d’ici',
      rename_title: 'Renommer le fichier', rename_label: 'Nouveau nom', save: 'Enregistrer', rename_empty: 'Saisissez un nom.', rename_bad: 'Un nom ne peut pas contenir / ou \\, ni dépasser 255 caractères.',
      renamed: 'Renommé', rename_failed: 'Impossible de renommer le fichier', rename_note: 'Les conversations qui affichent déjà ce fichier gardent l’ancien nom.',
      confirm_one: 'Supprimer ce fichier ?', confirm_many: 'Supprimer {n} fichiers ?',
      confirm_text_one: 'Il est supprimé d’Open WebUI, de toutes les conversations qui l’affichent et des bases de connaissances où il a été ajouté. Cette action est irréversible.',
      confirm_text_many: 'Ils sont supprimés d’Open WebUI, de toutes les conversations qui les affichent et des bases de connaissances où ils ont été ajoutés. Cette action est irréversible.',
      deleted_one: 'Fichier supprimé', deleted_many: '{n} fichiers supprimés', deleted_some: '{n} supprimés, {failed} n’ont pas pu l’être', delete_failed_one: 'Impossible de supprimer le fichier', delete_failed_many: 'Impossible de supprimer les fichiers',
      download_failed: 'Impossible de télécharger le fichier', copy_failed: 'Copie impossible', open_chat_to_attach: 'Ouvrez une conversation pour joindre des fichiers', attach_failed: 'Impossible de joindre le fichier',
      preparing: 'Préparation {i} sur {n}…', zip_stopped: 'Arrêt à {mb} Mo. Sélectionnez moins de fichiers.', zip_none: 'Aucun de ces fichiers n’a pu être téléchargé', zip_cancelled: 'Téléchargement annulé',
      saved_one: '{n} fichier enregistré', saved_other: '{n} fichiers enregistrés', skipped: ', {n} ignorés',
      all: 'Tous', view: 'Affichage', view_list: 'Liste', view_grid: 'Grille', col_name: 'Nom', col_kind: 'Type', col_size: 'Poids', col_date: 'Date',
      kinds: 'Types de fichier', kind_count: '{label}, {n}', more_for: 'Plus d’actions pour {name}', open: 'Ouvrir', copy_name: 'Copier le nom', name_copied: 'Nom copié',
      selected_n: '{n} sélectionnés', g_today: 'Aujourd’hui', g_yesterday: 'Hier', g_week: '7 derniers jours', g_month: '30 derniers jours', g_undated: 'Sans date',
      f_kind: 'Type', f_mime: 'Type de média', f_ext: 'Extension', f_bytes: '{n} octets', f_updated: 'Modifié le', f_from: 'Envoyé par', from_you: 'Vous', from_answer: 'Une réponse',
      f_lines: 'Lignes', f_words: 'Mots', f_chars: 'Caractères', f_rows: 'Lignes', f_cols: 'Colonnes', f_dims: 'Dimensions', f_duration: 'Durée', f_id: 'Identifiant',
      copy_id: 'Copier l’identifiant', id_copied: 'Identifiant copié', chat_missing: 'Cette conversation n’est plus disponible',
      rename_dots: 'Un nom ne peut pas être fait que de points.', delete_more: 'et {n} autres', zip_many: 'Une archive contient au plus {n} fichiers. Sélectionnez-en moins.',
      keys: 'Les flèches déplacent, Entrée ouvre, Espace sélectionne, Suppr supprime, la barre oblique recherche.'
    }
  };
  // Open WebUI writes the chosen language on <html lang> as soon as it changes; its saved setting is the fallback
  const language = () => {
    if (STRINGS[CONFIG.language]) return CONFIG.language;
    let locale = document.documentElement.getAttribute('lang') || '';
    if (!locale) { try { locale = localStorage.getItem('locale') || ''; } catch {} }
    locale = String(locale || navigator.language || 'en').toLowerCase();
    return STRINGS[locale.slice(0, 2)] ? locale.slice(0, 2) : 'en';
  };
  let lang = 'en';
  const t = (key, vars) => {
    const text = STRINGS[lang][key] ?? STRINGS.en[key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (whole, name) => name in vars ? vars[name] : whole) : text;
  };
  const tn = (key, n, vars) => t(key + (n === 1 ? '_one' : '_other'), {n, ...vars});

  // Every piece of state that changes over time is a small machine: a table of the moves it may make, so a late
  // answer or a second click can't leave it half in one state and half in another. The state is shown on <html>
  // as data-cf-<name>, which also lets the browser tests read it.
  function machine(name, initial, table, onEnter) {
    let current = initial;
    const attr = name ? 'cf' + name.charAt(0).toUpperCase() + name.slice(1) : '';
    const write = () => { if (attr) try { document.documentElement.dataset[attr] = current; } catch {} };
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
  // Direction controls can make "invoice\u202Efdp.exe" read as "invoiceexe.pdf". They are dropped from every name,
  // so what is shown, searched, renamed and saved is what the name really says.
  const BIDI_RE = /[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/g;
  const cleanName = name => String(name ?? '').replace(BIDI_RE, '');

  const icons = {
    files: 'M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z',
    close: 'M6 18 18 6M6 6l12 12',
    prev: 'M15.75 19.5 8.25 12l7.5-7.5',
    next: 'm8.25 4.5 7.5 7.5-7.5 7.5',
    back: 'M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18',
    download: 'M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3',
    copy: 'M8 8V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H16M5.5 8h9A1.5 1.5 0 0 1 16 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 18.5v-9A1.5 1.5 0 0 1 5.5 8Z',
    chat: 'M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z',
    attach: 'm18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13',
    external: 'M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25',
    pencil: 'm16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125',
    check: 'm6 12.5 4 4 8-9',
    list: 'M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z',
    grid: 'M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z',
    more: 'M6.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM12.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM18.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Z',
    eye: 'M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    search: 'm21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z',
    hash: 'M5.25 8.25h15m-16.5 7.5h15m-1.8-13.5-3.9 19.5m-2.1-19.5-3.9 19.5',
    refresh: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99',
    trash: 'm14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0'
  };
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
  const iconButton = (label, name, action, cls = 'cf-icon') => {
    const node = el('button', cls); node.type = 'button';
    setLabel(node, label);
    node.append(svg(name)); node.addEventListener('click', action);
    return node;
  };
  const textButton = (label, action) => {
    const node = el('button', 'cf-btn', label); node.type = 'button';
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
  async function readStart(url, limit, signal) {
    const response = await request(url, {headers: {Range: 'bytes=0-' + (limit - 1)}, signal}, 60000);
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    const total = response.status === 206 ? +(/\/(\d+)$/.exec(response.headers.get('content-range') || '')?.[1] || 0) : +(response.headers.get('content-length') || 0);
    const reader = response.body?.getReader();
    if (!reader) { const bytes = new Uint8Array(await response.arrayBuffer()); return {bytes: bytes.subarray(0, limit), truncated: bytes.length > limit}; }
    const parts = []; let size = 0, more = false;
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      parts.push(value); size += value.length;
      if (size >= limit) { more = true; reader.cancel().catch(() => {}); break; }
    }
    const bytes = new Uint8Array(Math.min(size, limit)); let at = 0;
    for (const part of parts) { const take = part.subarray(0, bytes.length - at); bytes.set(take, at); at += take.length; if (at >= bytes.length) break; }
    return {bytes, truncated: (total && total > bytes.length) || (more && size > limit) || (more && total === 0)};
  }
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

  /* ---- what kind of file it is ---- */
  const EXT = {
    pdf: ['pdf'],
    documents: ['doc','docx','odt','rtf','txt','text','md','markdown','rst','epub','pages','tex','org','adoc'],
    sheets: ['xls','xlsx','xlsm','ods','csv','tsv','numbers'],
    slides: ['ppt','pptx','odp','key'],
    code: ['json','jsonl','ndjson','xml','yaml','yml','toml','ini','cfg','conf','env','js','mjs','cjs','ts','tsx','jsx','py','ipynb','java','kt','c','h','cpp','hpp','cc','cs','go','rs','rb','php','swift','sh','bash','zsh','ps1','bat','sql','html','htm','css','scss','vue','svelte','r','m','lua','pl','dart','scala','log','diff','patch','tf','graphql','proto','dockerfile','makefile'],
    audio: ['mp3','wav','m4a','aac','ogg','oga','opus','flac','weba'],
    video: ['mp4','m4v','mov','mkv','avi','webm','ogv'],
    images: ['png','jpg','jpeg','gif','webp','bmp','avif','svg','heic','heif','tif','tiff','ico'],
    archives: ['zip','tar','gz','tgz','bz2','xz','7z','rar','zst']
  };
  const extOf = name => { const match = /\.([A-Za-z0-9]{1,10})$/.exec(String(name || '').trim()); return match ? match[1].toLowerCase() : ''; };
  const TEXT_EXT = new Set([...EXT.code, 'txt','text','md','markdown','rst','tex','org','adoc','csv','tsv','svg']);
  function classify(item) {
    const ext = extOf(item.name), type = String(item.contentType || '').toLowerCase().split(';')[0];
    if (item.ref) return 'references';
    if (ext === 'pdf' || type === 'application/pdf') return 'documents';
    for (const kind of ['sheets', 'slides', 'audio', 'video', 'images', 'archives', 'code', 'documents']) if (EXT[kind].includes(ext)) return kind;
    if (type.startsWith('image/')) return 'images';
    if (type.startsWith('audio/')) return 'audio';
    if (type.startsWith('video/')) return 'video';
    if (type.startsWith('text/')) return type === 'text/csv' || type === 'text/tab-separated-values' ? 'sheets' : 'documents';
    if (/json|xml|yaml|javascript|x-sh|x-python/.test(type)) return 'code';
    if (/zip|tar|compressed|gzip|x-7z|rar/.test(type)) return 'archives';
    if (/spreadsheet|excel/.test(type)) return 'sheets';
    if (/presentation|powerpoint/.test(type)) return 'slides';
    if (/word|opendocument\.text|rtf|epub/.test(type)) return 'documents';
    return 'other';
  }
  const badgeOf = item => {
    if (item.ref) return {text: {collection: 'KB', note: 'NOTE', chat: 'CHAT', folder: 'DIR', web: 'WEB', filesystem: 'TERM'}[item.ref] || 'REF', tone: 'ref'};
    const ext = extOf(item.name), kind = classify(item);
    const tone = ext === 'pdf' || item.contentType === 'application/pdf' ? 'pdf' : {documents: 'doc', sheets: 'sheet', slides: 'slide', code: 'code', audio: 'audio', video: 'video', images: 'image', archives: 'archive'}[kind] || 'other';
    return {text: (ext || (item.contentType || '').split('/').pop() || 'FILE').slice(0, 4).toUpperCase(), tone};
  };
  function previewKind(item) {
    if (item.ref) return 'ref';
    if (item.missing) return 'missing';
    const ext = extOf(item.name), type = String(item.contentType || '').toLowerCase().split(';')[0];
    if (item.bytes === 0 && item.sized) return 'empty';
    if (ext === 'pdf' || type === 'application/pdf') return 'pdf';
    if (ext === 'csv' || ext === 'tsv' || type === 'text/csv' || type === 'text/tab-separated-values') return 'table';
    if (ext === 'json' || ext === 'ipynb' || type === 'application/json') return 'json';
    if (type.startsWith('image/') && ext !== 'svg' && type !== 'image/svg+xml') return 'image';
    if (ext === 'svg' || type === 'image/svg+xml') return 'text';
    if (type.startsWith('audio/') || EXT.audio.includes(ext)) return 'audio';
    if (type.startsWith('video/') || EXT.video.includes(ext)) return 'video';
    if (TEXT_EXT.has(ext) || type.startsWith('text/') || /json|xml|yaml|javascript/.test(type)) return 'text';
    if (EXT.archives.includes(ext)) return 'none';
    return 'extracted';
  }
  const includeImages = () => CONFIG.includeImages === 'yes' || (CONFIG.includeImages === 'auto' && document.documentElement.dataset.chatGallery !== '1');
  const wanted = item => item.ref ? CONFIG.showReferences : includeImages() || classify(item) !== 'images';

  /* ---- collecting files ---- */
  const makeItem = fields => ({fileId: null, ref: '', refId: '', url: '', name: '', contentType: '', bytes: 0, sized: false, created: 0, updated: 0, sent: 0, messageId: null, chatId: null, fromUser: false, ownerId: '', status: '', error: '', missing: false, facts: null, ...fields});
  function applyRecord(item, record) {
    if (!record || typeof record !== 'object') return;
    const meta = record.meta && typeof record.meta === 'object' ? record.meta : {};
    const data = record.data && typeof record.data === 'object' ? record.data : {};
    item.name = cleanName(meta.name || record.filename || item.name || '');
    if (meta.content_type) item.contentType = meta.content_type;
    if (typeof meta.size === 'number') { item.bytes = meta.size; item.sized = true; }
    if (record.created_at) item.created = record.created_at;
    if (record.updated_at) item.updated = record.updated_at;
    if (typeof record.user_id === 'string') item.ownerId = record.user_id;
    if (typeof data.status === 'string') item.status = data.status;
    if (typeof data.error === 'string') item.error = data.error;
    const linked = meta.data && typeof meta.data === 'object' ? meta.data : {};
    if (!item.chatId && typeof linked.chat_id === 'string') item.chatId = linked.chat_id;
    if (!item.messageId && typeof linked.message_id === 'string') item.messageId = linked.message_id;
  }
  async function collectChat(chatId, signal) {
    const chat = await api('/api/v1/chats/' + encodeURIComponent(chatId), signal);
    const messages = Object.values(chat?.chat?.history?.messages || {}).sort((a,b) => (a?.timestamp || 0) - (b?.timestamp || 0));
    const items = [], seen = new Map();
    const add = (key, fields) => {
      if (!key || seen.has(key)) return;
      const item = makeItem({chatId, ...fields});
      seen.set(key, item); items.push(item);
    };
    const visit = (file, base) => {
      if (!file || typeof file !== 'object') return;
      const type = String(file.type || '');
      if (['collection', 'note', 'chat', 'folder'].includes(type) || (type === 'file' && file.legacy && file.type === 'collection')) {
        if (file.id) add(type + ':' + file.id, {...base, ref: type, refId: String(file.id), name: cleanName(file.name || file.meta?.name || file.title || '')});
        return;
      }
      if (type === 'web' || (type === 'url' && /^https?:/i.test(file.url || ''))) {
        for (const url of [file.url, ...(Array.isArray(file.data) ? file.data : [])]) {
          if (typeof url === 'string' && /^https?:\/\//i.test(url)) add('web:' + url, {...base, ref: 'web', url, name: cleanName(file.name && file.name !== url ? file.name : url)});
        }
        return;
      }
      if (type === 'filesystem') { if (file.name) add('fs:' + file.name, {...base, ref: 'filesystem', name: cleanName(file.name)}); return; }
      if (type === 'image') return;
      const record = file.file && typeof file.file === 'object' ? file.file : null;
      const url = typeof file.url === 'string' ? file.url : '';
      const id = record?.id || (UUID_RE.test(url) ? url : null) || fileIdOf(url) || (UUID_RE.test(file.id || '') ? file.id : null);
      if (!id) return;
      const item = makeItem({...base, chatId, fileId: id, url: fileUrl(id), name: cleanName(file.name || ''), contentType: String(file.content_type || file.meta?.content_type || ''), bytes: +file.size || 0, sized: typeof file.size === 'number'});
      if (seen.has(id)) return;
      if (record) applyRecord(item, record);
      if (!item.name) item.name = cleanName(file.name || '');
      seen.set(id, item); items.push(item);
    };
    for (const message of messages) {
      if (!message) continue;
      const base = {messageId: message.id, sent: message.timestamp || 0, fromUser: message.role === 'user'};
      for (const file of Array.isArray(message.files) ? message.files : []) visit(file, base);
      // Files a tool or the model links to in its answer, such as a report it wrote
      let text = typeof message.content === 'string' ? message.content : JSON.stringify(message.content || '');
      if (message.output) text += '\n' + JSON.stringify(message.output);
      text = decodeEntities(text).replace(/\\\//g, '/');
      for (const match of text.matchAll(FILE_RE)) if (!seen.has(match[1])) { const item = makeItem({...base, chatId, fileId: match[1], url: fileUrl(match[1]), linkedOnly: true}); seen.set(match[1], item); items.push(item); }
    }
    for (const file of Array.isArray(chat?.chat?.files) ? chat.chat.files : []) visit(file, {messageId: null, sent: chat?.created_at || 0, fromUser: true});
    // One small request per file says whether it still exists; the record itself, with its extracted text, is only read when previewed
    await pool(items.filter(item => item.fileId), 6, async item => {
      if (item.linkedOnly || !item.name) {
        try { applyRecord(item, await recordOf(item.fileId, signal)); } catch (error) { if (error.status === 404) item.missing = true; else item.unknown = true; }
        return;
      }
      try {
        const state = await api('/api/v1/files/' + encodeURIComponent(item.fileId) + '/process/status', signal);
        // The chat keeps the status from upload time. Open WebUI answers "pending" for files that were never processed,
        // so only a finished answer replaces what the chat says.
        if (state?.status === 'failed' || state?.status === 'completed') item.status = state.status;
      } catch (error) { if (error.status === 404) item.missing = true; }
    }, signal);
    // A link in an answer that turned out to be an image, or nothing at all, isn't one of this chat's files
    return items.filter(item => !(item.linkedOnly && (item.unknown || (!item.missing && !item.name))));
  }
  const records = new Map();
  function recordOf(id, signal) {
    if (!records.has(id)) {
      const promise = api('/api/v1/files/' + encodeURIComponent(id), signal);
      records.set(id, promise);
      promise.catch(() => records.delete(id));
    }
    return records.get(id);
  }
  async function pool(list, size, worker, signal) {
    let next = 0;
    const run = async () => { while (next < list.length) { if (signal?.aborted) return; await worker(list[next++]); } };
    await Promise.all(Array.from({length: Math.min(size, list.length)}, run));
  }

  /* ---- the panel ---- */
  let dialog, head, tools, tabChat, tabLibrary, search, kindBar, sortSelect, refreshButton, soloClose, selectButton, zipButton, allButton, deleteSelected, viewList, viewGrid;
  let scroller, colHead, columnButtons = {}, listEl, sentinel, foot, footText, footAction, live, toast;
  let viewer, stage, info, countEl, chatButton, openTabButton, copyButton, attachButton, downloadButton, renameButton, deleteButton, prevButton, nextButton, confirmBox, renameBox;
  let me = null;
  let scope = 'chat', items = [], shown = [], opener = null, headerButton = null, collectController = null, zipController = null;
  let chatError = '', chatLoadedAt = 0, kind = 'all', builtLang = '', anchorItem = null, footStatus = '', footActionSpec = null;
  const order = {chat: 'oldest', library: 'newest'};
  const selected = new Set();
  const chatExists = new Map();
  const rows = new Map(), mains = new WeakMap(), groupNodes = new Map();

  // The chat's files: no chat open, being collected, there, or not to be had
  const chatLoad = machine('chat', 'none', {'*': {none: 'none', load: 'loading'}, loading: {done: 'ready', fail: 'failed'}});
  // My files arrive a page at a time; each refresh starts a new list with its own machine, and a late page for an old list is dropped
  const freshLibrary = (initial = 'idle') => ({items: [], offset: 0, controller: new AbortController(),
    state: machine('library', initial, {idle: {load: 'loading'}, more: {load: 'loading'}, failed: {load: 'loading'}, loading: {page: 'more', last: 'done', fail: 'failed'}})});
  let library = freshLibrary();
  // Picking files to zip or delete
  const picking = machine('select', 'off', {off: {start: 'on'}, on: {stop: 'off'}});
  const selecting = () => picking.is('on');
  // One long job at a time: a zip being built, or files being deleted
  const job = machine('job', 'idle', {idle: {zip: 'zipping', delete: 'deleting'}, zipping: {done: 'idle'}, deleting: {done: 'idle'}});
  const busy = () => !job.is('idle');
  // List or grid, remembered in this browser
  const VIEW_KEY = 'owui-chat-files:view';
  const savedView = (() => { try { const value = localStorage.getItem(VIEW_KEY); return value === 'grid' || value === 'list' ? value : ''; } catch { return ''; } })();
  const layout = machine('view', savedView || (CONFIG.defaultView === 'grid' ? 'grid' : 'list'), {list: {grid: 'grid'}, grid: {list: 'list'}});
  const pickable = item => !!item && !item.ref && !item.missing;

  function say(text, action) {
    footText.textContent = text;
    footAction.hidden = !action;
    if (action) { footAction.textContent = action.label; footAction.onclick = action.run; }
  }
  function flash(text) {
    toast.textContent = text; toast.classList.add('cf-show');
    clearTimeout(flash.timer); flash.timer = setTimeout(() => toast.classList.remove('cf-show'), 2400);
    live.textContent = text;
  }
  function build() {
    lang = builtLang = language();
    rows.clear(); groupNodes.clear();
    dialog = el('div', 'cf-dialog'); dialog.id = 'owui-chat-files'; dialog.hidden = true; dialog.lang = lang;
    dialog.setAttribute('role', 'tabpanel'); dialog.setAttribute('aria-labelledby', 'owui-chat-files-tab');
    head = el('div', 'cf-head');
    const tabs = el('div', 'cf-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('files_to_show'));
    const scopeTab = (label, value) => {
      const node = el('button', 'cf-tab', label); node.type = 'button'; node.setAttribute('role', 'tab');
      node.id = 'cf-tab-' + value; node.setAttribute('aria-controls', 'cf-panel');
      node.addEventListener('click', () => setScope(value));
      node.addEventListener('keydown', event => {
        const visible = [tabChat, tabLibrary].filter(each => !each.hidden);
        const at = visible.indexOf(node);
        const forward = getComputedStyle(node).direction === 'rtl' ? 'ArrowLeft' : 'ArrowRight';
        const target = {[forward === 'ArrowRight' ? 'ArrowLeft' : 'ArrowRight']: visible[at - 1] || visible[visible.length - 1], [forward]: visible[at + 1] || visible[0], Home: visible[0], End: visible[visible.length - 1]}[event.key];
        if (!target) return;
        event.preventDefault();
        if (target !== node) { target.focus(); target.click(); }
      });
      tabs.append(node); return node;
    };
    tabChat = scopeTab(t('this_chat'), 'chat'); tabLibrary = scopeTab(t('my_files'), 'library');
    const views = el('div', 'cf-seg'); views.setAttribute('role', 'group'); views.setAttribute('aria-label', t('view'));
    viewList = iconButton(t('view_list'), 'list', () => setView('list'), 'cf-icon cf-segbtn');
    viewGrid = iconButton(t('view_grid'), 'grid', () => setView('grid'), 'cf-icon cf-segbtn');
    views.append(viewList, viewGrid);
    refreshButton = iconButton(t('refresh'), 'refresh', () => refresh());
    selectButton = textButton(t('select'), () => setSelecting(!selecting()));
    allButton = textButton(t('select_all'), () => selectAll());
    zipButton = textButton(t('download_all'), () => zipController ? zipController.abort() : downloadZip());
    zipButton.prepend(svg('download'));
    deleteSelected = textButton(t('delete'), () => deleteItems(shown.filter(item => selected.has(item) && deletable(item))));
    deleteSelected.prepend(svg('trash')); deleteSelected.hidden = true; deleteSelected.classList.add('cf-delete-selected');
    soloClose = iconButton(t('close_files'), 'close', () => closeTab(), 'cf-icon cf-close'); soloClose.hidden = true;
    head.append(tabs, el('span', 'cf-spacer'), views, refreshButton, selectButton, allButton, deleteSelected, zipButton, soloClose);
    tools = el('div', 'cf-tools');
    const searchBox = el('label', 'cf-searchbox'); searchBox.append(svg('search'));
    search = el('input', 'cf-search'); search.type = 'search'; search.placeholder = t('search'); search.maxLength = 200; search.spellcheck = false;
    search.setAttribute('aria-label', t('search_label')); search.title = t('search_label');
    searchBox.append(search);
    let searchTimer = 0;
    search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { scroller.scrollTop = 0; render(); }, 120); });
    sortSelect = el('select', 'cf-select'); sortSelect.setAttribute('aria-label', t('sort'));
    for (const value of ['newest', 'oldest', 'name', 'largest']) { const option = el('option', null, t('sort_' + value)); option.value = value; sortSelect.append(option); }
    sortSelect.addEventListener('change', () => setOrder(sortSelect.value));
    kindBar = el('div', 'cf-kinds'); kindBar.setAttribute('role', 'group'); kindBar.setAttribute('aria-label', t('kinds'));
    tools.append(searchBox, sortSelect);
    const body = el('div', 'cf-body');
    scroller = el('div', 'cf-scroll'); scroller.id = 'cf-panel'; scroller.setAttribute('role', 'tabpanel');
    scroller.addEventListener('scroll', () => closeMenu(false), {passive: true});
    colHead = buildColumns();
    listEl = el('ul', 'cf-list'); listEl.setAttribute('aria-label', t('list')); listEl.setAttribute('aria-description', t('keys'));
    listEl.addEventListener('keydown', listKeys);
    // A button acts on Space when the key comes up; here Space picks the file instead
    listEl.addEventListener('keyup', event => { if (event.key === ' ' && event.target.closest?.('.cf-main')) event.preventDefault(); });
    sentinel = el('div', 'cf-sentinel');
    scroller.append(colHead, listEl, sentinel);
    foot = el('div', 'cf-foot');
    footText = el('span'); footAction = textButton('', () => {}); footAction.hidden = true;
    foot.append(footText, footAction);
    live = el('div', 'cf-sr'); live.setAttribute('aria-live', 'polite');
    toast = el('div', 'cf-toast'); toast.setAttribute('aria-hidden', 'true');
    body.append(scroller, foot, buildViewer(), toast, live);
    dialog.append(head, tools, kindBar, body, buildConfirm(), buildRename());
    dialog.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.target.closest('dialog')) return;
      if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !viewerOpen() && !event.target.closest('input,textarea,select,[contenteditable]')) { event.preventDefault(); search.focus(); search.select(); return; }
      if (event.key !== 'Escape') return;
      if (menuEl) { event.preventDefault(); closeMenu(); }
      else if (viewerOpen()) { event.preventDefault(); closeViewer(); }
      else if (event.target === search) { event.preventDefault(); if (search.value) { search.value = ''; render(); } else listEl.querySelector('.cf-main')?.focus(); }
      else if (selecting()) { event.preventDefault(); setSelecting(false); }
      else if (dialog.classList.contains('cf-solo')) { event.preventDefault(); deactivate(); headerButton?.focus({preventScroll: true}); }
    });
    new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && scope === 'library' && isOpen()) loadLibrary();
    }, {root: scroller, rootMargin: '400px'}).observe(sentinel);
    paintView();
    document.body.append(dialog);
  }
  // Column titles over the list; each one that can order the files does so
  function buildColumns() {
    const row = el('div', 'cf-colhead');
    const column = (key, label, next) => {
      const button = el('button', 'cf-colbtn cf-col' + key); button.type = 'button';
      button.append(el('span', null, label), el('span', 'cf-arrow'));
      button.addEventListener('click', () => setOrder(next(order[scope])));
      row.append(button); return button;
    };
    columnButtons = {
      name: column('name', t('col_name'), () => 'name'),
      kind: (() => { const span = el('span', 'cf-colbtn cf-colkind', t('col_kind')); row.append(span); return span; })(),
      size: column('size', t('col_size'), () => 'largest'),
      date: column('date', t('col_date'), now => now === 'newest' ? 'oldest' : 'newest')
    };
    row.append(el('span', 'cf-colmore'));
    return row;
  }
  function paintColumns() {
    const how = order[scope], on = {name: how === 'name', size: how === 'largest', date: how === 'newest' || how === 'oldest'};
    for (const key of ['name', 'size', 'date']) {
      const button = columnButtons[key];
      button.setAttribute('aria-pressed', String(on[key]));
      button.lastChild.textContent = on[key] ? (key === 'date' && how === 'oldest' ? '↑' : key === 'name' ? '↑' : '↓') : '';
    }
  }
  function setOrder(value) {
    if (!['newest', 'oldest', 'name', 'largest'].includes(value)) return;
    order[scope] = value; scroller.scrollTop = 0; render();
  }
  function setView(value) {
    if (layout.send(value)) { try { localStorage.setItem(VIEW_KEY, value); } catch {} }
    paintView(); render();
    (rows.get(shown[0])?.main)?.scrollIntoView?.({block: 'nearest'});
  }
  function paintView() {
    listEl.dataset.view = layout.state;
    viewList.setAttribute('aria-pressed', String(layout.is('list'))); viewGrid.setAttribute('aria-pressed', String(layout.is('grid')));
    colHead.hidden = layout.is('grid');
  }
  // Grid columns, read from where the cards actually sit
  function columns() {
    if (!layout.is('grid')) return 1;
    const cards = listEl.querySelectorAll('.cf-item');
    if (cards.length < 2) return 1;
    const top = cards[0].offsetTop; let n = 0;
    for (const card of cards) { if (card.offsetTop !== top) break; n++; }
    return Math.max(1, n);
  }
  // Arrows move as in a file manager (in the grid, in two directions); Space picks, Delete deletes, the menu key opens the menu
  function listKeys(event) {
    const main = event.target.closest?.('.cf-main'), entry = main && mains.get(main);
    if (!entry || event.target !== main || event.altKey) return;
    const all = [...listEl.querySelectorAll('.cf-main')], at = all.indexOf(main);
    const rtl = getComputedStyle(listEl).direction === 'rtl', mod = event.ctrlKey || event.metaKey;
    let to = null;
    switch (event.key) {
      case 'ArrowDown': to = at + columns(); break;
      case 'ArrowUp': to = at - columns(); break;
      case 'ArrowRight': if (layout.is('grid')) to = at + (rtl ? -1 : 1); break;
      case 'ArrowLeft': if (layout.is('grid')) to = at + (rtl ? 1 : -1); break;
      case 'Home': to = 0; break;
      case 'End': to = all.length - 1; break;
      case ' ': if (!mod) { event.preventDefault(); toggle(entry.item, event.shiftKey); } return;
      case 'Delete': case 'Backspace': {
        const list = selecting() && selected.size ? shown.filter(item => selected.has(item)) : [entry.item];
        if (list.some(deletable)) { event.preventDefault(); deleteItems(list); }
        return;
      }
      case 'ContextMenu': event.preventDefault(); openRowMenu(entry, entry.more); return;
      case 'F10': if (event.shiftKey) { event.preventDefault(); openRowMenu(entry, entry.more); } return;
      case 'a': case 'A': if (mod) { event.preventDefault(); selectAll(); } return;
      default: return;
    }
    if (to === null) return;
    event.preventDefault();
    const next = all[Math.max(0, Math.min(all.length - 1, to))];
    if (next && next !== main) { next.focus(); next.scrollIntoView({block: 'nearest'}); }
  }
  function setSelecting(on) {
    picking.send(on ? 'start' : 'stop');
    if (!on) { selected.clear(); anchorItem = null; }
    listEl.classList.toggle('cf-selecting', selecting());
    selectButton.textContent = selecting() ? t('cancel') : t('select');
    selectButton.setAttribute('aria-pressed', String(selecting()));
    paintSelection();
  }
  // Click picks one; Shift picks everything from the last one picked
  function toggle(item, range) {
    if (!pickable(item)) return;
    if (!selecting()) setSelecting(true);
    if (range && anchorItem && shown.includes(anchorItem)) {
      const a = shown.indexOf(anchorItem), b = shown.indexOf(item);
      for (const each of shown.slice(Math.min(a, b), Math.max(a, b) + 1)) if (pickable(each)) selected.add(each);
    } else { selected.has(item) ? selected.delete(item) : selected.add(item); anchorItem = item; }
    paintSelection();
  }
  function selectAll() {
    const choosable = shown.filter(pickable);
    if (!choosable.length) return;
    if (!selecting()) setSelecting(true);
    const all = choosable.every(item => selected.has(item));
    for (const item of choosable) all ? selected.delete(item) : selected.add(item);
    paintSelection();
  }
  function paintSelection() {
    const choosable = shown.filter(pickable), on = selecting();
    allButton.hidden = !on;
    allButton.textContent = choosable.length && choosable.every(item => selected.has(item)) ? t('select_none') : t('select_all');
    for (const [item, entry] of rows) {
      if (!entry.li.isConnected) continue;
      const picked = on && pickable(item) && selected.has(item);
      if (on && pickable(item)) entry.main.setAttribute('aria-pressed', String(picked)); else entry.main.removeAttribute('aria-pressed');
      entry.row.classList.toggle('cf-picked', picked);
    }
    const picked = choosable.filter(item => selected.has(item)).length;
    zipButton.lastChild.textContent = zipController ? t('cancel') : on ? (picked ? t('download_n', {n: picked}) : t('download')) : t('download_all');
    zipButton.disabled = zipController ? false : busy() || !choosable.length || (on && !picked);
    const removable = shown.filter(item => selected.has(item) && deletable(item)).length;
    deleteSelected.hidden = !on || !shown.some(deletable);
    deleteSelected.lastChild.textContent = removable ? t('delete_n', {n: removable}) : t('delete');
    deleteSelected.disabled = busy() || !removable;
    paintFoot();
  }
  // The footer counts what is shown, or what is picked; while a zip is built it shows its progress instead
  function paintFoot() {
    if (job.is('zipping')) return;
    const picked = selecting() ? shown.filter(item => pickable(item) && selected.has(item)) : [];
    if (picked.length) { const bytes = picked.reduce((sum, item) => sum + (item.bytes || 0), 0); say(t('selected_n', {n: picked.length}) + (bytes ? ' · ' + formatBytes(bytes) : '')); }
    else say(footStatus, footActionSpec);
  }
  function setScope(value, load = true) {
    scope = value;
    tabChat.setAttribute('aria-selected', String(value === 'chat')); tabLibrary.setAttribute('aria-selected', String(value === 'library'));
    tabChat.tabIndex = value === 'chat' ? 0 : -1; tabLibrary.tabIndex = value === 'library' ? 0 : -1;
    scroller.setAttribute('aria-labelledby', 'cf-tab-' + value);
    if (kind === 'references' && value === 'library') kind = 'all';
    setSelecting(false); scroller.scrollTop = 0;
    if (!load) render();
    else if (value === 'chat') loadChat();
    else { render(); if (!library.items.length && !library.state.is('done', 'loading')) loadLibrary(); }
  }
  async function loadChat(quiet) {
    collectController?.abort();
    const controller = collectController = new AbortController();
    const chatId = chatIdFromPath();
    if (!quiet || !chatId) items = [];
    chatError = '';
    chatLoad.send(chatId ? 'load' : 'none');
    if (scope === 'chat') render();
    if (!chatId) return;
    try {
      let found = await collectChat(chatId, controller.signal);
      if (controller.signal.aborted) return;
      if (quiet) {
        // The same file keeps its object, so a selection and the open preview survive a refresh
        const known = new Map(items.map(item => [keyOf(item), item]));
        found = found.map(item => { const old = known.get(keyOf(item)); return old ? Object.assign(old, {missing: item.missing, status: item.status || old.status}) : item; });
      }
      items = found;
      chatLoad.send('done');
    } catch (error) {
      if (controller.signal.aborted) return;
      if (!quiet) items = [];
      chatError = error.status === 401 || error.status === 403 ? t('err_access') : error.status === 404 ? t('err_unsaved') : t('err_generic');
      chatLoad.send('fail');
    }
    chatLoadedAt = Date.now();
    if (scope === 'chat') render();
  }
  const keyOf = item => item.fileId || item.ref + ':' + (item.refId || item.url || item.name);
  async function loadLibrary() {
    const lib = library;
    if (!isOpen() || !lib.state.send('load')) return;
    if (scope === 'library') render();
    try {
      const page = await api('/api/v1/files/user/search?filename=*&content=false&skip=' + lib.offset + '&limit=' + PAGE_SIZE, lib.controller.signal);
      if (library !== lib) return;
      const list = Array.isArray(page) ? page : [];
      lib.offset += list.length;
      const known = new Set(lib.items.map(item => item.fileId));
      for (const record of list) {
        if (!record?.id || known.has(record.id)) continue;
        const item = makeItem({fileId: record.id, url: fileUrl(record.id)});
        applyRecord(item, record);
        records.set(record.id, Promise.resolve(record));
        lib.items.push(item); known.add(record.id);
      }
      lib.state.send(list.length < PAGE_SIZE ? 'last' : 'page');
    } catch (error) {
      if (library !== lib) return;
      lib.state.send('fail');
    }
    if (scope === 'library') render();
    if (isOpen() && scope === 'library' && lib.state.is('more') && scroller.scrollHeight <= scroller.clientHeight + 400) loadLibrary();
  }
  function refresh() {
    if (scope === 'chat') { records.clear(); loadChat(true); return; }
    library.controller.abort(); library = freshLibrary(); records.clear();
    setSelecting(false); render(); loadLibrary();
  }
  const current = () => (scope === 'chat' ? items : library.items).filter(wanted);
  function matches(item, query) {
    if (kind !== 'all' && classify(item) !== kind) return false;
    if (!query) return true;
    return [item.name, item.url, extOf(item.name), item.contentType, t('kind_' + classify(item)), item.ref ? t('ref_' + item.ref) : ''].some(value => String(value || '').toLocaleLowerCase(lang).includes(query));
  }
  const dateOf = item => toMs(item.created || item.sent);
  const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
  function sortList(list) {
    const how = order[scope];
    const sorted = list.slice();
    if (how === 'name') sorted.sort((a, b) => collator.compare(a.name || a.url, b.name || b.url));
    else if (how === 'largest') sorted.sort((a, b) => (b.bytes || 0) - (a.bytes || 0) || collator.compare(a.name, b.name));
    else if (scope === 'chat') { if (how === 'newest') sorted.reverse(); }
    else sorted.sort((a, b) => how === 'oldest' ? dateOf(a) - dateOf(b) : dateOf(b) - dateOf(a));
    return sorted;
  }
  // My files, ordered by date, fall into Today, Yesterday, the last week and month, then months
  const grouped = () => scope === 'library' && (order.library === 'newest' || order.library === 'oldest');
  function groupOf(item) {
    const ms = dateOf(item);
    if (!ms) return t('g_undated');
    const now = new Date(), day = 86400000, start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (ms >= start) return t('g_today');
    if (ms >= start - day) return t('g_yesterday');
    if (ms >= start - 7 * day) return t('g_week');
    if (ms >= start - 30 * day) return t('g_month');
    try { return new Date(ms).toLocaleDateString(lang, {month: 'long', year: 'numeric'}); } catch { return t('g_undated'); }
  }
  function groupNode(label) {
    let node = groupNodes.get(label);
    if (!node) { node = el('li', 'cf-group', label); node.dir = 'auto'; groupNodes.set(label, node); }
    return node;
  }
  const statusOf = item => item.missing ? 'missing' : item.status === 'failed' ? 'failed' : item.status === 'pending' || item.status === 'processing' ? 'processing' : '';
  // Pictures of images, small ones only, in the list's badge and the grid's card. An <img> never runs what it loads.
  const RASTER = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif', 'ico']);
  const THUMB_LIMIT = 8388608;
  const thumbable = item => CONFIG.thumbnails && pickable(item) && classify(item) === 'images' && !(item.sized && item.bytes > THUMB_LIMIT)
    && (RASTER.has(extOf(item.name)) || /^image\/(png|jpe?g|gif|webp|bmp|avif)$/.test(String(item.contentType).toLowerCase()));
  function paintThumb(entry) {
    const want = thumbable(entry.item) && !entry.thumbFailed;
    if (want && !entry.img) {
      const img = el('img', 'cf-thumbimg'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.draggable = false;
      img.addEventListener('load', () => entry.thumb.classList.add('cf-hasimg'), {once: true});
      img.addEventListener('error', () => { entry.thumbFailed = true; img.remove(); entry.img = null; entry.thumb.classList.remove('cf-hasimg'); }, {once: true});
      img.src = entry.item.url; entry.thumb.append(img); entry.img = img;
    } else if (!want && entry.img) { entry.img.remove(); entry.img = null; entry.thumb.classList.remove('cf-hasimg'); }
  }
  function rowFor(item) {
    let entry = rows.get(item);
    if (entry) return entry;
    const li = el('li', 'cf-item');
    const row = el('div', 'cf-row');
    const main = el('button', 'cf-main'); main.type = 'button';
    const check = el('span', 'cf-check'); check.append(svg('check'));
    const thumb = el('span', 'cf-thumb'), badge = el('span', 'cf-badge'); thumb.append(badge);
    const text = el('span', 'cf-text'), name = nameSpan('cf-name'), sub = el('span', 'cf-sub'), subText = el('span', 'cf-subtext');
    sub.append(subText); text.append(name, sub);
    main.append(check, thumb, text);
    // The row's label already says all of this, so the columns are for the eye only
    const cells = ['kind', 'size', 'date'].map(key => { const cell = el('span', 'cf-cell cf-' + key + 'cell'); cell.setAttribute('aria-hidden', 'true'); return cell; });
    const more = el('button', 'cf-icon cf-more'); more.type = 'button'; more.tabIndex = -1; more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-expanded', 'false'); more.append(svg('more'));
    row.append(main, ...cells, more);
    li.append(row);
    entry = {item, li, row, main, thumb, badge, name, sub, subText, kindCell: cells[0], sizeCell: cells[1], dateCell: cells[2], more, img: null, thumbFailed: false, index: 0};
    main.addEventListener('click', event => {
      if (pickable(item) && (selecting() || event.ctrlKey || event.metaKey || (event.shiftKey && anchorItem))) toggle(item, event.shiftKey);
      else if (!selecting()) openViewer(entry.index);
    });
    more.addEventListener('click', event => { event.stopPropagation(); openRowMenu(entry, more); });
    row.addEventListener('contextmenu', event => { event.preventDefault(); openRowMenu(entry, more, {x: event.clientX, y: event.clientY}); });
    mains.set(main, entry);
    rows.set(item, entry);
    return entry;
  }
  function paintRow(entry) {
    const item = entry.item, badge = badgeOf(item), state = statusOf(item);
    entry.badge.textContent = badge.text; entry.thumb.dataset.tone = badge.tone;
    entry.name.textContent = item.name || item.url || t('list');
    entry.name.title = item.name || item.url || '';
    const kindText = item.ref ? t('ref_' + item.ref) : t('kind_' + classify(item));
    const size = !item.ref && item.sized ? formatBytes(item.bytes) : '';
    const when = formatDate(item.created || item.sent);
    const parts = [kindText, size, when].filter(Boolean);
    entry.subText.textContent = parts.join(' · ');
    entry.sub.querySelector('.cf-flag')?.remove();
    if (state) { const flag = el('span', 'cf-flag', t('st_' + state)); flag.dataset.state = state; entry.sub.append(flag); }
    entry.kindCell.textContent = kindText; entry.sizeCell.textContent = size || '–'; entry.dateCell.textContent = when;
    entry.row.classList.toggle('cf-gone', item.missing);
    entry.main.setAttribute('aria-label', [item.name || item.url, ...parts, state ? t('st_' + state) : ''].filter(Boolean).join(', '));
    setLabel(entry.more, t('more_for', {name: item.name || item.url || ''}));
    paintThumb(entry);
  }
  function forgetRows() {
    const alive = new Set([...items, ...library.items]);
    for (const item of rows.keys()) if (!alive.has(item)) rows.delete(item);
  }
  // A chip per kind there is, with how many; the one chosen stays even when nothing of it is left
  function fillKinds(list) {
    const counts = new Map();
    for (const item of list) { const value = classify(item); counts.set(value, (counts.get(value) || 0) + 1); }
    const kinds = ['all', 'documents', 'sheets', 'slides', 'code', 'audio', 'video', 'images', 'archives', 'other', 'references'].filter(value => value === 'all' || counts.has(value) || value === kind);
    const signature = kinds.map(value => value + ':' + (value === 'all' ? list.length : counts.get(value) || 0)).join() + '|' + kind + '|' + lang;
    kindBar.hidden = kinds.length < 3 && kind === 'all';
    if (kindBar.dataset.kinds === signature) return;
    const focused = kindBar.contains(document.activeElement) ? document.activeElement.dataset.kind : null;
    kindBar.dataset.kinds = signature;
    kindBar.replaceChildren(...kinds.map(value => {
      const n = value === 'all' ? list.length : counts.get(value) || 0, label = t(value === 'all' ? 'all' : 'kind_' + value);
      const chip = el('button', 'cf-chip'); chip.type = 'button'; chip.dataset.kind = value;
      chip.setAttribute('aria-pressed', String(value === kind));
      chip.setAttribute('aria-label', t('kind_count', {label, n}));
      chip.append(el('span', null, label), el('span', 'cf-chipcount', String(n)));
      chip.addEventListener('click', () => { kind = value; scroller.scrollTop = 0; render(); });
      return chip;
    }));
    if (focused) kindBar.querySelector(`[data-kind="${focused}"]`)?.focus({preventScroll: true});
  }
  function render() {
    if (!dialog) return;
    const query = search.value.trim().toLocaleLowerCase(lang);
    const list = current();
    fillKinds(list);
    shown = sortList(list.filter(item => matches(item, query)));
    forgetRows();
    const want = [], groups = grouped();
    let lastGroup = null;
    shown.forEach((item, index) => {
      if (groups) { const label = groupOf(item); if (label !== lastGroup) { want.push(groupNode(label)); lastGroup = label; } }
      const entry = rowFor(item);
      entry.index = index;
      paintRow(entry);
      want.push(entry.li);
    });
    const loading = scope === 'library' ? library.state.is('loading') : chatLoad.is('loading');
    const filtered = !!query || kind !== 'all';
    if (!shown.length && !loading) {
      const empty = el('li', 'cf-empty');
      const words = (title, hint) => empty.append(el('strong', null, t(title)), el('span', null, hint));
      if (scope === 'chat' && !chatIdFromPath()) words('no_chat', t('no_chat_hint'));
      else if (scope === 'chat' && chatLoad.is('failed')) { words('chat_failed', chatError); empty.append(textButton(t('retry'), () => loadChat())); }
      else if (scope === 'library' && library.state.is('failed')) { words('library_failed', t('library_failed_hint')); empty.append(textButton(t('retry'), () => loadLibrary())); }
      else if (filtered) words('none_match', t('none_match_hint'));
      else if (scope === 'chat') words('chat_empty', t('chat_empty_hint'));
      else words('library_empty', t('library_empty_hint'));
      want.push(empty);
    }
    // Put the rows in place, moving as few as possible, so focus and scrolling stay where they are
    let node = listEl.firstElementChild;
    for (const wanted of want) { if (node === wanted) node = node.nextElementSibling; else listEl.insertBefore(wanted, node); }
    while (node) { const next = node.nextElementSibling; node.remove(); node = next; }
    colHead.hidden = layout.is('grid') || !shown.length;
    const filesIn = list.filter(item => !item.ref), refs = list.length - filesIn.length;
    const total = filesIn.length, more = scope === 'library' && !library.state.is('done');
    const bytes = filesIn.reduce((sum, item) => sum + (item.missing ? 0 : item.bytes || 0), 0);
    let status = '', action = null;
    if (loading && !list.length) status = scope === 'chat' ? t('collecting') : t('loading');
    else if (loading && scope === 'library') status = t('loading_more', {n: total});
    else if (list.length) {
      const counted = total ? tn('count', total) + (bytes ? ' · ' + formatBytes(bytes) : '') : '';
      const words = [counted, refs ? tn('refs', refs) : ''].filter(Boolean).join(' · ');
      status = (filtered ? t('of', {shown: shown.length, total: words}) : words) + (more ? t('so_far') : '');
    }
    if (!loading && scope === 'library' && library.state.is('failed') && list.length) action = {label: t('retry'), run: () => loadLibrary()};
    footStatus = status; footActionSpec = action;
    if (sortSelect.value !== order[scope]) sortSelect.value = order[scope];
    paintColumns();
    refreshButton.disabled = loading || (scope === 'chat' && !chatIdFromPath());
    if (menuEl && !shown.includes(menuEl.cfItem)) closeMenu(false);
    paintSelection();
  }

  /* ---- a file's menu: the ⋯ button, a right click, or the menu key ---- */
  let menuEl = null;
  const outside = event => { if (menuEl && !menuEl.contains(event.target) && event.target !== menuEl.cfAnchor && !menuEl.cfAnchor?.contains(event.target)) closeMenu(false); };
  function closeMenu(focusBack = true) {
    if (!menuEl) return;
    const box = menuEl; menuEl = null;
    box.cfAnchor?.setAttribute('aria-expanded', 'false');
    box.remove();
    document.removeEventListener('pointerdown', outside, true);
    if (focusBack && box.cfBack?.isConnected) box.cfBack.focus({preventScroll: true});
  }
  function openRowMenu(entry, anchor, point, refresh = false) {
    const item = entry.item;
    if (!refresh && menuEl?.cfItem === item && menuEl.cfAnchor === anchor && !point) { closeMenu(); return; }
    const keepFocus = refresh && menuEl ? [...menuEl.querySelectorAll('.cf-menuitem')].indexOf(document.activeElement) : 0;
    closeMenu(false);
    if (!dialog || !entry.li.isConnected) return;
    const box = el('div', 'cf-menu'); box.setAttribute('role', 'menu'); box.setAttribute('aria-label', item.name || item.url || '');
    const add = (label, icon, run, danger) => {
      const button = el('button', 'cf-menuitem' + (danger ? ' cf-menudanger' : '')); button.type = 'button';
      button.setAttribute('role', 'menuitem'); button.tabIndex = -1;
      button.append(svg(icon), el('span', null, label));
      button.addEventListener('click', () => { closeMenu(false); run(); });
      box.append(button);
    };
    const file = pickable(item);
    add(t(item.ref ? 'open_ref' : 'open'), 'eye', () => openViewer(entry.index));
    if (file) add(t('download'), 'download', () => downloadOne(item));
    if (file && composer()) add(t('attach'), 'attach', () => attachItem(item));
    if (item.chatId && (scope !== 'chat' || item.messageId)) add(t(item.chatId === chatIdFromPath() ? 'show_in_chat' : 'open_in_chat'), 'chat', () => goToChat(item));
    add(t('copy_name'), 'copy', () => copyToClipboard(item.name || item.url || '', t('name_copied')));
    if (renamable(item)) add(t('rename'), 'pencil', () => renameItem(item));
    if (deletable(item)) add(t('delete'), 'trash', () => deleteItems([item]), true);
    box.cfItem = item; box.cfAnchor = anchor; box.cfBack = entry.main;
    box.addEventListener('keydown', event => {
      const all = [...box.querySelectorAll('.cf-menuitem')], at = all.indexOf(document.activeElement);
      const go = {ArrowDown: all[(at + 1) % all.length], ArrowUp: all[(at - 1 + all.length) % all.length], Home: all[0], End: all[all.length - 1]}[event.key];
      if (go) { event.preventDefault(); go.focus(); return; }
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu(); }
      else if (event.key === 'Tab') { event.preventDefault(); closeMenu(); }
    });
    dialog.append(box); menuEl = box;
    anchor.setAttribute('aria-expanded', 'true');
    // By the button, or where the pointer was, and always inside the panel
    const frame = dialog.getBoundingClientRect(), size = box.getBoundingClientRect(), near = anchor.getBoundingClientRect();
    const from = point || {x: near.right - size.width, y: near.bottom + 4};
    const left = Math.max(8, Math.min(from.x - frame.left, frame.width - size.width - 8));
    let top = from.y - frame.top;
    if (top + size.height > frame.height - 8) top = Math.max(8, (point ? point.y : near.top) - frame.top - size.height - 4);
    box.style.left = Math.round(left) + 'px'; box.style.top = Math.round(top) + 'px';
    const choices = box.querySelectorAll('.cf-menuitem');
    (choices[Math.max(0, Math.min(choices.length - 1, keepFocus))] || choices[0])?.focus({preventScroll: true});
    setTimeout(() => { if (menuEl === box) document.addEventListener('pointerdown', outside, true); });
    // Rename and delete depend on who owns the file, which a chat doesn't always say: ask, then offer them
    if (file && !item.ownerId) recordOf(item.fileId).then(record => {
      applyRecord(item, record);
      if (menuEl === box && (renamable(item) || deletable(item))) openRowMenu(entry, anchor, point, true);
    }).catch(() => {});
  }

  /* ---- preview ---- */
  let index = 0, viewItem = null, viewController = null, objectUrls = [], returnRow = null, viewText = '';
  // The preview: closed, loading a file, showing it, or unable to
  const preview = machine('preview', 'closed', {'*': {close: 'closed'}, closed: {open: 'loading'}, loading: {open: 'loading', ready: 'ready', fail: 'failed'}, ready: {open: 'loading'}, failed: {open: 'loading'}}, next => {
    if (!viewer) return;
    const open = next !== 'closed';
    viewer.classList.toggle('cf-open', open); dialog.classList.toggle('cf-viewing', open);
    head.inert = tools.inert = kindBar.inert = scroller.inert = open;
    stage.classList.toggle('cf-loading', next === 'loading');
  });
  const viewerOpen = () => !preview.is('closed');
  function buildViewer() {
    viewer = el('div', 'cf-viewer'); viewer.setAttribute('role', 'group'); viewer.setAttribute('aria-roledescription', t('viewer')); viewer.setAttribute('aria-label', t('viewer'));
    const bar = el('div', 'cf-vbar');
    countEl = el('span', 'cf-count');
    chatButton = iconButton(t('show_in_chat'), 'chat', showInChat);
    openTabButton = iconButton(t('open_tab'), 'external', openElsewhere);
    copyButton = iconButton(t('copy_text'), 'copy', copyText);
    attachButton = iconButton(t('attach'), 'attach', attachToComposer);
    downloadButton = iconButton(t('download'), 'download', () => downloadOne(viewItem));
    renameButton = iconButton(t('rename'), 'pencil', () => renameItem(viewItem));
    deleteButton = iconButton(t('delete'), 'trash', () => deleteItems([viewItem]));
    prevButton = iconButton(t('previous'), 'prev', () => step(-1));
    nextButton = iconButton(t('next'), 'next', () => step(1));
    bar.append(iconButton(t('back'), 'back', () => closeViewer()), prevButton, countEl, nextButton, el('span', 'cf-spacer'), chatButton, openTabButton, copyButton, attachButton, downloadButton, renameButton, deleteButton);
    stage = el('div', 'cf-stage'); stage.tabIndex = -1;
    info = el('div', 'cf-info');
    viewer.append(bar, stage, info);
    viewer.addEventListener('keydown', event => {
      if (event.target.closest('input,textarea,select,dialog,audio,video,iframe')) return;
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const back = getComputedStyle(viewer).direction === 'rtl' ? 'ArrowRight' : 'ArrowLeft';
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); step(event.key === back ? -1 : 1); }
    });
    return viewer;
  }
  function openViewer(at) {
    if (!shown[at]) return;
    closeMenu(false);
    returnRow = rows.get(shown[at])?.main || null;
    show(at);
    viewer.querySelector('.cf-vbar .cf-icon').focus();
  }
  function releaseUrls() { for (const url of objectUrls) URL.revokeObjectURL(url); objectUrls = []; }
  function closeViewer(silent) {
    if (!viewerOpen()) return;
    viewController?.abort(); releaseUrls();
    preview.send('close');
    stage.replaceChildren(); info.replaceChildren();
    const item = viewItem; viewItem = null;
    if (!silent) (rows.get(item)?.li.isConnected ? rows.get(item).main : returnRow)?.focus();
  }
  function step(delta) {
    if (shown.length < 2) return;
    show((index + delta + shown.length) % shown.length);
  }
  const notice = (title, hint, extra) => {
    const box = el('div', 'cf-notice');
    box.append(el('strong', null, title));
    if (hint) box.append(el('span', null, hint));
    if (extra) box.append(extra);
    return box;
  };
  async function show(at) {
    index = at;
    const item = viewItem = shown[at];
    viewController?.abort(); releaseUrls(); viewText = '';
    const controller = viewController = new AbortController(), signal = controller.signal;
    preview.send('open');
    countEl.textContent = (at + 1) + ' / ' + shown.length;
    prevButton.hidden = nextButton.hidden = shown.length < 2;
    const file = !item.ref && !item.missing;
    chatButton.hidden = true; openTabButton.hidden = !(item.ref ? refLink(item) : file && previewKind(item) === 'pdf');
    setLabel(openTabButton, t(item.ref ? 'open_ref' : 'open_tab'));
    copyButton.hidden = true;
    attachButton.hidden = !file || !composer();
    downloadButton.hidden = !file;
    renameButton.hidden = !renamable(item); deleteButton.hidden = !deletable(item);
    paintInfo();
    stage.replaceChildren(notice(t('preview_loading')));
    checkChatLink(item, signal);
    // Who owns a file decides whether it can be renamed or deleted; a chat may not say, so ask once
    // A failed file's reason is only in its record, which a chat doesn't carry
    if (file && (!item.ownerId || (item.status === 'failed' && !item.error))) recordOf(item.fileId, signal).then(record => { if (viewItem === item) { applyRecord(item, record); renameButton.hidden = !renamable(item); deleteButton.hidden = !deletable(item); paintInfo(); } }).catch(() => {});
    try {
      const view = await renderPreview(item, signal);
      if (signal.aborted || viewItem !== item) return;
      stage.replaceChildren(view);
      copyButton.hidden = !viewText;
      preview.send('ready');
      paintInfo();
    } catch (error) {
      if (signal.aborted || viewItem !== item) return;
      preview.send('fail');
      if (error.status === 404) { item.missing = true; const entry = rows.get(item); if (entry) paintRow(entry); downloadButton.hidden = attachButton.hidden = renameButton.hidden = deleteButton.hidden = true; paintInfo(); stage.replaceChildren(notice(t('missing'), t('missing_hint'))); }
      else stage.replaceChildren(notice(t('preview_failed'), '', textButton(t('retry'), () => show(index))));
    }
  }
  const decodeText = bytes => {
    let text = new TextDecoder('utf-8').decode(bytes);
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    return text;
  };
  const looksBinary = bytes => { const sample = bytes.subarray(0, 8000); for (let i = 0; i < sample.length; i++) if (sample[i] === 0) return true; return false; };
  // A media element or picture loads straight from Open WebUI with the sign-in cookie; without one, it falls back to a fetch
  function mediaFrom(item, tag) {
    const node = el(tag, 'cf-media');
    if (tag !== 'img') { node.controls = true; node.preload = 'metadata'; } else node.alt = item.name;
    const learn = facts => { item.facts = {...(item.facts || {}), ...facts}; if (viewItem === item) paintInfo(); };
    if (tag === 'img') node.addEventListener('load', () => { if (node.naturalWidth) learn({width: node.naturalWidth, height: node.naturalHeight}); });
    else node.addEventListener('loadedmetadata', () => { if (Number.isFinite(node.duration) && node.duration > 0) learn({duration: node.duration, ...(tag === 'video' && node.videoWidth ? {width: node.videoWidth, height: node.videoHeight} : {})}); });
    let fellBack = false;
    node.addEventListener('error', async () => {
      if (fellBack || !viewItem || viewItem !== item) return;
      fellBack = true;
      try { const blob = await fetchBlob(item.url, viewController.signal); const url = URL.createObjectURL(blob); objectUrls.push(url); node.src = url; }
      catch { node.replaceWith(notice(t('preview_failed'))); }
    });
    node.src = item.url;
    return node;
  }
  async function renderPreview(item, signal) {
    const mode = previewKind(item);
    if (mode === 'ref') {
      const link = refLink(item);
      return notice(item.ref === 'web' ? item.url : t('ref_' + item.ref), t(link ? 'ref_hint' : 'ref_unavailable'), link ? textButton(t('open_ref'), openElsewhere) : null);
    }
    if (mode === 'missing') return notice(t('missing'), t('missing_hint'));
    if (mode === 'empty') return notice(t('empty_file'));
    if (mode === 'image') return mediaFrom(item, 'img');
    if (mode === 'audio') return mediaFrom(item, 'audio');
    if (mode === 'video') return mediaFrom(item, 'video');
    if (mode === 'pdf') {
      if (item.bytes > PDF_LIMIT) return notice(t('too_large'), '', textButton(t('open_tab'), openElsewhere));
      const blob = await fetchBlob(item.url, signal);
      // Only a real PDF goes to the browser's PDF viewer; anything else named .pdf is treated as unknown
      const magic = new TextDecoder('latin1').decode(new Uint8Array(await blob.slice(0, 1024).arrayBuffer()));
      if (!magic.includes('%PDF-')) return previewExtracted(item, signal, t('not_pdf'));
      const url = URL.createObjectURL(new Blob([blob], {type: 'application/pdf'})); objectUrls.push(url);
      const frame = el('iframe', 'cf-pdf'); frame.title = item.name; frame.src = url + '#view=FitH';
      return frame;
    }
    if (mode === 'text' || mode === 'json' || mode === 'table') {
      const {bytes, truncated} = await readStart(item.url, TEXT_LIMIT, signal);
      if (looksBinary(bytes)) return previewExtracted(item, signal, t('binary'));
      let text = decodeText(bytes);
      if (truncated) text = text.slice(0, Math.max(text.lastIndexOf('\n'), text.length - 2000));
      viewText = text;
      if (mode !== 'table') item.facts = {lines: text ? text.split('\n').length - (text.endsWith('\n') ? 1 : 0) : 0, words: (text.match(/\S+/g) || []).length, chars: [...text].length, more: truncated};
      const wrap = el('div', 'cf-textwrap');
      if (truncated) wrap.append(el('p', 'cf-cut', t('truncated', {size: formatBytes(TEXT_LIMIT)})));
      if (mode === 'table') { wrap.append(tableOf(text, item, truncated)); return wrap; }
      if (mode === 'json' && !truncated) { try { text = JSON.stringify(JSON.parse(text), null, 2); viewText = text; } catch {} }
      const pre = el('pre', 'cf-pre' + (TEXT_EXT.has(extOf(item.name)) && !['txt','text','md','markdown','rst','org','adoc'].includes(extOf(item.name)) || mode === 'json' ? ' cf-code' : ''), text);
      pre.dir = 'auto'; pre.tabIndex = 0;
      wrap.append(pre);
      return wrap;
    }
    if (mode === 'none') return notice(t('no_preview'), t('no_preview_hint'), textButton(t('download'), () => downloadOne(item)));
    return previewExtracted(item, signal);
  }
  // Word, PowerPoint and the like: show the text Open WebUI already pulled out of them
  async function previewExtracted(item, signal, reason) {
    let content = '';
    try { content = String((await api('/api/v1/files/' + encodeURIComponent(item.fileId) + '/data/content', signal))?.content || ''); }
    catch (error) { if (error.status === 404) throw error; }
    if (!content.trim()) {
      if (!item.error && item.status === 'failed') { try { applyRecord(item, await recordOf(item.fileId, signal)); paintInfo(); } catch {} }
      return notice(reason || t('no_preview'), item.status === 'failed' && item.error ? item.error : t('no_preview_hint'), textButton(t('download'), () => downloadOne(item)));
    }
    const limit = 200000, cut = content.length > limit;
    viewText = content;
    const wrap = el('div', 'cf-textwrap');
    wrap.append(el('p', 'cf-cut', reason ? reason + ' · ' + t('extracted') : t('extracted')));
    if (cut) wrap.append(el('p', 'cf-cut', t('truncated', {size: formatBytes(limit)})));
    const pre = el('pre', 'cf-pre', cut ? content.slice(0, limit) : content); pre.dir = 'auto'; pre.tabIndex = 0;
    wrap.append(pre);
    return wrap;
  }
  // RFC 4180 with quoted fields, doubled quotes and line breaks inside quotes; semicolons where the file uses them
  function parseDelimited(text, delimiter, maxRows) {
    const out = []; let row = [], field = '', quoted = false;
    for (let i = 0; i < text.length && out.length < maxRows; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false; }
        else field += c;
      } else if (c === '"' && field === '') quoted = true;
      else if (c === delimiter) { row.push(field); field = ''; }
      else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(field); out.push(row); row = []; field = ''; }
      else field += c;
    }
    if ((field || row.length) && out.length < maxRows) { row.push(field); out.push(row); }
    return out;
  }
  function tableOf(text, item, truncated) {
    const firstLine = text.slice(0, text.indexOf('\n') >>> 0 || text.length);
    const delimiter = extOf(item.name) === 'tsv' || /tab-separated/.test(item.contentType) ? '\t' : (firstLine.split(';').length > firstLine.split(',').length ? ';' : ',');
    const maxRows = 500, maxCols = 50;
    const data = parseDelimited(text, delimiter, maxRows + 1);
    const widest = Math.max(0, ...data.map(row => row.length)), cols = Math.min(maxCols, widest);
    item.facts = {rows: Math.max(0, Math.min(data.length, maxRows) - 1), cols: widest, more: truncated || data.length > maxRows};
    const box = el('div', 'cf-tablebox'); box.tabIndex = 0;
    const table = el('table', 'cf-table');
    data.slice(0, maxRows).forEach((row, r) => {
      const tr = el('tr');
      for (let c = 0; c < cols; c++) { const cell = el(r === 0 ? 'th' : 'td', null, row[c] ?? ''); cell.dir = 'auto'; tr.append(cell); }
      (r === 0 ? (table.tHead || table.createTHead()) : (table.tBodies[0] || table.createTBody())).append(tr);
    });
    box.append(table);
    const wrap = el('div');
    if (data.length > maxRows || data.some(row => row.length > maxCols)) wrap.append(el('p', 'cf-cut', t('table_cut', {rows: maxRows, cols: maxCols})));
    wrap.append(box);
    return wrap;
  }
  const wholeNumber = n => { try { return new Intl.NumberFormat(lang).format(n); } catch { return String(n); } };
  const duration = seconds => { const s = Math.round(seconds), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, rest = String(s % 60).padStart(2, '0'); return h ? h + ':' + String(m).padStart(2, '0') + ':' + rest : m + ':' + rest; };
  function paintInfo() {
    const item = viewItem;
    if (!item) return;
    info.replaceChildren();
    const title = el('h2', 'cf-title'); title.append(nameSpan(null, item.name || item.url || ''));
    info.append(title);
    const facts = [];
    facts.push([t('f_kind'), item.ref ? t('ref_' + item.ref) : t('kind_' + classify(item))]);
    if (!item.ref) {
      if (item.contentType) facts.push([t('f_mime'), item.contentType]);
      const ext = extOf(item.name);
      if (ext) facts.push([t('f_ext'), '.' + ext]);
      if (item.sized) facts.push([t('f_size'), formatBytes(item.bytes) + (item.bytes >= 1024 ? ' (' + t('f_bytes', {n: wholeNumber(item.bytes)}) + ')' : '')]);
      if (item.created) facts.push([t('f_created'), formatDate(item.created, true)]);
      if (item.updated && toMs(item.updated) - toMs(item.created) > 60000) facts.push([t('f_updated'), formatDate(item.updated, true)]);
    }
    if (scope === 'chat' && item.sent) {
      facts.push([t('f_sent'), formatDate(item.sent, true)]);
      if (item.messageId) facts.push([t('f_from'), t(item.fromUser ? 'from_you' : 'from_answer')]);
    }
    const state = statusOf(item);
    if (!item.ref) facts.push([t('f_status'), t('st_' + (state || 'ready'))]);
    if (!item.ref && item.ownerId && me) facts.push([t('f_owner'), t(item.ownerId === me.id ? 'owner_you' : 'owner_other')]);
    // What the preview found inside: lines and words, rows and columns, size of a picture, length of a recording
    const found = item.facts, plus = found?.more ? '+' : '';
    if (found) {
      if (found.rows != null) facts.push([t('f_rows'), wholeNumber(found.rows) + plus], [t('f_cols'), wholeNumber(found.cols)]);
      if (found.lines != null) facts.push([t('f_lines'), wholeNumber(found.lines) + plus], [t('f_words'), wholeNumber(found.words) + plus], [t('f_chars'), wholeNumber(found.chars) + plus]);
      if (found.width) facts.push([t('f_dims'), wholeNumber(found.width) + ' × ' + wholeNumber(found.height)]);
      if (found.duration) facts.push([t('f_duration'), duration(found.duration)]);
    }
    const list = el('dl', 'cf-facts');
    for (const [key, value] of facts) { const dd = el('dd', null, value); dd.dir = 'auto'; list.append(el('dt', null, key), dd); }
    if (item.fileId) {
      const dd = el('dd', 'cf-iddd'), code = el('code', 'cf-id', item.fileId);
      dd.append(code, iconButton(t('copy_id'), 'copy', () => copyToClipboard(item.fileId, t('id_copied')), 'cf-icon cf-mini'));
      list.append(el('dt', null, t('f_id')), dd);
    }
    info.append(list);
    if (state === 'failed' && item.error) { const error = el('p', 'cf-error', item.error); error.dir = 'auto'; info.append(error); }
  }
  async function checkChatLink(item, signal) {
    const chatId = item.chatId;
    if (!chatId || (scope === 'chat' && !item.messageId)) return;
    const ok = await chatIsThere(chatId);
    if (!signal.aborted && viewItem === item) {
      chatButton.hidden = !ok;
      setLabel(chatButton, t(chatId === chatIdFromPath() ? 'show_in_chat' : 'open_in_chat'));
    }
  }
  const navigate = href => { const link = el('a'); link.href = href; document.body.append(link); link.click(); link.remove(); };
  const chatIsThere = chatId => {
    if (!chatExists.has(chatId)) chatExists.set(chatId, request('/api/v1/chats/' + encodeURIComponent(chatId)).then(r => r.ok).catch(() => false));
    return chatExists.get(chatId);
  };
  const showInChat = () => goToChat(viewItem);
  async function goToChat(item) {
    if (!item?.chatId) return;
    if (!(await chatIsThere(item.chatId))) { flash(t('chat_missing')); return; }
    const params = new URLSearchParams(location.search);
    deactivate();
    if (item.chatId === chatIdFromPath() && item.messageId && params.get('message') === item.messageId) {
      document.getElementById('message-' + item.messageId)?.scrollIntoView({block: 'center', behavior: 'smooth'});
      return;
    }
    navigate('/c/' + encodeURIComponent(item.chatId) + (item.messageId ? '?message=' + encodeURIComponent(item.messageId) : ''));
  }
  const refLink = item => ({
    collection: item.refId && '/workspace/knowledge/' + encodeURIComponent(item.refId),
    note: item.refId && '/notes/' + encodeURIComponent(item.refId),
    chat: item.refId && '/c/' + encodeURIComponent(item.refId),
    web: /^https?:\/\//i.test(item.url) ? item.url : ''
  })[item.ref] || '';
  function openElsewhere() {
    const item = viewItem;
    if (!item) return;
    if (item.ref) {
      const link = refLink(item);
      if (!link) return;
      if (item.ref === 'web') { window.open(link, '_blank', 'noopener,noreferrer'); return; }
      deactivate(); navigate(link); return;
    }
    // Open WebUI serves PDFs inline, so its own address opens in the browser's viewer
    window.open(item.url, '_blank', 'noopener');
  }

  /* ---- rename and delete: only files you own ---- */
  const deletable = item => !!(CONFIG.allowDelete && item && item.fileId && !item.missing && me && item.ownerId === me.id);
  const renamable = item => !!(CONFIG.allowRename && item && item.fileId && !item.missing && me && item.ownerId === me.id);
  function buildConfirm() {
    confirmBox = el('dialog', 'cf-modal');
    confirmBox.setAttribute('aria-labelledby', 'cf-confirm-title'); confirmBox.setAttribute('aria-describedby', 'cf-confirm-text');
    return confirmBox;
  }
  function buildRename() {
    renameBox = el('dialog', 'cf-modal');
    renameBox.setAttribute('aria-labelledby', 'cf-rename-title');
    return renameBox;
  }
  // The box names what is about to go, so nothing is deleted by mistake
  function confirmDelete(list) {
    const count = list.length;
    return new Promise(done => {
      const names = el('ul', 'cf-names');
      for (const item of list.slice(0, 5)) { const li = el('li'); li.append(nameSpan(null, item.name || item.url || '')); names.append(li); }
      if (count > 5) names.append(el('li', 'cf-namesmore', t('delete_more', {n: count - 5})));
      const title = el('h3', null, count === 1 ? t('confirm_one') : t('confirm_many', {n: count})); title.id = 'cf-confirm-title';
      const text = el('p', null, t(count === 1 ? 'confirm_text_one' : 'confirm_text_many')); text.id = 'cf-confirm-text';
      const cancel = textButton(t('cancel'), () => confirmBox.close('cancel'));
      const ok = textButton(t('delete'), () => confirmBox.close('delete')); ok.classList.add('cf-danger');
      const actions = el('div', 'cf-actions'); actions.append(cancel, ok);
      confirmBox.replaceChildren(title, names, text, actions);
      confirmBox.returnValue = '';
      confirmBox.addEventListener('close', () => done(confirmBox.returnValue === 'delete'), {once: true});
      confirmBox.showModal(); cancel.focus();
    });
  }
  const validName = name => !/[\\/\x00-\x1f\x7f]/.test(name) && name.length <= 255;
  function renameItem(item) {
    if (!renamable(item) || busy()) return;
    const title = el('h3', null, t('rename_title')); title.id = 'cf-rename-title';
    const label = el('label', 'cf-label', t('rename_label'));
    const input = el('input', 'cf-input'); input.type = 'text'; input.value = item.name; input.dir = 'auto'; input.maxLength = 255; input.spellcheck = false;
    label.append(input);
    const message = el('p', 'cf-hint', t('rename_note')); message.setAttribute('aria-live', 'polite');
    const cancel = textButton(t('cancel'), () => renameBox.close());
    const save = el('button', 'cf-btn cf-primary', t('save')); save.type = 'submit';
    const actions = el('div', 'cf-actions'); actions.append(cancel, save);
    const form = el('form'); form.method = 'dialog'; form.append(title, label, message, actions);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const name = cleanName(input.value).trim();
      if (!name) { message.textContent = t('rename_empty'); message.classList.add('cf-bad'); input.focus(); return; }
      if (!validName(name)) { message.textContent = t('rename_bad'); message.classList.add('cf-bad'); input.focus(); return; }
      if (/^\.+$/.test(name)) { message.textContent = t('rename_dots'); message.classList.add('cf-bad'); input.focus(); return; }
      if (name === item.name) { renameBox.close(); return; }
      save.disabled = true;
      try {
        await api('/api/v1/files/' + encodeURIComponent(item.fileId) + '/rename', null, {method: 'POST', body: JSON.stringify({filename: name})});
        for (const each of [...items, ...library.items]) if (each.fileId === item.fileId) each.name = name;
        records.delete(item.fileId);
        renameBox.close(); flash(t('renamed')); render(); if (viewItem) { paintInfo(); index = Math.max(0, shown.indexOf(viewItem)); countEl.textContent = (index + 1) + ' / ' + shown.length; }
      } catch { save.disabled = false; message.textContent = t('rename_failed'); message.classList.add('cf-bad'); }
    });
    renameBox.replaceChildren(form);
    renameBox.showModal();
    // Select the name without its extension, as file managers do
    const dot = item.name.lastIndexOf('.');
    input.focus(); input.setSelectionRange(0, dot > 0 ? dot : item.name.length);
  }
  async function deleteItems(list) {
    list = list.filter(deletable);
    if (!list.length || busy() || !(await confirmDelete(list)) || !job.send('delete')) return;
    paintSelection();
    const gone = new Set();
    try {
      await pool(list, 4, async item => {
        try {
          const response = await request('/api/v1/files/' + encodeURIComponent(item.fileId), {method: 'DELETE'});
          if (response.ok || response.status === 404) gone.add(item.fileId);
        } catch {}
      });
    } finally { job.send('done'); }
    if (!dialog) return;
    for (const id of gone) records.delete(id);
    const wasViewing = viewerOpen() && viewItem && gone.has(viewItem.fileId);
    const at = index;
    // A chat still mentions a deleted file, so it stays there marked deleted; the library forgets it
    for (const item of items) if (gone.has(item.fileId)) item.missing = true;
    library.items = library.items.filter(item => !gone.has(item.fileId));
    for (const item of [...selected]) if (gone.has(item.fileId)) selected.delete(item);
    const failed = list.length - gone.size;
    flash(failed ? (gone.size ? t('deleted_some', {n: gone.size, failed}) : t(list.length === 1 ? 'delete_failed_one' : 'delete_failed_many')) : (gone.size === 1 ? t('deleted_one') : t('deleted_many', {n: gone.size})));
    if (selecting() && !selected.size) setSelecting(false);
    render();
    if (wasViewing) { if (shown.length) show(Math.min(at, shown.length - 1)); else closeViewer(); }
  }

  /* ---- copy, download, attach ---- */
  async function copyToClipboard(text, done) {
    if (!text) return;
    try { await navigator.clipboard.writeText(text); flash(done); }
    catch {
      const area = el('textarea'); area.value = text; area.style.cssText = 'position:fixed;opacity:0';
      dialog.append(area); area.select();
      try { flash(document.execCommand('copy') ? done : t('copy_failed')); } catch { flash(t('copy_failed')); }
      area.remove();
    }
  }
  const copyText = () => copyToClipboard(viewText, t('text_copied'));
  const safeName = (name, fallback = 'file') => {
    let clean = cleanName(name).replace(/[\x00-\x1f\x7f/\\:*?"<>|]/g, '_').replace(/^[\s.]+|[\s.]+$/g, '');
    if (!clean) clean = fallback;
    if (clean.length > 180) { const ext = extOf(clean); clean = clean.slice(0, 170 - ext.length) + (ext ? '.' + ext : ''); }
    return clean;
  };
  function save(blob, name) {
    const link = el('a'); const url = URL.createObjectURL(blob);
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
  }
  async function downloadOne(item) {
    if (!item || item.ref || item.missing) return;
    // A very large file downloads straight from Open WebUI, with the sign-in cookie, instead of through memory
    if (item.bytes > BLOB_DOWNLOAD_LIMIT) { navigateDownload(item); return; }
    try { save(await fetchBlob(item.url), safeName(item.name)); }
    catch (error) { if (error.status === 404) { item.missing = true; render(); } flash(t('download_failed')); }
  }
  function navigateDownload(item) {
    const link = el('a'); link.href = item.url + '?attachment=true'; link.download = safeName(item.name); link.rel = 'noopener';
    document.body.append(link); link.click(); link.remove();
  }
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
  // Hands the file to the composer's own file input, so Open WebUI's upload and model checks apply as usual
  const attachToComposer = () => attachItem(viewItem);
  async function attachItem(item) {
    if (!pickable(item)) return;
    try {
      const blob = await fetchBlob(item.url);
      const input = composer();
      if (!input) { flash(t('open_chat_to_attach')); return; }
      const transfer = new DataTransfer();
      transfer.items.add(new File([blob], safeName(item.name), {type: item.contentType || blob.type || 'application/octet-stream'}));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
      deactivate();
      document.getElementById('chat-input')?.focus({preventScroll: true});
    } catch { flash(t('attach_failed')); }
  }

  /* ---- zip, stored without compression ---- */
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
  async function downloadZip() {
    if (busy()) return;
    const choosable = shown.filter(pickable);
    const list = selecting() ? choosable.filter(item => selected.has(item)) : choosable;
    if (!list.length) return;
    // A zip without its 64-bit extension holds at most 65,535 entries
    if (list.length > 65535) { flash(t('zip_many', {n: wholeNumber(65535)})); return; }
    const title = (document.querySelector('#chat-container nav h1')?.textContent || document.title || '').replace(/\s*[·|•-]\s*Open WebUI\s*$/i, '').trim();
    // Browsers drop a download name they can't use, so the zip's own name keeps to plain letters and digits
    const ascii = title.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
    const zipName = 'files-' + (scope === 'chat' ? ascii || 'chat' : 'mine') + '.zip';
    if (!job.send('zip')) return;
    const controller = zipController = new AbortController();
    paintSelection();
    const files = [], taken = new Set();
    let total = 0, skipped = 0, stopped = '';
    for (let i = 0; i < list.length && !stopped; i++) {
      const item = list[i];
      say(t('preparing', {i: i + 1, n: list.length}));
      if (item.bytes && total + item.bytes > CONFIG.maxZipMb * 1048576) { stopped = t('zip_stopped', {mb: CONFIG.maxZipMb}); break; }
      try {
        const blob = await fetchBlob(item.url, controller.signal);
        total += blob.size;
        if (total > CONFIG.maxZipMb * 1048576) { stopped = t('zip_stopped', {mb: CONFIG.maxZipMb}); break; }
        files.push({name: uniqueName(safeName(item.name), taken), data: new Uint8Array(await blob.arrayBuffer())});
      } catch { if (controller.signal.aborted) stopped = t('zip_cancelled'); else skipped++; }
    }
    job.send('done'); zipController = null;
    if (!dialog) return;
    if (stopped) { flash(stopped); render(); return; }
    if (!files.length) { flash(t('zip_none')); render(); return; }
    save(makeZip(files), zipName);
    flash(tn('saved', files.length) + (skipped ? t('skipped', {n: skipped}) : ''));
    if (selecting()) setSelecting(false); else render();
  }

  /* ---- the Files tab in the Tabs panel ---- */
  // Found by markers that stayed stable from 0.13.0 to 0.13.3: the tab rows (.panel-tab-strip) and the split-pane
  // groups (data-panel-group). Chat Gallery adds a tab the same way; this one always sits after it.
  const tabBase = 'flex items-center gap-1.5 h-7 rounded-lg text-sm font-normal whitespace-nowrap shrink-0 transition';
  const activeClass = 'bg-gray-100/40 dark:bg-gray-800/25 text-gray-700 dark:text-gray-200';
  const idleClass = 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300';
  const headerClass = 'flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200';
  const barClass = 'flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0';
  // The Files tab: not open, open in front, or open behind another tab
  const tab = machine('tab', 'closed', {closed: {open: 'front'}, front: {hide: 'behind', close: 'closed'}, behind: {show: 'front', close: 'closed'}});
  const isOpen = () => !tab.is('closed'), isFront = () => tab.is('front');
  let solo = false, strip = null, tabEl = null, tabButton = null, homeGroup = null, menuGroup = null;
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
    if (!isFront()) return null;
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
    if (!spot) { if (isFront()) tracking = requestAnimationFrame(place); return; }
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
    const found = isFront() && panel(), want = new Set();
    if (isFront() && solo && !found) { const area = soloArea(); if (area) want.add(area); }
    if (found?.group) {
      const id = found.group.dataset.panelGroup;
      for (const node of document.querySelectorAll('[data-panel-group]')) {
        if (node.dataset.panelGroup === id && node !== found.group && (node.getAttribute('role') === 'tabpanel' || node.id === 'chat-panel-main-chat-view')) want.add(node);
      }
    } else if (found) {
      if (found.content) want.add(found.content);
      if (found.full) { const main = document.getElementById('chat-panel-main-chat-view'); if (main) want.add(main); }
    }
    for (const node of document.querySelectorAll('[data-cf-covered]')) if (!want.has(node)) { node.removeAttribute('data-cf-covered'); if (node.dataset.cfInert) { node.inert = false; delete node.dataset.cfInert; } }
    for (const node of want) if (!node.hasAttribute('data-cf-covered')) { node.setAttribute('data-cf-covered', ''); if (!node.inert) { node.inert = true; node.dataset.cfInert = '1'; } }
  }
  function paintTab() {
    cover();
    if (headerButton) headerButton.setAttribute('aria-pressed', String(isFront()));
    if (!tabEl) return;
    const found = panel();
    const padding = found?.list?.querySelector('[data-panel-tab]')?.classList.contains('ps-2.5') ? 'ps-2.5 pe-2' : 'px-2.5';
    const wanted = tabBase + ' ' + padding + ' ' + (isFront() ? activeClass : idleClass);
    if (tabEl.className !== wanted) tabEl.className = wanted;
    if (tabButton.getAttribute('aria-selected') !== String(isFront())) tabButton.setAttribute('aria-selected', String(isFront()));
    tabButton.tabIndex = isFront() ? 0 : -1;
    for (const list of document.querySelectorAll('.panel-tab-strip')) list.classList.toggle('cf-dim', isFront() && list === found?.list);
    const alone = solo && !found;
    if (dialog) { dialog.classList.toggle('cf-solo', alone); soloClose.hidden = !alone; }
    track();
  }
  function buildTab() {
    strip = el('div', 'flex items-center shrink-0'); strip.id = 'owui-chat-files-tabs';
    strip.setAttribute('role', 'tablist'); strip.setAttribute('aria-label', t('files'));
    tabEl = el('div');
    tabButton = el('button', 'flex h-full items-center gap-1.5 min-w-0'); tabButton.type = 'button'; tabButton.id = 'owui-chat-files-tab';
    tabButton.setAttribute('role', 'tab'); tabButton.setAttribute('aria-controls', 'owui-chat-files');
    const icon = svg('files'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14'); icon.classList.add('shrink-0');
    tabButton.append(icon, el('span', 'max-w-30 overflow-hidden text-ellipsis', t('files')));
    tabButton.addEventListener('click', () => activate());
    const close = el('button', 'flex items-center justify-center w-4 h-4 rounded text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white');
    close.type = 'button'; setLabel(close, t('close_files'));
    const x = svg('close'); x.setAttribute('width', '12'); x.setAttribute('height', '12'); close.append(x);
    close.addEventListener('click', closeTab);
    tabEl.append(tabButton, close); strip.append(tabEl);
  }
  const announce = () => dispatchEvent(new CustomEvent('owui-panel-tab-front', {detail: {owner: 'chat-files'}}));
  const tellState = () => dispatchEvent(new CustomEvent('owui-chat-files-state', {detail: {open: isOpen()}}));
  function activate() {
    if (!isOpen()) return;
    const back = tab.send('show');
    lastNative = nativeSignature(panel()?.list); announce(); paintTab();
    if (tabEl.isConnected) tabEl.scrollIntoView({block: 'nearest', inline: 'nearest'});
    // Back in front after a while: the chat may have new files
    if (back && scope === 'chat' && chatLoadedAt && !chatLoad.is('loading') && Date.now() - chatLoadedAt > 3000 && !viewerOpen()) loadChat(true);
  }
  function deactivate() { if (tab.send('hide')) { closeMenu(false); paintTab(); } }
  function teardown() { closeMenu(false); dialog?.remove(); strip?.remove(); dialog = strip = tabEl = tabButton = null; rows.clear(); groupNodes.clear(); }
  function openTab() {
    if (dialog && tab.is('closed') && builtLang !== language()) { teardown(); opener?.remove(); headerButton?.remove(); opener = headerButton = null; }
    if (!dialog) build();
    if (menuGroup) homeGroup = menuGroup;
    if (!strip) buildTab();
    const fresh = tab.is('closed');
    solo = !panel();
    if (fresh) {
      search.value = ''; kind = 'all'; order.chat = 'oldest'; order.library = 'newest';
      library = freshLibrary();
      chatExists.clear();
      if (!me) api('/api/v1/auths/').then(user => { me = user; if (isOpen() && dialog) { paintSelection(); if (viewItem) { renameButton.hidden = !renamable(viewItem); deleteButton.hidden = !deletable(viewItem); paintInfo(); } } }).catch(() => {});
    }
    if (fresh) tab.send('open');
    schedule(); activate(); tellState();
    if (fresh) { const chatId = chatIdFromPath(); tabChat.hidden = !chatId; setScope(chatId ? 'chat' : 'library'); }
    setTimeout(() => { if (isOpen() && dialog) (tabChat.hidden ? tabLibrary : scope === 'chat' ? tabChat : tabLibrary).focus({preventScroll: true}); });
  }
  function closeTab() {
    if (!dialog) return;
    collectController?.abort(); library.controller.abort(); zipController?.abort(); closeViewer(true); closeMenu(false); setSelecting(false);
    records.clear(); items = []; library = freshLibrary('done'); forgetRows(); listEl.replaceChildren();
    const found = panel(), wasSolo = solo && !found;
    tab.send('close'); solo = false; strip?.remove(); paintTab(); dialog.hidden = true;
    if (found?.list) (found.list.querySelector('[role="tab"][aria-selected="true"]') || plusOf(found.list))?.focus({preventScroll: true});
    else if (wasSolo) headerButton?.focus({preventScroll: true});
    tellState();
  }
  // Where the tab row has a + menu, Files is chosen from that menu and needs no button of its own. The button is
  // the way in where there is no such menu: Open WebUI's classic top bar, phones, accounts without the Tabs panel.
  // With Chat Gallery's button there too, Gallery's stays nearest the edge and this one sits beside it.
  function syncHeader(found) {
    let host = null, inBar = false;
    const menuThere = !!(found?.list && plusOf(found.list));
    if (CONFIG.headerButton && isChatRoute() && (!menuThere || CONFIG.buttonBesideMenu)) {
      const group = [...document.querySelectorAll('#chat-container nav .flex.flex-none.items-center.self-center')].pop();
      if (group && group.getClientRects().length) host = group;
      else if (found?.lists?.length) { host = found.lists[0].parentElement; inBar = true; }
    }
    if (!host) { headerButton?.remove(); return; }
    if (!headerButton) {
      lang = language();
      headerButton = iconButton(t('files'), 'files', () => {
        if (isFront()) { deactivate(); return; }
        if (isOpen()) { solo = !panel(); activate(); return; }
        menuGroup = headerButton.closest('[data-panel-group]')?.dataset.panelGroup ?? null; openTab();
      }, headerClass);
      headerButton.id = 'owui-chat-files-header'; headerButton.setAttribute('aria-pressed', 'false');
    }
    const wantedClass = inBar ? barClass : headerClass;
    if (headerButton.className !== wantedClass) headerButton.className = wantedClass;
    const gallery = document.getElementById('owui-chat-gallery-header');
    const besideGallery = gallery?.parentElement === host ? gallery : null;
    if (inBar) {
      const next = headerButton.nextElementSibling;
      if (headerButton.parentElement !== host || (next && next !== besideGallery)) besideGallery ? host.insertBefore(headerButton, besideGallery) : host.append(headerButton);
    } else {
      const previous = headerButton.previousElementSibling;
      if (headerButton.parentElement !== host || (previous && previous !== besideGallery)) besideGallery ? besideGallery.after(headerButton) : host.prepend(headerButton);
    }
  }
  function sync() {
    syncPending = false;
    const found = panel();
    if (lastPath === null) lastPath = location.pathname;
    if (location.pathname !== lastPath) {
      lastPath = location.pathname; keepUntil = Date.now() + 1500;
      if (isOpen() && dialog) { chatExists.clear(); const chatId = chatIdFromPath(); tabChat.hidden = !chatId; if (!chatId && scope === 'chat') { closeViewer(true); setScope('library'); } else if (scope === 'chat') { closeViewer(true); setSelecting(false); loadChat(); } }
    }
    syncHeader(found);
    const open = found?.lists.find(list => plusOf(list)?.getAttribute('aria-expanded') === 'true');
    const menu = open && [...document.querySelectorAll('body > [role="menu"]')].pop()?.querySelector('.app-dropdown-menu');
    if (menu) {
      menuGroup = open.closest('[data-panel-group]')?.dataset.panelGroup ?? null;
      if (!opener) {
        lang = language();
        opener = el('button'); opener.type = 'button'; opener.id = 'owui-chat-files-button';
        const icon = svg('files'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14');
        opener.append(icon, t('files'));
        opener.addEventListener('click', openTab);
      }
      if (opener.parentElement !== menu) menu.append(opener);
    } else opener?.remove();
    if (!isOpen() || !found || !strip) { if (isOpen()) paintTab(); else track(); return; }
    if (found.group && found.group.dataset.panelGroup !== homeGroup) homeGroup = found.group.dataset.panelGroup;
    let anchor = found.list;
    if (anchor.nextElementSibling?.id === 'owui-chat-workspace-tabs') anchor = anchor.nextElementSibling;
    if (anchor.nextElementSibling?.id === 'owui-chat-gallery-tabs') anchor = anchor.nextElementSibling;
    const moved = strip.previousElementSibling !== anchor;
    if (moved) anchor.after(strip);
    const signature = nativeSignature(found.list), groups = found.lists.length;
    if (isFront() && !moved && signature !== lastNative && !(groups !== lastGroups && lastGroups !== -1) && Date.now() > keepUntil) deactivate();
    lastNative = signature; lastGroups = groups;
    paintTab();
  }
  function relang() {
    lang = language();
    opener?.remove(); headerButton?.remove(); opener = headerButton = null;
    if (!dialog) { schedule(); return; }
    if (tab.is('closed')) { teardown(); schedule(); return; }
    const keep = {scope, query: search.value, items, library, kind};
    closeViewer(true); setSelecting(false); teardown();
    build(); buildTab();
    items = keep.items; library = keep.library; kind = keep.kind; search.value = keep.query;
    tabChat.hidden = !chatIdFromPath();
    setScope(keep.scope, false);
    lastNative = ''; paintTab(); schedule();
  }
  let syncPending = false;
  const schedule = () => { if (!syncPending) { syncPending = true; requestAnimationFrame(sync); } };
  const start = () => {
    const style = document.createElement('style'); style.textContent = __CSS__; document.head.append(style);
    new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-selected', 'aria-expanded']});
    addEventListener('owui-panel-tab-front', event => { if (event.detail?.owner !== 'chat-files') deactivate(); });
    addEventListener('owui-chat-files-open', () => { menuGroup = null; openTab(); });
    document.documentElement.dataset.chatFiles = '1';
    document.addEventListener('click', event => {
      const clicked = event.target.closest?.('#controls-container .panel-tab-strip [role="tab"]');
      if (clicked && clicked.closest('.panel-tab-strip') === panel()?.list) deactivate();
      const choice = event.target.closest?.('.app-dropdown-menu > button');
      if (choice && choice !== opener && opener?.parentElement === choice.parentElement && (menuGroup ?? null) === (homeGroup ?? menuGroup ?? null)) deactivate();
    }, true);
    addEventListener('popstate', schedule);
    // Open WebUI sets <html lang> when someone changes language; everything is redrawn in the new one at once
    let shownLang = language();
    new MutationObserver(() => { const now = language(); if (now !== shownLang) { shownLang = now; relang(); } }).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
    sync();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
"""


class Event:
    class Valves(BaseModel):
        max_zip_mb: int = Field(
            default=300,
            ge=1,
            le=2000,
            description="Largest zip, in MB, that Download builds in the browser. A zip is held in memory until it is saved, so keep this low for phones.",
        )
        include_images: Literal["auto", "yes", "no"] = Field(
            default="auto",
            description="Whether images are listed too. 'auto' leaves them out when Chat Gallery is enabled on this server, which shows them better.",
        )
        show_references: bool = Field(
            default=True,
            description="In This chat, also list the knowledge bases, notes, chats and web pages the chat refers to.",
        )
        allow_rename: bool = Field(
            default=True,
            description="Let people rename their own files from the Files tab.",
        )
        allow_delete: bool = Field(
            default=True,
            description="Let people delete their own files. Deleting removes the file from Open WebUI, from every chat that shows it and from any knowledge base it was added to.",
        )
        header_button: bool = Field(
            default=True,
            description="Add a Files button where the chat has no Tabs panel + menu to choose Files from: Open WebUI's classic top bar, phones, and accounts without the Tabs panel. Where the + menu is there, Files is in it.",
        )
        button_beside_menu: bool = Field(
            default=False,
            description="Also show the Files button beside the Tabs panel's + menu, as well as in it.",
        )
        default_view: Literal["list", "grid"] = Field(
            default="list",
            description="How files are laid out until a person chooses for themselves: a list with columns, or a grid of cards. Each person's choice is kept in their browser.",
        )
        thumbnails: bool = Field(
            default=True,
            description="Show small pictures of images (PNG, JPEG, GIF, WebP and similar, up to 8 MB) in the list and the grid, when images are listed.",
        )
        language: Literal["auto", "en", "fr"] = Field(
            default="auto",
            description="Language of the Files tab. 'auto' follows each person's Open WebUI language, and uses English where there is no translation.",
        )

    LOADER_BLOCK_START = "// owui-chat-files:start"
    LOADER_BLOCK_END = "// owui-chat-files:end"
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
            "maxZipMb": int(valves.max_zip_mb),
            "includeImages": valves.include_images,
            "showReferences": bool(valves.show_references),
            "allowRename": bool(valves.allow_rename),
            "allowDelete": bool(valves.allow_delete),
            "headerButton": bool(valves.header_button),
            "buttonBesideMenu": bool(valves.button_beside_menu),
            "defaultView": valves.default_view,
            "thumbnails": bool(valves.thumbnails),
            "language": valves.language,
        }

    def _loader_fragment(self) -> str:
        # Runs on the event loop for every page load: rebuild only when a valve changed.
        key = _json.dumps(self._config(), sort_keys=True)
        if key != self._fragment_key:
            script = LOADER_SCRIPT.replace("__CSS__", _json.dumps(FILES_CSS)).replace(
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
