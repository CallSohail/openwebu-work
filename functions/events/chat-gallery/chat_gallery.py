"""
title: Chat Gallery
description: See every image in a chat, and every image you've uploaded or generated, in one gallery with zoom, generation details, filters, copy, download, zip and a jump back to the message.
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://buymeacoffee.com/iamg30
version: 1.1.0
license: MIT
required_open_webui_version: 0.13.0
"""

# Chat Gallery 1.0.0 is by @G30 (MIT). This 1.1.0 build is a maintained fork:
# see CHANGELOG.md beside this file for what changed. The two protocol blocks
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


CSP_CSS = r"""
#owui-chat-gallery-button svg{pointer-events:none}
.cg-dialog{--cg-surface:#fff;--cg-text:var(--color-gray-800,#262626);--cg-muted:var(--color-gray-500,#737373);--cg-soft:var(--color-gray-50,#f9f9f9);--cg-hover:var(--color-gray-100,#ececec);--cg-line:var(--color-gray-100,#ececec);--cg-tile:var(--color-gray-100,#ececec);--cg-primary:#000;--cg-on-primary:#fff;--cg-accent:var(--color-blue-500,#3b82f6);--cg-danger:var(--color-red-600,#dc2626)}
.dark .cg-dialog{--cg-surface:var(--color-gray-900,#171717);--cg-text:var(--color-gray-100,#ececec);--cg-muted:var(--color-gray-400,#b4b4b4);--cg-soft:var(--color-gray-850,#262626);--cg-hover:var(--color-gray-800,#333);--cg-line:var(--color-gray-850,#262626);--cg-tile:var(--color-gray-850,#262626);--cg-primary:#fff;--cg-on-primary:#000;color-scheme:dark}
.cg-dialog{position:fixed;left:0;top:0;z-index:45;box-sizing:border-box;display:flex;flex-direction:column;container:cg/inline-size;margin:0;padding:0;border:0;background:transparent;color:var(--cg-text);overflow:hidden;overscroll-behavior:contain;font-family:inherit;font-size:.875rem;line-height:1.4}
.cg-dialog[hidden]{display:none}
.cg-dialog.cg-solo{background:var(--cg-surface)}
[data-cg-covered]{opacity:0!important;pointer-events:none!important}
.cg-viewing>.cg-head{display:none}
.cg-viewing .cg-scroll,.cg-viewing .cg-foot{visibility:hidden}
.cg-viewing .cg-search{display:none}
.panel-tab-strip.cg-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){background:transparent!important;color:var(--color-gray-500,#6b7280)!important}
.dark .panel-tab-strip.cg-dim > :is([data-panel-tab], :has(> #chat-panel-main-chat)){color:var(--color-gray-400,#9ca3af)!important}
#owui-chat-gallery-tabs{scrollbar-width:none}
#owui-chat-gallery-tabs[hidden]{display:none}
#owui-chat-gallery-header svg{width:1.125rem;height:1.125rem;pointer-events:none}
#owui-chat-gallery-header.w-7 svg{width:.875rem;height:.875rem}
#owui-chat-gallery-header[aria-pressed=true]{color:var(--color-gray-700,#4e4e4e)}
.dark #owui-chat-gallery-header[aria-pressed=true]{color:var(--color-gray-200,#e3e3e3)}
@media(prefers-reduced-motion:reduce){.cg-grid img{transition:none!important}}
.cg-dialog *{box-sizing:border-box}
.cg-dialog button,.cg-dialog select{font:inherit;color:inherit}
.cg-dialog :focus-visible{outline:2px solid var(--cg-accent);outline-offset:2px}
.cg-head{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;padding:1rem 1.25rem .75rem}
.cg-tabs{display:flex;gap:2px;padding:2px;border-radius:.75rem;background:var(--cg-soft)}
.cg-tab{border:0;background:transparent;padding:.25rem .75rem;border-radius:.625rem;cursor:pointer;color:var(--cg-muted);white-space:nowrap}
.cg-tab[aria-selected=true]{background:var(--cg-surface);color:var(--cg-text);box-shadow:0 1px 2px rgb(0 0 0/.08)}
.cg-tab[hidden]{display:none}
.cg-spacer{flex:1}
.cg-search{width:min(14rem,100%);height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--cg-soft);color:inherit;font:inherit;font-size:.875rem;outline:none}
.cg-search::placeholder{color:var(--cg-muted)}
.cg-select{height:2rem;max-width:11rem;padding:0 .5rem;border:0;border-radius:.75rem;background:var(--cg-soft);cursor:pointer}
.cg-select[hidden]{display:none}
.cg-btn{display:inline-flex;align-items:center;justify-content:center;gap:.375rem;height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--cg-soft);cursor:pointer;white-space:nowrap}
.cg-btn:hover{background:var(--cg-hover)}
.cg-btn:disabled{opacity:.5;cursor:default}
.cg-btn[aria-pressed=true]{background:var(--cg-primary);color:var(--cg-on-primary)}
.cg-btn.cg-danger{background:var(--cg-danger);color:#fff}
.cg-btn.cg-danger:hover{background:var(--cg-danger);filter:brightness(1.08)}
.cg-confirm{box-sizing:border-box;width:min(24rem,calc(100vw - 2rem));margin:auto;padding:1.25rem;border:1px solid var(--cg-line);border-radius:1.25rem;background:var(--cg-surface);color:var(--cg-text);box-shadow:0 20px 40px -12px rgb(0 0 0/.35);font-size:.875rem;line-height:1.45}
.cg-confirm::backdrop{background:rgb(0 0 0/.35)}
.cg-confirm h3{margin:0 0 .5rem;font-size:1rem;font-weight:600}
.cg-confirm p{margin:0 0 1rem;color:var(--cg-muted)}
.cg-confirm-actions{display:flex;justify-content:flex-end;gap:.5rem}
.cg-icon{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;border:0;border-radius:.75rem;background:transparent;cursor:pointer;color:var(--cg-muted)}
.cg-icon:hover{background:var(--cg-hover);color:var(--cg-text)}
.cg-icon:disabled{opacity:.4;cursor:default}
.cg-icon svg,.cg-btn svg{width:1.125rem;height:1.125rem}
.cg-icon[hidden],.cg-btn[hidden]{display:none}
.cg-body{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
.cg-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:0 1.25rem 1rem}
.cg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(9.5rem,1fr));gap:.5rem;margin:0;padding:0;list-style:none}
.cg-grid li{position:relative;margin:0}
.cg-grid li:not(.cg-empty){content-visibility:auto;contain-intrinsic-size:auto 9.5rem}
.cg-tile{position:relative;display:block;width:100%;aspect-ratio:1;padding:0;border:0;border-radius:.75rem;overflow:hidden;background:var(--cg-tile);cursor:pointer}
.cg-tile img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform .15s;-webkit-touch-callout:none;-webkit-user-drag:none}
.cg-tile img:not([src]){visibility:hidden}
.cg-tile:hover img{transform:scale(1.04)}
.cg-tile.cg-broken::after{content:attr(data-broken);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:.5rem;font-size:.75rem;color:var(--cg-muted);text-align:center}
.cg-num{position:absolute;left:.375rem;bottom:.375rem;padding:0 .375rem;border-radius:.375rem;background:rgb(0 0 0/.6);color:#fff;font-size:.6875rem;pointer-events:none}
.cg-check{display:none;position:absolute;right:.375rem;top:.375rem;width:1.375rem;height:1.375rem;border:1.5px solid #fff;border-radius:50%;background:rgb(0 0 0/.35);pointer-events:none}
.cg-check svg{width:100%;height:100%;color:#fff;opacity:0}
.cg-selecting .cg-check{display:block}
.cg-tile[aria-pressed=true] .cg-check{background:var(--cg-accent);border-color:var(--cg-accent)}
.cg-tile[aria-pressed=true] .cg-check svg{opacity:1}
.cg-tile[aria-pressed=true] img{opacity:.6}
.cg-empty{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.25rem;min-height:12rem;padding:2rem;color:var(--cg-muted);text-align:center}
.cg-empty strong{color:var(--cg-text);font-weight:500}
.cg-empty .cg-btn{margin-top:.5rem;color:var(--cg-text)}
.cg-sentinel{height:1px}
.cg-foot{display:flex;align-items:center;gap:.5rem;min-height:2.5rem;padding:.25rem 1.25rem .75rem;color:var(--cg-muted);font-size:.8125rem}
.cg-foot .cg-btn{height:1.625rem;padding:0 .5rem;font-size:.75rem;color:var(--cg-text)}
.cg-viewer{position:absolute;inset:0;z-index:2;display:none;grid-template-columns:minmax(0,1fr) 20rem;grid-template-rows:auto minmax(0,1fr);background:transparent}
.cg-viewer:fullscreen{background:#000}
.cg-viewer.cg-open{display:grid}
.cg-vbar{grid-column:1/-1;display:flex;align-items:center;gap:.25rem;padding:.75rem 1rem;border-bottom:1px solid var(--cg-line)}
.cg-count{margin:0 .5rem;color:var(--cg-muted);font-variant-numeric:tabular-nums}
.cg-stage:focus{outline:none}
.cg-stage{position:relative;min-width:0;min-height:0;overflow:hidden;display:flex;align-items:center;justify-content:center;background:transparent;touch-action:none;user-select:none;-webkit-user-select:none}
.cg-stage img{max-width:100%;max-height:100%;object-fit:contain;transform-origin:center;cursor:zoom-in;-webkit-user-drag:none;will-change:transform}
.cg-stage.cg-zoomed img{cursor:grab}
.cg-stage.cg-loading img{opacity:.35}
.cg-nav{position:absolute;top:50%;translate:0 -50%;width:2.5rem;height:2.5rem;border:0;border-radius:50%;background:rgb(0 0 0/.45);color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center}
.cg-nav:hover{background:rgb(0 0 0/.65)}
.cg-nav svg{width:1.25rem;height:1.25rem}
.cg-prev{left:.75rem}.cg-next{right:.75rem}
.cg-nav[hidden]{display:none}
.cg-info{min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:1rem 1.25rem;border-left:1px solid var(--cg-line);display:flex;flex-direction:column;gap:1rem}
.cg-info h3{margin:0 0 .375rem;font-size:.75rem;font-weight:500;color:var(--cg-muted);text-transform:uppercase;letter-spacing:.03em;display:flex;align-items:center;justify-content:space-between;gap:.5rem}
.cg-info h3 .cg-btn{height:1.625rem;padding:0 .5rem;font-size:.75rem;text-transform:none;letter-spacing:0;color:var(--cg-text)}
.cg-prompt{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;max-height:14rem;overflow-y:auto}
.cg-negative{color:var(--cg-muted)}
.cg-facts{display:grid;grid-template-columns:auto 1fr;gap:.25rem .75rem;margin:0}
.cg-facts dt{color:var(--cg-muted)}
.cg-facts dd{margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.cg-section[hidden]{display:none}
.cg-viewer:fullscreen{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr)}
.cg-viewer:fullscreen .cg-info{display:none}
.cg-viewer:fullscreen .cg-vbar{position:absolute;inset:0 0 auto;z-index:1;border:0;background:linear-gradient(rgb(0 0 0/.55),transparent);color:#fff}
.cg-viewer:fullscreen .cg-vbar .cg-icon,.cg-viewer:fullscreen .cg-count{color:#fff}
.cg-viewer:fullscreen .cg-stage{grid-row:1/-1;background:#000}
.cg-toast{position:absolute;left:50%;bottom:1rem;translate:-50% 0;z-index:3;max-width:calc(100% - 2rem);padding:.5rem .875rem;border-radius:.75rem;background:var(--cg-primary);color:var(--cg-on-primary);font-size:.8125rem;pointer-events:none;opacity:0;transition:opacity .15s}
.cg-toast.cg-show{opacity:1}
.cg-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@container cg (max-width:760px){
.cg-head{padding:.75rem .75rem .5rem}
.cg-search{order:9;flex:1;width:auto;min-width:8rem}
.cg-scroll{padding:0 .75rem .75rem}
.cg-grid{grid-template-columns:repeat(auto-fill,minmax(6.5rem,1fr));gap:.375rem}
.cg-grid li:not(.cg-empty){contain-intrinsic-size:auto 6.5rem}
.cg-foot{padding:.25rem .75rem .75rem}
.cg-viewer{grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(40%,1fr) minmax(0,auto)}
.cg-vbar{padding:.5rem}
.cg-info{border-left:0;border-top:1px solid var(--cg-line);padding:.75rem}
.cg-nav{display:none}
}
"""

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiChatGallery) return;
  window.__owuiChatGallery = true;
  const CONFIG = __CONFIG__;
  const MAX_ZIP_MB = CONFIG.maxZipMb;
  const ALL_IMAGES = CONFIG.library === 'all';
  // Every file is listed to find the images among them, so pages are larger than when only generated images are asked for
  const PAGE_SIZE = ALL_IMAGES ? 200 : 48;
  const DRY_PAGES = 5;
  const FILE_RE = /\/api\/v1\/files\/([0-9a-fA-F-]{36})\/content/g;
  const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
  const MD_IMG_RE = /!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g;
  const HTML_IMG_RE = /<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi;
  const IMG_EXT_RE = /\.(png|jpe?g|gif|webp|bmp|avif)(?:[?#]|$)/i;
  const SKIP_DATA = new Set(['prompt','negative_prompt','model','chat_id','message_id','channel_id','session_id','terminal_id','workflow','width','height','n','size','info','instances','contents','response_format','image','images','output_format','content','user_id','file_hash']);

  /* ---- words, in the language Open WebUI is set to ---- */
  const STRINGS = {
    en: {
      gallery: 'Gallery', close_gallery: 'Close Gallery', images_to_show: 'Images to show', this_chat: 'This chat', all_generated: 'All generated', my_images: 'My images',
      search: 'Search images', search_label: 'Search prompts, models and file names', select: 'Select', cancel: 'Cancel', select_all: 'Select all', select_none: 'Select none',
      download_all: 'Download all', download: 'Download', download_n: 'Download {n}', delete: 'Delete', delete_n: 'Delete {n}', images: 'Images',
      kind: 'Kind of image', kind_all: 'All kinds', kind_generated: 'Generated', kind_uploaded: 'Uploaded', kind_other: 'In replies and linked',
      newest_first: 'Newest first. Show oldest first', oldest_first: 'Oldest first. Show newest first', refresh: 'Refresh',
      image_n: 'Image {n}: {label}', image: 'Image', unavailable: 'Image unavailable',
      none_match: 'No matching images', none_match_hint: 'Try other words, another kind, or clear the search.',
      no_chat: 'No chat open', no_chat_hint: 'Open a chat to see its images, or look through all of your images.',
      chat_failed: 'Couldn’t show this chat’s images', chat_empty: 'No images in this chat yet', chat_empty_hint: 'Uploaded, generated and linked images appear here.',
      library_failed: 'Couldn’t load your images', library_failed_hint: 'Check your connection, then try again.', retry: 'Try again',
      generated_empty: 'No generated images yet', generated_empty_hint: 'Images you generate in any chat appear here.',
      library_empty: 'No images yet', library_empty_hint: 'Images you upload or generate in any chat appear here.',
      err_access: 'You no longer have access to this chat.', err_unsaved: 'This chat isn’t saved yet.', err_generic: 'Couldn’t load this chat’s images. Try again in a moment.',
      collecting: 'Collecting images…', loading: 'Loading…', loading_more: '{n} images so far, loading more…', so_far: ' so far', of: '{shown} of {total}',
      count_one: '{n} image', count_other: '{n} images', linked_hidden_one: '{n} linked image hidden', linked_hidden_other: '{n} linked images hidden',
      keep_looking: 'Keep looking', paused: 'Paused after {n} files without an image.',
      viewer: 'Image viewer', back: 'Back to gallery', show_in_chat: 'Show in chat', open_in_chat: 'Open in chat', attach: 'Attach to message', copy_image: 'Copy image',
      delete_image: 'Delete image', full_screen: 'Full screen', previous: 'Previous image', next: 'Next image',
      prompt: 'Prompt', description: 'Description', negative: 'Negative prompt', details: 'Details', copy: 'Copy', prompt_copied: 'Prompt copied',
      f_model: 'Model', f_dimensions: 'Dimensions', f_size: 'Size', f_bytes: 'File size', f_created: 'Created', f_source: 'Source', f_name: 'File name', f_note: 'Note', file_deleted: 'The file was deleted',
      src_generated: 'Generated', src_uploaded: 'Uploaded', src_reply: 'In reply', src_linked: 'Linked',
      confirm_one: 'Delete this image?', confirm_many: 'Delete {n} images?',
      confirm_text_one: 'It is removed from Open WebUI, and from every chat that shows it. This can’t be undone.',
      confirm_text_many: 'They are removed from Open WebUI, and from every chat that shows them. This can’t be undone.',
      deleted_one: 'Image deleted', deleted_many: '{n} images deleted', deleted_some: '{n} deleted, {failed} couldn’t be deleted', delete_failed_one: 'Couldn’t delete the image', delete_failed_many: 'Couldn’t delete the images',
      no_fullscreen: 'Full screen isn’t available here', copy_failed: 'Couldn’t copy', image_copied: 'Image copied', copy_hosted: 'This image is hosted elsewhere and can’t be copied', copy_image_failed: 'Couldn’t copy the image',
      download_failed: 'Couldn’t download the image', open_chat_to_attach: 'Open a chat to attach images', attach_hosted: 'This image is hosted elsewhere and can’t be attached', attach_failed: 'Couldn’t attach the image',
      preparing: 'Preparing {i} of {n}…', zip_stopped: 'Stopped at {mb} MB. Select fewer images.', zip_none: 'None of these images could be downloaded', zip_cancelled: 'Download cancelled',
      saved_one: '{n} image saved', saved_other: '{n} images saved', skipped: ', {n} skipped',
      z_prompt: 'Prompt', z_negative: 'Negative prompt', z_model: 'Model', z_created: 'Created'
    },
    fr: {
      gallery: 'Galerie', close_gallery: 'Fermer la galerie', images_to_show: 'Images à afficher', this_chat: 'Cette conversation', all_generated: 'Images générées', my_images: 'Mes images',
      search: 'Rechercher', search_label: 'Rechercher dans les prompts, les modèles et les noms de fichier', select: 'Sélectionner', cancel: 'Annuler', select_all: 'Tout sélectionner', select_none: 'Tout désélectionner',
      download_all: 'Tout télécharger', download: 'Télécharger', download_n: 'Télécharger {n}', delete: 'Supprimer', delete_n: 'Supprimer {n}', images: 'Images',
      kind: 'Type d’image', kind_all: 'Tous les types', kind_generated: 'Générées', kind_uploaded: 'Importées', kind_other: 'Dans les réponses et liées',
      newest_first: 'Plus récentes d’abord. Afficher les plus anciennes d’abord', oldest_first: 'Plus anciennes d’abord. Afficher les plus récentes d’abord', refresh: 'Actualiser',
      image_n: 'Image {n} : {label}', image: 'Image', unavailable: 'Image indisponible',
      none_match: 'Aucune image correspondante', none_match_hint: 'Essayez d’autres mots, un autre type, ou effacez la recherche.',
      no_chat: 'Aucune conversation ouverte', no_chat_hint: 'Ouvrez une conversation pour voir ses images, ou parcourez toutes vos images.',
      chat_failed: 'Impossible d’afficher les images de cette conversation', chat_empty: 'Pas encore d’image dans cette conversation', chat_empty_hint: 'Les images importées, générées et liées apparaissent ici.',
      library_failed: 'Impossible de charger vos images', library_failed_hint: 'Vérifiez votre connexion, puis réessayez.', retry: 'Réessayer',
      generated_empty: 'Pas encore d’image générée', generated_empty_hint: 'Les images que vous générez dans vos conversations apparaissent ici.',
      library_empty: 'Pas encore d’image', library_empty_hint: 'Les images que vous importez ou générez dans vos conversations apparaissent ici.',
      err_access: 'Vous n’avez plus accès à cette conversation.', err_unsaved: 'Cette conversation n’est pas encore enregistrée.', err_generic: 'Impossible de charger les images de cette conversation. Réessayez dans un instant.',
      collecting: 'Recherche des images…', loading: 'Chargement…', loading_more: '{n} images pour l’instant, chargement…', so_far: ' pour l’instant', of: '{shown} sur {total}',
      count_one: '{n} image', count_other: '{n} images', linked_hidden_one: '{n} image liée masquée', linked_hidden_other: '{n} images liées masquées',
      keep_looking: 'Continuer la recherche', paused: 'En pause après {n} fichiers sans image.',
      viewer: 'Visionneuse d’images', back: 'Retour à la galerie', show_in_chat: 'Afficher dans la conversation', open_in_chat: 'Ouvrir dans la conversation', attach: 'Joindre au message', copy_image: 'Copier l’image',
      delete_image: 'Supprimer l’image', full_screen: 'Plein écran', previous: 'Image précédente', next: 'Image suivante',
      prompt: 'Prompt', description: 'Description', negative: 'Prompt négatif', details: 'Détails', copy: 'Copier', prompt_copied: 'Prompt copié',
      f_model: 'Modèle', f_dimensions: 'Dimensions', f_size: 'Taille', f_bytes: 'Poids', f_created: 'Créée le', f_source: 'Source', f_name: 'Nom du fichier', f_note: 'Note', file_deleted: 'Le fichier a été supprimé',
      src_generated: 'Générée', src_uploaded: 'Importée', src_reply: 'Dans une réponse', src_linked: 'Liée',
      confirm_one: 'Supprimer cette image ?', confirm_many: 'Supprimer {n} images ?',
      confirm_text_one: 'Elle est supprimée d’Open WebUI et de toutes les conversations qui l’affichent. Cette action est irréversible.',
      confirm_text_many: 'Elles sont supprimées d’Open WebUI et de toutes les conversations qui les affichent. Cette action est irréversible.',
      deleted_one: 'Image supprimée', deleted_many: '{n} images supprimées', deleted_some: '{n} supprimées, {failed} n’ont pas pu l’être', delete_failed_one: 'Impossible de supprimer l’image', delete_failed_many: 'Impossible de supprimer les images',
      no_fullscreen: 'Le plein écran n’est pas disponible ici', copy_failed: 'Copie impossible', image_copied: 'Image copiée', copy_hosted: 'Cette image est hébergée ailleurs et ne peut pas être copiée', copy_image_failed: 'Impossible de copier l’image',
      download_failed: 'Impossible de télécharger l’image', open_chat_to_attach: 'Ouvrez une conversation pour joindre des images', attach_hosted: 'Cette image est hébergée ailleurs et ne peut pas être jointe', attach_failed: 'Impossible de joindre l’image',
      preparing: 'Préparation {i} sur {n}…', zip_stopped: 'Arrêt à {mb} Mo. Sélectionnez moins d’images.', zip_none: 'Aucune de ces images n’a pu être téléchargée', zip_cancelled: 'Téléchargement annulé',
      saved_one: '{n} image enregistrée', saved_other: '{n} images enregistrées', skipped: ', {n} ignorées',
      z_prompt: 'Prompt', z_negative: 'Prompt négatif', z_model: 'Modèle', z_created: 'Créée le'
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
    gallery: 'M2.25 15.75l5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909M3.75 21h16.5A2.25 2.25 0 0 0 22.5 18.75V5.25A2.25 2.25 0 0 0 20.25 3H3.75A2.25 2.25 0 0 0 1.5 5.25v13.5A2.25 2.25 0 0 0 3.75 21Zm10.5-11.25h.008v.008h-.008V9.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z',
    close: 'M6 18 18 6M6 6l12 12',
    prev: 'M15.75 19.5 8.25 12l7.5-7.5',
    next: 'm8.25 4.5 7.5 7.5-7.5 7.5',
    back: 'M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18',
    download: 'M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3',
    copy: 'M8 8V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H16M5.5 8h9A1.5 1.5 0 0 1 16 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 18.5v-9A1.5 1.5 0 0 1 5.5 8Z',
    chat: 'M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z',
    attach: 'm18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13',
    expand: 'M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15',
    check: 'm6 12.5 4 4 8-9',
    refresh: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99',
    sort: 'M3 7.5 7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5',
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
  const iconButton = (label, name, action, cls = 'cg-icon') => {
    const node = el('button', cls); node.type = 'button';
    setLabel(node, label);
    node.append(svg(name)); node.addEventListener('click', action);
    return node;
  };
  const textButton = (label, action) => {
    const node = el('button', 'cg-btn', label); node.type = 'button';
    node.addEventListener('click', action); return node;
  };

  /* ---- talking to Open WebUI ---- */
  const token = () => { try { return localStorage.token || ''; } catch { return ''; } };
  const authHeaders = () => token() ? {Authorization: 'Bearer ' + token()} : {};
  // Decided by the address the browser would really request: "/\host" and "//host" both leave this site
  const sameOrigin = url => { try { return new URL(url, location.href).origin === location.origin; } catch { return false; } };
  const external = url => !/^data:/i.test(url) && !sameOrigin(url);
  const RETRY_STATUS = new Set([408, 429, 502, 503, 504]);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  // A fetch that gives up after a while, and tries a failed read once more
  async function request(url, options = {}, timeout = 30000) {
    const signal = options.signal, reading = !options.method || options.method === 'GET';
    for (let attempt = 0; ; attempt++) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      const timer = new AbortController(), stop = () => timer.abort();
      signal?.addEventListener('abort', stop, {once: true});
      const clock = setTimeout(stop, timeout);
      try {
        const response = await fetch(url, {...options, signal: timer.signal});
        if (!(reading && attempt < 1 && RETRY_STATUS.has(response.status))) return response;
      } catch (error) {
        if (signal?.aborted || !reading || attempt >= 1) throw error;
      } finally { clearTimeout(clock); signal?.removeEventListener('abort', stop); }
      await pause(600);
    }
  }
  async function api(path, signal) {
    const response = await request(path, {headers: {Accept: 'application/json', ...authHeaders()}, signal});
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    return response.json();
  }
  // The status check matters: a failed response still has a body, which would otherwise be saved as the image
  async function fetchBlob(url, signal) {
    const own = sameOrigin(url);
    const response = await request(url, {headers: own ? authHeaders() : {}, signal, credentials: own ? 'same-origin' : 'omit'}, 60000);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.blob();
  }
  const fileUrl = id => '/api/v1/files/' + id + '/content';
  const fileIdOf = url => { FILE_RE.lastIndex = 0; const match = FILE_RE.exec(url || ''); return match ? match[1] : null; };
  const chatIdFromPath = () => { const match = /^\/c\/([^/?#]+)/.exec(location.pathname); return match ? decodeURIComponent(match[1]) : null; };
  const isChatRoute = () => location.pathname === '/' || /^\/c\//.test(location.pathname);
  const decodeEntities = text => text.replace(/&(quot|amp|lt|gt|#39|#x27|#x2F);/g, (_, name) => ({quot:'"',amp:'&',lt:'<',gt:'>','#39':"'",'#x27':"'",'#x2F':'/'})[name]);
  const slug = text => String(text || '').toLowerCase().replace(/\.(png|jpe?g|gif|webp|bmp|avif)$/, '').replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,48) || 'image';
  const formatBytes = n => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  const formatDate = seconds => {
    if (!seconds) return '';
    const ms = seconds > 1e14 ? seconds / 1e6 : seconds > 1e11 ? seconds : seconds * 1000;
    try { return new Date(ms).toLocaleString(lang, {dateStyle:'medium', timeStyle:'short'}); } catch { return ''; }
  };
  const extFor = (type, url) => {
    const known = {'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif','image/bmp':'bmp','image/avif':'avif','image/svg+xml':'svg'};
    if (known[type]) return known[type];
    const match = IMG_EXT_RE.exec(url || ''); return match ? match[1].toLowerCase().replace('jpeg','jpg') : 'png';
  };

  /* ---- metadata: what Open WebUI stored, and what the image file itself carries ---- */
  function promptOf(data) {
    if (!data) return '';
    if (typeof data.prompt === 'string') return data.prompt;
    if (typeof data.instances?.prompt === 'string') return data.instances.prompt;
    const parts = Array.isArray(data.contents) ? data.contents.flatMap(c => c?.parts || []) : [];
    return parts.find(p => typeof p?.text === 'string')?.text || '';
  }
  function parseInfo(info) {
    const out = {};
    if (typeof info !== 'string' || !info) return out;
    try {
      const parsed = JSON.parse(info);
      const pick = {seed:'Seed', steps:'Steps', cfg_scale:'CFG scale', sampler_name:'Sampler', scheduler:'Schedule type', denoising_strength:'Denoising', sd_model_name:'Model'};
      for (const [key,label] of Object.entries(pick)) if (parsed[key] != null && parsed[key] !== '') out[label] = String(parsed[key]);
      if (!Object.keys(out).length && Array.isArray(parsed.infotexts) && parsed.infotexts[0]) return parseA1111(parsed.infotexts[0]).params;
    } catch { return parseA1111(info).params; }
    return out;
  }
  // "prompt\nNegative prompt: ...\nSteps: 20, Sampler: Euler a, Seed: 1, ..." as written by AUTOMATIC1111, Forge and Fooocus
  function parseA1111(text) {
    const lines = String(text).trim().split('\n');
    const params = {};
    if (lines.length && /(^|, )Steps: /.test(lines[lines.length - 1])) {
      const last = lines.pop();
      for (const match of last.matchAll(/\s*([^:,]+):\s*("(?:\\.|[^"\\])*"|[^,]*)(?:,|$)/g)) {
        const key = match[1].trim(); if (key) params[key] = match[2].trim().replace(/^"|"$/g,'');
      }
    }
    const negativeAt = lines.findIndex(line => line.startsWith('Negative prompt:'));
    const prompt = (negativeAt < 0 ? lines : lines.slice(0, negativeAt)).join('\n').trim();
    const negative = negativeAt < 0 ? '' : lines.slice(negativeAt).join('\n').replace(/^Negative prompt:\s*/,'').trim();
    return {prompt, negative, params};
  }
  const SAMPLER_NODES = new Set(['KSampler','KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced','RandomNoise']);
  function comfyParams(graph) {
    const out = {}, loras = [];
    let model = '', positive = '', negative = '';
    if (!graph || typeof graph !== 'object') return {params: out};
    const scalar = value => value != null && typeof value !== 'object';
    const textOf = ref => {
      if (!Array.isArray(ref)) return '';
      const node = graph[ref[0]];
      return typeof node?.inputs?.text === 'string' ? node.inputs.text : '';
    };
    for (const node of Object.values(graph)) {
      if (!node || typeof node !== 'object') continue;
      const type = String(node.class_type || ''), inputs = node.inputs || {};
      if (SAMPLER_NODES.has(type) || 'seed' in inputs || 'noise_seed' in inputs) {
        for (const [from,to] of [['seed','Seed'],['noise_seed','Seed'],['steps','Steps'],['cfg','CFG'],['sampler_name','Sampler'],['scheduler','Scheduler'],['denoise','Denoise']])
          if (scalar(inputs[from]) && !(to in out)) out[to] = String(inputs[from]);
        if (!positive) positive = textOf(inputs.positive);
        if (!negative) negative = textOf(inputs.negative);
      }
      if (type.startsWith('LoraLoader') && typeof inputs.lora_name === 'string')
        loras.push(inputs.lora_name.split(/[\\/]/).pop().replace(/\.safetensors$/,'') + (scalar(inputs.strength_model) ? ' × ' + inputs.strength_model : ''));
      if (!model) for (const key of ['unet_name','ckpt_name','model_name'])
        if (typeof inputs[key] === 'string' && inputs[key]) { model = inputs[key].split(/[\\/]/).pop().replace(/\.safetensors$/,''); break; }
    }
    if (model) out.Checkpoint = model;
    if (loras.length) out.LoRAs = loras.join(', ');
    return {params: out, positive, negative};
  }
  async function inflate(bytes) {
    if (typeof DecompressionStream !== 'function') return null;
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    } catch { return null; }
  }
  const utf8 = new TextDecoder('utf-8'), latin1 = new TextDecoder('latin1');
  const strictUtf8 = bytes => { try { return new TextDecoder('utf-8', {fatal:true}).decode(bytes); } catch { return latin1.decode(bytes); } };
  // Text chunks of a PNG: ComfyUI writes its API graph as "prompt", AUTOMATIC1111 its settings as "parameters"
  async function pngText(buffer) {
    const bytes = new Uint8Array(buffer), view = new DataView(buffer), out = {};
    const signature = [137,80,78,71,13,10,26,10];
    if (bytes.length < 8 || signature.some((b,i) => bytes[i] !== b)) return out;
    let at = 8;
    while (at + 8 <= bytes.length) {
      const length = view.getUint32(at), type = latin1.decode(bytes.subarray(at + 4, at + 8));
      const start = at + 8, end = start + length;
      if (end > bytes.length || type === 'IEND') break;
      if ((type === 'tEXt' || type === 'zTXt' || type === 'iTXt') && length < 16777216) {
        const zero = bytes.indexOf(0, start);
        if (zero > start && zero < end) {
          const key = latin1.decode(bytes.subarray(start, zero));
          let value = null;
          if (type === 'tEXt') value = strictUtf8(bytes.subarray(zero + 1, end));
          else if (type === 'zTXt') { const raw = await inflate(bytes.subarray(zero + 2, end)); if (raw) value = latin1.decode(raw); }
          else {
            const compressed = bytes[zero + 1] === 1;
            const languageEnd = bytes.indexOf(0, zero + 3), translatedEnd = languageEnd < 0 ? -1 : bytes.indexOf(0, languageEnd + 1);
            if (translatedEnd >= 0 && translatedEnd < end) {
              const body = bytes.subarray(translatedEnd + 1, end);
              const raw = compressed ? await inflate(body) : body;
              if (raw) value = utf8.decode(raw);
            }
          }
          if (value != null && !(key in out)) out[key] = value;
        }
      }
      at = end + 4;
    }
    return out;
  }
  const isGeneratedName = name => /^generated-image/.test(name || '');
  const isImageRecord = record => {
    const type = record?.meta?.content_type;
    return typeof type === 'string' && type ? type.startsWith('image/') : IMG_EXT_RE.test(record?.meta?.name || record?.filename || '');
  };
  // Fills in prompt, model and settings once, from Open WebUI's file record and the PNG's own text
  function applyRecord(item, record) {
    const meta = record?.meta || {};
    const data = meta.data && typeof meta.data === 'object' ? meta.data : {};
    item.contentType = meta.content_type || item.contentType || '';
    item.bytes = meta.size || item.bytes || 0;
    item.name = meta.name || record?.filename || item.name || '';
    item.created = record?.created_at || item.created || 0;
    item.prompt = item.prompt || promptOf(data);
    item.negative = item.negative || (typeof data.negative_prompt === 'string' ? data.negative_prompt : '');
    if (typeof data.model === 'string' && data.model) item.model = data.model;
    if (typeof data.size === 'string' && data.size.includes('x')) item.requested = data.size;
    else if (data.width && data.height) item.requested = data.width + 'x' + data.height;
    Object.assign(item.params, parseInfo(data.info));
    for (const [key,value] of Object.entries(data)) {
      if (SKIP_DATA.has(key) || value == null || value === '' || typeof value === 'object') continue;
      if (!(key in item.params)) item.params[key] = String(value).slice(0, 160);
    }
    if (item.params.Model && !item.model) item.model = item.params.Model;
    delete item.params.Model;
    if (!item.chatId && typeof data.chat_id === 'string') item.chatId = data.chat_id;
    if (!item.messageId && typeof data.message_id === 'string') item.messageId = data.message_id;
    if (isGeneratedName(record?.filename) || item.prompt) item.source = 'generated';
    if (typeof record?.user_id === 'string') item.ownerId = record.user_id;
    item.recordLoaded = true;
  }
  async function applyFile(item, blob) {
    item.bytes = blob.size || item.bytes;
    if (item.fileParsed || !/png/.test(blob.type || item.contentType || '')) return;
    item.fileParsed = true;
    const text = await pngText(await blob.arrayBuffer());
    if (text.prompt) {
      try {
        const comfy = comfyParams(JSON.parse(text.prompt));
        if (comfy.params.Checkpoint && !item.model) item.model = comfy.params.Checkpoint;
        delete comfy.params.Checkpoint;
        for (const [key,value] of Object.entries(comfy.params)) if (!(key in item.params)) item.params[key] = value;
        if (!item.prompt && comfy.positive) item.prompt = comfy.positive;
        if (!item.negative && comfy.negative) item.negative = comfy.negative;
      } catch {}
    }
    if (text.parameters) {
      const a1111 = parseA1111(text.parameters);
      if (!item.prompt) item.prompt = a1111.prompt;
      if (!item.negative) item.negative = a1111.negative;
      if (a1111.params.Model && !item.model) item.model = a1111.params.Model;
      delete a1111.params.Model;
      for (const [key,value] of Object.entries(a1111.params)) if (!(key in item.params)) item.params[key] = value;
    }
  }

  /* ---- collecting images ---- */
  const records = new Map();
  async function fileRecord(id) {
    if (!records.has(id)) records.set(id, api('/api/v1/files/' + encodeURIComponent(id)).catch(error => ({error})));
    return records.get(id);
  }
  const makeItem = fields => ({url:'', fileId:null, messageId:null, chatId:null, alt:'', name:'', prompt:'', negative:'', model:'', requested:'', contentType:'', bytes:0, created:0, source:'', params:{}, ...fields});
  // Only addresses an <img> can safely show: this site, http(s) elsewhere, or an inline image
  const usable = url => {
    if (!url || typeof url !== 'string') return false;
    if (/^data:/i.test(url)) return /^data:image\//i.test(url);
    try { return /^https?:$/.test(new URL(url, location.href).protocol); } catch { return false; }
  };
  async function collectChat(chatId, signal) {
    const chat = await api('/api/v1/chats/' + encodeURIComponent(chatId), signal);
    const messages = Object.values(chat?.chat?.history?.messages || {}).sort((a,b) => (a?.timestamp || 0) - (b?.timestamp || 0));
    const items = [], seen = new Map();
    const add = (url, fields, verify) => {
      if (!usable(url)) return;
      const fileId = fields.fileId || fileIdOf(url);
      const key = fileId || url;
      if (seen.has(key)) { const known = seen.get(key); if (!verify) known.verify = false; return; }
      const item = makeItem({url: fileId ? fileUrl(fileId) : url, fileId, chatId, verify: !!verify, ...fields});
      seen.set(key, item); items.push(item);
    };
    for (const message of messages) {
      if (!message) continue;
      const base = {messageId: message.id, created: message.timestamp || 0, source: message.role === 'user' ? 'uploaded' : 'reply'};
      for (const file of message.files || []) {
        if (!file || typeof file !== 'object') continue;
        const type = String(file.content_type || file.meta?.content_type || file.file?.meta?.content_type || '');
        if (file.type !== 'image' && !type.startsWith('image/')) continue;
        let url = typeof file.url === 'string' ? file.url : '';
        const inline = /^(data:|https?:)/i.test(url) && !fileIdOf(url);
        const id = file.file?.id || (UUID_RE.test(url) ? url : null) || fileIdOf(url) || (!inline && UUID_RE.test(file.id || '') ? file.id : null);
        if (id) url = fileUrl(id);
        add(url, {...base, fileId: id, name: file.name || '', contentType: type});
      }
      let text = typeof message.content === 'string' ? message.content : JSON.stringify(message.content || '');
      if (message.output) text += '\n' + JSON.stringify(message.output);
      text = decodeEntities(text).replace(/\\\//g, '/');
      for (const match of text.matchAll(MD_IMG_RE)) add(match[2], {...base, alt: match[1].trim()});
      for (const match of text.matchAll(HTML_IMG_RE)) add(match[1], {...base});
      for (const match of text.matchAll(FILE_RE)) add(fileUrl(match[1]), {...base, fileId: match[1]}, true);
    }
    for (const item of items) if (!item.fileId && external(item.url)) item.source = 'linked';
    await pool(items.filter(item => item.fileId), 6, async item => {
      const record = await fileRecord(item.fileId);
      if (record?.error) { item.missing = record.error.status === 404; return; }
      applyRecord(item, record);
    }, signal);
    return items.filter(item => {
      if (item.contentType && !item.contentType.startsWith('image/')) return false;
      if (item.verify && !item.recordLoaded) return false;
      if (item.verify && !item.contentType) return false;
      return !(sameOrigin(item.url) && !item.fileId && !IMG_EXT_RE.test(item.url) && !item.alt);
    });
  }
  async function pool(list, size, worker, signal) {
    let next = 0;
    const run = async () => { while (next < list.length) { if (signal?.aborted) return; await worker(list[next++]); } };
    await Promise.all(Array.from({length: Math.min(size, list.length)}, run));
  }

  /* ---- small pictures for the grid, so a wall of photos doesn't hold every full image in memory ---- */
  const THUMB_PX = Math.max(0, CONFIG.thumbnailPx | 0);
  const thumbs = new Map(), thumbJobs = [];
  let thumbRunning = 0, thumbWatcher = null;
  const thumbKey = item => item.fileId || item.url;
  async function makeThumb(blob) {
    // Small files, and formats a canvas would flatten or freeze, are shown as they are
    if (blob.size < 90000 || /svg|gif/.test(blob.type) || typeof createImageBitmap !== 'function') return blob;
    const bitmap = await createImageBitmap(blob);
    try {
      const want = THUMB_PX * Math.min(2, window.devicePixelRatio || 1);
      const ratio = Math.min(1, want / Math.min(bitmap.width, bitmap.height));
      if (ratio > 0.7) return blob;
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * ratio)); canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
      const context = canvas.getContext('2d'); context.imageSmoothingQuality = 'high';
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const small = await new Promise(resolve => canvas.toBlob(resolve, 'image/webp', 0.82));
      canvas.width = canvas.height = 0;
      return small && small.size < blob.size ? small : blob;
    } finally { bitmap.close?.(); }
  }
  function pumpThumbs() {
    while (thumbRunning < 4 && thumbJobs.length) {
      const job = thumbJobs.shift();
      if (job.dropped) continue;
      thumbRunning++;
      (async () => {
        const key = thumbKey(job.item);
        try {
          if (!thumbs.has(key)) {
            const small = await makeThumb(await fetchBlob(job.item.url, thumbAbort.signal));
            if (!thumbs.has(key)) thumbs.set(key, URL.createObjectURL(small));
            // Oldest out first; a picture already on screen keeps showing after its address is released
            while (thumbs.size > 800) { const oldest = thumbs.keys().next().value; URL.revokeObjectURL(thumbs.get(oldest)); thumbs.delete(oldest); }
          }
          job.entry.img.src = thumbs.get(key);
        } catch { if (!thumbAbort.signal.aborted) job.entry.img.src = job.item.url; }
        job.entry.job = null;
      })().finally(() => { thumbRunning--; pumpThumbs(); });
    }
  }
  let thumbAbort = new AbortController();
  function dropThumbs() {
    thumbAbort.abort(); thumbAbort = new AbortController(); thumbJobs.length = 0;
    for (const url of thumbs.values()) URL.revokeObjectURL(url);
    thumbs.clear();
  }

  /* ---- the dialog ---- */
  let dialog, head, tabChat, tabLibrary, search, kindSelect, sortButton, refreshButton, soloClose, selectButton, zipButton, allButton, scroller, grid, sentinel, foot, footText, footAction, live, toast;
  let viewer, stage, big, fullscreenButton, prevButton, nextButton, countEl, chatButton, attachButton, copyImageButton, deleteButton, deleteSelectedButton, confirmBox, info;
  let me = null;
  let scope = 'chat', items = [], shown = [], selecting = false, busy = false, opener = null, headerButton = null, collectController = null, zipController = null;
  let chatLoading = false, chatError = '', chatLoadedAt = 0, hiddenLinked = 0, kind = 'all', builtLang = '';
  const newestFirst = {chat: false, library: true};
  const freshLibrary = () => ({items: [], offset: 0, done: false, loading: false, error: null, dry: 0, paused: false, controller: new AbortController()});
  let library = freshLibrary();
  const selected = new Set();
  const chatExists = new Map();
  const tiles = new Map();

  function say(text, action) {
    footText.textContent = text;
    footAction.hidden = !action;
    if (action) { footAction.textContent = action.label; footAction.onclick = action.run; }
  }
  function flash(text) {
    toast.textContent = text; toast.classList.add('cg-show');
    clearTimeout(flash.timer); flash.timer = setTimeout(() => toast.classList.remove('cg-show'), 2200);
    live.textContent = text;
  }
  function build() {
    lang = builtLang = language();
    dialog = el('div', 'cg-dialog'); dialog.id = 'owui-chat-gallery'; dialog.hidden = true;
    dialog.setAttribute('role', 'tabpanel'); dialog.setAttribute('aria-labelledby', 'owui-chat-gallery-tab');
    dialog.lang = lang;
    head = el('div', 'cg-head');
    const tabs = el('div', 'cg-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('images_to_show'));
    const tab = (label, value) => {
      const node = el('button', 'cg-tab', label); node.type = 'button'; node.setAttribute('role', 'tab');
      node.addEventListener('click', () => setScope(value));
      node.id = 'cg-tab-' + value; node.setAttribute('aria-controls', 'cg-panel');
      node.addEventListener('keydown', event => {
        const visible = [tabChat, tabLibrary].filter(each => !each.hidden);
        const at = visible.indexOf(node);
        const target = {ArrowLeft: visible[at - 1] || visible[visible.length - 1], ArrowRight: visible[at + 1] || visible[0], Home: visible[0], End: visible[visible.length - 1]}[event.key];
        if (!target) return;
        event.preventDefault();
        if (target !== node) { target.focus(); target.click(); }
      });
      tabs.append(node); return node;
    };
    tabChat = tab(t('this_chat'), 'chat'); tabLibrary = tab(t(ALL_IMAGES ? 'my_images' : 'all_generated'), 'library');
    search = el('input', 'cg-search'); search.type = 'search'; search.placeholder = t('search');
    search.setAttribute('aria-label', t('search_label')); search.title = t('search_label');
    let searchTimer = 0;
    search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(render, 120); });
    kindSelect = el('select', 'cg-select'); kindSelect.setAttribute('aria-label', t('kind'));
    for (const value of ['all', 'generated', 'uploaded', 'other']) { const option = el('option', null, t('kind_' + value)); option.value = value; kindSelect.append(option); }
    kindSelect.addEventListener('change', () => { kind = kindSelect.value; scroller.scrollTop = 0; render(); });
    sortButton = iconButton(t('oldest_first'), 'sort', () => { newestFirst[scope] = !newestFirst[scope]; scroller.scrollTop = 0; render(); });
    refreshButton = iconButton(t('refresh'), 'refresh', () => refresh());
    selectButton = textButton(t('select'), () => setSelecting(!selecting));
    allButton = textButton(t('select_all'), () => {
      const all = shown.every(item => selected.has(item));
      for (const item of shown) all ? selected.delete(item) : selected.add(item);
      paintSelection();
    });
    zipButton = textButton(t('download_all'), () => zipController ? zipController.abort() : downloadZip());
    zipButton.prepend(svg('download'));
    deleteSelectedButton = textButton(t('delete'), () => deleteItems(shown.filter(item => selected.has(item) && deletable(item))));
    deleteSelectedButton.prepend(svg('trash')); deleteSelectedButton.hidden = true;
    soloClose = iconButton(t('close_gallery'), 'close', () => closeTab()); soloClose.hidden = true;
    head.append(tabs, el('span', 'cg-spacer'), search, kindSelect, sortButton, refreshButton, selectButton, allButton, deleteSelectedButton, zipButton, soloClose);
    const body = el('div', 'cg-body');
    scroller = el('div', 'cg-scroll'); scroller.id = 'cg-panel'; scroller.setAttribute('role', 'tabpanel');
    grid = el('ul', 'cg-grid'); grid.setAttribute('aria-label', t('images'));
    sentinel = el('div', 'cg-sentinel');
    scroller.append(grid, sentinel);
    foot = el('div', 'cg-foot');
    footText = el('span'); footAction = textButton('', () => {}); footAction.hidden = true;
    foot.append(footText, footAction);
    live = el('div', 'cg-sr'); live.setAttribute('aria-live', 'polite');
    toast = el('div', 'cg-toast'); toast.setAttribute('aria-hidden', 'true');
    body.append(scroller, foot, buildViewer(), toast, live);
    dialog.append(head, body, buildConfirm());
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (viewer.classList.contains('cg-open')) { event.preventDefault(); closeViewer(); }
      else if (selecting) { event.preventDefault(); setSelecting(false); }
      else if (dialog.classList.contains('cg-solo')) { event.preventDefault(); deactivate(); headerButton?.focus({preventScroll: true}); }
    });
    new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && scope === 'library' && tabOpen && !library.paused) loadLibrary();
    }, {root: scroller, rootMargin: '400px'}).observe(sentinel);
    // A tile's picture is only fetched once it is close to being seen
    thumbWatcher = new IntersectionObserver(entries => {
      for (const {target, isIntersecting} of entries) {
        const entry = target.cgEntry;
        if (!entry || entry.img.hasAttribute('src') || !entry.img.isConnected) continue;
        if (isIntersecting) {
          const ready = thumbs.get(thumbKey(entry.item));
          if (ready) entry.img.src = ready;
          else if (!entry.job) { entry.job = {item: entry.item, entry}; thumbJobs.push(entry.job); }
        } else if (entry.job) { entry.job.dropped = true; entry.job = null; }
      }
      pumpThumbs();
    }, {root: scroller, rootMargin: '600px'});
    document.body.append(dialog);
  }
  function setSelecting(on) {
    selecting = on; if (!on) selected.clear();
    grid.classList.toggle('cg-selecting', on);
    selectButton.textContent = on ? t('cancel') : t('select');
    selectButton.setAttribute('aria-pressed', String(on));
    paintSelection();
  }
  function paintSelection() {
    allButton.hidden = !selecting;
    allButton.textContent = shown.length && shown.every(item => selected.has(item)) ? t('select_none') : t('select_all');
    for (const [item, entry] of tiles) {
      if (!entry.li.isConnected) continue;
      if (selecting) entry.tile.setAttribute('aria-pressed', String(selected.has(item))); else entry.tile.removeAttribute('aria-pressed');
    }
    const picked = shown.filter(item => selected.has(item)).length;
    zipButton.lastChild.textContent = zipController ? t('cancel') : selecting ? (picked ? t('download_n', {n: picked}) : t('download')) : t('download_all');
    zipButton.disabled = zipController ? false : busy || !shown.length || (selecting && !picked);
    const removable = shown.filter(item => selected.has(item) && deletable(item)).length;
    deleteSelectedButton.hidden = !selecting || !shown.some(deletable);
    deleteSelectedButton.lastChild.textContent = removable ? t('delete_n', {n: removable}) : t('delete');
    deleteSelectedButton.disabled = busy || !removable;
  }
  function setScope(value) {
    scope = value;
    tabChat.setAttribute('aria-selected', String(value === 'chat')); tabLibrary.setAttribute('aria-selected', String(value === 'library'));
    tabChat.tabIndex = value === 'chat' ? 0 : -1; tabLibrary.tabIndex = value === 'library' ? 0 : -1;
    scroller.setAttribute('aria-labelledby', 'cg-tab-' + value);
    setSelecting(false); scroller.scrollTop = 0;
    if (value === 'chat') loadChat(); else { render(); if (!library.items.length && !library.done) loadLibrary(); }
  }
  // quiet: keep showing what is there while the chat is read again
  async function loadChat(quiet) {
    collectController?.abort();
    const controller = collectController = new AbortController();
    const chatId = chatIdFromPath();
    if (!quiet || !chatId) { items = []; hiddenLinked = 0; }
    chatError = ''; chatLoading = !!chatId;
    if (scope === 'chat') render();
    if (!chatId) return;
    try {
      let found = await collectChat(chatId, controller.signal);
      if (controller.signal.aborted) return;
      const all = found.length;
      if (!CONFIG.loadLinked) found = found.filter(item => !external(item.url));
      hiddenLinked = all - found.length;
      if (quiet) {
        // The same image keeps its object, so a selection and the open viewer survive the refresh
        const known = new Map(items.map(item => [thumbKey(item), item]));
        found = found.map(item => known.get(thumbKey(item)) || item);
      }
      items = found;
    } catch (error) {
      if (controller.signal.aborted) return;
      if (!quiet) items = [];
      chatError = error.status === 401 || error.status === 403 ? t('err_access') : error.status === 404 ? t('err_unsaved') : t('err_generic');
    }
    chatLoading = false; chatLoadedAt = Date.now();
    if (scope === 'chat') render();
  }
  async function loadLibrary() {
    const lib = library;
    if (lib.loading || lib.done || !tabOpen) return;
    lib.loading = true; lib.error = null; lib.paused = false; if (scope === 'library') render();
    try {
      const page = await api('/api/v1/files/user/search?filename=' + encodeURIComponent(ALL_IMAGES ? '*' : 'generated-image*') + '&content=false&skip=' + lib.offset + '&limit=' + PAGE_SIZE, lib.controller.signal);
      if (library !== lib) return;
      const list = Array.isArray(page) ? page : [];
      library.offset += list.length;
      const known = new Set(library.items.map(item => item.fileId));
      let added = 0;
      for (const record of list) {
        if (!record?.id || known.has(record.id) || !isImageRecord(record)) continue;
        const item = makeItem({url: fileUrl(record.id), fileId: record.id, source: 'uploaded'});
        applyRecord(item, record);
        if (!ALL_IMAGES) item.source = 'generated';
        records.set(record.id, Promise.resolve(record));
        library.items.push(item); added++;
      }
      if (list.length < PAGE_SIZE) library.done = true;
      // Someone with thousands of documents and no pictures shouldn't have every page fetched unasked
      library.dry = added ? 0 : library.dry + 1;
      if (!library.done && library.dry >= DRY_PAGES) { library.paused = true; library.dry = 0; }
    } catch (error) {
      if (library !== lib) return;
      library.error = error;
    }
    library.loading = false;
    if (scope === 'library') render();
    // Keep filling until the grid scrolls, so the sentinel can take over
    if (tabOpen && scope === 'library' && !library.done && !library.error && !library.paused && scroller.scrollHeight <= scroller.clientHeight + 400) loadLibrary();
  }
  function refresh() {
    if (scope === 'chat') { records.clear(); loadChat(true); return; }
    library.controller.abort(); library = freshLibrary(); records.clear();
    setSelecting(false); render(); loadLibrary();
  }
  const current = () => scope === 'chat' ? items : library.items;
  const kindOf = item => item.source === 'generated' || item.source === 'uploaded' ? item.source : 'other';
  function matches(item, query) {
    if (kind !== 'all' && kindOf(item) !== kind) return false;
    if (!query) return true;
    return [item.prompt, item.negative, item.model, item.name, item.alt, ...Object.values(item.params)].some(value => String(value || '').toLowerCase().includes(query));
  }
  function tileFor(item) {
    let entry = tiles.get(item);
    if (entry) return entry;
    const li = el('li');
    const tile = el('button', 'cg-tile'); tile.type = 'button';
    tile.dataset.broken = t('unavailable');
    const img = el('img'); img.decoding = 'async'; img.alt = ''; img.draggable = false;
    img.referrerPolicy = 'no-referrer';
    const num = el('span', 'cg-num');
    const check = el('span', 'cg-check'); check.append(svg('check'));
    tile.append(img, num, check);
    entry = {item, li, tile, img, num, index: 0, job: null};
    img.addEventListener('error', () => {
      // A small picture that failed to show falls back to the image itself before the tile gives up
      if (img.src.startsWith('blob:') && !external(item.url)) { thumbs.delete(thumbKey(item)); img.src = item.url; return; }
      tile.classList.add('cg-broken'); img.remove();
    });
    if (THUMB_PX && !external(item.url)) { li.cgEntry = entry; thumbWatcher.observe(li); }
    else { img.loading = 'lazy'; img.src = item.url; }
    tile.addEventListener('click', () => {
      if (selecting) { selected.has(item) ? selected.delete(item) : selected.add(item); paintSelection(); }
      else openViewer(entry.index);
    });
    // A long press starts selecting, as on a phone's photo gallery
    let timer = null, touch = false;
    tile.addEventListener('pointerdown', event => {
      touch = event.pointerType === 'touch'; timer = null;
      if (!touch || selecting) return;
      timer = setTimeout(() => { timer = 'fired'; setSelecting(true); selected.add(item); paintSelection(); }, 500);
    });
    for (const type of ['pointerup','pointercancel','pointerleave']) tile.addEventListener(type, () => { if (timer !== 'fired') clearTimeout(timer); });
    tile.addEventListener('click', event => { if (timer === 'fired') { timer = null; event.stopImmediatePropagation(); } }, true);
    tile.addEventListener('contextmenu', event => { if (touch) event.preventDefault(); });
    li.append(tile);
    tiles.set(item, entry);
    return entry;
  }
  function forgetTiles() {
    const alive = new Set([...items, ...library.items]);
    for (const [item, entry] of tiles) if (!alive.has(item)) { thumbWatcher.unobserve(entry.li); if (entry.job) entry.job.dropped = true; tiles.delete(item); }
  }
  function render() {
    const query = search.value.trim().toLowerCase();
    const list = current();
    shown = list.filter(item => matches(item, query));
    // "This chat" arrives oldest first and the library newest first
    if (newestFirst[scope] !== (scope === 'library')) shown.reverse();
    forgetTiles();
    // Tiles are kept and moved, not rebuilt, so a search or a new page doesn't reload every picture
    let node = grid.firstElementChild;
    shown.forEach((item, index) => {
      const entry = tileFor(item);
      entry.index = index; entry.tile.dataset.index = index;
      const label = item.prompt || item.alt || item.name || t('image');
      entry.tile.setAttribute('aria-label', t('image_n', {n: index + 1, label: label.slice(0, 120)}));
      entry.num.textContent = String(index + 1);
      if (node === entry.li) node = node.nextElementSibling; else grid.insertBefore(entry.li, node);
    });
    while (node) { const next = node.nextElementSibling; node.remove(); node = next; }
    const loading = scope === 'library' ? library.loading : chatLoading;
    const filtered = !!query || kind !== 'all';
    if (!shown.length && !loading) {
      const empty = el('li', 'cg-empty');
      const words = (title, hint) => empty.append(el('strong', null, t(title)), el('span', null, hint));
      if (scope === 'chat' && !chatIdFromPath()) words('no_chat', t('no_chat_hint'));
      else if (scope === 'chat' && chatError) { words('chat_failed', chatError); empty.append(textButton(t('retry'), () => loadChat())); }
      else if (scope === 'library' && library.error) { words('library_failed', t('library_failed_hint')); empty.append(textButton(t('retry'), () => loadLibrary())); }
      else if (filtered && list.length) words('none_match', t('none_match_hint'));
      else if (scope === 'library' && library.paused) words('library_empty', t('paused', {n: library.offset}));
      else if (filtered) words('none_match', t('none_match_hint'));
      else if (scope === 'chat') words('chat_empty', t('chat_empty_hint'));
      else words(ALL_IMAGES ? 'library_empty' : 'generated_empty', t(ALL_IMAGES ? 'library_empty_hint' : 'generated_empty_hint'));
      grid.append(empty);
    }
    const total = list.length, more = scope === 'library' && !library.done;
    let status = '', action = null;
    if (loading) status = scope === 'chat' && !total ? t('collecting') : scope === 'chat' ? tn('count', total) : total ? t('loading_more', {n: total}) : t('loading');
    else if (total) status = (filtered ? t('of', {shown: shown.length, total: tn('count', total)}) : tn('count', total)) + (more ? t('so_far') : '');
    if (!loading && scope === 'chat' && hiddenLinked) status += (status ? ' · ' : '') + tn('linked_hidden', hiddenLinked);
    if (!loading && scope === 'library' && library.paused) action = {label: t('keep_looking'), run: () => loadLibrary()};
    else if (!loading && scope === 'library' && library.error && total) action = {label: t('retry'), run: () => loadLibrary()};
    say(status, action);
    kindSelect.hidden = scope === 'library' && !ALL_IMAGES;
    if (kindSelect.value !== kind) kindSelect.value = kind;
    setLabel(sortButton, t(newestFirst[scope] ? 'newest_first' : 'oldest_first'));
    sortButton.disabled = shown.length < 2;
    refreshButton.disabled = loading || (scope === 'chat' && !chatIdFromPath());
    paintSelection();
  }

  /* ---- viewer ---- */
  let index = 0, viewItem = null, scale = 1, tx = 0, ty = 0, viewController = null, objectUrl = null, returnTile = null, pendingItem = null, loadedItem = null;
  const cache = new Map();
  function buildViewer() {
    viewer = el('div', 'cg-viewer'); viewer.setAttribute('role', 'group'); viewer.setAttribute('aria-roledescription', t('viewer'));
    viewer.setAttribute('aria-label', t('viewer'));
    const bar = el('div', 'cg-vbar');
    countEl = el('span', 'cg-count');
    chatButton = iconButton(t('show_in_chat'), 'chat', showInChat);
    attachButton = iconButton(t('attach'), 'attach', attachToComposer);
    copyImageButton = iconButton(t('copy_image'), 'copy', copyImage);
    deleteButton = iconButton(t('delete_image'), 'trash', () => deleteItems([viewItem]));
    bar.append(iconButton(t('back'), 'back', () => closeViewer()), countEl, el('span', 'cg-spacer'), chatButton, attachButton, copyImageButton,
      iconButton(t('download'), 'download', () => downloadOne(viewItem)), deleteButton, fullscreenButton = iconButton(t('full_screen'), 'expand', toggleFullscreen));
    stage = el('div', 'cg-stage'); stage.tabIndex = -1;
    big = el('img'); big.alt = ''; big.draggable = false; big.referrerPolicy = 'no-referrer';
    big.addEventListener('load', () => { loadedItem = pendingItem; stage.classList.remove('cg-loading'); paintInfo(); });
    big.addEventListener('error', () => { stage.classList.remove('cg-loading'); });
    prevButton = iconButton(t('previous'), 'prev', () => step(-1), 'cg-nav cg-prev');
    nextButton = iconButton(t('next'), 'next', () => step(1), 'cg-nav cg-next');
    stage.append(big, prevButton, nextButton);
    info = el('div', 'cg-info');
    viewer.append(bar, stage, info);
    viewer.addEventListener('keydown', event => {
      if (event.target.closest('input,textarea,select')) return;
      if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
      else if (event.key === 'ArrowRight') { event.preventDefault(); step(1); }
      else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomAt(scale * 1.25); }
      else if (event.key === '-') { event.preventDefault(); zoomAt(scale / 1.25); }
      else if (event.key === '0') { event.preventDefault(); resetZoom(); }
      else if (event.key === 'f' && !event.ctrlKey && !event.metaKey && !event.altKey && document.fullscreenEnabled) { event.preventDefault(); toggleFullscreen(); }
    });
    gestures();
    return viewer;
  }
  function openViewer(at) {
    returnTile = grid.querySelector('.cg-tile[data-index="' + at + '"]');
    viewer.classList.add('cg-open'); dialog.classList.add('cg-viewing');
    head.inert = scroller.inert = true;
    show(at);
    viewer.querySelector('.cg-vbar .cg-icon').focus();
  }
  function closeViewer(silent) {
    if (!viewer.classList.contains('cg-open')) return;
    viewController?.abort();
    if (document.fullscreenElement === viewer) document.exitFullscreen().catch(() => {});
    viewer.classList.remove('cg-open'); dialog.classList.remove('cg-viewing'); big.removeAttribute('src');
    head.inert = scroller.inert = false;
    viewItem = loadedItem = pendingItem = null;
    if (!silent) (grid.querySelector('.cg-tile[data-index="' + index + '"]') || returnTile)?.focus();
  }
  function step(delta) {
    if (shown.length < 2) return;
    show((index + delta + shown.length) % shown.length);
  }
  async function show(at) {
    index = at; resetZoom();
    const item = viewItem = shown[at];
    const many = shown.length > 1;
    prevButton.hidden = nextButton.hidden = !many;
    countEl.textContent = (at + 1) + ' / ' + shown.length;
    big.alt = item.prompt || item.alt || item.name || t('image') + ' ' + (at + 1);
    chatButton.hidden = true; attachButton.hidden = !composer();
    copyImageButton.hidden = !(navigator.clipboard?.write && window.ClipboardItem);
    fullscreenButton.hidden = !document.fullscreenEnabled;
    deleteButton.hidden = !deletable(item);
    paintInfo();
    viewController?.abort();
    const controller = viewController = new AbortController();
    stage.classList.add('cg-loading');
    checkChatLink(item, controller.signal);
    loadedItem = null;
    if (external(item.url)) { pendingItem = item; big.src = item.url; return; }
    try {
      if (item.fileId && !item.recordLoaded) { const record = await fileRecord(item.fileId); if (!record?.error) applyRecord(item, record); }
      if (viewItem === item) deleteButton.hidden = !deletable(item);
      const blob = await loadBlob(item, controller.signal);
      if (controller.signal.aborted) return;
      await applyFile(item, blob);
      if (controller.signal.aborted) return;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob); pendingItem = item; big.src = objectUrl;
      paintInfo();
      // Warm the neighbour so stepping through stays quick
      if (many) loadBlob(shown[(at + 1) % shown.length], controller.signal).catch(() => {});
    } catch (error) {
      if (controller.signal.aborted) return;
      pendingItem = item; big.src = item.url;
    }
  }
  async function loadBlob(item, signal) {
    if (external(item.url)) throw new Error('external');
    if (cache.has(item.url)) return cache.get(item.url);
    const blob = await fetchBlob(item.url, signal);
    cache.set(item.url, blob);
    while (cache.size > 6) cache.delete(cache.keys().next().value);
    return blob;
  }
  function paintInfo() {
    const item = viewItem;
    if (!item) return;
    info.replaceChildren();
    const section = (title, extra) => {
      const node = el('section', 'cg-section'); const h = el('h3', null, title);
      if (extra) h.append(extra);
      node.append(h); info.append(node); return node;
    };
    const prompt = item.prompt || item.alt;
    if (prompt) {
      const copy = textButton(t('copy'), () => copyText(prompt, t('prompt_copied')));
      section(t(item.prompt ? 'prompt' : 'description'), copy).append(el('p', 'cg-prompt', prompt));
    }
    if (item.negative) section(t('negative')).append(el('p', 'cg-prompt cg-negative', item.negative));
    const facts = [];
    if (item.model) facts.push([t('f_model'), item.model]);
    if (loadedItem === item && big.naturalWidth) facts.push([t('f_dimensions'), big.naturalWidth + ' × ' + big.naturalHeight]);
    else if (item.requested) facts.push([t('f_size'), item.requested.replace('x', ' × ')]);
    if (item.bytes) facts.push([t('f_bytes'), formatBytes(item.bytes)]);
    if (item.created) facts.push([t('f_created'), formatDate(item.created)]);
    if (item.source) facts.push([t('f_source'), t('src_' + item.source)]);
    if (item.name && !isGeneratedName(item.name)) facts.push([t('f_name'), item.name]);
    if (item.missing) facts.push([t('f_note'), t('file_deleted')]);
    // The same setting can arrive twice, once from the image's record and once from its settings line
    const seen = new Set([...facts.map(([key]) => key.toLowerCase()), 'model', 'size', 'source']);
    for (const [key,value] of Object.entries(item.params)) {
      const name = key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
      if (!seen.has(name.toLowerCase())) { seen.add(name.toLowerCase()); facts.push([name, value]); }
    }
    if (facts.length) {
      const list = el('dl', 'cg-facts');
      for (const [key,value] of facts) list.append(el('dt', null, key), el('dd', null, value));
      section(t('details')).append(list);
    }
  }
  async function checkChatLink(item, signal) {
    const chatId = item.chatId;
    if (!chatId || !item.messageId && scope === 'chat') return;
    if (!chatExists.has(chatId)) chatExists.set(chatId, request('/api/v1/chats/' + encodeURIComponent(chatId), {headers: authHeaders()}).then(r => r.ok).catch(() => false));
    const ok = await chatExists.get(chatId);
    if (!signal.aborted && viewItem === item) {
      chatButton.hidden = !ok;
      setLabel(chatButton, t(chatId === chatIdFromPath() ? 'show_in_chat' : 'open_in_chat'));
    }
  }
  function showInChat() {
    const item = viewItem;
    if (!item?.chatId) return;
    const params = new URLSearchParams(location.search);
    deactivate();
    // Open WebUI's chat page reveals ?message= itself, branch included; when it is already there, scroll by hand
    if (item.chatId === chatIdFromPath() && item.messageId && params.get('message') === item.messageId) {
      document.getElementById('message-' + item.messageId)?.scrollIntoView({block: 'center', behavior: 'smooth'});
      return;
    }
    const link = el('a'); link.href = '/c/' + encodeURIComponent(item.chatId) + (item.messageId ? '?message=' + encodeURIComponent(item.messageId) : '');
    document.body.append(link); link.click(); link.remove();
  }

  /* ---- delete ---- */
  // Only files of your own; an admin's gallery never offers someone else's
  const deletable = item => !!(CONFIG.allowDelete && item && item.fileId && me && item.ownerId === me.id && !item.missing);
  function buildConfirm() {
    confirmBox = el('dialog', 'cg-confirm');
    confirmBox.setAttribute('aria-labelledby', 'cg-confirm-title'); confirmBox.setAttribute('aria-describedby', 'cg-confirm-text');
    return confirmBox;
  }
  function confirmDelete(count) {
    return new Promise(done => {
      const title = el('h3', null, count === 1 ? t('confirm_one') : t('confirm_many', {n: count})); title.id = 'cg-confirm-title';
      const text = el('p', null, t(count === 1 ? 'confirm_text_one' : 'confirm_text_many'));
      text.id = 'cg-confirm-text';
      const cancel = textButton(t('cancel'), () => confirmBox.close('cancel'));
      const ok = textButton(t('delete'), () => confirmBox.close('delete')); ok.classList.add('cg-danger');
      const actions = el('div', 'cg-confirm-actions'); actions.append(cancel, ok);
      confirmBox.replaceChildren(title, text, actions);
      confirmBox.returnValue = '';
      confirmBox.addEventListener('close', () => done(confirmBox.returnValue === 'delete'), {once: true});
      confirmBox.showModal(); cancel.focus();
    });
  }
  async function deleteItems(list) {
    list = list.filter(deletable);
    if (!list.length || busy || !(await confirmDelete(list.length))) return;
    busy = true; paintSelection();
    const gone = new Set();
    await pool(list, 4, async item => {
      try {
        const response = await request('/api/v1/files/' + encodeURIComponent(item.fileId), {method: 'DELETE', headers: authHeaders()});
        if (response.ok || response.status === 404) gone.add(item.fileId);
      } catch {}
    });
    busy = false;
    for (const id of gone) { records.delete(id); cache.delete(fileUrl(id)); const small = thumbs.get(id); if (small) { URL.revokeObjectURL(small); thumbs.delete(id); } }
    const keep = item => !gone.has(item.fileId);
    const wasViewing = viewer.classList.contains('cg-open') && viewItem && gone.has(viewItem.fileId);
    const at = index;
    items = items.filter(keep); library.items = library.items.filter(keep);
    for (const item of [...selected]) if (!keep(item)) selected.delete(item);
    const failed = list.length - gone.size;
    flash(failed ? (gone.size ? t('deleted_some', {n: gone.size, failed}) : t(list.length === 1 ? 'delete_failed_one' : 'delete_failed_many')) : (gone.size === 1 ? t('deleted_one') : t('deleted_many', {n: gone.size})));
    if (selecting && !selected.size) setSelecting(false);
    render();
    if (wasViewing) { if (shown.length) show(Math.min(at, shown.length - 1)); else closeViewer(); }
  }

  /* ---- zoom, pan and swipe ---- */
  const apply = () => {
    big.style.transform = scale === 1 && !tx && !ty ? '' : 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    stage.classList.toggle('cg-zoomed', scale > 1);
  };
  function resetZoom() { scale = 1; tx = ty = 0; apply(); }
  function clampPan() {
    const box = stage.getBoundingClientRect(), w = big.offsetWidth * scale, h = big.offsetHeight * scale;
    const mx = Math.max(0, (w - box.width) / 2), my = Math.max(0, (h - box.height) / 2);
    tx = Math.min(mx, Math.max(-mx, tx)); ty = Math.min(my, Math.max(-my, ty));
  }
  function zoomAt(next, clientX, clientY) {
    next = Math.min(8, Math.max(1, next));
    if (next === 1) { resetZoom(); return; }
    const box = stage.getBoundingClientRect();
    const cx = clientX == null ? 0 : clientX - (box.left + box.width / 2), cy = clientY == null ? 0 : clientY - (box.top + box.height / 2);
    tx = cx - (cx - tx) * next / scale; ty = cy - (cy - ty) * next / scale; scale = next;
    clampPan(); apply();
  }
  function gestures() {
    const points = new Map();
    let pinch = null, drag = null, lastTap = 0;
    stage.addEventListener('wheel', event => {
      event.preventDefault();
      zoomAt(scale * Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0015)), event.clientX, event.clientY);
    }, {passive: false});
    const onImage = event => {
      const box = big.getBoundingClientRect();
      return event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    };
    stage.addEventListener('dblclick', event => {
      if (event.target.closest('button') || !onImage(event)) return;
      scale > 1 ? resetZoom() : zoomAt(2.5, event.clientX, event.clientY);
    });
    stage.addEventListener('pointerdown', event => {
      if (event.target.closest('button')) return;
      try { stage.setPointerCapture(event.pointerId); } catch {}
      points.set(event.pointerId, {x: event.clientX, y: event.clientY});
      if (points.size === 2) {
        const [a,b] = [...points.values()];
        pinch = {distance: Math.hypot(a.x - b.x, a.y - b.y) || 1, scale, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}; drag = null;
      } else if (points.size === 1) drag = {x: event.clientX, y: event.clientY, tx, ty, moved: false, image: onImage(event)};
    });
    stage.addEventListener('pointermove', event => {
      if (!points.has(event.pointerId)) return;
      points.set(event.pointerId, {x: event.clientX, y: event.clientY});
      if (pinch && points.size === 2) {
        const [a,b] = [...points.values()];
        const target = pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / pinch.distance;
        zoomAt(target, pinch.x, pinch.y);
        return;
      }
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.abs(dx) > 6 || Math.abs(dy) > 6) drag.moved = true;
      if (scale > 1) { tx = drag.tx + dx; ty = drag.ty + dy; clampPan(); apply(); }
    });
    const end = event => {
      if (!points.has(event.pointerId)) return;
      points.delete(event.pointerId);
      if (pinch) { if (points.size < 2) pinch = null; drag = null; return; }
      if (!drag || event.type !== 'pointerup') { drag = null; return; }
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (scale === 1 && drag.moved && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
      else if (!drag.moved && event.pointerType === 'touch' && drag.image) {
        // Double tap zooms where you tapped; dblclick covers the mouse
        const now = Date.now();
        if (now - lastTap < 300) { lastTap = 0; scale > 1 ? resetZoom() : zoomAt(2.5, event.clientX, event.clientY); }
        else lastTap = now;
      }
      drag = null;
    };
    stage.addEventListener('pointerup', end); stage.addEventListener('pointercancel', end);
  }
  function toggleFullscreen() {
    if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
    viewer.requestFullscreen?.().catch(() => flash(t('no_fullscreen')));
  }
  document.addEventListener('fullscreenchange', () => { if (viewer && document.fullscreenElement !== viewer) resetZoom(); });

  /* ---- actions ---- */
  async function copyText(text, done) {
    try { await navigator.clipboard.writeText(text); flash(done); }
    catch {
      const area = el('textarea'); area.value = text; area.style.cssText = 'position:fixed;opacity:0';
      dialog.append(area); area.select();
      try { document.execCommand('copy'); flash(done); } catch { flash(t('copy_failed')); }
      area.remove();
    }
  }
  async function asPng(blob) {
    if (blob.type === 'image/png') return blob;
    const bitmap = await createImageBitmap(blob);
    const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
    canvas.getContext('2d').drawImage(bitmap, 0, 0); bitmap.close?.();
    return new Promise((resolve, reject) => canvas.toBlob(png => png ? resolve(png) : reject(new Error('encode')), 'image/png'));
  }
  async function copyImage() {
    const item = viewItem;
    if (!item) return;
    try {
      // The promise keeps Safari's user-gesture window open while the image downloads
      const png = loadBlob(item).then(asPng);
      await navigator.clipboard.write([new ClipboardItem({'image/png': png})]);
      flash(t('image_copied'));
    } catch { flash(t(external(item.url) ? 'copy_hosted' : 'copy_image_failed')); }
  }
  function save(blob, name) {
    const link = el('a'); const url = URL.createObjectURL(blob);
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
  }
  const fileNameFor = (item, blob) => slug(item.prompt || item.alt || item.name) + '.' + extFor(blob.type, item.url);
  async function downloadOne(item) {
    if (!item) return;
    if (external(item.url)) {
      try { const blob = await fetchBlob(item.url); save(blob, fileNameFor(item, blob)); }
      catch { window.open(item.url, '_blank', 'noopener,noreferrer'); }
      return;
    }
    try { const blob = await loadBlob(item); save(blob, fileNameFor(item, blob)); }
    catch { flash(t('download_failed')); }
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
  async function attachToComposer() {
    const item = viewItem;
    if (!item) return;
    try {
      const blob = await loadBlob(item);
      const input = composer();
      if (!input) { flash(t('open_chat_to_attach')); return; }
      const transfer = new DataTransfer();
      transfer.items.add(new File([blob], fileNameFor(item, blob), {type: blob.type || 'image/png'}));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
      deactivate();
      document.getElementById('chat-input')?.focus({preventScroll: true});
    } catch { flash(t(external(item.url) ? 'attach_hosted' : 'attach_failed')); }
  }

  /* ---- zip, stored without compression: images are already compressed ---- */
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
  async function downloadZip() {
    if (busy) return;
    const list = selecting ? shown.filter(item => selected.has(item)) : shown.slice();
    if (!list.length) return;
    const numbers = new Map(shown.map((item, i) => [item, i + 1]));
    const zipName = 'gallery-' + (scope === 'chat' ? slug((document.querySelector('#chat-container nav h1')?.textContent || document.title || '').trim() || 'chat') : ALL_IMAGES ? 'images' : 'generated') + '.zip';
    const controller = zipController = new AbortController();
    busy = true; paintSelection();
    const files = [], notes = [];
    let total = 0, skipped = 0, stopped = '';
    for (let i = 0; i < list.length && !stopped; i++) {
      const item = list[i];
      say(t('preparing', {i: i + 1, n: list.length}));
      try {
        if (item.fileId && !item.recordLoaded) { const record = await fileRecord(item.fileId); if (!record?.error) applyRecord(item, record); }
        const blob = await fetchBlob(item.url, controller.signal);
        total += blob.size;
        if (total > MAX_ZIP_MB * 1048576) { stopped = t('zip_stopped', {mb: MAX_ZIP_MB}); break; }
        await applyFile(item, blob);
        const name = String(numbers.get(item)).padStart(3, '0') + '-' + fileNameFor(item, blob);
        files.push({name, data: new Uint8Array(await blob.arrayBuffer())});
        const lines = [name];
        if (item.prompt || item.alt) lines.push(t('z_prompt') + ': ' + (item.prompt || item.alt));
        if (item.negative) lines.push(t('z_negative') + ': ' + item.negative);
        if (item.model) lines.push(t('z_model') + ': ' + item.model);
        if (item.created) lines.push(t('z_created') + ': ' + formatDate(item.created));
        const params = Object.entries(item.params).map(([k,v]) => k + ': ' + v).join(', ');
        if (params) lines.push(params);
        notes.push(lines.join('\n'));
      } catch { if (controller.signal.aborted) stopped = t('zip_cancelled'); else skipped++; }
    }
    busy = false; zipController = null;
    if (stopped) { flash(stopped); render(); return; }
    if (!files.length) { flash(t('zip_none')); render(); return; }
    files.push({name: 'prompts.txt', data: new TextEncoder().encode(notes.join('\n\n') + '\n')});
    save(makeZip(files), zipName);
    flash(tn('saved', files.length - 1) + (skipped ? t('skipped', {n: skipped}) : ''));
    if (selecting) setSelecting(false); else render();
  }

  /* ---- the Gallery tab in the Tabs panel ---- */
  // The panel is found by what stays stable across releases: its tab rows (.panel-tab-strip) and, from
  // v0.13.1, the split-pane groups (data-panel-group) they sit in. The Gallery tab lives in one home group.
  const tabBase = 'flex items-center gap-1.5 h-7 rounded-lg text-sm font-normal whitespace-nowrap shrink-0 transition';
  const activeClass = 'bg-gray-100/40 dark:bg-gray-800/25 text-gray-700 dark:text-gray-200';
  const idleClass = 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300';
  const headerClass = 'flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200';
  const barClass = 'flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0';
  // solo: opened with no Tabs panel on screen, so the gallery lies over the chat instead of sitting in a pane
  let tabOpen = false, active = false, solo = false, strip = null, tabEl = null, tabButton = null, homeGroup = null, menuGroup = null;
  let lastNative = '', lastGroups = -1, lastPath = null, keepUntil = 0, tracking = 0;
  const plusOf = list => list.parentElement?.querySelector(':scope > :not([role="tablist"]) > button[aria-haspopup="true"]') ?? null;
  function panel() {
    const container = document.getElementById('controls-container');
    const lists = container ? [...container.querySelectorAll('.panel-tab-strip')] : [];
    if (!lists.length || container.classList.contains('hidden')) {
      // With no Tabs panel on screen, Chat Tabs' pinned pane can hold the Gallery tab instead
      const pane = document.querySelector('#owui-chat-tabs-pane:not([hidden])');
      const slot = pane?.querySelector('.cw-pane-slot'), body = pane?.querySelector('.cw-pane-body');
      return slot && body ? {container: pane, lists: [], list: null, slot, content: body, pane: true} : null;
    }
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
      // Start below the chat's top bar, so its buttons, the Gallery button among them, stay in reach
      const bar = document.querySelector('#chat-container nav'), top = bar?.getClientRects().length ? Math.max(box.top, Math.min(bar.getBoundingClientRect().bottom, box.top + 64)) : box.top;
      const rect = {left: box.left, top, width: box.width, height: box.bottom - top};
      return rect.width > 0 && rect.height > 0 ? {rect, layer: 45} : null;
    }
    let rect;
    if (found.pane) return {rect: found.content.getBoundingClientRect(), layer: innerWidth < 768 ? 1001 : 46};
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
    // With nowhere to sit yet, as between leaving a chat and Chat Tabs showing its pane, keep looking while the tab is in front
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
  // What the panel shows under the Gallery is hidden, not covered, so a page background image shows through
  function cover() {
    const found = active && panel(), want = new Set();
    if (active && solo && !found) { const area = soloArea(); if (area) want.add(area); }
    if (found?.pane) {
    } else if (found?.group) {
      const id = found.group.dataset.panelGroup;
      for (const node of document.querySelectorAll('[data-panel-group]')) {
        if (node.dataset.panelGroup === id && node !== found.group && (node.getAttribute('role') === 'tabpanel' || node.id === 'chat-panel-main-chat-view')) want.add(node);
      }
    } else if (found) {
      if (found.content) want.add(found.content);
      if (found.full) { const main = document.getElementById('chat-panel-main-chat-view'); if (main) want.add(main); }
    }
    for (const node of document.querySelectorAll('[data-cg-covered]')) if (!want.has(node)) { node.removeAttribute('data-cg-covered'); if (node.dataset.cgInert) { node.inert = false; delete node.dataset.cgInert; } }
    for (const node of want) if (!node.hasAttribute('data-cg-covered')) { node.setAttribute('data-cg-covered', ''); if (!node.inert) { node.inert = true; node.dataset.cgInert = '1'; } }
  }
  function paintTab() {
    cover();
    if (headerButton) headerButton.setAttribute('aria-pressed', String(tabOpen && active));
    if (!tabEl) return;
    const found = panel();
    // Open WebUI 0.13.2 changed a tab's side padding; follow whichever the panel's own tabs use
    const padding = found?.list?.querySelector('[data-panel-tab]')?.classList.contains('ps-2.5') ? 'ps-2.5 pe-2' : 'px-2.5';
    const wanted = tabBase + ' ' + padding + ' ' + (active ? activeClass : idleClass);
    if (tabEl.className !== wanted) tabEl.className = wanted;
    if (tabButton.getAttribute('aria-selected') !== String(active)) tabButton.setAttribute('aria-selected', String(active));
    tabButton.tabIndex = active ? 0 : -1;
    for (const list of document.querySelectorAll('.panel-tab-strip')) list.classList.toggle('cg-dim', active && list === found?.list);
    const alone = solo && !found;
    if (dialog) { dialog.classList.toggle('cg-solo', alone); soloClose.hidden = !alone; }
    track();
  }
  function buildTab() {
    strip = el('div', 'flex items-center shrink-0'); strip.id = 'owui-chat-gallery-tabs';
    strip.setAttribute('role', 'tablist'); strip.setAttribute('aria-label', t('gallery'));
    tabEl = el('div');
    tabButton = el('button', 'flex h-full items-center gap-1.5 min-w-0'); tabButton.type = 'button'; tabButton.id = 'owui-chat-gallery-tab';
    tabButton.setAttribute('role', 'tab'); tabButton.setAttribute('aria-controls', 'owui-chat-gallery');
    const icon = svg('gallery'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14'); icon.classList.add('shrink-0');
    tabButton.append(icon, el('span', 'max-w-30 overflow-hidden text-ellipsis', t('gallery')));
    tabButton.addEventListener('click', () => activate());
    const close = el('button', 'flex items-center justify-center w-4 h-4 rounded text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white');
    close.type = 'button'; setLabel(close, t('close_gallery'));
    const x = svg('close'); x.setAttribute('width', '12'); x.setAttribute('height', '12'); close.append(x);
    close.addEventListener('click', closeTab);
    tabEl.append(tabButton, close); strip.append(tabEl);
  }
  // Chat Tabs and the Gallery take turns in front of the panel's own tabs; Chat Tabs' pinned pane stays open for an open Gallery tab
  const announce = () => dispatchEvent(new CustomEvent('owui-panel-tab-front', {detail: {owner: 'chat-gallery'}}));
  const tellState = () => dispatchEvent(new CustomEvent('owui-chat-gallery-state', {detail: {open: tabOpen}}));
  function activate() {
    if (!tabOpen) return;
    const back = !active;
    active = true; lastNative = nativeSignature(panel()?.list); announce(); paintTab();
    if (tabEl.isConnected) tabEl.scrollIntoView({block: 'nearest', inline: 'nearest'});
    // Coming back to the gallery picks up images the chat gained meanwhile
    if (back && scope === 'chat' && chatLoadedAt && !chatLoading && Date.now() - chatLoadedAt > 3000 && !viewer.classList.contains('cg-open')) loadChat(true);
  }
  function deactivate() { if (active) { active = false; paintTab(); } }
  function openTab() {
    // Open WebUI can change language without a reload; the gallery is rebuilt in the new one the next time it opens
    if (dialog && !tabOpen && builtLang !== language()) { dialog.remove(); strip?.remove(); opener?.remove(); headerButton?.remove(); dialog = strip = opener = headerButton = null; tiles.clear(); }
    if (!dialog) build();
    if (menuGroup) homeGroup = menuGroup;
    if (!strip) buildTab();
    const fresh = !tabOpen;
    tabOpen = true; solo = !panel();
    if (fresh) {
      search.value = ''; kind = 'all'; newestFirst.chat = false; newestFirst.library = true;
      library = freshLibrary();
      chatExists.clear();
      if (!me) api('/api/v1/auths/').then(user => { me = user; if (tabOpen) { paintSelection(); if (viewItem) deleteButton.hidden = !deletable(viewItem); } }).catch(() => {});
    }
    schedule(); activate(); tellState();
    if (fresh) { const chatId = chatIdFromPath(); tabChat.hidden = !chatId; setScope(chatId ? 'chat' : 'library'); }
    setTimeout(() => { if (tabOpen) (tabChat.hidden ? tabLibrary : scope === 'chat' ? tabChat : tabLibrary).focus({preventScroll: true}); });
  }
  function closeTab() {
    collectController?.abort(); library.controller.abort(); zipController?.abort(); closeViewer(true); setSelecting(false);
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    cache.clear(); records.clear(); dropThumbs();
    items = []; library = freshLibrary(); library.done = true; forgetTiles(); grid.replaceChildren();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    const found = panel(), wasSolo = solo && !found;
    tabOpen = false; active = false; solo = false; strip?.remove(); paintTab(); dialog.hidden = true;
    if (found?.list) (found.list.querySelector('[role="tab"][aria-selected="true"]') || plusOf(found.list))?.focus({preventScroll: true});
    else if (wasSolo) headerButton?.focus({preventScroll: true});
    tellState();
  }
  // A way in for people whose Tabs panel has no + menu: a button in the chat's top bar, or, when Open WebUI
  // shows its full-width tab row in place of that bar, at the end of the tab row
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
      headerButton = iconButton(t('gallery'), 'gallery', () => {
        if (tabOpen && active) { deactivate(); return; }
        if (tabOpen) { solo = !panel(); activate(); return; }
        menuGroup = headerButton.closest('[data-panel-group]')?.dataset.panelGroup ?? null; openTab();
      }, headerClass);
      headerButton.id = 'owui-chat-gallery-header'; headerButton.setAttribute('aria-pressed', 'false');
    }
    const wanted = inBar ? barClass : headerClass;
    if (headerButton.className !== wanted) headerButton.className = wanted;
    if (inBar ? headerButton.parentElement !== host || headerButton.nextElementSibling : headerButton.parentElement !== host || headerButton.previousElementSibling) inBar ? host.append(headerButton) : host.prepend(headerButton);
  }
  function sync() {
    syncPending = false;
    const found = panel();
    if (lastPath === null) lastPath = location.pathname;
    if (location.pathname !== lastPath) {
      lastPath = location.pathname; keepUntil = Date.now() + 1500;
      if (tabOpen) { chatExists.clear(); const chatId = chatIdFromPath(); tabChat.hidden = !chatId; if (!chatId && scope === 'chat') { closeViewer(true); setScope('library'); } else if (scope === 'chat') { closeViewer(true); setSelecting(false); loadChat(); } }
    }
    syncHeader(found);
    const open = found?.lists.find(list => plusOf(list)?.getAttribute('aria-expanded') === 'true');
    const menu = open && [...document.querySelectorAll('body > [role="menu"]')].pop()?.querySelector('.app-dropdown-menu');
    if (menu) {
      menuGroup = open.closest('[data-panel-group]')?.dataset.panelGroup ?? null;
      if (!opener) {
        lang = language();
        opener = el('button'); opener.type = 'button'; opener.id = 'owui-chat-gallery-button';
        const icon = svg('gallery'); icon.setAttribute('width', '14'); icon.setAttribute('height', '14');
        opener.append(icon, t('gallery'));
        opener.addEventListener('click', openTab);
      }
      if (opener.parentElement !== menu) menu.append(opener);
    } else opener?.remove();
    if (!tabOpen || !found) { if (tabOpen) paintTab(); else track(); return; }
    if (found.pane) {
      const chatTabs = found.slot.querySelector(':scope > #owui-chat-workspace-tabs');
      if (chatTabs ? strip.previousElementSibling !== chatTabs : strip.parentElement !== found.slot) chatTabs ? chatTabs.after(strip) : found.slot.prepend(strip);
      paintTab(); return;
    }
    if (found.group && found.group.dataset.panelGroup !== homeGroup) homeGroup = found.group.dataset.panelGroup;
    const anchor = found.list.nextElementSibling?.id === 'owui-chat-workspace-tabs' ? found.list.nextElementSibling : found.list;
    const moved = strip.previousElementSibling !== anchor;
    if (moved) anchor.after(strip);
    // A layout change or a new chat moves the panel's tabs about without the Gallery being set aside
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
    addEventListener('owui-panel-tab-front', event => { if (event.detail?.owner !== 'chat-gallery') deactivate(); });
    addEventListener('owui-chat-gallery-open', () => { menuGroup = null; openTab(); });
    document.documentElement.dataset.chatGallery = '1';
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
        max_zip_mb: int = Field(
            default=300,
            ge=1,
            le=2000,
            description="Largest zip, in MB, that Download builds in the browser. A zip is held in memory until it is saved, so keep this low for phones.",
        )
        library: Literal["all", "generated"] = Field(
            default="all",
            description="What the second view lists. 'all' shows My images: every image the user uploaded or generated. 'generated' shows only images generated in Open WebUI.",
        )
        allow_delete: bool = Field(
            default=True,
            description="Let people delete their own image files from the gallery. Deleting removes the file from Open WebUI and from every chat that shows it.",
        )
        load_linked_images: bool = Field(
            default=True,
            description="Show images that chats link from other websites. Turn off to keep the gallery from contacting other sites; those images are then left out and counted in the footer.",
        )
        header_button: bool = Field(
            default=True,
            description="Add a Gallery button to the chat's top bar. Without it the gallery opens only from the Tabs panel's + menu, which many user accounts don't have.",
        )
        thumbnail_px: int = Field(
            default=320,
            ge=0,
            le=1024,
            description="Size, in pixels, of the small pictures made in the browser for the grid. 0 shows the full images instead, which uses much more memory with large photos.",
        )
        language: Literal["auto", "en", "fr"] = Field(
            default="auto",
            description="Language of the gallery. 'auto' follows each person's Open WebUI language, and uses English where there is no translation.",
        )

    LOADER_BLOCK_START = "// owui-chat-gallery:start"
    LOADER_BLOCK_END = "// owui-chat-gallery:end"
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
            "library": valves.library,
            "allowDelete": bool(valves.allow_delete),
            "loadLinked": bool(valves.load_linked_images),
            "headerButton": bool(valves.header_button),
            "thumbnailPx": int(valves.thumbnail_px),
            "language": valves.language,
        }

    def _loader_fragment(self) -> str:
        # Runs on the event loop for every page load: rebuild only when a valve changed.
        key = _json.dumps(self._config(), sort_keys=True)
        if key != self._fragment_key:
            script = LOADER_SCRIPT.replace("__CSS__", _json.dumps(CSP_CSS)).replace(
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
