"""
title: Mods
description: Add features to Open WebUI by describing them. A mod is a small add-on (a counter, a timer, a / command, a panel, a theme) that runs in a sandbox with only the permissions you approve. Start from eight ready-made mods, create your own, and let administrators publish mods for everyone.
author: Muhammad Sohail
version: 1.0.0
license: MIT
required_open_webui_version: 0.13.0
"""

# Mods for Open WebUI. The two protocol blocks below are Classic298's Shared
# Assets Protocol and its live reload add-on (BSD 3-Clause), copied byte for byte.

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



MODS_CSS = r"""
.md-ui{--md-surface:#fff;--md-text:var(--color-gray-800,#262626);--md-muted:var(--color-gray-600,#5d5d5d);--md-soft:var(--color-gray-50,#f9f9f9);--md-hover:var(--color-gray-100,#ececec);--md-line:var(--color-gray-100,#ececec);--md-strong-line:var(--color-gray-200,#e3e3e3);--md-primary:var(--color-gray-900,#171717);--md-on-primary:#fff;--md-modal:#fff;--md-danger:#dc2626;--md-danger-text:#b91c1c;--md-warn:#92400e;--md-ok:#166534;--md-shadow:0 10px 30px -10px rgb(0 0 0/.25);color:var(--md-text);font-family:inherit}
.dark .md-ui{--md-surface:var(--color-gray-900,#171717);--md-text:var(--color-gray-100,#ececec);--md-muted:var(--color-gray-400,#b4b4b4);--md-soft:var(--color-gray-850,#262626);--md-hover:var(--color-gray-800,#333);--md-line:var(--color-gray-800,#333);--md-strong-line:var(--color-gray-700,#4e4e4e);--md-primary:var(--color-gray-100,#ececec);--md-on-primary:var(--color-gray-900,#171717);--md-modal:var(--color-gray-950,#0d0d0d);--md-danger:#dc2626;--md-danger-text:#f87171;--md-warn:#fbbf24;--md-ok:#4ade80;--md-shadow:0 10px 30px -10px rgb(0 0 0/.6);color-scheme:dark}
.md-ui *{box-sizing:border-box}
.md-ui button{touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.md-ui button,.md-ui input,.md-ui select,.md-ui textarea{font:inherit;color:inherit}
.md-ui :focus-visible{outline:2px solid var(--md-text)!important;outline-offset:2px!important}
.md-ui [hidden]{display:none!important}
.md-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.md-btn{display:inline-flex;align-items:center;justify-content:center;gap:.375rem;height:2rem;padding:0 .75rem;border:0;border-radius:.75rem;background:var(--md-soft);cursor:pointer;white-space:nowrap;font-size:.8125rem;touch-action:manipulation;transition:background-color .12s,color .12s,opacity .12s}
.md-btn:hover{background:var(--md-hover)}
.md-btn:disabled{opacity:.5;cursor:default}
.md-btn.md-primary{background:var(--md-primary);color:var(--md-on-primary)}
.md-btn.md-primary:hover{filter:brightness(.9)}
.dark .md-btn.md-primary:hover{filter:brightness(1.1)}
.md-btn.md-danger{background:var(--md-danger);color:#fff}
.md-btn svg,.md-icon svg{width:1rem;height:1rem;flex:none}
.md-icon{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;border:0;border-radius:.75rem;background:transparent;color:var(--md-muted);cursor:pointer;touch-action:manipulation;transition:background-color .12s,color .12s}
.md-icon:hover{background:var(--md-hover);color:var(--md-text)}
.md-icon:disabled{opacity:.4;cursor:default}
/* the bar above the message box: bands, then labels and / command hints */
#owui-mods-bar{display:flex;flex-direction:column;gap:.375rem;margin:0 .25rem .125rem;font-size:.8125rem}
#owui-mods-bar:empty{display:none}
.md-band{display:flex;align-items:center;gap:.5rem;min-height:2.25rem;padding:.25rem .375rem .25rem .75rem;border:1px solid var(--md-line);border-radius:1rem;background:var(--md-surface);box-shadow:0 1px 2px rgb(0 0 0/.04)}
.md-bandtext{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-variant-numeric:tabular-nums}
.md-bandname{flex:none;color:var(--md-muted);font-size:.75rem}
.md-band .md-btn{height:1.75rem;padding:0 .625rem;font-size:.75rem}
.md-chips{display:flex;flex-wrap:wrap;gap:.375rem;align-items:center}
.md-chip{display:inline-flex;align-items:center;gap:.375rem;max-width:100%;height:1.625rem;padding:0 .625rem;border:1px solid var(--md-line);border-radius:999px;background:var(--md-surface);color:var(--md-muted);font-size:.75rem;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button.md-chip{cursor:pointer;transition:background-color .12s,color .12s}
button.md-chip:hover{background:var(--md-soft);color:var(--md-text)}
.md-chip[data-tone=warn]{color:var(--md-warn)}
.md-chip[data-tone=ok]{color:var(--md-ok)}
.md-cmd{color:var(--md-text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
/* buttons a mod adds under an answer */
.md-ui.md-msgrow{display:flex;flex-wrap:wrap;gap:.375rem;margin:.375rem 0 .125rem}
.md-msgrow .md-msgbtn{display:inline-flex;align-items:center;gap:.25rem;height:1.625rem;margin:0;padding:0 .625rem;border:1px solid var(--md-line);border-radius:999px;background:transparent;color:var(--md-muted);font-size:.75rem;line-height:1;font-weight:400;cursor:pointer;transition:background-color .12s,color .12s}
.md-msgrow .md-msgbtn:hover{background:var(--md-soft);color:var(--md-text)}
/* a mod's own panel beside the chat */
#owui-mods-pane{position:fixed;top:0;right:0;bottom:0;z-index:45;display:flex;flex-direction:column;width:min(24rem,100vw);border-inline-start:1px solid var(--md-line);background:var(--md-surface);box-shadow:var(--md-shadow);overscroll-behavior:contain}
.md-panehead{display:flex;align-items:center;gap:.25rem;height:3.25rem;padding:0 .5rem 0 1rem;border-bottom:1px solid var(--md-line)}
.md-panetitle{flex:1;min-width:0;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.9375rem;font-weight:600}
.md-panebody{position:relative;flex:1;min-height:0}
.md-frame{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent;color-scheme:normal}
.md-frame[data-shown=false]{visibility:hidden;pointer-events:none}

/* the Mods window */
.md-dialog{position:fixed;inset:0;max-width:none;width:min(46rem,calc(100vw - 1.5rem));height:min(44rem,calc(100dvh - 1.5rem));max-height:none;margin:auto;padding:0;border:1px solid var(--md-line);border-radius:1.5rem;background:var(--md-modal);color:var(--md-text);box-shadow:var(--md-shadow);overflow:hidden;overscroll-behavior:contain}
.md-dialog[open]{display:flex;flex-direction:column}
.md-dialog:focus,.md-ask:focus{outline:none}
.md-kbd{display:inline-block;padding:0 .3rem;border:1px solid var(--md-strong-line);border-radius:.375rem;font:inherit;font-size:.75rem;white-space:nowrap;color:var(--md-text)}
.md-dialog::backdrop{background:rgb(0 0 0/.6)}
.md-dhead{display:flex;align-items:center;gap:.5rem;padding:1.125rem 1rem .75rem 1.5rem}
.md-dtitle{flex:1;margin:0;font-size:1.125rem;font-weight:600;text-wrap:balance}
.md-tabs{display:flex;gap:2px;margin:0 1.5rem;padding:2px;border-radius:.75rem;background:var(--md-soft);width:fit-content;max-width:calc(100% - 3rem);overflow-x:auto;scrollbar-width:none}
.md-tab{height:1.875rem;padding:0 .875rem;border:0;border-radius:.625rem;background:transparent;color:var(--md-muted);cursor:pointer;white-space:nowrap;font-size:.8125rem;transition:background-color .12s,color .12s}
.md-tab:hover{color:var(--md-text)}
.md-ui .md-tab:focus-visible{outline-offset:-2px!important}
.md-tab[aria-selected=true]{background:var(--md-surface);color:var(--md-text);box-shadow:0 1px 2px rgb(0 0 0/.08)}
.dark .md-tab[aria-selected=true]{background:var(--md-hover)}
.md-dbody{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:1rem 1.5rem 1.5rem}
.md-note{margin:0 0 .75rem;color:var(--md-muted);font-size:.8125rem;line-height:1.45;text-wrap:pretty}
.md-note strong{color:var(--md-text);font-weight:600}
.md-list{display:flex;flex-direction:column;gap:.5rem;margin:0;padding:0;list-style:none}
.md-card{display:flex;gap:.75rem;align-items:flex-start;padding:.75rem .75rem .75rem .875rem;border:1px solid var(--md-line);border-radius:1rem;background:var(--md-surface);min-width:0}
.md-card[data-state=failed]{border-color:color-mix(in srgb,var(--md-danger-text) 45%,var(--md-line))}
.md-cardicon{flex:none;display:flex;align-items:center;justify-content:center;width:2.25rem;height:2.25rem;border-radius:.75rem;background:var(--md-soft);font-size:1.125rem}
.md-cardicon svg{width:1.125rem;height:1.125rem;color:var(--md-muted)}
.md-cardtext{flex:1;min-width:0;display:flex;flex-direction:column;gap:.25rem}
.md-cardname{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;font-weight:600;font-size:.875rem;overflow-wrap:anywhere}
.md-carddesc{color:var(--md-muted);font-size:.8125rem;line-height:1.4;overflow-wrap:anywhere}
.md-tag{display:inline-flex;align-items:center;height:1.25rem;padding:0 .4rem;border-radius:.375rem;background:var(--md-soft);color:var(--md-muted);font-size:.6875rem;font-weight:600}
.md-tag[data-tone=warn],.md-perm[data-risk=high]{color:var(--md-warn)}
.md-tag[data-tone=bad]{color:var(--md-danger-text)}
.md-perms{display:flex;flex-wrap:wrap;gap:.25rem}
.md-perm{display:inline-flex;align-items:center;min-height:1.375rem;padding:.125rem .5rem;line-height:1.3;border:1px solid var(--md-line);border-radius:.75rem;color:var(--md-muted);font-size:.6875rem}
.md-cardactions{flex:none;display:flex;align-items:center;gap:.125rem}
.md-switch{position:relative;width:2.25rem;height:1.375rem;flex:none;border:0;border-radius:999px;background:var(--md-strong-line);cursor:pointer;transition:background-color .15s}
.md-switch::after{content:'';position:absolute;top:3px;left:3px;width:1rem;height:1rem;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgb(0 0 0/.25);transition:transform .15s}
.md-switch[aria-checked=true]{background:var(--md-primary)}
.dark .md-switch[aria-checked=true]::after{background:var(--color-gray-900,#171717)}
.md-switch[aria-checked=true]::after{transform:translateX(.875rem)}
.md-switch:disabled{opacity:.5;cursor:default}
.md-section{margin:1.25rem 0 .5rem;color:var(--md-muted);font-size:.75rem;font-weight:600;letter-spacing:.02em}
.md-section:first-child{margin-top:0}
.md-empty{display:flex;flex-direction:column;align-items:center;gap:.375rem;padding:2.5rem 1rem;color:var(--md-muted);text-align:center;font-size:.875rem}
.md-empty strong{color:var(--md-text);font-weight:500}
.md-field{display:flex;flex-direction:column;gap:.375rem;margin:0 0 .75rem;font-size:.8125rem;color:var(--md-muted)}
.md-input,.md-textarea,.md-select{width:100%;padding:.5rem .75rem;border:1px solid var(--md-line);border-radius:.75rem;background:var(--md-soft);color:var(--md-text);font-size:.875rem;outline:none;transition:border-color .12s}
.md-select{height:2.25rem;padding:0 .5rem;background-color:var(--md-soft);color:var(--md-text)}
.md-select option{background:var(--md-surface);color:var(--md-text)}
.md-textarea{min-height:5.5rem;resize:vertical;line-height:1.45}
.md-input:focus,.md-textarea:focus,.md-select:focus{border-color:var(--md-strong-line)}
.md-code{margin:0;max-height:18rem;overflow:auto;padding:.75rem;border-radius:.75rem;background:var(--md-soft);font:.75rem/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;tab-size:2}
.md-row{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.md-spacer{flex:1}
.md-error{margin:.5rem 0;padding:.5rem .75rem;border-radius:.625rem;color:var(--md-danger-text);background:color-mix(in srgb,var(--md-danger-text) 10%,transparent);font-size:.8125rem;overflow-wrap:anywhere}
.md-details{margin:.5rem 0;border:1px solid var(--md-line);border-radius:.75rem}
.md-details>summary{padding:.5rem .75rem;cursor:pointer;color:var(--md-muted);font-size:.8125rem}
.md-details[open]>summary{border-bottom:1px solid var(--md-line)}
.md-details .md-code{border-radius:0 0 .75rem .75rem}
.md-log{margin:.25rem 0 0;padding:0;list-style:none;font:.6875rem/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--md-danger-text);overflow-wrap:anywhere}
.md-spin{width:1rem;height:1rem;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:md-spin .8s linear infinite}
@keyframes md-spin{to{transform:rotate(360deg)}}
/* a small dialog over the window: permissions, confirmations */
.md-ask{position:fixed;inset:0;max-width:none;width:min(28rem,calc(100vw - 2rem));height:fit-content;max-height:calc(100dvh - 2rem);margin:auto;padding:1.5rem;border:1px solid var(--md-line);border-radius:1.5rem;background:var(--md-modal);color:var(--md-text);box-shadow:var(--md-shadow);overflow:auto;overscroll-behavior:contain}
.md-ask::backdrop{background:rgb(0 0 0/.6)}
.md-ask h3{margin:0 0 .5rem;font-size:1.0625rem;font-weight:600;text-wrap:balance;overflow-wrap:anywhere}
.md-ask p{margin:0 0 .75rem;color:var(--md-muted);font-size:.875rem;line-height:1.45}
.md-asklist{margin:0 0 1rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--md-soft);list-style:none;font-size:.8125rem;display:flex;flex-direction:column;gap:.375rem}
.md-asklist li{display:flex;gap:.5rem;align-items:flex-start}
.md-asklist svg{width:1rem;height:1rem;flex:none;margin-top:.1rem;color:var(--md-muted)}
.md-asklist [data-risk=high] svg,.md-asklist [data-risk=high]{color:var(--md-warn)}
.md-actions{display:flex;justify-content:flex-end;gap:.5rem;flex-wrap:wrap}
#owui-mods-toast{position:fixed;left:50%;bottom:1.25rem;translate:-50% 0;z-index:9999;max-width:calc(100vw - 2rem);padding:.5rem .875rem;border-radius:999px;background:var(--md-primary);color:var(--md-on-primary);font-size:.8125rem;pointer-events:none;opacity:0;transition:opacity .15s}
#owui-mods-toast[data-show=true]{opacity:1}
#owui-mods-menu-item svg,#owui-mods-plus svg{pointer-events:none}
@media (prefers-reduced-motion:reduce){.md-ui *,#owui-mods-toast{transition:none!important;animation:none!important}}
@media (forced-colors:active){.md-btn,.md-card,.md-chip,.md-switch,.md-tab{border:1px solid CanvasText}.md-switch[aria-checked=true]{background:Highlight}}
@media (max-width:640px){.md-dialog{width:100vw;height:100dvh;border-radius:0;border:0}.md-dbody{padding:.75rem 1rem 1rem}.md-tabs{margin:0 1rem}.md-dhead{padding:.75rem .5rem .5rem 1rem}#owui-mods-pane{width:100vw}.md-card{flex-wrap:wrap}.md-cardactions{width:100%;justify-content:flex-end}}
"""

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiMods) return;
  window.__owuiMods = true;
  const CONFIG = __CONFIG__;

  /* ---- words, in the language Open WebUI is set to ---- */
  const STRINGS = {
    en: {
      mods: 'Mods', open_mods: 'Open Mods', close: 'Close', installed: 'Installed', discover: 'Discover', create: 'Create',
      intro: 'Mods add small features to Open WebUI: a counter, a timer, a / command, a panel, a theme. Each one runs in a sandbox and can only do what you allow.',
      safe_mode: 'Safe mode: mods are switched off in this tab.', leave_safe: 'Turn Mods Back On', safe_on: 'Open in Safe Mode',
      off_admin: 'An administrator has switched mods off.', no_user_mods: 'Only mods published by your administrators can be installed here.',
      none_installed: 'No mods yet', none_installed_hint: 'Install a ready-made mod from Discover, or describe one in Create.',
      starters: 'Ready-made', organisation: 'From your organisation', none_published: 'Nothing published yet.',
      src_user: 'Yours', src_starter: 'Ready-made', src_catalog: 'Organisation', pinned: 'For everyone', failed: 'Stopped', update: 'Update available', running: 'On',
      enable: 'Turn on {name}', disable: 'Turn off {name}', install: 'Install', installed_ok: '{name} installed', update_btn: 'Update', updated: '{name} updated', reinstall: 'Installed',
      more: 'More for {name}', edit: 'Edit in Create', export: 'Export', remove: 'Remove', publish: 'Publish for Everyone', unpublish: 'Unpublish', pin: 'Turn On for Everyone', unpin: 'Make Optional',
      published: '{name} published', unpublished: '{name} unpublished', removed: '{name} removed', log_title: 'Last errors',
      ask_install: 'Install “{name}”?', ask_update: 'Update “{name}”?', ask_try: 'Try “{name}”?', ask_can: 'It will be able to:', ask_nothing: 'It asks for no permissions: it can show messages and keep its own settings.',
      ask_sandbox: 'Mods run in a sandbox: they can’t see your password, your other chats or other sites, and only use what you allow here.',
      ask_trust: 'Only install mods from people you trust: a mod that can read your chat could pass it on.',
      ask_more: 'This version asks for more than before.', cancel: 'Cancel', allow_install: 'Install', allow_update: 'Update', allow_try: 'Try It',
      ask_remove: 'Remove “{name}”?', ask_remove_text: 'Its settings on this device are deleted too.', ask_remove_pinned: 'It was turned on for everyone; it won’t come back unless your administrator changes it.',
      perm_composer_read: 'Read what you type in the message box', perm_composer_write: 'Write in the message box', perm_chat_read: 'Read the messages of the chat you have open',
      perm_chat_send: 'Send messages in your name', perm_ui_status: 'Show a label above the message box', perm_ui_band: 'Show a bar above the message box',
      perm_ui_pane: 'Open its own panel beside the chat', perm_ui_style: 'Change how Open WebUI looks', perm_commands: 'Add / commands to the message box',
      perm_message_buttons: 'Add buttons under answers, and read the answer a button is under', perm_ai: 'Ask the AI model (counts toward your usage)', perm_download: 'Save files to your device',
      not_allowed: 'This server doesn’t allow: {list}. Ask your administrator, or remove it from the mod.', too_many: 'You can have up to {n} mods. Remove one first.',
      describe: 'Describe the feature', describe_ph: 'For example: a timer above the message box that counts how long I’ve been studying…', model: 'Model',
      generate: 'Create Mod', generating: 'Creating…', change: 'Describe a change', change_ph: 'For example: make it count characters too…', apply_change: 'Change Mod',
      no_model: 'No model is available to create mods.', gen_failed: 'The model didn’t return a mod. Try again or describe it differently.', ai_failed: 'Couldn’t reach the model: {error}',
      paste: 'Paste or import a mod', paste_ph: 'name: My mod\npermissions: ui.status\n---\nmod.status.set(\'Hello\');', import_file: 'Import File…', read_mod: 'Read Mod', bad_mod: 'This isn’t a mod: {error}',
      preview: 'Preview', code: 'Code', try_it: 'Try It', stop_try: 'Stop Trying', trying: 'Trying it now; nothing is saved.', save_install: 'Install', save_update: 'Save Changes',
      e_header: 'the settings must end with a line containing only ---', e_name: 'it needs a name of 1 to 60 characters', e_code: 'it has no code', e_long: 'its code is longer than {n} characters', e_perm: '“{value}” isn’t a permission', e_key: '“{value}” isn’t a setting',
      saved_failed: 'Couldn’t save your mods: {error}. Try again in a moment.', load_failed: 'Couldn’t load your mods. Reload the page to try again.', stopped_errors: '{name} was turned off: it kept failing.', stopped_escape: '{name} was turned off: it tried to leave its sandbox.',
      cmd_taken: 'The command /{name} is already used by another mod.', cmd_mod: 'Describe a feature and create it as a mod', copied: 'Copied',
      shortcut: 'Shortcut:', for_everyone_note: 'Published mods are part of the page every signed-in person loads, so anyone can read their code.'
    },
    fr: {
      mods: 'Mods', open_mods: 'Ouvrir les mods', close: 'Fermer', installed: 'Installés', discover: 'Découvrir', create: 'Créer',
      intro: 'Les mods ajoutent de petites fonctions à Open WebUI : un compteur, un minuteur, une commande /, un panneau, un thème. Chacun fonctionne dans un bac à sable et ne peut faire que ce que vous autorisez.',
      safe_mode: 'Mode sans échec : les mods sont désactivés dans cet onglet.', leave_safe: 'Réactiver les mods', safe_on: 'Ouvrir en mode sans échec',
      off_admin: 'Un administrateur a désactivé les mods.', no_user_mods: 'Seuls les mods publiés par vos administrateurs peuvent être installés ici.',
      none_installed: 'Pas encore de mod', none_installed_hint: 'Installez un mod prêt à l’emploi depuis Découvrir, ou décrivez-en un dans Créer.',
      starters: 'Prêts à l’emploi', organisation: 'De votre organisation', none_published: 'Rien n’est encore publié.',
      src_user: 'À vous', src_starter: 'Prêt à l’emploi', src_catalog: 'Organisation', pinned: 'Pour tous', failed: 'Arrêté', update: 'Mise à jour disponible', running: 'Actif',
      enable: 'Activer {name}', disable: 'Désactiver {name}', install: 'Installer', installed_ok: '{name} installé', update_btn: 'Mettre à jour', updated: '{name} mis à jour', reinstall: 'Installé',
      more: 'Plus pour {name}', edit: 'Modifier dans Créer', export: 'Exporter', remove: 'Supprimer', publish: 'Publier pour tous', unpublish: 'Retirer la publication', pin: 'Activer pour tous', unpin: 'Rendre facultatif',
      published: '{name} publié', unpublished: '{name} retiré', removed: '{name} supprimé', log_title: 'Dernières erreurs',
      ask_install: 'Installer « {name} » ?', ask_update: 'Mettre à jour « {name} » ?', ask_try: 'Essayer « {name} » ?', ask_can: 'Il pourra :', ask_nothing: 'Il ne demande aucune permission : il peut afficher des messages et garder ses propres réglages.',
      ask_sandbox: 'Les mods fonctionnent dans un bac à sable : ils ne voient ni votre mot de passe, ni vos autres conversations, ni d’autres sites, et n’utilisent que ce que vous autorisez ici.',
      ask_trust: 'N’installez que des mods de personnes de confiance : un mod qui peut lire votre conversation pourrait la transmettre.',
      ask_more: 'Cette version demande plus qu’avant.', cancel: 'Annuler', allow_install: 'Installer', allow_update: 'Mettre à jour', allow_try: 'Essayer',
      ask_remove: 'Supprimer « {name} » ?', ask_remove_text: 'Ses réglages sur cet appareil sont aussi supprimés.', ask_remove_pinned: 'Il était activé pour tous ; il ne reviendra pas sauf si votre administrateur le change.',
      perm_composer_read: 'Lire ce que vous tapez dans la zone de message', perm_composer_write: 'Écrire dans la zone de message', perm_chat_read: 'Lire les messages de la conversation ouverte',
      perm_chat_send: 'Envoyer des messages en votre nom', perm_ui_status: 'Afficher une étiquette au-dessus de la zone de message', perm_ui_band: 'Afficher une barre au-dessus de la zone de message',
      perm_ui_pane: 'Ouvrir son propre panneau à côté de la conversation', perm_ui_style: 'Changer l’apparence d’Open WebUI', perm_commands: 'Ajouter des commandes / à la zone de message',
      perm_message_buttons: 'Ajouter des boutons sous les réponses, et lire la réponse sous laquelle un bouton se trouve', perm_ai: 'Interroger le modèle d’IA (compte dans votre utilisation)', perm_download: 'Enregistrer des fichiers sur votre appareil',
      not_allowed: 'Ce serveur n’autorise pas : {list}. Demandez à votre administrateur, ou retirez-le du mod.', too_many: 'Vous pouvez avoir jusqu’à {n} mods. Supprimez-en un d’abord.',
      describe: 'Décrivez la fonction', describe_ph: 'Par exemple : un minuteur au-dessus de la zone de message qui compte depuis combien de temps j’étudie…', model: 'Modèle',
      generate: 'Créer le mod', generating: 'Création…', change: 'Décrivez une modification', change_ph: 'Par exemple : compte aussi les caractères…', apply_change: 'Modifier le mod',
      no_model: 'Aucun modèle n’est disponible pour créer des mods.', gen_failed: 'Le modèle n’a pas renvoyé de mod. Réessayez ou décrivez-le autrement.', ai_failed: 'Impossible de joindre le modèle : {error}',
      paste: 'Coller ou importer un mod', paste_ph: 'name: Mon mod\npermissions: ui.status\n---\nmod.status.set(\'Bonjour\');', import_file: 'Importer un fichier…', read_mod: 'Lire le mod', bad_mod: 'Ce n’est pas un mod : {error}',
      preview: 'Aperçu', code: 'Code', try_it: 'Essayer', stop_try: 'Arrêter l’essai', trying: 'Essai en cours ; rien n’est enregistré.', save_install: 'Installer', save_update: 'Enregistrer',
      e_header: 'les réglages doivent finir par une ligne contenant seulement ---', e_name: 'il faut un nom de 1 à 60 caractères', e_code: 'il n’a pas de code', e_long: 'son code dépasse {n} caractères', e_perm: '« {value} » n’est pas une permission', e_key: '« {value} » n’est pas un réglage',
      saved_failed: 'Impossible d’enregistrer vos mods : {error}. Réessayez dans un instant.', load_failed: 'Impossible de charger vos mods. Rechargez la page pour réessayer.', stopped_errors: '{name} a été désactivé : il échouait sans cesse.', stopped_escape: '{name} a été désactivé : il a tenté de sortir de son bac à sable.',
      cmd_taken: 'La commande /{name} est déjà utilisée par un autre mod.', cmd_mod: 'Décrire une fonction et la créer comme mod', copied: 'Copié',
      shortcut: 'Raccourci :', for_everyone_note: 'Les mods publiés font partie de la page que chaque personne connectée charge : tout le monde peut lire leur code.'
    }
  };
  const language = () => {
    if (STRINGS[CONFIG.language]) return CONFIG.language;
    let locale = document.documentElement.getAttribute('lang') || '';
    if (!locale) { try { locale = localStorage.getItem('locale') || ''; } catch {} }
    locale = String(locale || navigator.language || 'en').toLowerCase();
    return STRINGS[locale.slice(0, 2)] ? locale.slice(0, 2) : 'en';
  };
  let lang = language();
  const t = (key, vars) => { const text = STRINGS[lang][key] ?? STRINGS.en[key] ?? key; return vars ? text.replace(/\{(\w+)\}/g, (whole, name) => name in vars ? String(vars[name]) : whole) : text; };

  // Every state that changes over time is a small machine; its state is shown on <html> as data-md-<name>
  function machine(name, initial, table, onEnter) {
    let current = initial;
    const attr = 'md' + name.charAt(0).toUpperCase() + name.slice(1);
    const write = () => { try { document.documentElement.dataset[attr] = current; } catch {} };
    write();
    return {
      get state() { return current; }, is: (...states) => states.includes(current),
      send(event, data) { const rule = table[current]?.[event] ?? table['*']?.[event]; if (rule === undefined) return false; const next = typeof rule === 'function' ? rule(data, current) : rule; if (!next) return false; const from = current; current = next; write(); onEnter?.(next, from, event, data); return true; }
    };
  }

  /* ---- small helpers ---- */
  const ICONS = {
    mods: 'M14.25 6.087c0-.355.186-.676.401-.959.221-.29.349-.634.349-1.003 0-1.036-1.007-1.875-2.25-1.875s-2.25.84-2.25 1.875c0 .369.128.713.349 1.003.215.283.401.604.401.959v0a.64.64 0 0 1-.657.643 48.39 48.39 0 0 1-4.163-.3c.186 1.613.293 3.25.315 4.907a.656.656 0 0 1-.658.663v0c-.355 0-.676-.186-.959-.401a1.647 1.647 0 0 0-1.003-.349c-1.036 0-1.875 1.007-1.875 2.25s.84 2.25 1.875 2.25c.369 0 .713-.128 1.003-.349.283-.215.604-.401.959-.401v0c.31 0 .555.26.532.57a48.039 48.039 0 0 1-.642 5.056c1.518.19 3.058.309 4.616.354a.64.64 0 0 0 .657-.643v0c0-.355-.186-.676-.401-.959a1.647 1.647 0 0 1-.349-1.003c0-1.035 1.008-1.875 2.25-1.875 1.243 0 2.25.84 2.25 1.875 0 .369-.128.713-.349 1.003-.215.283-.4.604-.4.959v0c0 .333.277.599.61.58a48.1 48.1 0 0 0 5.427-.63 48.05 48.05 0 0 0 .582-4.717.532.532 0 0 0-.533-.57v0c-.355 0-.676.186-.959.401-.29.221-.634.349-1.003.349-1.035 0-1.875-1.007-1.875-2.25s.84-2.25 1.875-2.25c.37 0 .713.128 1.003.349.283.215.604.401.96.401v0a.656.656 0 0 0 .658-.663 48.422 48.422 0 0 0-.37-5.36c-1.886.342-3.81.574-5.766.689a.578.578 0 0 1-.61-.58v0Z',
    close: 'M6 18 18 6M6 6l12 12', more: 'M6.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM12.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM18.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Z',
    check: 'm4.5 12.75 6 6 9-13.5', shield: 'M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z',
    warn: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
    sparkle: 'M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z',
    upload: 'M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5'
  };
  const svgIcon = name => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [k, v] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false'})) node.setAttribute(k, v);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', ICONS[name] || ICONS.mods); node.append(path);
    return node;
  };
  const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
  const setLabel = (node, label) => { node.setAttribute('aria-label', label); node.title = label; };
  const button = (label, action, cls = 'md-btn') => { const node = el('button', cls, label); node.type = 'button'; node.addEventListener('click', action); return node; };
  const iconButton = (label, icon, action, cls = 'md-icon') => { const node = el('button', cls); node.type = 'button'; setLabel(node, label); node.append(svgIcon(icon)); node.addEventListener('click', action); return node; };
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clip = (value, n) => String(value ?? '').slice(0, n);

  /* ---- talking to Open WebUI: this site only ---- */
  const token = () => { try { return localStorage.token || ''; } catch { return ''; } };
  async function request(url, options = {}, timeout = 30000) {
    let own = false; try { own = new URL(url, location.href).origin === location.origin; } catch {}
    if (!own) throw new Error('Refusing a request to another site');
    const timer = new AbortController(), clock = setTimeout(() => timer.abort(), timeout);
    try { return await fetch(url, {...options, headers: {...(options.headers || {}), ...(token() ? {Authorization: 'Bearer ' + token()} : {})}, credentials: 'same-origin', signal: timer.signal}); }
    finally { clearTimeout(clock); }
  }
  async function api(path, options = {}, timeout) {
    const response = await request(path, {headers: {Accept: 'application/json', ...(options.body && typeof options.body === 'string' ? {'Content-Type': 'application/json'} : {})}, ...options}, timeout);
    const text = await response.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch {}
    if (!response.ok) { const error = new Error(clip(data?.detail || ('HTTP ' + response.status), 300)); error.status = response.status; throw error; }
    return data;
  }
  const chatIdNow = () => { const m = /^\/c\/([^/?#]+)/.exec(location.pathname); return m ? decodeURIComponent(m[1]) : null; };
  const safeMode = (() => { try { return new URLSearchParams(location.search).get('mods') === 'off' || sessionStorage.getItem('owui-mods-safe') === '1'; } catch { return false; } })();

  /* ---- what a mod is ---- */
  // A mod is text: settings ("name:", "description:", "permissions:", "version:", "icon:"), a line "---", then its JavaScript
  const PERMS = {'composer.read': 'low', 'composer.write': 'low', 'chat.read': 'high', 'chat.send': 'high', 'ui.status': 'low', 'ui.band': 'low', 'ui.pane': 'low', 'ui.style': 'medium', 'commands': 'low', 'message.buttons': 'medium', 'ai': 'medium', 'download': 'low'};
  const CODE_LIMIT = 60000;
  const permLabel = perm => t('perm_' + perm.replace('.', '_'));
  const slug = name => String(name).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'mod';
  function parseMod(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const fence = lines.findIndex(line => /^\s*(```|~~~)\s*mod\b/i.test(line));
    let body = lines;
    if (fence >= 0) { const end = lines.findIndex((line, i) => i > fence && /^\s*(```|~~~)\s*$/.test(line)); body = lines.slice(fence + 1, end > fence ? end : undefined); }
    const split = body.findIndex(line => /^\s*---\s*$/.test(line));
    if (split < 0) throw new Error(t('e_header'));
    const spec = {name: '', description: '', permissions: [], version: 1, icon: '', id: ''};
    for (const line of body.slice(0, split)) {
      if (!line.trim() || /^\s*(\/\/|#)/.test(line)) continue;
      const m = /^\s*([a-z]+)\s*:\s*(.*?)\s*$/i.exec(line);
      if (!m) throw new Error(t('e_key', {value: clip(line.trim(), 40)}));
      const key = m[1].toLowerCase(), value = m[2];
      if (key === 'name') spec.name = value;
      else if (key === 'description') spec.description = clip(value, 200);
      else if (key === 'version') spec.version = clamp(parseInt(value, 10) || 1, 1, 100000);
      else if (key === 'icon') spec.icon = [...value].slice(0, 2).join('');
      else if (key === 'id') spec.id = slug(value);
      else if (key === 'permissions' || key === 'permission') {
        for (const each of value.split(/[\s,]+/).filter(Boolean)) { const perm = each.toLowerCase(); if (!PERMS[perm]) throw new Error(t('e_perm', {value: clip(each, 30)})); if (!spec.permissions.includes(perm)) spec.permissions.push(perm); }
      } else throw new Error(t('e_key', {value: clip(key, 30)}));
    }
    spec.name = spec.name.trim();
    if (!spec.name || spec.name.length > 60) throw new Error(t('e_name'));
    const code = body.slice(split + 1).join('\n').replace(/^\s*\n|\s*$/g, '');
    if (!code) throw new Error(t('e_code'));
    if (code.length > CODE_LIMIT) throw new Error(t('e_long', {n: CODE_LIMIT.toLocaleString(lang)}));
    spec.code = code; spec.id = spec.id || slug(spec.name);
    return spec;
  }
  const modText = mod => ['name: ' + mod.name, mod.description ? 'description: ' + mod.description : '', 'version: ' + (mod.version || 1), mod.icon ? 'icon: ' + mod.icon : '', 'permissions: ' + (mod.permissions || []).join(', '), '---', mod.code, ''].filter((line, i) => line || i >= 5).join('\n');

  /* ---- the person and their mods ---- */
  let me = null, data = {mods: [], dismissed: []}, loaded = false;
  const store = machine('store', 'idle', {'*': {load: 'loading'}, loading: {done: 'ready', fail: 'failed'}, ready: {save: 'saving'}, saving: {done: 'ready', fail: 'ready'}});
  let saving = Promise.resolve();
  const valvesUrl = '/api/v1/functions/id/' + encodeURIComponent(CONFIG.functionId) + '/valves';
  async function loadMine() {
    store.send('load');
    try {
      me = me || await api('/api/v1/auths/');
      const valves = await api(valvesUrl + '/user');
      const parsed = valves?.mods ? JSON.parse(valves.mods) : null;
      data = {mods: Array.isArray(parsed?.mods) ? parsed.mods : [], dismissed: Array.isArray(parsed?.dismissed) ? parsed.dismissed : []};
      loaded = true; store.send('done');
    } catch { store.send('fail'); }
  }
  // Writes go one after another; the server checks every mod again before it keeps it
  function saveMine() {
    saving = saving.then(async () => {
      store.send('save');
      try { await api(valvesUrl + '/user/update', {method: 'POST', body: JSON.stringify({mods: JSON.stringify(data)})}); store.send('done'); }
      catch (error) { store.send('fail'); toast(t('saved_failed', {error: error.message})); throw error; }
    });
    return saving;
  }
  const isAdmin = () => me?.role === 'admin';
  const allowedPerm = perm => CONFIG.allowedPermissions.includes(perm);
  const catalog = () => (CONFIG.catalog || []).map(m => ({...m, source: 'catalog'}));
  const starters = () => (CONFIG.starters || []).map(m => ({...m, source: 'starter'}));
  const findInstalled = id => data.mods.find(m => m.id === id);

  /* ---- toasts ---- */
  let toastEl = null;
  function toast(text) {
    if (!toastEl) { toastEl = el('div', 'md-ui'); toastEl.id = 'owui-mods-toast'; toastEl.setAttribute('role', 'status'); toastEl.setAttribute('aria-live', 'polite'); document.body.append(toastEl); }
    toastEl.textContent = clip(text, 200); toastEl.dataset.show = 'true';
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { toastEl.dataset.show = 'false'; }, 2800);
  }

  /* ---- theme handed to mods, so their panels match light and dark ---- */
  function themeNow() {
    const probe = el('div', 'md-ui'); probe.style.cssText = 'position:fixed;visibility:hidden'; document.body.append(probe);
    const cs = getComputedStyle(probe), v = name => cs.getPropertyValue(name).trim();
    const read = name => { const box = el('span'); box.style.color = v(name); probe.append(box); const value = getComputedStyle(box).color; box.remove(); return value; };
    const vars = {bg: read('--md-surface'), text: read('--md-text'), muted: read('--md-muted'), soft: read('--md-soft'), line: read('--md-line'), accent: read('--md-primary'), 'on-accent': read('--md-on-primary'), danger: read('--md-danger-text')};
    probe.remove();
    return {dark: document.documentElement.classList.contains('dark'), lang: document.documentElement.lang || lang, font: getComputedStyle(document.body).fontFamily, vars};
  }

  /* ---- the sandbox each mod runs in ---- */
  // A frame with scripts but no origin of its own: it can't read this page, its storage or its sign-in, and its
  // content policy lets it load nothing from any network. Everything else goes through the bridge below, checked
  // against the permissions the person granted.
  const SANDBOX = 'allow-scripts';
  const FRAME_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; worker-src 'none'; manifest-src 'none'";
  const FRAME_CSS = ":root{--mod-bg:#fff;--mod-text:#262626;--mod-muted:#5d5d5d;--mod-soft:#f9f9f9;--mod-line:#ececec;--mod-accent:#171717;--mod-on-accent:#fff;--mod-danger:#b91c1c}html,body{margin:0;background:transparent;color:var(--mod-text);font:14px/1.5 var(--mod-font,system-ui,sans-serif)}body{padding:16px}*{box-sizing:border-box}button{font:inherit;height:32px;padding:0 12px;border:0;border-radius:12px;background:var(--mod-soft);color:var(--mod-text);cursor:pointer}button:hover{filter:brightness(.96)}button.primary{background:var(--mod-accent);color:var(--mod-on-accent)}input,textarea,select{font:inherit;width:100%;padding:8px 12px;border:1px solid var(--mod-line);border-radius:12px;background:var(--mod-soft);color:var(--mod-text)}:focus-visible{outline:2px solid var(--mod-text);outline-offset:2px}a{color:inherit}h1,h2,h3{margin:0 0 8px;line-height:1.25}.muted{color:var(--mod-muted)}.card{padding:12px;border:1px solid var(--mod-line);border-radius:14px;background:var(--mod-bg)}@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}";
  function modBoot(NONCE, THEME) {
    'use strict';
    const host = window.parent;
    let seq = 0, theme = THEME;
    const waiting = new Map(), listeners = {}, commands = new Map(), buttons = new Map(), actions = new Map();
    const post = (type, payload) => host.postMessage({__mods: NONCE, type, data: payload}, '*');
    const call = (name, payload) => new Promise((resolve, reject) => { const id = ++seq; waiting.set(id, {resolve, reject}); post('call', {id, name, data: payload}); setTimeout(() => { if (waiting.delete(id)) reject(new Error('No answer from Open WebUI')); }, 60000); });
    const report = error => post('error', {message: String((error && (error.stack || error.message)) || error).slice(0, 1200)});
    const applyTheme = next => {
      theme = next; const root = document.documentElement;
      for (const [key, value] of Object.entries(next.vars || {})) root.style.setProperty('--mod-' + key, value);
      if (next.font) root.style.setProperty('--mod-font', next.font);
      root.dataset.theme = next.dark ? 'dark' : 'light'; root.style.colorScheme = next.dark ? 'dark' : 'light'; root.lang = next.lang || 'en';
    };
    applyTheme(THEME);
    window.addEventListener('message', event => {
      if (event.source !== host) return;
      const message = event.data;
      if (!message || message.__mods !== NONCE) return;
      if (message.type === 'reply') { const w = waiting.get(message.data.id); if (!w) return; waiting.delete(message.data.id); if (message.data.error) w.reject(new Error(message.data.error)); else w.resolve(message.data.value); return; }
      if (message.type !== 'event') return;
      const {name, payload, reply} = message.data;
      if (name === 'theme') applyTheme(payload);
      let result;
      try {
        if (name === 'command') { const fn = commands.get(payload.name); result = fn ? fn(payload.args || '') : undefined; }
        else if (name === 'button') { const fn = buttons.get(payload.id); result = fn ? fn(payload.message) : undefined; }
        else if (name === 'action') { const fn = actions.get(payload.id); result = fn ? fn() : undefined; }
        for (const fn of listeners[name] || []) { try { fn(payload); } catch (error) { report(error); } }
      } catch (error) { report(error); if (reply) post('result', {id: reply, error: String(error && error.message || error)}); return; }
      if (reply) Promise.resolve(result).then(value => post('result', {id: reply, value: value === undefined ? null : value}), error => { report(error); post('result', {id: reply, error: String(error && error.message || error)}); });
    });
    window.addEventListener('error', event => report(event.error || event.message));
    window.addEventListener('unhandledrejection', event => report(event.reason));
    const text = value => String(value ?? '');
    const mod = {
      on(name, fn) { if (typeof fn !== 'function') throw new TypeError('mod.on needs a function'); (listeners[name] = listeners[name] || []).push(fn); post('on', {name: text(name)}); },
      toast: message => call('toast', text(message)),
      log: (...parts) => post('log', {message: parts.map(text).join(' ').slice(0, 500)}),
      status: {
        set(label, options = {}) { if (typeof options.onClick === 'function') actions.set('status', options.onClick); else actions.delete('status'); return call('status.set', {text: text(label), tone: text(options.tone), clickable: typeof options.onClick === 'function'}); },
        clear: () => call('status.clear')
      },
      band: {
        set(spec = {}) { const list = (spec.actions || []).slice(0, 3).map((action, i) => { actions.set('band' + i, typeof action.onClick === 'function' ? action.onClick : () => {}); return {id: 'band' + i, label: text(action.label)}; }); return call('band.set', {text: text(spec.text), actions: list}); },
        clear: () => call('band.clear')
      },
      pane: {open: (options = {}) => call('pane.open', {title: text(options.title)}), close: () => call('pane.close')},
      composer: {get: () => call('composer.get'), set: value => call('composer.set', text(value)), insert: value => call('composer.insert', text(value))},
      chat: {current: () => call('chat.current'), send: value => call('chat.send', text(value)), goto: id => call('chat.goto', text(id))},
      commands: {add(name, description, fn) { if (typeof description === 'function') { fn = description; description = ''; } const key = text(name).replace(/^\//, '').toLowerCase(); commands.set(key, fn); return call('commands.add', {name: key, description: text(description)}); }},
      messages: {button(label, fn) { const id = 'b' + buttons.size; buttons.set(id, fn); return call('messages.button', {id, label: text(label)}); }},
      style: css => call('style.set', text(css)),
      ai: {complete: prompt => call('ai.complete', text(prompt))},
      storage: {get: key => call('storage.get', text(key)), set: (key, value) => call('storage.set', {key: text(key), value}), remove: key => call('storage.remove', text(key))},
      download: (name, content, type) => call('download', {name: text(name), content: text(content), type: text(type || 'text/plain')}),
      theme: () => JSON.parse(JSON.stringify(theme))
    };
    Object.freeze(mod.status); Object.freeze(mod.band); Object.freeze(mod.pane); Object.freeze(mod.composer); Object.freeze(mod.chat); Object.freeze(mod.commands); Object.freeze(mod.messages); Object.freeze(mod.ai); Object.freeze(mod.storage);
    Object.defineProperty(window, 'mod', {value: Object.freeze(mod), writable: false, configurable: false});
    window.__modReport = report;
    post('ready', {});
  }
  const scriptSafe = code => String(code).replace(/<\/(script)/gi, '<\\/$1').replace(/<!--/g, '<\\!--');
  function frameDoc(mod, nonce) {
    const boot = '(' + modBoot.toString() + ')(' + JSON.stringify(nonce) + ',' + JSON.stringify(themeNow()) + ');';
    const run = '(async function(mod){"use strict";\n' + mod.code + '\n})(window.mod).catch(function(e){window.__modReport&&window.__modReport(e)});';
    return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + FRAME_CSP + '"><meta name="viewport" content="width=device-width,initial-scale=1"><style>' + FRAME_CSS + '</style><script>' + scriptSafe(boot) + '</script></head><body><script>' + scriptSafe(run) + '</script></body></html>';
  }

  /* ---- running mods ---- */
  const runners = new Map();
  const randomId = () => { const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); return [...bytes].map(b => b.toString(16).padStart(2, '0')).join(''); };
  let pane = null, paneTitle = null, paneBody = null, openPane = null;
  function ensureFramesHost() {
    if (!pane) {
      pane = el('aside', 'md-ui'); pane.id = 'owui-mods-pane'; pane.hidden = true; pane.setAttribute('aria-labelledby', 'owui-mods-pane-title');
      const head = el('div', 'md-panehead'); paneTitle = el('h2', 'md-panetitle'); paneTitle.id = 'owui-mods-pane-title'; paneTitle.dir = 'auto';
      head.append(paneTitle, iconButton(t('close'), 'close', () => closePane()));
      paneBody = el('div', 'md-panebody');
      pane.append(head, paneBody);
      pane.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closePane(); } });
      document.body.append(pane);
    }
  }
  // A mod's frame never moves once made (moving a frame reloads it); its panel shows it where it already is
  function start(mod, {preview = false} = {}) {
    if (runners.has(mod.id)) stop(mod.id);
    if (!CONFIG.enabled || safeMode || !permitted(mod)) return null;
    ensureFramesHost();
    const nonce = randomId();
    const frame = el('iframe', 'md-frame'); frame.setAttribute('sandbox', SANDBOX); frame.setAttribute('referrerpolicy', 'no-referrer'); frame.setAttribute('allow', '');
    frame.title = mod.name; frame.dataset.shown = 'false'; frame.dataset.mod = mod.id;
    const runner = {mod, frame, nonce, preview, granted: new Set((mod.granted || mod.permissions || []).filter(allowedPerm)), subs: new Set(), status: null, band: null, buttons: [], commands: [], style: null, errors: [], loads: 0, toasts: [], ai: [], sends: [], pending: new Map(), seq: 0};
    frame.addEventListener('load', () => {
      // The first load is the document we wrote; any other means the mod navigated its frame somewhere else
      if (++runner.loads > 1) { stop(mod.id); fail(mod, 'stopped_escape'); }
    });
    frame.srcdoc = frameDoc(mod, nonce);
    paneBody.append(frame);
    runners.set(mod.id, runner);
    return runner;
  }
  // Where people may not write mods, only code identical to a ready-made or published mod runs (administrators excepted)
  function permitted(mod) {
    if (CONFIG.allowUserMods || isAdmin()) return true;
    return [...starters(), ...catalog()].some(known => known.code === mod.code);
  }
  function stop(id) {
    const runner = runners.get(id);
    if (!runner) return;
    runners.delete(id);
    if (openPane === runner) closePane();
    runner.frame.remove(); runner.style?.remove();
    for (const [, waiter] of runner.pending) waiter.reject(new Error('stopped'));
    paintBar(); paintMessageRows(true);
  }
  async function fail(mod, key) {
    toast(t(key, {name: mod.name}));
    const installed = findInstalled(mod.id);
    if (installed && installed.enabled) { installed.enabled = false; installed.failed = true; await saveMine().catch(() => {}); repaintDialog(); }
  }
  const can = (runner, perm) => runner.granted.has(perm);
  function send(runner, type, payload) { try { runner.frame.contentWindow?.postMessage({__mods: runner.nonce, type, data: payload}, '*'); } catch {} }
  function emit(runner, name, payload, wantResult = false) {
    if (!wantResult) { send(runner, 'event', {name, payload}); return Promise.resolve(); }
    const id = ++runner.seq;
    return new Promise((resolve, reject) => {
      runner.pending.set(id, {resolve, reject});
      send(runner, 'event', {name, payload, reply: id});
      setTimeout(() => { if (runner.pending.delete(id)) reject(new Error('timeout')); }, 15000);
    });
  }
  const tooOften = (list, max, ms) => { const now = Date.now(); while (list.length && now - list[0] > ms) list.shift(); if (list.length >= max) return true; list.push(now); return false; };

  /* ---- what a mod may ask for ---- */
  const CSS_FORBIDDEN = /@import|@namespace|expression\s*\(|behavior\s*:|-moz-binding|javascript:|image-set|\bsrc\s*\(|owui-mods|\bmd-|\\[0-9a-z]/i;
  // A style may change colours, sizes and layout, but not load anything from another site (which could carry what
  // is on the page out in its address), and not touch the Mods interface itself (so a mod can't hide itself)
  function cleanCss(css) {
    // Comments go first, and escapes like \75rl( are refused, so nothing can be spelt to slip past the checks
    css = String(css || '').slice(0, 30000).replace(/\/\*[\s\S]*?\*\//g, '');
    if (CSS_FORBIDDEN.test(css)) throw new Error('This style is not allowed');
    return css.replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi, (whole, q, address) => /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(address) ? whole : 'none');
  }
  const DOWNLOAD_TYPES = {'text/plain': 'txt', 'text/markdown': 'md', 'text/csv': 'csv', 'application/json': 'json', 'text/html': 'html'};
  const safeFile = (name, ext) => (String(name || 'mod').replace(/[\x00-\x1f\x7f/\\:*?"<>|؜‎‏‪-‮⁦-⁩]/g, '_').replace(/^[\s.]+|[\s.]+$/g, '').slice(0, 100) || 'mod').replace(/\.[a-z0-9]{1,8}$/i, '') + '.' + ext;
  function save(blob, name) { const link = el('a'); const url = URL.createObjectURL(blob); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 15000); }
  const storageKey = runner => 'owui-mods:' + (me?.id || 'anon') + ':' + runner.mod.id;
  const readStore = runner => { try { return JSON.parse(localStorage.getItem(storageKey(runner)) || '{}') || {}; } catch { return {}; } };
  const composerText = () => { const input = document.getElementById('chat-input'); return input ? clip((input.value ?? input.innerText ?? '').replace(/\n$/, ''), 20000) : ''; };
  const setComposer = (text, submit = false) => { if (!document.getElementById('chat-input')) throw new Error('No message box on this page'); window.postMessage({type: submit ? 'input:prompt:submit' : 'input:prompt', text: clip(text, 20000)}, location.origin); };
  const generating = () => !!document.querySelector('#messages-container [id^="message-"]:last-child .animate-pulse, button[aria-label="Stop"], #stop-response-button');
  let chatCache = {id: null, at: 0, value: null};
  async function readChat(id) {
    if (chatCache.id === id && Date.now() - chatCache.at < 2500) return chatCache.value;
    const chat = await api('/api/v1/chats/' + encodeURIComponent(id));
    const messages = chat?.chat?.history?.messages || {}, list = [];
    let at = chat?.chat?.history?.currentId; const seen = new Set();
    while (at && messages[at] && !seen.has(at) && list.length < 400) { seen.add(at); list.unshift(messages[at]); at = messages[at].parentId; }
    const text = m => clip((typeof m.content === 'string' ? m.content : '').replace(/<details\b[^>]*>[\s\S]*?<\/details>/gi, '').trim(), 20000);
    const value = {id, title: clip(chat?.title || chat?.chat?.title || '', 200), messages: list.slice(-200).map(m => ({id: String(m.id), role: m.role === 'user' ? 'user' : 'assistant', text: text(m)}))};
    chatCache = {id, at: Date.now(), value};
    return value;
  }
  async function pickModel() {
    const list = await api('/api/models');
    const models = (Array.isArray(list?.data) ? list.data : Array.isArray(list) ? list : []).filter(m => m?.id && !/^arena/i.test(m.id));
    return models;
  }
  async function complete(model, messages, timeout = 120000) {
    const answer = await api('/api/chat/completions', {method: 'POST', body: JSON.stringify({model, messages, stream: false})}, timeout);
    return String(answer?.choices?.[0]?.message?.content ?? '');
  }
  const HANDLERS = {
    toast: {fn: (r, text) => { if (tooOften(r.toasts, 4, 10000)) throw new Error('Too many messages'); toast(r.mod.name + ': ' + clip(text, 160)); }},
    'status.set': {perm: 'ui.status', fn: (r, d) => { r.status = {text: clip(d?.text, 80), tone: ['warn', 'ok'].includes(d?.tone) ? d.tone : '', clickable: !!d?.clickable}; paintBar(); }},
    'status.clear': {perm: 'ui.status', fn: r => { r.status = null; paintBar(); }},
    'band.set': {perm: 'ui.band', fn: (r, d) => { r.band = {text: clip(d?.text, 160), actions: (Array.isArray(d?.actions) ? d.actions : []).slice(0, 3).map(a => ({id: clip(a?.id, 10), label: clip(a?.label, 30)})).filter(a => a.label)}; paintBar(); }},
    'band.clear': {perm: 'ui.band', fn: r => { r.band = null; paintBar(); }},
    'pane.open': {perm: 'ui.pane', fn: (r, d) => showPane(r, clip(d?.title, 60) || r.mod.name)},
    'pane.close': {perm: 'ui.pane', fn: r => { if (openPane === r) closePane(); }},
    'composer.get': {perm: 'composer.read', fn: () => composerText()},
    'composer.set': {perm: 'composer.write', fn: (r, text) => setComposer(text)},
    'composer.insert': {perm: 'composer.write', fn: (r, text) => setComposer((composerText() + ' ' + clip(text, 20000)).trim())},
    'chat.current': {perm: 'chat.read', fn: async () => { const id = chatIdNow(); return id ? readChat(id) : null; }},
    'chat.send': {perm: 'chat.send', fn: (r, text) => { if (!String(text).trim()) throw new Error('Nothing to send'); if (generating()) throw new Error('An answer is still being written'); if (tooOften(r.sends, 3, 60000)) throw new Error('Too many messages'); setComposer(text, true); }},
    'chat.goto': {perm: 'chat.read', fn: (r, id) => { const node = document.getElementById('message-' + String(id).replace(/[^\w-]/g, '')); if (!node) return false; node.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}); return true; }},
    'commands.add': {perm: 'commands', fn: (r, d) => {
      const name = String(d?.name || '').toLowerCase();
      if (!/^[a-z][a-z0-9-]{0,23}$/.test(name)) throw new Error('A command name is 1 to 24 letters, digits or dashes');
      if (name === 'mod') throw new Error('/mod belongs to Mods itself');
      for (const [id, other] of runners) if (id !== r.mod.id && other.commands.some(c => c.name === name)) throw new Error(t('cmd_taken', {name}));
      if (!r.commands.some(c => c.name === name)) { if (r.commands.length >= 8) throw new Error('A mod can add up to 8 commands'); r.commands.push({name, description: clip(d?.description, 80)}); }
      paintBar();
    }},
    'messages.button': {perm: 'message.buttons', fn: (r, d) => { if (r.buttons.length >= 3) throw new Error('A mod can add up to 3 buttons'); r.buttons.push({id: clip(d?.id, 10), label: clip(d?.label, 24) || '•'}); paintMessageRows(true); }},
    'style.set': {perm: 'ui.style', fn: (r, css) => { const clean = cleanCss(css); if (!r.style) { r.style = el('style'); r.style.dataset.mod = r.mod.id; document.head.append(r.style); } r.style.textContent = clean; }},
    'style.clear': {perm: 'ui.style', fn: r => { r.style?.remove(); r.style = null; }},
    'ai.complete': {perm: 'ai', fn: async (r, prompt) => {
      if (tooOften(r.ai, CONFIG.aiPerMinute, 60000)) throw new Error('Too many requests; wait a minute');
      const models = await pickModel(), model = models.find(m => m.id === CONFIG.studioModel)?.id || models[0]?.id;
      if (!model) throw new Error(t('no_model'));
      return clip(await complete(model, [{role: 'user', content: clip(prompt, 12000)}]), 40000);
    }},
    'storage.get': {fn: (r, key) => readStore(r)[clip(key, 100)] ?? null},
    'storage.set': {fn: (r, d) => { const all = readStore(r); all[clip(d?.key, 100)] = d?.value ?? null; const text = JSON.stringify(all); if (text.length > 65536) throw new Error('A mod can keep up to 64 KB'); localStorage.setItem(storageKey(r), text); }},
    'storage.remove': {fn: (r, key) => { const all = readStore(r); delete all[clip(key, 100)]; localStorage.setItem(storageKey(r), JSON.stringify(all)); }},
    download: {perm: 'download', fn: (r, d) => { const type = DOWNLOAD_TYPES[d?.type] ? d.type : 'text/plain'; const content = String(d?.content ?? ''); if (content.length > 5e6) throw new Error('Files can be up to 5 MB'); save(new Blob([content], {type: type + ';charset=utf-8'}), safeFile(d?.name, DOWNLOAD_TYPES[type])); }}
  };
  window.addEventListener('message', async event => {
    const message = event.data;
    if (!message || typeof message !== 'object' || typeof message.__mods !== 'string') return;
    let runner = null;
    for (const each of runners.values()) if (each.frame.contentWindow === event.source && each.nonce === message.__mods) { runner = each; break; }
    if (!runner) return;
    const {type, data: payload} = message;
    if (type === 'ready') { send(runner, 'event', {name: 'theme', payload: themeNow()}); return; }
    // A mod that starts listening hears the current state at once, even if it awaited something first
    if (type === 'on') { runner.subs.add(clip(payload?.name, 30)); if (payload?.name === 'composer' && can(runner, 'composer.read')) emit(runner, 'composer', composerText()); if (payload?.name === 'chat') emit(runner, 'chat', chatIdNow() ? {id: chatIdNow()} : null); return; }
    if (type === 'log') { runner.errors.push({at: Date.now(), message: 'log: ' + clip(payload?.message, 300)}); runner.errors = runner.errors.slice(-20); return; }
    if (type === 'error') {
      runner.errors.push({at: Date.now(), message: clip(payload?.message, 600)}); runner.errors = runner.errors.slice(-20);
      // A mod that keeps failing is turned off, so it can't make the page unusable
      const recent = runner.errors.filter(e => !e.message.startsWith('log:') && Date.now() - e.at < 10000);
      if (recent.length >= 5 && !runner.preview) { stop(runner.mod.id); fail(runner.mod, 'stopped_errors'); }
      repaintDialog(); return;
    }
    if (type === 'result') { const waiter = runner.pending.get(payload?.id); if (!waiter) return; runner.pending.delete(payload.id); if (payload.error) waiter.reject(new Error(clip(payload.error, 300))); else waiter.resolve(payload.value); return; }
    if (type !== 'call') return;
    const handler = HANDLERS[payload?.name];
    let reply;
    try {
      if (!handler) throw new Error('Unknown request');
      if (handler.perm && !can(runner, handler.perm)) throw new Error('Not allowed: ' + handler.perm);
      reply = {id: payload.id, value: (await handler.fn(runner, payload.data)) ?? null};
    } catch (error) { reply = {id: payload?.id, error: clip(error?.message || error, 300)}; }
    try { JSON.stringify(reply.value); } catch { reply = {id: reply.id, error: 'The answer could not be sent'}; }
    send(runner, 'reply', reply);
  });

  /* ---- what mods show: the bar, the panel, buttons under answers ---- */
  let bar = null;
  function paintBar() {
    const form = document.getElementById('message-input-container')?.closest('form');
    const bands = [...runners.values()].filter(r => r.band && can(r, 'ui.band'));
    const statuses = [...runners.values()].filter(r => r.status && can(r, 'ui.status'));
    const typed = composerText(), hint = /^\/[a-z0-9-]*$/i.test(typed.trim()) ? typed.trim().slice(1).toLowerCase() : null;
    const own = canCreate() ? [{name: 'mod', description: t('cmd_mod')}] : [];
    const commands = hint == null ? [] : [...own, ...[...runners.values()].filter(r => can(r, 'commands')).flatMap(r => r.commands)].filter(c => c.name.startsWith(hint)).slice(0, 6);
    if (!form || (!bands.length && !statuses.length && !commands.length)) { bar?.remove(); return; }
    if (!bar) { bar = el('div', 'md-ui'); bar.id = 'owui-mods-bar'; bar.setAttribute('role', 'region'); setLabel(bar, t('mods')); bar.removeAttribute('title'); }
    const nodes = [];
    for (const r of bands.slice(0, 3)) {
      const band = el('div', 'md-band'); band.dataset.mod = r.mod.id;
      const text = el('span', 'md-bandtext', r.band.text); text.dir = 'auto';
      // The mod's icon, or its name, says whose bar it is, unless its text already starts with that icon
      if (!(r.mod.icon && r.band.text.startsWith(r.mod.icon))) band.append(el('span', 'md-bandname', r.mod.icon || r.mod.name));
      band.append(text);
      for (const action of r.band.actions) band.append(button(action.label, () => emit(r, 'action', {id: action.id})));
      nodes.push(band);
    }
    if (statuses.length || commands.length) {
      const chips = el('div', 'md-chips');
      for (const r of statuses) {
        const chip = r.status.clickable ? button(r.status.text, () => emit(r, 'action', {id: 'status'}), 'md-chip') : el('span', 'md-chip', r.status.text);
        chip.dataset.mod = r.mod.id; chip.dir = 'auto'; if (r.status.tone) chip.dataset.tone = r.status.tone; chip.title = r.mod.name;
        chips.append(chip);
      }
      for (const c of commands) {
        const chip = button('', () => { setComposer('/' + c.name + ' '); setTimeout(() => document.getElementById('chat-input')?.focus(), 60); }, 'md-chip');
        const cmd = el('span', 'md-cmd', '/' + c.name); cmd.translate = false;
        chip.append(cmd, document.createTextNode(c.description ? ' · ' + c.description : ''));
        chips.append(chip);
      }
      nodes.push(chips);
    }
    bar.replaceChildren(...nodes);
    if (bar.parentElement !== form || form.firstElementChild !== bar) form.prepend(bar);
  }
  function showPane(runner, title) {
    ensureFramesHost();
    for (const each of runners.values()) each.frame.dataset.shown = String(each === runner);
    openPane = runner; paneTitle.textContent = title; pane.hidden = false;
    runner.frame.removeAttribute('tabindex');
    emit(runner, 'pane', {open: true});
  }
  function closePane() {
    if (!pane || pane.hidden) return;
    const was = openPane; openPane = null; pane.hidden = true;
    for (const each of runners.values()) each.frame.dataset.shown = 'false';
    if (was) emit(was, 'pane', {open: false});
  }
  let rowsSignature = '';
  function paintMessageRows(force) {
    const owners = [...runners.values()].filter(r => r.buttons.length && can(r, 'message.buttons'));
    if (!owners.length && !force && !rowsSignature) return;
    const signature = owners.map(r => r.mod.id + ':' + r.buttons.map(b => b.label).join(',')).join('|');
    if (!force && signature === rowsSignature && !document.querySelector('#messages-container [id^="message-"] #response-content-container:not(:has(> .md-msgrow))')) return;
    rowsSignature = signature;
    for (const message of document.querySelectorAll('#messages-container [id^="message-"]')) {
      const content = message.querySelector('#response-content-container');
      let row = content?.querySelector(':scope > .md-msgrow');
      if (!content || !owners.length) { row?.remove(); continue; }
      if (row && row.dataset.sig === signature) continue;
      if (!row) { row = el('div', 'md-ui md-msgrow'); row.setAttribute('role', 'group'); setLabel(row, t('mods')); row.removeAttribute('title'); }
      row.dataset.sig = signature;
      const id = message.id.slice(8);
      row.replaceChildren(...owners.flatMap(r => r.buttons.map(b => {
        const node = button(b.label, async () => {
          const chatId = chatIdNow(); let found = null;
          try { const chat = chatId ? await readChat(chatId) : null; found = chat?.messages.find(m => m.id === id) || null; } catch {}
          emit(r, 'button', {id: b.id, message: found ? {...found, chatId} : {id, role: 'assistant', text: '', chatId}});
        }, 'md-msgbtn');
        node.title = r.mod.name; node.dir = 'auto';
        return node;
      })));
      if (row.parentElement !== content) content.append(row);
    }
  }

  /* ---- watching the page for what mods asked to hear about ---- */
  // Typing fires input events; Open WebUI's editor clears or fills the box without one, so redraws are checked too
  let composerTimer = 0, composerLast = '';
  function composerMaybeChanged() {
    clearTimeout(composerTimer);
    composerTimer = setTimeout(() => {
      const text = composerText();
      if (text === composerLast) return;
      composerLast = text;
      for (const r of runners.values()) if (r.subs.has('composer') && can(r, 'composer.read')) emit(r, 'composer', text);
      paintBar();
    }, 150);
  }
  document.addEventListener('input', event => { if (event.target?.closest?.('#chat-input')) composerMaybeChanged(); }, true);
  // "/name …" and Enter runs a mod's command instead of sending; its answer goes into the message box
  document.addEventListener('keydown', async event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || !event.target?.closest?.('#chat-input')) return;
    const m = /^\/([a-z][a-z0-9-]{0,23})(?:\s+([\s\S]*))?$/i.exec(composerText().trim());
    if (!m) return;
    const name = m[1].toLowerCase();
    // "/mod <what you want>" is Mods' own: it opens Create with that description and starts writing the mod
    if (name === 'mod' && canCreate()) {
      event.preventDefault(); event.stopImmediatePropagation();
      const ask = clip((m[2] || '').trim(), 4000);
      setComposer('');
      createFrom(ask);
      return;
    }
    const runner = [...runners.values()].find(r => can(r, 'commands') && r.commands.some(c => c.name === name));
    if (!runner) return;
    event.preventDefault(); event.stopImmediatePropagation();
    try {
      const result = await emit(runner, 'command', {name, args: clip(m[2] || '', 20000)}, true);
      if (typeof result === 'string' && result) { if (!can(runner, 'composer.write')) throw new Error('Not allowed: composer.write'); setComposer(result); }
      else if (result && typeof result.send === 'string') { if (!can(runner, 'chat.send')) throw new Error('Not allowed: chat.send'); setComposer(result.send, true); }
      else setComposer('');
    } catch (error) { toast(runner.mod.name + ': ' + clip(error.message, 160)); }
  }, true);
  // A finished answer is announced once, to mods allowed to read the chat; answers already there when a chat opens are not
  const seenChats = new Map();
  setInterval(async () => {
    const listeners = [...runners.values()].filter(r => r.subs.has('message') && can(r, 'chat.read'));
    const chatId = chatIdNow();
    if (!listeners.length || !chatId || generating()) return;
    const answers = [...document.querySelectorAll('#messages-container [id^="message-"]')].filter(n => n.querySelector('#response-content-container'));
    const last = answers[answers.length - 1];
    if (!last) return;
    const key = last.id + ':' + (last.textContent || '').length;
    const known = seenChats.get(chatId);
    if (!known) { seenChats.set(chatId, {key, stable: key}); return; }
    if (key !== known.stable) { known.stable = key; return; }
    if (key === known.key) return;
    known.key = key;
    try {
      const chat = await readChat(chatId), message = chat.messages.find(m => m.id === last.id.slice(8));
      if (message && message.role === 'assistant') for (const r of listeners) emit(r, 'message', {...message, chatId});
    } catch {}
  }, 1100);
  let lastChat = chatIdNow(), lastTheme = '';
  function watchContext() {
    const chat = chatIdNow();
    if (chat !== lastChat) { lastChat = chat; chatCache = {id: null, at: 0, value: null}; for (const r of runners.values()) emit(r, 'chat', chat ? {id: chat} : null); }
    const theme = document.documentElement.className + '|' + document.documentElement.lang;
    if (theme !== lastTheme) { const first = !lastTheme; lastTheme = theme; if (!first) { const now = themeNow(); for (const r of runners.values()) send(r, 'event', {name: 'theme', payload: now}); } }
  }

  /* ---- ways in: the chat's ⋯ menu, the + menu of the tab row, and Ctrl+Shift+. ---- */
  const MENU_CLASS = 'flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] cursor-pointer select-none hover:bg-gray-50/40 dark:hover:bg-gray-800/40';
  let chatItem = null, plusItem = null;
  const visible = node => !!node && node.getClientRects().length > 0;
  function menuButton(id) {
    const item = el('button', MENU_CLASS); item.type = 'button'; item.id = id; item.setAttribute('role', 'menuitem');
    const icon = svgIcon('mods'); icon.setAttribute('class', 'size-4'); item.append(icon, el('div', 'flex items-center', t('mods')));
    item.addEventListener('click', () => { document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); setTimeout(openDialog, 0); });
    return item;
  }
  function syncMenus() {
    const menus = [...document.querySelectorAll('body > [role="menu"] .app-dropdown-menu')];
    const chatOpen = CONFIG.chatMenu && [...document.querySelectorAll('#chat-context-menu-button[aria-expanded="true"]')].some(visible);
    const plusOpen = CONFIG.tabMenu && [...document.querySelectorAll('.panel-tab-strip')].some(list => list.parentElement?.querySelector(':scope > :not([role="tablist"]) > button[aria-haspopup="true"][aria-expanded="true"]'));
    if (chatOpen && menus.length) { chatItem = chatItem || menuButton('owui-mods-menu-item'); const target = menus[menus.length - 1]; if (chatItem.parentElement !== target) target.append(chatItem); } else chatItem?.remove();
    if (plusOpen && !chatOpen && menus.length) { plusItem = plusItem || menuButton('owui-mods-plus'); const target = menus[menus.length - 1]; if (plusItem.parentElement !== target) target.append(plusItem); } else plusItem?.remove();
  }
  // Ctrl+Shift+. (⌘⇧. on a Mac): free in Open WebUI's own shortcuts and in browsers. Caught before Open WebUI sees it.
  const IS_MAC = /Mac|iPhone|iPad/.test(navigator.userAgent);
  const SHORTCUT = IS_MAC ? '⌘⇧.' : 'Ctrl+Shift+.';
  window.addEventListener('keydown', event => {
    if (event.code !== 'Period' || !event.shiftKey || event.altKey || !(IS_MAC ? event.metaKey : event.ctrlKey)) return;
    event.preventDefault(); event.stopImmediatePropagation(); openDialog();
  }, true);

  const canCreate = () => CONFIG.enabled && !safeMode && loaded && (CONFIG.allowUserMods || isAdmin());
  // Opens Create with a description; with one, starts writing the mod at once
  function createFrom(description) {
    tab = 'create';
    if (description) { studio.description = description; studio.change = ''; studio.editing = null; }
    if (dialog?.open) paintDialog(); else openDialog();
    if (description) generate();
    else dialog?.querySelector('[data-focus="describe"]')?.focus();
  }

  /* ---- the Mods window ---- */
  let dialog = null, tab = 'installed', studio = {description: '', model: '', result: null, error: '', busy: false, change: '', editing: null, paste: ''};
  const repaintDialog = () => { if (dialog?.open) paintDialog(); };
  function openDialog() {
    if (dialog?.open) { dialog.focus(); return; }
    dialog = el('dialog', 'md-ui md-dialog'); dialog.setAttribute('aria-labelledby', 'owui-mods-title'); dialog.id = 'owui-mods-dialog';
    dialog.addEventListener('close', () => { stopPreview(); dialog.remove(); dialog = null; });
    document.body.append(dialog); dialog.showModal();
    paintDialog();
    dialog.querySelector('[role="tab"][aria-selected="true"]')?.focus();
    if (store.is('idle', 'failed')) loadMine().then(() => { applyCatalog(); repaintDialog(); });
  }
  function paintDialog() {
    if (!dialog) return;
    const focusKey = document.activeElement?.dataset?.focus;
    const head = el('div', 'md-dhead'); const title = el('h2', 'md-dtitle', t('mods')); title.id = 'owui-mods-title';
    head.append(title, iconButton(t('close'), 'close', () => dialog.close()));
    const tabs = el('div', 'md-tabs'); tabs.setAttribute('role', 'tablist'); setLabel(tabs, t('mods')); tabs.removeAttribute('title');
    const list = ['installed', 'discover', ...(CONFIG.allowUserMods || isAdmin() ? ['create'] : [])];
    for (const name of list) {
      const b = button(t(name), () => { tab = name; paintDialog(); }, 'md-tab'); b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(tab === name)); b.tabIndex = tab === name ? 0 : -1; b.dataset.focus = 'tab-' + name; b.id = 'md-tab-' + name;
      b.addEventListener('keydown', event => { const i = list.indexOf(name), next = {ArrowRight: list[(i + 1) % list.length], ArrowLeft: list[(i - 1 + list.length) % list.length], Home: list[0], End: list[list.length - 1]}[event.key]; if (next) { event.preventDefault(); tab = next; paintDialog(); dialog.querySelector('#md-tab-' + next)?.focus(); } });
      tabs.append(b);
    }
    if (!list.includes(tab)) tab = 'installed';
    const body = el('div', 'md-dbody'); body.setAttribute('role', 'tabpanel'); body.setAttribute('aria-labelledby', 'md-tab-' + tab);
    if (!CONFIG.enabled) body.append(el('p', 'md-error', t('off_admin')));
    else if (safeMode) { const p = el('div', 'md-error'); p.append(el('span', null, t('safe_mode') + ' '), button(t('leave_safe'), () => { try { sessionStorage.removeItem('owui-mods-safe'); } catch {} const u = new URL(location.href); u.searchParams.delete('mods'); location.href = u.href; })); body.append(p); }
    if (store.is('loading')) { const p = el('div', 'md-empty'); p.append(el('span', 'md-spin'), el('span', null, '…')); body.append(p); }
    else if (store.is('failed')) body.append(el('p', 'md-error', t('load_failed')));
    else if (tab === 'installed') paintInstalled(body);
    else if (tab === 'discover') paintDiscover(body);
    else paintCreate(body);
    dialog.replaceChildren(head, tabs, body);
    if (focusKey) dialog.querySelector(`[data-focus="${CSS.escape(focusKey)}"]`)?.focus({preventScroll: true});
  }
  const sourceTag = mod => { const tag = el('span', 'md-tag', t('src_' + (mod.source || 'user'))); return tag; };
  function permChips(perms) { const box = el('div', 'md-perms'); for (const p of perms) { const chip = el('span', 'md-perm', permLabel(p)); chip.dataset.risk = PERMS[p]; box.append(chip); } return box; }
  function cardFor(mod, actions, extraTags = []) {
    const card = el('li', 'md-card'); card.dataset.mod = mod.id; if (mod.failed) card.dataset.state = 'failed';
    const icon = el('span', 'md-cardicon'); if (mod.icon) icon.textContent = mod.icon; else icon.append(svgIcon('mods')); icon.setAttribute('aria-hidden', 'true');
    const text = el('div', 'md-cardtext');
    const name = el('div', 'md-cardname'); const n = el('span', null, mod.name); n.dir = 'auto'; name.append(n, sourceTag(mod), ...extraTags);
    text.append(name);
    if (mod.description) { const d = el('div', 'md-carddesc', mod.description); d.dir = 'auto'; text.append(d); }
    if ((mod.permissions || []).length) text.append(permChips(mod.permissions));
    const runner = runners.get(mod.id), errors = runner?.errors.filter(e => !e.message.startsWith('log:')) || [];
    if (errors.length) { const log = el('ul', 'md-log'); setLabel(log, t('log_title')); log.removeAttribute('title'); for (const e of errors.slice(-3)) log.append(el('li', null, e.message.split('\n')[0])); text.append(log); }
    const box = el('div', 'md-cardactions'); box.append(...actions);
    card.append(icon, text, box);
    return card;
  }
  function paintInstalled(body) {
    const intro = el('p', 'md-note', t('intro') + ' '); const keys = el('kbd', 'md-kbd', SHORTCUT); keys.translate = false;
    intro.append(t('shortcut') + ' ', keys); body.append(intro);
    if (!data.mods.length) { const empty = el('div', 'md-empty'); empty.append(el('strong', null, t('none_installed')), el('span', null, t('none_installed_hint')), button(t('discover'), () => { tab = 'discover'; paintDialog(); }, 'md-btn md-primary')); body.append(empty); return; }
    const list = el('ul', 'md-list');
    for (const mod of data.mods) {
      const toggle = el('button', 'md-switch'); toggle.type = 'button'; toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-checked', String(!!mod.enabled)); setLabel(toggle, t(mod.enabled ? 'disable' : 'enable', {name: mod.name})); toggle.dataset.focus = 'switch-' + mod.id;
      toggle.disabled = !CONFIG.enabled || store.is('saving');
      toggle.addEventListener('click', () => setEnabled(mod, !mod.enabled));
      const more = iconButton(t('more', {name: mod.name}), 'more', event => moreMenu(event.currentTarget, mod)); more.dataset.focus = 'more-' + mod.id; more.setAttribute('aria-haspopup', 'menu');
      const tags = [];
      if (mod.failed) { const tag = el('span', 'md-tag', t('failed')); tag.dataset.tone = 'bad'; tags.push(tag); }
      const newer = mod.source === 'catalog' ? catalog().find(c => c.id === mod.id) : mod.source === 'starter' ? starters().find(c => c.id === mod.id) : null;
      const actions = [];
      if (newer && (newer.version || 1) > (mod.version || 1)) { const tag = el('span', 'md-tag', t('update')); tag.dataset.tone = 'warn'; tags.push(tag); actions.push(button(t('update_btn'), () => install(newer, {update: mod}))); }
      if (CONFIG.catalog?.some(c => c.id === mod.id && c.pinned)) tags.push(el('span', 'md-tag', t('pinned')));
      list.append(cardFor(mod, [...actions, toggle, more], tags));
    }
    body.append(list);
  }
  function paintDiscover(body) {
    const section = (title, mods) => {
      body.append(el('h3', 'md-section', title));
      if (!mods.length) { body.append(el('p', 'md-note', t('none_published'))); return; }
      const list = el('ul', 'md-list');
      for (const mod of mods) {
        const have = findInstalled(mod.id);
        const blocked = mod.permissions.filter(p => !allowedPerm(p));
        const action = have ? button(t('reinstall'), () => {}, 'md-btn') : button(t('install'), () => install(mod), 'md-btn md-primary');
        if (have || blocked.length || !CONFIG.enabled) action.disabled = true;
        if (blocked.length) action.title = t('not_allowed', {list: blocked.map(permLabel).join(', ')});
        action.dataset.focus = 'install-' + mod.id;
        const extra = isAdmin() && mod.source === 'catalog' ? [iconButton(t('more', {name: mod.name}), 'more', event => catalogMenu(event.currentTarget, mod))] : [];
        const tags = mod.pinned ? [el('span', 'md-tag', t('pinned'))] : [];
        list.append(cardFor(mod, [action, ...extra], tags));
      }
      body.append(list);
    };
    if (catalog().length || isAdmin()) section(t('organisation'), catalog());
    section(t('starters'), starters());
    if (isAdmin()) body.append(el('p', 'md-note', t('for_everyone_note')));
  }
  function paintCreate(body) {
    if (!CONFIG.allowUserMods && !isAdmin()) { body.append(el('p', 'md-note', t('no_user_mods'))); return; }
    const describe = el('label', 'md-field', t('describe'));
    const area = el('textarea', 'md-textarea'); area.name = 'mod-description'; area.placeholder = t('describe_ph'); area.value = studio.description; area.maxLength = 4000; area.autocomplete = 'off'; area.dataset.focus = 'describe'; area.dir = 'auto';
    area.addEventListener('input', () => { studio.description = area.value; });
    describe.append(area);
    const modelField = el('label', 'md-field', t('model')); const select = el('select', 'md-select'); select.name = 'mod-model'; select.dataset.focus = 'model';
    modelField.append(select);
    pickModel().then(models => { select.replaceChildren(...models.map(m => { const o = el('option', null, m.name || m.id); o.value = m.id; return o; })); select.value = studio.model || CONFIG.studioModel || models[0]?.id || ''; studio.model = select.value; if (!models.length) select.disabled = true; }).catch(() => { select.disabled = true; });
    select.addEventListener('change', () => { studio.model = select.value; });
    const go = button(studio.busy ? t('generating') : t('generate'), () => generate(), 'md-btn md-primary'); go.dataset.focus = 'generate';
    go.disabled = studio.busy; if (studio.busy) go.prepend(el('span', 'md-spin'));
    const row = el('div', 'md-row'); row.append(go);
    body.append(describe, modelField, row);
    if (studio.error) { const err = el('p', 'md-error', studio.error); err.setAttribute('role', 'alert'); body.append(err); }
    if (studio.result) {
      const mod = studio.result;
      body.append(el('h3', 'md-section', t('preview')));
      const list = el('ul', 'md-list'); list.append(cardFor({...mod, source: 'user'}, [])); body.append(list);
      const details = el('details', 'md-details'); const pre = el('pre', 'md-code', mod.code); pre.translate = false; details.append(el('summary', null, t('code')), pre); body.append(details);
      const changeField = el('label', 'md-field', t('change')); const change = el('textarea', 'md-textarea'); change.name = 'mod-change'; change.placeholder = t('change_ph'); change.value = studio.change; change.maxLength = 4000; change.autocomplete = 'off'; change.dataset.focus = 'change'; change.dir = 'auto';
      change.addEventListener('input', () => { studio.change = change.value; }); changeField.append(change); body.append(changeField);
      const actions = el('div', 'md-row');
      const tryButton = runners.get('preview:' + mod.id) ? button(t('stop_try'), () => { stopPreview(); paintDialog(); }) : button(t('try_it'), () => tryMod(mod));
      tryButton.dataset.focus = 'try';
      const changeButton = button(t('apply_change'), () => generate(true)); changeButton.disabled = studio.busy; changeButton.dataset.focus = 'apply-change';
      const keep = button(studio.editing ? t('save_update') : t('save_install'), () => install(mod, {update: studio.editing ? findInstalled(studio.editing) : findInstalled(mod.id)}), 'md-btn md-primary'); keep.dataset.focus = 'keep';
      actions.append(tryButton, changeButton, el('span', 'md-spacer'), keep);
      body.append(actions);
      if (runners.get('preview:' + mod.id)) body.append(el('p', 'md-note', t('trying')));
    }
    // A mod written elsewhere: pasted, or a .mod file
    const paste = el('details', 'md-details'); paste.append(el('summary', null, t('paste')));
    const pasteBox = el('div'); pasteBox.style.padding = '.75rem';
    const pasteArea = el('textarea', 'md-textarea md-code'); pasteArea.name = 'mod-text'; pasteArea.placeholder = t('paste_ph'); pasteArea.value = studio.paste; pasteArea.spellcheck = false; pasteArea.autocomplete = 'off'; setLabel(pasteArea, t('paste')); pasteArea.removeAttribute('title'); pasteArea.dataset.focus = 'paste';
    pasteArea.addEventListener('input', () => { studio.paste = pasteArea.value; });
    const file = el('input'); file.type = 'file'; file.accept = '.mod,.txt,.js,text/plain'; file.hidden = true;
    file.addEventListener('change', async () => { const f = file.files?.[0]; file.value = ''; if (!f) return; if (f.size > 200000) { studio.error = t('bad_mod', {error: t('e_long', {n: CODE_LIMIT})}); paintDialog(); return; } studio.paste = await f.text(); readPasted(); });
    const pasteRow = el('div', 'md-row'); pasteRow.append(button(t('read_mod'), () => readPasted()), button(t('import_file'), () => file.click()), file);
    pasteBox.append(pasteArea, pasteRow); paste.append(pasteBox); if (studio.paste) paste.open = true;
    body.append(paste);
  }
  function readPasted() {
    try { studio.result = parseMod(studio.paste); studio.error = ''; studio.editing = null; }
    catch (error) { studio.error = t('bad_mod', {error: error.message}); }
    paintDialog();
  }
  // The model is taught the mod format and its rules, and answers with one ```mod block
  const GUIDE = [
    'You write mods for Open WebUI. A mod is plain JavaScript that runs in a sandboxed iframe: it cannot touch the Open WebUI page, its storage, cookies or the network. It talks to Open WebUI only through the global `mod` object, and only with the permissions it declares.',
    'Answer with exactly one fenced block opened with ```mod, nothing else. Inside: settings lines, a line containing only ---, then the code.',
    'Settings: "name: …" (required, under 60 characters), "description: …" (one sentence), "icon: <one emoji>", "version: 1", "permissions: …" (comma separated; ask only for what the code uses).',
    'Permissions and the API they unlock (all calls return promises):',
    '- composer.read: mod.on("composer", text => …) fires as the person types; mod.composer.get().',
    '- composer.write: mod.composer.set(text), mod.composer.insert(text).',
    '- chat.read: mod.on("message", m => …) fires when an answer finishes (m.id, m.role, m.text, m.chatId); mod.chat.current() returns {id, title, messages:[{id, role, text}]}; mod.chat.goto(messageId).',
    '- chat.send: mod.chat.send(text) sends a message in the person\'s name. Use only when the person clearly asked for it.',
    '- ui.status: mod.status.set(text, {tone: "warn"|"ok", onClick}) shows a small label above the message box; mod.status.clear().',
    '- ui.band: mod.band.set({text, actions:[{label, onClick}]}) shows a bar above the message box (up to 3 actions); mod.band.clear().',
    '- ui.pane: mod.pane.open({title}) shows the mod\'s own document (document.body) in a panel beside the chat; mod.pane.close(). Build that UI with normal DOM code; CSS variables --mod-bg, --mod-text, --mod-muted, --mod-soft, --mod-line, --mod-accent, --mod-on-accent follow light and dark mode; buttons, inputs and .card, .muted, button.primary are already styled. mod.on("pane", e => e.open).',
    '- commands: mod.commands.add("name", "description", args => string | {send: string}). Typing /name and Enter calls it; a returned string replaces the message box (needs composer.write), {send} sends it (needs chat.send).',
    '- message.buttons: mod.messages.button("Label", message => …) adds a button under every answer; message is {id, role, text, chatId}.',
    '- ui.style: mod.style(css) applies CSS to Open WebUI. No @import, no url() except data: images, and never select anything containing "owui-mods" or "md-". Use .dark for dark mode rules.',
    '- ai: mod.ai.complete(prompt) returns the model\'s answer as text (rate limited).',
    '- download: mod.download(name, text, "text/markdown"|"text/plain"|"text/csv"|"application/json").',
    'Always available: mod.toast(text), mod.storage.get(key)/set(key, value)/remove(key) (per person, up to 64 KB), mod.theme() returns {dark, lang}, mod.on("theme", t => …), mod.on("chat", c => …) when the open chat changes (c is {id} or null), mod.log(...).',
    'Rules: no network, no eval, no external libraries; keep labels short; handle empty text; debounce work on "composer"; support French when mod.theme().lang starts with "fr" if the person writes in French; keep the code under 300 lines.'
  ].join('\n');
  async function generate(changing = false) {
    if (studio.busy) return;
    const ask = (changing ? studio.change : studio.description).trim();
    if (!ask) { dialog?.querySelector(changing ? '[data-focus="change"]' : '[data-focus="describe"]')?.focus(); return; }
    studio.busy = true; studio.error = ''; paintDialog();
    try {
      const models = await pickModel(), model = models.find(m => m.id === studio.model)?.id || models.find(m => m.id === CONFIG.studioModel)?.id || models[0]?.id;
      if (!model) throw new Error(t('no_model'));
      const messages = [{role: 'system', content: GUIDE}];
      if (changing && studio.result) messages.push({role: 'user', content: 'This is the current mod:\n```mod\n' + modText(studio.result) + '\n```\nChange it: ' + ask});
      else messages.push({role: 'user', content: 'Make a mod that: ' + ask});
      let answer;
      try { answer = await complete(model, messages); } catch (error) { throw new Error(t('ai_failed', {error: error.message})); }
      let mod;
      try { mod = parseMod(answer); } catch (error) { throw new Error(t('gen_failed') + ' (' + error.message + ')'); }
      if (changing && studio.result) mod.id = studio.result.id;
      stopPreview();
      studio.result = mod; studio.change = '';
    } catch (error) { studio.error = error.message; }
    studio.busy = false; paintDialog();
  }
  function stopPreview() { for (const id of [...runners.keys()]) if (id.startsWith('preview:')) stop(id); }
  async function tryMod(mod) {
    const blocked = mod.permissions.filter(p => !allowedPerm(p));
    if (blocked.length) { studio.error = t('not_allowed', {list: blocked.map(permLabel).join(', ')}); paintDialog(); return; }
    if (!(await askPermissions(mod, 'try'))) return;
    stopPreview();
    start({...mod, id: 'preview:' + mod.id, granted: mod.permissions}, {preview: true});
    paintDialog();
  }

  /* ---- installing, updating, turning on and off, removing ---- */
  function askPermissions(mod, kind, previous = null) {
    return new Promise(resolve => {
      const ask = el('dialog', 'md-ui md-ask'); ask.setAttribute('aria-labelledby', 'md-ask-title');
      const title = el('h3', null, t({install: 'ask_install', update: 'ask_update', try: 'ask_try'}[kind], {name: mod.name})); title.id = 'md-ask-title';
      ask.append(title);
      const extra = previous ? mod.permissions.filter(p => !previous.includes(p)) : [];
      if (extra.length) { const warn = el('p', null, t('ask_more')); warn.style.color = 'var(--md-warn)'; ask.append(warn); }
      if (mod.permissions.length) {
        ask.append(el('p', null, t('ask_can')));
        const list = el('ul', 'md-asklist');
        for (const p of mod.permissions) { const li = el('li'); li.dataset.risk = PERMS[p]; li.append(svgIcon(PERMS[p] === 'high' ? 'warn' : 'check'), el('span', null, permLabel(p))); list.append(li); }
        ask.append(list);
      } else ask.append(el('p', null, t('ask_nothing')));
      const note = el('p', null, t('ask_sandbox') + (mod.permissions.some(p => PERMS[p] === 'high') ? ' ' + t('ask_trust') : '')); note.style.fontSize = '.8125rem'; ask.append(note);
      const no = button(t('cancel'), () => ask.close('no')), yes = button(t({install: 'allow_install', update: 'allow_update', try: 'allow_try'}[kind]), () => ask.close('yes'), 'md-btn md-primary');
      const actions = el('div', 'md-actions'); actions.append(no, yes); ask.append(actions);
      ask.addEventListener('close', () => { const ok = ask.returnValue === 'yes'; ask.remove(); resolve(ok); });
      (dialog || document.body).append(ask); ask.showModal(); no.focus();
    });
  }
  async function install(mod, {update = null, quiet = false} = {}) {
    const blocked = mod.permissions.filter(p => !allowedPerm(p));
    if (blocked.length) { toast(t('not_allowed', {list: blocked.map(permLabel).join(', ')})); return false; }
    if (!update && data.mods.length >= CONFIG.maxMods) { toast(t('too_many', {n: CONFIG.maxMods})); return false; }
    const previous = update?.granted || null;
    const needsAsk = !quiet && (!update || mod.permissions.some(p => !previous.includes(p)));
    if (needsAsk && !(await askPermissions(mod, update ? 'update' : 'install', previous))) return false;
    stopPreview();
    let id = update?.id || mod.id;
    if (!update) { const base = id; let n = 2; while (findInstalled(id)) id = base.slice(0, 36) + '-' + n++; }
    const record = {id, name: clip(mod.name, 60), description: clip(mod.description, 200), icon: mod.icon || '', version: mod.version || 1, permissions: [...mod.permissions], granted: [...mod.permissions], code: mod.code, source: update?.source === 'catalog' && mod.source !== 'catalog' ? 'user' : (mod.source || 'user'), enabled: true, updated: Date.now()};
    if (update) data.mods = data.mods.map(m => m.id === update.id ? record : m); else data.mods.push(record);
    data.dismissed = data.dismissed.filter(x => x !== id);
    try { await saveMine(); } catch { return false; }
    start(record);
    if (!quiet) toast(t(update ? 'updated' : 'installed_ok', {name: record.name}));
    if (studio.result && (studio.result.id === mod.id)) { studio.result = null; studio.editing = null; studio.description = ''; studio.paste = ''; tab = 'installed'; }
    repaintDialog();
    return true;
  }
  async function setEnabled(mod, on) {
    mod.enabled = on; mod.failed = false;
    if (on) start(mod); else stop(mod.id);
    repaintDialog();
    await saveMine().catch(() => {});
  }
  async function remove(mod) {
    const pinned = CONFIG.catalog?.some(c => c.id === mod.id && c.pinned);
    if (!(await confirmBox(t('ask_remove', {name: mod.name}), t(pinned ? 'ask_remove_pinned' : 'ask_remove_text'), t('remove')))) return;
    stop(mod.id);
    data.mods = data.mods.filter(m => m.id !== mod.id);
    if (pinned && !data.dismissed.includes(mod.id)) data.dismissed.push(mod.id);
    try { localStorage.removeItem('owui-mods:' + (me?.id || 'anon') + ':' + mod.id); } catch {}
    await saveMine().catch(() => {});
    toast(t('removed', {name: mod.name})); repaintDialog();
  }
  function confirmBox(title, text, action) {
    return new Promise(resolve => {
      const ask = el('dialog', 'md-ui md-ask'); ask.setAttribute('aria-labelledby', 'md-confirm-title');
      const h = el('h3', null, title); h.id = 'md-confirm-title';
      const no = button(t('cancel'), () => ask.close('no')), yes = button(action, () => ask.close('yes'), 'md-btn md-danger');
      const actions = el('div', 'md-actions'); actions.append(no, yes);
      ask.append(h, el('p', null, text), actions);
      ask.addEventListener('close', () => { const ok = ask.returnValue === 'yes'; ask.remove(); resolve(ok); });
      (dialog || document.body).append(ask); ask.showModal(); no.focus();
    });
  }
  function popMenu(anchor, items) {
    const ask = el('dialog', 'md-ui md-ask'); ask.style.width = 'min(18rem, calc(100vw - 2rem))'; ask.style.padding = '.5rem';
    const list = el('div'); list.setAttribute('role', 'menu');
    for (const [label, run, danger] of items) { const b = button(label, () => { ask.close(); run(); }, 'md-btn'); b.setAttribute('role', 'menuitem'); b.style.cssText = 'width:100%;justify-content:flex-start;background:transparent'; if (danger) b.style.color = 'var(--md-danger-text)'; list.append(b); }
    ask.append(list);
    ask.addEventListener('close', () => { ask.remove(); anchor?.focus?.(); });
    ask.addEventListener('click', event => { if (event.target === ask) ask.close(); });
    (dialog || document.body).append(ask); ask.showModal(); list.querySelector('button')?.focus();
  }
  function moreMenu(anchor, mod) {
    const items = [];
    if (CONFIG.allowUserMods || isAdmin()) items.push([t('edit'), () => { studio = {...studio, result: {...mod}, editing: mod.id, error: '', change: ''}; tab = 'create'; paintDialog(); }]);
    items.push([t('export'), () => save(new Blob([modText(mod)], {type: 'text/plain;charset=utf-8'}), safeFile(mod.id, 'mod'))]);
    if (isAdmin()) items.push([CONFIG.catalog.some(c => c.id === mod.id) ? t('unpublish') : t('publish'), () => publish(mod, !CONFIG.catalog.some(c => c.id === mod.id))]);
    items.push([t('remove'), () => remove(mod), true]);
    popMenu(anchor, items);
  }
  function catalogMenu(anchor, mod) {
    popMenu(anchor, [[mod.pinned ? t('unpin') : t('pin'), () => publish(mod, true, !mod.pinned)], [t('unpublish'), () => publish(mod, false), true]]);
  }
  // Administrators publish to the catalog, kept in the function's own settings; the server checks it before keeping it
  async function publish(mod, on, pinned = false) {
    try {
      const valves = await api(valvesUrl);
      let list = []; try { list = JSON.parse(valves?.catalog || '[]'); } catch {}
      list = list.filter(m => m.id !== mod.id);
      if (on) list.push({id: mod.id, name: mod.name, description: mod.description || '', icon: mod.icon || '', version: mod.version || 1, permissions: mod.permissions, code: mod.code, pinned: !!pinned});
      await api(valvesUrl + '/update', {method: 'POST', body: JSON.stringify({...valves, catalog: JSON.stringify(list)})});
      CONFIG.catalog = list;
      toast(t(on ? 'published' : 'unpublished', {name: mod.name}));
      repaintDialog();
    } catch (error) { toast(t('saved_failed', {error: error.message})); }
  }
  // Mods an administrator turned on for everyone are installed once; someone who removed one doesn't get it back
  function applyCatalog() {
    let changed = false;
    for (const mod of catalog()) {
      if (!mod.pinned || findInstalled(mod.id) || data.dismissed.includes(mod.id) || mod.permissions.some(p => !allowedPerm(p))) continue;
      if (data.mods.length >= CONFIG.maxMods) break;
      data.mods.push({id: mod.id, name: mod.name, description: mod.description || '', icon: mod.icon || '', version: mod.version || 1, permissions: mod.permissions, granted: mod.permissions, code: mod.code, source: 'catalog', enabled: true, updated: Date.now()});
      changed = true;
    }
    if (changed) saveMine().catch(() => {});
    for (const mod of data.mods) if (mod.enabled && !runners.has(mod.id)) start(mod);
  }

  /* ---- keeping everything in place as Open WebUI redraws ---- */
  let syncPending = false, shownLang = lang;
  const schedule = () => { if (!syncPending) { syncPending = true; requestAnimationFrame(() => { syncPending = false; sync(); }); } };
  function sync() {
    watchContext();
    syncMenus();
    if ((runners.size || bar?.isConnected) && composerText() !== composerLast) composerMaybeChanged();
    if (runners.size) { if (bar && !bar.isConnected) paintBar(); else if (!bar) paintBar(); paintMessageRows(false); }
    const now = language();
    if (now !== shownLang) { shownLang = lang = now; chatItem?.remove(); plusItem?.remove(); chatItem = plusItem = null; if (pane) { pane.remove(); pane = null; for (const r of runners.values()) r.frame.remove(); const list = [...runners.values()].map(r => r.mod); runners.clear(); for (const m of list) if (!m.id.startsWith('preview:')) start(m); } paintBar(); repaintDialog(); }
  }
  function boot() {
    const style = el('style'); style.id = 'owui-mods-style'; style.textContent = __CSS__; document.head.append(style);
    document.documentElement.dataset.mods = '1';
    new MutationObserver(schedule).observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-expanded', 'class', 'lang']});
    addEventListener('popstate', schedule);
    if (!CONFIG.enabled || !token()) return;
    if (safeMode) { document.documentElement.dataset.modsSafe = '1'; return; }
    loadMine().then(applyCatalog).then(schedule);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true}); else boot();
})();
"""

from pydantic import field_validator

# What a mod may ask for. The browser checks every request against the permissions a person granted;
# the server checks that what is stored only names these.
PERMISSIONS = (
    "composer.read",
    "composer.write",
    "chat.read",
    "chat.send",
    "ui.status",
    "ui.band",
    "ui.pane",
    "ui.style",
    "commands",
    "message.buttons",
    "ai",
    "download",
)
MOD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
CODE_LIMIT = 60000
USER_MOD_LIMIT = 24
CATALOG_LIMIT = 40


def _clean_mod(raw: Any, source_default: str, catalog: bool = False) -> dict:
    """One mod, checked and trimmed to the fields Mods knows. Raises ValueError on anything unexpected."""
    if not isinstance(raw, dict):
        raise ValueError("each mod must be an object")
    mod_id = raw.get("id")
    if not isinstance(mod_id, str) or not MOD_ID.match(mod_id):
        raise ValueError(f"mod id {str(mod_id)[:40]!r} must be 1-40 lowercase letters, digits or dashes")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        raise ValueError(f"mod {mod_id}: name must be 1-60 characters")
    description = raw.get("description") or ""
    icon = raw.get("icon") or ""
    code = raw.get("code")
    if not isinstance(description, str) or len(description) > 200:
        raise ValueError(f"mod {mod_id}: description must be up to 200 characters")
    if not isinstance(icon, str) or len(icon) > 8:
        raise ValueError(f"mod {mod_id}: icon must be one emoji")
    if not isinstance(code, str) or not code.strip() or len(code) > CODE_LIMIT:
        raise ValueError(f"mod {mod_id}: code must be 1-{CODE_LIMIT} characters")
    permissions = raw.get("permissions") or []
    if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
        raise ValueError(f"mod {mod_id}: unknown permission")
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 100000:
        raise ValueError(f"mod {mod_id}: version must be a whole number")
    clean = {
        "id": mod_id,
        "name": name.strip(),
        "description": description,
        "icon": icon,
        "version": version,
        "permissions": list(dict.fromkeys(permissions)),
        "code": code,
    }
    if catalog:
        clean["pinned"] = bool(raw.get("pinned", False))
        return clean
    granted = raw.get("granted", permissions) or []
    if not isinstance(granted, list) or any(p not in clean["permissions"] for p in granted):
        raise ValueError(f"mod {mod_id}: granted permissions must be among those it asks for")
    source = raw.get("source", source_default)
    if source not in ("user", "starter", "catalog"):
        raise ValueError(f"mod {mod_id}: unknown source")
    updated = raw.get("updated", 0)
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        updated = 0
    clean.update(
        granted=list(dict.fromkeys(granted)),
        source=source,
        enabled=bool(raw.get("enabled", True)),
        failed=bool(raw.get("failed", False)),
        updated=int(updated),
    )
    return clean


def _clean_list(text: str, limit: int, catalog: bool) -> list:
    try:
        value = _json.loads(text or "[]")
    except ValueError:
        raise ValueError("must be valid JSON")
    if isinstance(value, dict) and not catalog:
        value = value.get("mods", [])
    if not isinstance(value, list):
        raise ValueError("must be a list of mods")
    if len(value) > limit:
        raise ValueError(f"can hold up to {limit} mods")
    mods = [_clean_mod(each, "user", catalog) for each in value]
    ids = [m["id"] for m in mods]
    if len(set(ids)) != len(ids):
        raise ValueError("two mods have the same id")
    return mods


# Ready-made mods. Each is a normal mod, so it is also an example of how to write one.
STARTERS = [
    {
        "id": "word-counter",
        "name": "Word Counter",
        "icon": "🔢",
        "version": 1,
        "description": "Counts the words and characters of your message while you type.",
        "permissions": ["composer.read", "ui.status"],
        "code": r"""const fr = () => mod.theme().lang.startsWith('fr');
const number = new Intl.NumberFormat(mod.theme().lang);
mod.on('composer', text => {
  const words = (text.trim().match(/\S+/g) || []).length;
  if (!words) { mod.status.clear(); return; }
  const label = fr() ? (words === 1 ? 'mot' : 'mots') : (words === 1 ? 'word' : 'words');
  mod.status.set(number.format(words) + ' ' + label + ' · ' + number.format(text.length) + (fr() ? ' car.' : ' chars'));
});""",
    },
    {
        "id": "prompt-shortcuts",
        "name": "Prompt Shortcuts",
        "icon": "⚡",
        "version": 1,
        "description": "Type /summarize, /translate, /simplify, /quiz or /proofread to start a ready-written prompt.",
        "permissions": ["commands", "composer.write"],
        "code": r"""const fr = mod.theme().lang.startsWith('fr');
const prompts = fr ? {
  summarize: ['Résumer un texte', 'Résume ce texte en 5 points clés, puis en une phrase :\n\n'],
  translate: ['Traduire en anglais', 'Traduis ce texte en anglais, en gardant le ton :\n\n'],
  simplify: ['Expliquer simplement', 'Explique-moi ceci comme à un élève de 12 ans, avec un exemple :\n\n'],
  quiz: ['Créer un quiz', 'Crée un quiz de 5 questions à choix multiples (avec les réponses à la fin) sur :\n\n'],
  proofread: ['Corriger', 'Corrige l’orthographe et la grammaire de ce texte, puis liste les changements :\n\n']
} : {
  summarize: ['Summarize a text', 'Summarize this text in 5 key points, then in one sentence:\n\n'],
  translate: ['Translate to French', 'Translate this text into French, keeping its tone:\n\n'],
  simplify: ['Explain simply', 'Explain this to me as if I were 12, with one example:\n\n'],
  quiz: ['Make a quiz', 'Write a 5-question multiple-choice quiz (answers at the end) about:\n\n'],
  proofread: ['Proofread', 'Fix the spelling and grammar of this text, then list what you changed:\n\n']
};
for (const [name, [description, prompt]] of Object.entries(prompts)) {
  mod.commands.add(name, description, args => prompt + args);
}""",
    },
    {
        "id": "focus-mode",
        "name": "Focus Mode",
        "icon": "🎯",
        "version": 1,
        "description": "Hides the sidebar and widens the chat. Turn it on and off from the label above the message box.",
        "permissions": ["ui.style", "ui.status"],
        "code": r"""const fr = mod.theme().lang.startsWith('fr');
const css = `#sidebar, #sidebar-open-button { display: none !important; }
#messages-container .max-w-\\[58rem\\], #message-input-container .max-w-\\[58rem\\], form .max-w-\\[58rem\\] { max-width: 72rem !important; }`;
let on = (await mod.storage.get('on')) ?? false;
async function paint() {
  await mod.style(on ? css : '');
  mod.status.set(on ? (fr ? '🎯 Concentration : activée' : '🎯 Focus: on') : (fr ? '🎯 Concentration' : '🎯 Focus'), { tone: on ? 'ok' : '', onClick: toggle });
}
async function toggle() { on = !on; await mod.storage.set('on', on); paint(); }
paint();""",
    },
    {
        "id": "pomodoro",
        "name": "Pomodoro Timer",
        "icon": "🍅",
        "version": 1,
        "description": "A 25-minute study timer with 5-minute breaks, in a bar above the message box.",
        "permissions": ["ui.band"],
        "code": r"""const fr = mod.theme().lang.startsWith('fr');
const W = fr ? { focus: 'Travail', pause: 'Pause', start: 'Démarrer', stop: 'Arrêter', reset: 'Réinitialiser', done: 'Session terminée : faites une pause.', back: 'Pause finie : on reprend.' }
             : { focus: 'Focus', pause: 'Break', start: 'Start', stop: 'Pause', reset: 'Reset', done: 'Session done: take a break.', back: 'Break over: back to work.' };
const LENGTH = { focus: 25 * 60, pause: 5 * 60 };
let phase = 'focus', left = LENGTH.focus, running = false, rounds = 0, timer = 0;
const clock = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
function paint() {
  mod.band.set({
    text: '🍅 ' + W[phase] + ' · ' + clock(left) + (rounds ? '  ·  ' + '●'.repeat(Math.min(rounds, 8)) : ''),
    actions: [{ label: running ? W.stop : W.start, onClick: toggle }, { label: W.reset, onClick: reset }]
  });
}
function tick() {
  left -= 1;
  if (left <= 0) {
    if (phase === 'focus') rounds += 1;
    mod.toast(phase === 'focus' ? W.done : W.back);
    phase = phase === 'focus' ? 'pause' : 'focus';
    left = LENGTH[phase];
  }
  paint();
}
function toggle() { running = !running; clearInterval(timer); if (running) timer = setInterval(tick, 1000); paint(); }
function reset() { running = false; clearInterval(timer); phase = 'focus'; left = LENGTH.focus; paint(); }
paint();""",
    },
    {
        "id": "bookmarks",
        "name": "Bookmarks",
        "icon": "🔖",
        "version": 1,
        "description": "Bookmark answers with a button under each one, and find them again in a panel.",
        "permissions": ["message.buttons", "ui.pane", "ui.status", "chat.read"],
        "code": r"""const fr = mod.theme().lang.startsWith('fr');
const W = fr ? { save: '🔖 Marquer', saved: 'Réponse marquée', title: 'Signets', empty: 'Aucun signet. Utilisez 🔖 Marquer sous une réponse.', open: 'Ouvrir', remove: 'Retirer', other: 'Dans une autre conversation' }
             : { save: '🔖 Bookmark', saved: 'Answer bookmarked', title: 'Bookmarks', empty: 'No bookmarks yet. Use 🔖 Bookmark under an answer.', open: 'Go To', remove: 'Remove', other: 'In another chat' };
let marks = (await mod.storage.get('marks')) || [];
let chatId = null;
const date = new Intl.DateTimeFormat(mod.theme().lang, { dateStyle: 'medium', timeStyle: 'short' });
function label() { mod.status.set('🔖 ' + marks.length, { onClick: () => mod.pane.open({ title: W.title }) }); }
function render() {
  document.body.replaceChildren();
  if (!marks.length) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = W.empty; document.body.append(p); return; }
  for (const mark of [...marks].reverse()) {
    const card = document.createElement('div'); card.className = 'card'; card.style.marginBottom = '8px';
    const text = document.createElement('p'); text.style.margin = '0 0 8px'; text.dir = 'auto';
    text.textContent = mark.text.length > 280 ? mark.text.slice(0, 280) + '…' : mark.text;
    const meta = document.createElement('div'); meta.className = 'muted'; meta.style.fontSize = '12px'; meta.style.marginBottom = '8px';
    meta.textContent = date.format(mark.at) + (mark.chatId !== chatId ? ' · ' + W.other : '');
    const row = document.createElement('div'); row.style.display = 'flex'; row.style.gap = '6px';
    if (mark.chatId === chatId) { const go = document.createElement('button'); go.textContent = W.open; go.onclick = () => mod.chat.goto(mark.id); row.append(go); }
    const del = document.createElement('button'); del.textContent = W.remove;
    del.onclick = async () => { marks = marks.filter(m => m !== mark); await mod.storage.set('marks', marks); render(); label(); };
    row.append(del); card.append(text, meta, row); document.body.append(card);
  }
}
mod.messages.button(W.save, async message => {
  if (!message.text || marks.some(m => m.id === message.id)) return;
  marks.push({ id: message.id, chatId: message.chatId, text: message.text.slice(0, 2000), at: Date.now() });
  marks = marks.slice(-100);
  await mod.storage.set('marks', marks);
  mod.toast(W.saved); render(); label();
});
mod.on('chat', chat => { chatId = chat ? chat.id : null; render(); });
mod.on('pane', () => render());
render(); label();""",
    },
    {
        "id": "readability",
        "name": "Readability",
        "icon": "📖",
        "version": 1,
        "description": "Shows how easy the last answer is to read, from very easy to very hard.",
        "permissions": ["chat.read", "ui.status"],
        "code": r"""const fr = mod.theme().lang.startsWith('fr');
const levels = fr ? ['Très facile', 'Facile', 'Assez facile', 'Standard', 'Assez difficile', 'Difficile', 'Très difficile']
                  : ['Very easy', 'Easy', 'Fairly easy', 'Standard', 'Fairly hard', 'Hard', 'Very hard'];
function syllables(word) {
  const w = word.toLowerCase().replace(/[^a-zà-ÿ]/g, '');
  if (!w) return 0;
  const groups = w.replace(/e$/, '').match(/[aeiouyàâäéèêëîïôöùûü]+/g);
  return Math.max(1, groups ? groups.length : 1);
}
function score(text) {
  const plain = text.replace(/```[\s\S]*?```/g, ' ').replace(/[#*_>`|-]/g, ' ');
  const words = plain.match(/[A-Za-zÀ-ÿ']+/g) || [];
  const sentences = Math.max(1, (plain.match(/[.!?]+(\s|$)/g) || []).length);
  if (words.length < 20) return null;
  const syl = words.reduce((n, w) => n + syllables(w), 0);
  // Flesch reading ease, with Kandel & Moles' constants for French
  return fr ? 207 - 1.015 * (words.length / sentences) - 73.6 * (syl / words.length)
            : 206.835 - 1.015 * (words.length / sentences) - 84.6 * (syl / words.length);
}
function show(text) {
  const s = score(text || '');
  if (s === null) { mod.status.clear(); return; }
  const i = s >= 90 ? 0 : s >= 80 ? 1 : s >= 70 ? 2 : s >= 60 ? 3 : s >= 50 ? 4 : s >= 30 ? 5 : 6;
  mod.status.set('📖 ' + levels[i] + ' · ' + Math.round(Math.max(0, Math.min(100, s))), { tone: i >= 5 ? 'warn' : '' });
}
async function latest() {
  const chat = await mod.chat.current();
  const last = chat && [...chat.messages].reverse().find(m => m.role === 'assistant');
  show(last ? last.text : '');
}
mod.on('message', message => show(message.text));
mod.on('chat', () => latest().catch(() => mod.status.clear()));
latest().catch(() => {});""",
    },
    {
        "id": "export-markdown",
        "name": "Export to Markdown",
        "icon": "📝",
        "version": 1,
        "description": "Saves the open chat as a Markdown file, from a label above the message box or with /export.",
        "permissions": ["chat.read", "download", "ui.status", "commands"],
        "code": r"""const fr = mod.theme().lang.startsWith('fr');
const W = fr ? { label: '📝 Exporter', none: 'Ouvrez une conversation pour l’exporter.', you: 'Vous', ai: 'Assistant', done: 'Conversation exportée', cmd: 'Exporter la conversation en Markdown' }
             : { label: '📝 Export', none: 'Open a chat to export it.', you: 'You', ai: 'Assistant', done: 'Chat exported', cmd: 'Save this chat as Markdown' };
async function exportChat() {
  const chat = await mod.chat.current();
  if (!chat || !chat.messages.length) { mod.toast(W.none); return; }
  const title = chat.title || 'Chat';
  const parts = ['# ' + title, '', '_' + new Intl.DateTimeFormat(mod.theme().lang, { dateStyle: 'long', timeStyle: 'short' }).format(new Date()) + '_', ''];
  for (const m of chat.messages) parts.push('## ' + (m.role === 'user' ? W.you : W.ai), '', m.text, '');
  await mod.download(title, parts.join('\n'), 'text/markdown');
  mod.toast(W.done);
}
function label(chat) { if (chat) mod.status.set(W.label, { onClick: exportChat }); else mod.status.clear(); }
mod.commands.add('export', W.cmd, async () => { await exportChat(); return ''; });
mod.on('chat', label);
label(await mod.chat.current().catch(() => null));""",
    },
    {
        "id": "warm-reading",
        "name": "Warm Reading Theme",
        "icon": "☕",
        "version": 1,
        "description": "Softer, warmer greys for long reading sessions, in light and dark mode.",
        "permissions": ["ui.style"],
        "code": r"""mod.style(`
html:not(.dark) {
  --color-gray-50: #faf7f2; --color-gray-100: #f1ece3; --color-gray-200: #e4dccf; --color-gray-300: #cfc4b3;
  --color-gray-400: #a89c8a; --color-gray-500: #847866; --color-gray-600: #62584a; --color-gray-700: #4a4237;
  --color-gray-800: #352f27; --color-gray-850: #2b2620; --color-gray-900: #221e19; --color-gray-950: #171410;
}
html:not(.dark) body, html:not(.dark) .bg-white { background-color: #fdfaf5 !important; }
html.dark {
  --color-gray-50: #f5f0e8; --color-gray-100: #e8e1d6; --color-gray-200: #d6cdbf; --color-gray-300: #b9ae9d;
  --color-gray-400: #9a8f7e; --color-gray-500: #7a705f; --color-gray-600: #5c5447; --color-gray-700: #463f35;
  --color-gray-800: #332e27; --color-gray-850: #29251f; --color-gray-900: #1f1c17; --color-gray-950: #161410;
}
`);""",
    },
]


class Event:
    class Valves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="Switch every mod off at once, for everyone, without uninstalling anything.",
        )
        allow_user_mods: bool = Field(
            default=True,
            description="Let people create and import their own mods. When off, they can only install the ready-made mods and those you publish.",
        )
        allowed_permissions: str = Field(
            default=",".join(PERMISSIONS),
            description="Permissions mods may use on this server, separated by commas. Remove one (for example chat.send or ai) and no mod that asks for it can be installed or run. Known: "
            + ", ".join(PERMISSIONS)
            + ".",
        )
        max_mods_per_user: int = Field(
            default=12,
            ge=1,
            le=USER_MOD_LIMIT,
            description="How many mods one person can have installed. Each mod that is on runs in its own sandboxed frame in their browser.",
        )
        ai_calls_per_minute: int = Field(
            default=6,
            ge=0,
            le=60,
            description="How often one mod may ask the AI model a question, per minute. 0 stops mods from calling the model.",
        )
        studio_model: str = Field(
            default="",
            description="Model that writes mods in Create, and answers mods that ask the AI. Leave empty to use the first model each person can use.",
        )
        show_starters: bool = Field(
            default=True,
            description="Offer the 8 ready-made mods in Discover.",
        )
        show_in_chat_menu: bool = Field(
            default=True,
            description="Add Mods to the ⋯ menu of a chat.",
        )
        show_in_tab_menu: bool = Field(
            default=True,
            description="Add Mods to the + menu of the Tabs panel, where Open WebUI shows it.",
        )
        language: Literal["auto", "en", "fr"] = Field(
            default="auto",
            description="Language of Mods. 'auto' follows each person's Open WebUI language, and uses English where there is no translation.",
        )
        catalog: str = Field(
            default="[]",
            description="Mods published to everyone, as JSON. Managed from Mods > Installed > ⋯ > Publish for Everyone; edit by hand only to fix something. Published code is part of the page every signed-in person loads.",
        )

        @field_validator("allowed_permissions")
        @classmethod
        def _check_permissions(cls, value: str) -> str:
            chosen = [p.strip() for p in (value or "").split(",") if p.strip()]
            unknown = [p for p in chosen if p not in PERMISSIONS]
            if unknown:
                raise ValueError("unknown permission: " + ", ".join(unknown))
            return ",".join(dict.fromkeys(chosen))

        @field_validator("catalog")
        @classmethod
        def _check_catalog(cls, value: str) -> str:
            return _json.dumps(_clean_list(value, CATALOG_LIMIT, True), ensure_ascii=False)

    class UserValves(BaseModel):
        mods: str = Field(
            default='{"mods": [], "dismissed": []}',
            description="Your mods, managed from the Mods window (⋯ menu of a chat, or Ctrl+Shift+.). Edit by hand only to fix something.",
        )

        @field_validator("mods")
        @classmethod
        def _check_mods(cls, value: str) -> str:
            # Every save comes back here: what is stored can only be well-formed mods
            try:
                raw = _json.loads(value or "{}")
            except ValueError:
                raise ValueError("must be valid JSON")
            if isinstance(raw, list):
                raw = {"mods": raw}
            if not isinstance(raw, dict):
                raise ValueError("must be an object with mods")
            mods = _clean_list(_json.dumps(raw.get("mods", [])), USER_MOD_LIMIT, False)
            dismissed = raw.get("dismissed", [])
            if not isinstance(dismissed, list) or len(dismissed) > 200:
                raise ValueError("dismissed must be a short list")
            dismissed = [d for d in dismissed if isinstance(d, str) and MOD_ID.match(d)]
            return _json.dumps(
                {"mods": mods, "dismissed": list(dict.fromkeys(dismissed))},
                ensure_ascii=False,
            )

    LOADER_BLOCK_START = "// owui-mods:start"
    LOADER_BLOCK_END = "// owui-mods:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment_key = None
        self._fragment = ""
        reload_bootstrap()

    def _config(self) -> dict:
        valves = self.valves
        try:
            catalog = _clean_list(valves.catalog, CATALOG_LIMIT, True)
        except ValueError:
            catalog = []
        return {
            "functionId": RELOAD_FUNCTION_ID,
            "enabled": bool(valves.enabled),
            "allowUserMods": bool(valves.allow_user_mods),
            "allowedPermissions": [
                p for p in valves.allowed_permissions.split(",") if p in PERMISSIONS
            ],
            "maxMods": int(valves.max_mods_per_user),
            "aiPerMinute": int(valves.ai_calls_per_minute),
            "studioModel": valves.studio_model.strip(),
            "chatMenu": bool(valves.show_in_chat_menu),
            "tabMenu": bool(valves.show_in_tab_menu),
            "language": valves.language,
            "catalog": catalog,
            "starters": STARTERS if valves.show_starters else [],
        }

    def _loader_fragment(self) -> str:
        # Runs on the event loop for every page load: rebuild only when a valve changed.
        key = _json.dumps(self._config(), sort_keys=True)
        if key != self._fragment_key:
            # "</" never appears inside the script's data, so no part of it can end a script element early
            config = key.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
            script = LOADER_SCRIPT.replace("__CSS__", _json.dumps(MODS_CSS)).replace(
                "__CONFIG__", config
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
