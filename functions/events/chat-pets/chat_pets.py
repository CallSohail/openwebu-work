"""
title: Chat Pets
description: A companion that lives on Open WebUI's message box, the way ChatGPT's pets do. Each person picks one of six animals or makes their own. It follows what the AI does live (web search, documents, notes, memory, tasks, calendar, automations, code, images, skills, sub-agents), reacts to errors, warnings and lost connections, announces automations and calendar reminders, and offers shortcuts for its job. Admins choose who gets a pet by role, group or person.
author: Muhammad Sohail
version: 2.0.0
license: MIT
required_open_webui_version: 0.13.0
"""

# The two protocol blocks below are Classic298's Shared Assets Protocol and its
# live reload add-on (BSD 3-Clause), copied byte for byte from Chat Inspector.

import hashlib as _hashlib
import json as _json
import re as _re
import time as _time
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

# ===========================================================================
# Chat Pets: styles
# ===========================================================================

PETS_CSS = r"""
#owp-pet,#owp-bubble,#owp-menu,#owp-paw,.owp-modal{--owp-surface:#fff;--owp-text:#1f2328;--owp-muted:#5d6470;--owp-line:#e2e5ea;--owp-soft:#f4f5f7;--owp-accent:#2f6fde;--owp-ring:rgba(47,111,222,.35);font-family:inherit;box-sizing:border-box}
html.dark #owp-pet,html.dark #owp-bubble,html.dark #owp-menu,html.dark #owp-paw,html.dark .owp-modal{--owp-surface:#1e2127;--owp-text:#eceef2;--owp-muted:#a3a9b4;--owp-line:#343a44;--owp-soft:#272b33;--owp-accent:#7aa7ff;--owp-ring:rgba(122,167,255,.4)}
#owp-pet *,#owp-bubble *,#owp-menu *,.owp-modal *{box-sizing:border-box}
#owp-pet{position:fixed;left:0;top:0;z-index:30;width:var(--owp-size,58px);height:var(--owp-size,58px);transform:translate3d(var(--owp-x,-200px),var(--owp-y,-200px),0);pointer-events:none;will-change:transform}
#owp-pet.owp-walking{transition:transform var(--owp-walk,1s) linear}
#owp-pet[hidden],#owp-bubble[hidden],#owp-menu[hidden],#owp-paw[hidden],.owp-modal[hidden]{display:none!important}
#owp-pet .owp-hit{position:absolute;inset:0;pointer-events:auto;cursor:grab;border:0;padding:0;margin:0;background:transparent;border-radius:40%;touch-action:none;-webkit-tap-highlight-color:transparent}
#owp-pet.owp-dragging .owp-hit{cursor:grabbing}
#owp-pet .owp-hit:focus-visible{outline:2px solid var(--owp-accent);outline-offset:3px}
#owp-pet .owp-flip{width:100%;height:100%;transform-origin:50% 100%}
#owp-pet.owp-left .owp-flip{transform:scaleX(-1)}
#owp-pet .owp-jump{width:100%;height:100%;transform-origin:50% 100%}
#owp-pet svg{display:block;width:100%;height:100%;overflow:visible}
#owp-pet svg *{transform-box:view-box}
.owp-body,.owp-head,.owp-eyes,.owp-tail,.owp-leg-a,.owp-leg-b,.owp-pupils,.owp-wing{transform-box:view-box}
#owp-pet .owp-body{transform-origin:32px 62px;animation:owp-breathe 3.2s ease-in-out infinite}
#owp-pet .owp-eyes{animation:owp-blink 4.6s infinite}
#owp-pet .owp-tail{animation:owp-sway 2.8s ease-in-out infinite}
#owp-pet .owp-head{transition:transform .35s ease}
#owp-pet .owp-pupils{transition:transform .25s ease}
#owp-pet[data-state=walk] .owp-leg-a{animation:owp-step .42s ease-in-out infinite}
#owp-pet[data-state=walk] .owp-leg-b{animation:owp-step .42s ease-in-out infinite .21s}
#owp-pet[data-state=walk] .owp-jump{animation:owp-bob .42s ease-in-out infinite}
#owp-pet[data-state=watch] .owp-head{transform:rotate(-7deg) translateY(1px)}
#owp-pet[data-state=watch] .owp-pupils{transform:translate(1.2px,1.6px)}
#owp-pet[data-state=think] .owp-head{animation:owp-nod 1.1s ease-in-out infinite}
#owp-pet[data-state=think] .owp-tail{animation-duration:.7s}
#owp-pet[data-state=think] .owp-pupils{transform:translate(0,-1.6px)}
#owp-pet[data-state=happy] .owp-jump{animation:owp-hop .9s ease-out 2}
#owp-pet[data-state=happy] .owp-tail{animation-duration:.45s}
#owp-pet[data-state=greet] .owp-jump{animation:owp-hop .8s ease-out 1}
#owp-pet[data-state=sleep] .owp-eyes{animation:none;transform:scaleY(.12)}
#owp-pet[data-state=sleep] .owp-body{animation-duration:5s}
#owp-pet[data-state=sleep] .owp-head{transform:rotate(9deg) translateY(2px)}
#owp-pet[data-state=sleep] .owp-tail{animation:none}
#owp-pet .owp-fx{position:absolute;left:70%;top:-2%;pointer-events:none;font:700 13px/1 system-ui,sans-serif;color:var(--owp-muted);white-space:nowrap}
#owp-pet.owp-left .owp-fx{left:auto;right:70%}
#owp-pet .owp-fx span{display:inline-block;opacity:0}
#owp-pet[data-state=sleep] .owp-fx span{animation:owp-z 3s ease-in infinite}
#owp-pet[data-state=sleep] .owp-fx span:nth-child(2){animation-delay:1s}
#owp-pet[data-state=sleep] .owp-fx span:nth-child(3){animation-delay:2s}
#owp-pet[data-state=think] .owp-fx span{animation:owp-dots 1.2s ease-in-out infinite;opacity:1}
#owp-pet[data-state=think] .owp-fx span:nth-child(2){animation-delay:.2s}
#owp-pet[data-state=think] .owp-fx span:nth-child(3){animation-delay:.4s}
#owp-pet[data-state=happy] .owp-fx span{animation:owp-spark 1s ease-out 2;opacity:0;color:#e8a316}
#owp-pet.owp-paused *,#owp-pet.owp-calm *{animation-play-state:paused!important}
#owp-pet.owp-calm .owp-jump{animation:none!important}
#owp-pet.owp-calm[data-state=think] .owp-fx span,#owp-pet.owp-calm[data-state=sleep] .owp-fx span{opacity:1;animation:none!important}
#owp-pet.owp-corner{--owp-size:46px}
#owp-pet img.owp-photo{display:none}
@keyframes owp-breathe{0%,100%{transform:scale(1,1)}50%{transform:scale(1.015,1.04)}}
@keyframes owp-blink{0%,93%,100%{transform:scaleY(1)}95.5%{transform:scaleY(.1)}}
@keyframes owp-sway{0%,100%{transform:rotate(-5deg)}50%{transform:rotate(7deg)}}
@keyframes owp-step{0%,100%{transform:translateY(0)}50%{transform:translateY(-2.5px)}}
@keyframes owp-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}}
@keyframes owp-nod{0%,100%{transform:rotate(-3deg)}50%{transform:rotate(4deg) translateY(1px)}}
@keyframes owp-hop{0%,100%{transform:translateY(0) scale(1,1)}12%{transform:translateY(0) scale(1.06,.9)}35%{transform:translateY(-14px) scale(.96,1.05)}60%{transform:translateY(0) scale(1.05,.94)}78%{transform:translateY(-4px)}}
@keyframes owp-z{0%{opacity:0;transform:translate(0,0) scale(.7)}25%{opacity:1}100%{opacity:0;transform:translate(10px,-18px) scale(1.2)}}
@keyframes owp-dots{0%,100%{transform:translateY(0);opacity:.35}50%{transform:translateY(-4px);opacity:1}}
@keyframes owp-spark{0%{opacity:0;transform:scale(.4)}30%{opacity:1;transform:scale(1.2)}100%{opacity:0;transform:translateY(-12px) scale(.8)}}
#owp-bubble{position:fixed;left:0;top:0;z-index:31;max-width:min(250px,calc(100vw - 24px));padding:8px 11px;border-radius:14px;background:var(--owp-surface);color:var(--owp-text);border:1px solid var(--owp-line);box-shadow:0 8px 24px -8px rgba(0,0,0,.25);font-size:13px;line-height:1.4;cursor:pointer;animation:owp-pop .22s ease-out}
#owp-bubble::after{content:"";position:absolute;bottom:-6px;left:var(--owp-tail-x,24px);width:10px;height:10px;background:var(--owp-surface);border-right:1px solid var(--owp-line);border-bottom:1px solid var(--owp-line);transform:rotate(45deg)}
#owp-bubble strong{font-weight:600}
@keyframes owp-pop{from{opacity:0;transform:translateY(4px) scale(.96)}to{opacity:1;transform:none}}
#owp-menu{position:fixed;left:0;top:0;z-index:32;width:min(300px,calc(100vw - 24px));max-height:min(480px,calc(100vh - 24px));overflow:auto;padding:8px;border-radius:16px;background:var(--owp-surface);color:var(--owp-text);border:1px solid var(--owp-line);box-shadow:0 18px 48px -12px rgba(0,0,0,.35);font-size:13.5px;animation:owp-pop .16s ease-out}
.owp-mhead{display:flex;gap:10px;align-items:center;padding:6px 8px 10px;border-bottom:1px solid var(--owp-line);margin-bottom:6px}
.owp-mhead .owp-av{width:40px;height:40px;flex:none}
.owp-mhead .owp-av svg{width:100%;height:100%;display:block;overflow:visible}
.owp-mname{font-weight:650;font-size:14.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.owp-mrole{color:var(--owp-muted);font-size:12px}
.owp-mlabel{color:var(--owp-muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:8px 8px 4px}
.owp-mi{display:flex;width:100%;align-items:center;gap:8px;padding:7px 8px;border:0;border-radius:9px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}
.owp-mi:hover,.owp-mi:focus-visible{background:var(--owp-soft);outline:none}
.owp-mi:focus-visible{box-shadow:0 0 0 2px var(--owp-ring)}
.owp-mi .owp-k{margin-left:auto;color:var(--owp-muted);font-size:12px}
.owp-mi svg{width:16px;height:16px;flex:none;color:var(--owp-muted)}
.owp-sep{height:1px;background:var(--owp-line);margin:6px 4px}
#owp-paw{position:fixed;left:0;top:0;z-index:30;width:30px;height:30px;border-radius:999px;border:1px solid var(--owp-line);background:var(--owp-surface);color:var(--owp-muted);display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:.7;box-shadow:0 4px 12px -6px rgba(0,0,0,.3);transition:opacity .15s,transform .15s}
#owp-paw:hover,#owp-paw:focus-visible{opacity:1;transform:scale(1.08);outline:none;box-shadow:0 0 0 2px var(--owp-ring)}
#owp-paw svg{width:17px;height:17px}
#owp-paw.owp-invite{opacity:1;color:var(--owp-accent);animation:owp-wiggle 2.4s ease-in-out 3}
@keyframes owp-wiggle{0%,80%,100%{transform:rotate(0)}85%{transform:rotate(-14deg)}90%{transform:rotate(12deg)}95%{transform:rotate(-6deg)}}
.owp-modal{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(15,18,24,.45);color:var(--owp-text);animation:owp-fade .15s ease-out}
@keyframes owp-fade{from{opacity:0}to{opacity:1}}
.owp-dialog{width:min(760px,100%);max-height:calc(100vh - 32px);max-height:calc(100dvh - 32px);overflow:auto;background:var(--owp-surface);border:1px solid var(--owp-line);border-radius:20px;box-shadow:0 30px 80px -20px rgba(0,0,0,.45);padding:22px 22px 18px}
.owp-dhead{display:flex;align-items:flex-start;gap:12px;margin-bottom:16px}
.owp-dhead h2{margin:0;font-size:19px;font-weight:700;letter-spacing:-.01em}
.owp-dhead p{margin:3px 0 0;color:var(--owp-muted);font-size:13.5px}
.owp-x{margin-left:auto;flex:none;width:32px;height:32px;border-radius:10px;border:0;background:transparent;color:var(--owp-muted);cursor:pointer;display:flex;align-items:center;justify-content:center}
.owp-x:hover,.owp-x:focus-visible{background:var(--owp-soft);color:var(--owp-text);outline:none}
.owp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.owp-card{position:relative;display:flex;flex-direction:column;align-items:center;gap:4px;padding:12px 10px 12px;border-radius:16px;border:1.5px solid var(--owp-line);background:var(--owp-surface);color:inherit;font:inherit;cursor:pointer;text-align:center;min-width:0}
.owp-card:hover{border-color:var(--owp-muted)}
.owp-card:focus-visible{outline:none;box-shadow:0 0 0 3px var(--owp-ring)}
.owp-card[aria-checked=true]{border-color:var(--owp-accent);box-shadow:0 0 0 3px var(--owp-ring)}
.owp-card .owp-cv{width:74px;height:74px;margin-bottom:2px}
.owp-card .owp-cv svg{width:100%;height:100%;display:block;overflow:visible}
.owp-card .owp-cv .owp-body{transform-origin:32px 62px;animation:owp-breathe 3.2s ease-in-out infinite}
.owp-card .owp-cv .owp-eyes{animation:owp-blink 4.6s infinite}
.owp-card .owp-cv .owp-tail{animation:owp-sway 2.8s ease-in-out infinite}
.owp-card:hover .owp-cv{animation:owp-hop .9s ease-out 1}
.owp-cname{font-weight:650;font-size:14.5px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.owp-crole{font-size:12px;color:var(--owp-accent);font-weight:600}
.owp-cblurb{font-size:12px;color:var(--owp-muted);line-height:1.35}
.owp-plus{width:74px;height:74px;border-radius:50%;border:2px dashed var(--owp-line);display:flex;align-items:center;justify-content:center;color:var(--owp-muted);margin-bottom:2px}
.owp-plus svg{width:26px;height:26px}
.owp-dfoot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px}
.owp-btn{height:36px;padding:0 14px;border-radius:11px;border:1px solid var(--owp-line);background:var(--owp-surface);color:var(--owp-text);font:inherit;font-size:13.5px;font-weight:550;cursor:pointer}
.owp-btn:hover{background:var(--owp-soft)}
.owp-btn:focus-visible{outline:none;box-shadow:0 0 0 3px var(--owp-ring)}
.owp-btn.owp-primary{background:var(--owp-text);color:var(--owp-surface);border-color:var(--owp-text)}
.owp-btn.owp-primary:hover{opacity:.9}
.owp-btn:disabled{opacity:.45;cursor:default}
.owp-link{border:0;background:none;color:var(--owp-muted);font:inherit;font-size:13px;cursor:pointer;text-decoration:underline;text-underline-offset:3px;padding:4px}
.owp-spacer{flex:1}
.owp-form{display:grid;grid-template-columns:150px minmax(0,1fr);gap:18px;align-items:start}
.owp-preview{display:flex;flex-direction:column;align-items:center;gap:6px;padding:14px;border-radius:16px;background:var(--owp-soft)}
.owp-preview .owp-cv{width:110px;height:110px}
.owp-preview .owp-cv svg{width:100%;height:100%;display:block;overflow:visible}
.owp-preview .owp-cv .owp-body{transform-origin:32px 62px;animation:owp-breathe 3.2s ease-in-out infinite}
.owp-preview .owp-cv .owp-eyes{animation:owp-blink 4.6s infinite}
.owp-preview .owp-cv .owp-tail{animation:owp-sway 2.8s ease-in-out infinite}
.owp-fields{display:flex;flex-direction:column;gap:12px;min-width:0}
.owp-field label,.owp-field .owp-fl{display:block;font-size:12.5px;font-weight:600;margin-bottom:5px}
.owp-field input[type=text]{width:100%;height:36px;padding:0 10px;border-radius:10px;border:1px solid var(--owp-line);background:var(--owp-surface);color:var(--owp-text);font:inherit;font-size:14px}
.owp-field input[type=text]:focus{outline:none;box-shadow:0 0 0 3px var(--owp-ring);border-color:var(--owp-accent)}
.owp-hint{font-size:11.5px;color:var(--owp-muted);margin-top:4px}
.owp-err{font-size:12px;color:#c0392b;margin-top:4px}
html.dark .owp-err{color:#ff8b7f}
.owp-row{display:flex;flex-wrap:wrap;gap:6px}
.owp-chip{height:32px;padding:0 10px;border-radius:999px;border:1.5px solid var(--owp-line);background:var(--owp-surface);color:inherit;font:inherit;font-size:12.5px;cursor:pointer}
.owp-chip[aria-checked=true]{border-color:var(--owp-accent);color:var(--owp-accent);font-weight:600}
.owp-chip:focus-visible,.owp-sw:focus-visible{outline:none;box-shadow:0 0 0 3px var(--owp-ring)}
.owp-sw{width:28px;height:28px;border-radius:50%;border:2px solid var(--owp-surface);box-shadow:0 0 0 1.5px var(--owp-line);cursor:pointer;padding:0}
.owp-sw[aria-checked=true]{box-shadow:0 0 0 2.5px var(--owp-accent)}
.owp-sr{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media (max-width:560px){.owp-form{grid-template-columns:1fr}.owp-preview{flex-direction:row;justify-content:center}.owp-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.owp-dialog{padding:16px}.owp-card .owp-cv{width:60px;height:60px}}
#owp-pet .owp-emote{position:absolute;right:-12%;top:-16%;width:24px;height:24px;border-radius:50%;background:var(--owp-surface);border:1.5px solid var(--owp-tone);color:var(--owp-tone);display:flex;align-items:center;justify-content:center;box-shadow:0 3px 10px -4px rgba(0,0,0,.35);pointer-events:none;--owp-tone:var(--owp-accent);animation:owp-pop-in .28s cubic-bezier(.3,1.6,.6,1)}
#owp-pet.owp-left .owp-emote{right:auto;left:-12%}
#owp-pet .owp-emote svg{width:14px;height:14px;display:block}
#owp-pet .owp-emote[data-tone=ok]{--owp-tone:#1f9d55}
#owp-pet .owp-emote[data-tone=warn]{--owp-tone:#c27c0e}
#owp-pet .owp-emote[data-tone=err]{--owp-tone:#d64545}
#owp-pet .owp-emote[data-tone=muted]{--owp-tone:var(--owp-muted)}
html.dark #owp-pet .owp-emote[data-tone=ok]{--owp-tone:#4cc38a}
html.dark #owp-pet .owp-emote[data-tone=warn]{--owp-tone:#f0b45c}
html.dark #owp-pet .owp-emote[data-tone=err]{--owp-tone:#ff7a6b}
#owp-pet .owp-emote.owp-pulse{animation:owp-pop-in .28s cubic-bezier(.3,1.6,.6,1),owp-pulse 1.5s ease-in-out .3s infinite}
#owp-pet.owp-corner .owp-emote{width:20px;height:20px}
#owp-pet.owp-corner .owp-emote svg{width:12px;height:12px}
@keyframes owp-pop-in{from{transform:scale(.2);opacity:0}to{transform:scale(1);opacity:1}}
@keyframes owp-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.13)}}
#owp-pet[data-state=sniff] .owp-head{animation:owp-sniff .45s ease-in-out infinite alternate}
#owp-pet[data-state=sniff] .owp-tail{animation-duration:.6s}
#owp-pet[data-state=read] .owp-head{animation:owp-read 2.4s ease-in-out infinite}
#owp-pet[data-state=read] .owp-pupils{transform:translate(0,1.8px)}
#owp-pet[data-state=write] .owp-head{animation:owp-scribble .32s ease-in-out infinite alternate}
#owp-pet[data-state=write] .owp-pupils{transform:translate(.8px,1.8px)}
#owp-pet[data-state=type] .owp-head{animation:owp-scribble .22s ease-in-out infinite alternate}
#owp-pet[data-state=type] .owp-leg-a{animation:owp-step .22s ease-in-out infinite}
#owp-pet[data-state=type] .owp-leg-b{animation:owp-step .22s ease-in-out infinite .11s}
#owp-pet[data-state=type] .owp-pupils{transform:translate(0,1.8px)}
#owp-pet[data-state=paint] .owp-jump{animation:owp-sway-body 1.3s ease-in-out infinite;transform-origin:50% 100%}
#owp-pet[data-state=paint] .owp-head{transform:rotate(-6deg)}
#owp-pet[data-state=ponder] .owp-head{animation:owp-ponder 2.6s ease-in-out infinite}
#owp-pet[data-state=ponder] .owp-pupils{transform:translate(1.4px,-1.8px)}
#owp-pet[data-state=listen] .owp-head{transform:rotate(-11deg) translateY(-1px)}
#owp-pet[data-state=listen] .owp-pupils{transform:translate(-1.4px,0)}
#owp-pet[data-state=listen] .owp-tail{animation-duration:1.6s}
#owp-pet[data-state=worried] .owp-jump{animation:owp-shake .45s ease-in-out 3}
#owp-pet[data-state=worried] .owp-eyes{animation:none;transform:scaleY(.55)}
#owp-pet[data-state=worried] .owp-head{transform:rotate(6deg) translateY(2px)}
#owp-pet[data-state=worried] .owp-tail{animation:none;transform:rotate(-14deg)}
#owp-pet[data-state=puzzled] .owp-head{transform:rotate(15deg)}
#owp-pet[data-state=puzzled] .owp-pupils{transform:translate(1.5px,-1px)}
#owp-pet[data-state=celebrate] .owp-jump{animation:owp-hop .7s ease-out 3}
#owp-pet[data-state=celebrate] .owp-tail{animation-duration:.35s}
#owp-pet[data-state=celebrate] .owp-fx span{animation:owp-spark .9s ease-out 3;opacity:0;color:#e8a316}
#owp-pet[data-state=alarm] .owp-jump{animation:owp-ring .11s linear 14;transform-origin:50% 100%}
#owp-pet[data-state=alarm] .owp-eyes{animation:none;transform:scale(1.15)}
#owp-pet[data-state=offline] .owp-flip{filter:grayscale(.85);opacity:.7}
#owp-pet[data-state=offline] .owp-head{transform:rotate(8deg) translateY(2px)}
#owp-pet[data-state=offline] .owp-tail,#owp-pet[data-state=offline] .owp-body{animation:none}
#owp-pet[data-state=shrug] .owp-jump{animation:owp-shrug .9s ease-in-out 1}
#owp-pet[data-state=shrug] .owp-head{transform:rotate(-9deg)}
#owp-pet[data-state=squish] .owp-jump{animation:owp-squish .8s ease-in-out 1;transform-origin:50% 100%}
#owp-pet[data-state=wait] .owp-head{animation:owp-ponder 1.6s ease-in-out infinite}
#owp-pet[data-state=wait] .owp-pupils{transform:translate(-1.5px,.5px)}
@keyframes owp-sniff{from{transform:rotate(3deg) translate(0,1px)}to{transform:rotate(9deg) translate(1.5px,3px)}}
@keyframes owp-read{0%,100%{transform:rotate(7deg) translateY(2px)}50%{transform:rotate(3deg) translateY(1px)}}
@keyframes owp-scribble{from{transform:rotate(4deg) translateY(1.5px)}to{transform:rotate(8deg) translateY(2.5px)}}
@keyframes owp-sway-body{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(4deg)}}
@keyframes owp-ponder{0%,100%{transform:rotate(-8deg) translateY(-1px)}50%{transform:rotate(-4deg) translateY(-2px)}}
@keyframes owp-shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-3px)}75%{transform:translateX(3px)}}
@keyframes owp-ring{0%,100%{transform:rotate(-5deg)}50%{transform:rotate(5deg)}}
@keyframes owp-shrug{0%,100%{transform:translateY(0)}40%{transform:translateY(-4px) scale(1.03,.97)}}
@keyframes owp-squish{0%,100%{transform:scale(1,1)}35%{transform:scale(1.14,.78)}65%{transform:scale(.94,1.07)}}
#owp-pet.owp-calm .owp-emote{animation:none!important}
#owp-bubble.owp-action{cursor:pointer}
#owp-bubble .owp-bact{display:block;margin-top:4px;color:var(--owp-accent);font-weight:600;font-size:12px}
#owp-bubble[data-tone=err]{border-color:#e5a3a3}
#owp-bubble[data-tone=warn]{border-color:#e7c48a}
html.dark #owp-bubble[data-tone=err]{border-color:#7a3a36}
html.dark #owp-bubble[data-tone=warn]{border-color:#6e5527}
.owp-settings{display:flex;flex-direction:column;gap:14px}
.owp-sgroup{border:1px solid var(--owp-line);border-radius:14px;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
.owp-sgroup h3{margin:0;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--owp-muted);font-weight:650}
.owp-srow{display:flex;align-items:center;gap:12px;justify-content:space-between;min-width:0}
.owp-srow > span{min-width:0}
.owp-srow b{display:block;font-weight:600;font-size:13.5px}
.owp-srow small{display:block;color:var(--owp-muted);font-size:12px;margin-top:1px}
.owp-switch{position:relative;flex:none;width:40px;height:24px;border-radius:999px;border:0;background:var(--owp-line);cursor:pointer;padding:0;transition:background .15s}
.owp-switch::after{content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .15s}
.owp-switch[aria-checked=true]{background:var(--owp-accent)}
.owp-switch[aria-checked=true]::after{transform:translateX(16px)}
.owp-switch:focus-visible{outline:none;box-shadow:0 0 0 3px var(--owp-ring)}
.owp-seg{display:inline-flex;flex:none;border:1px solid var(--owp-line);border-radius:10px;overflow:hidden}
.owp-seg button{height:30px;padding:0 10px;border:0;background:transparent;color:var(--owp-muted);font:inherit;font-size:12.5px;cursor:pointer}
.owp-seg button + button{border-left:1px solid var(--owp-line)}
.owp-seg button[aria-checked=true]{background:var(--owp-soft);color:var(--owp-text);font-weight:600}
.owp-seg button:focus-visible{outline:none;box-shadow:inset 0 0 0 2px var(--owp-ring)}
@media (max-width:560px){.owp-srow{flex-wrap:wrap}}
@media (prefers-reduced-motion:reduce){.owp-card .owp-cv *,.owp-preview .owp-cv *,.owp-card:hover .owp-cv,#owp-paw.owp-invite,#owp-bubble,#owp-menu,.owp-modal,#owp-pet .owp-emote,.owp-switch,.owp-switch::after{animation:none!important;transition:none!important}}
"""

# ===========================================================================
# Chat Pets: browser script
# ===========================================================================

LOADER_SCRIPT = r"""
(() => {
  'use strict';
  if (window.__owuiChatPets) return;
  window.__owuiChatPets = true;
  const CONFIG = __CONFIG__;
  const CSS = __CSS__;
  const D = document;
  const now = () => Date.now();

  /* ================================================================
     Early hooks. loader.js runs before Open WebUI opens its socket,
     so these must be in place before anything else. Both only listen:
     they never change, delay or drop what Open WebUI sends or receives.
     ================================================================ */
  const frameQueue = [];
  let frameSink = null, socketOpen = false;
  const pushFrame = data => { if (frameSink) frameSink(data); else if (frameQueue.length < 200) frameQueue.push(data); };
  (function hookSocket() {
    const Native = window.WebSocket;
    if (!Native || Native.__owpHooked) return;
    const Hooked = function (url, protocols) {
      const ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
      try {
        const u = new URL(String(url), location.href);
        if (u.host === location.host && u.pathname.indexOf('/ws/socket.io') === 0) {
          ws.addEventListener('open', () => { socketOpen = true; pushFrame({ kind: 'open' }); });
          ws.addEventListener('close', () => { socketOpen = false; pushFrame({ kind: 'close' }); });
          ws.addEventListener('message', e => { if (typeof e.data === 'string' && e.data.charCodeAt(0) === 52 && e.data.charCodeAt(1) === 50) pushFrame(e.data); });
        }
      } catch {}
      return ws;
    };
    Hooked.prototype = Native.prototype;
    for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) { try { Object.defineProperty(Hooked, k, { value: Native[k] }); } catch {} }
    Object.defineProperty(Hooked, '__owpHooked', { value: true });
    try { Object.setPrototypeOf(Hooked, Native); } catch {}
    window.WebSocket = Hooked;
  })();
  let micTracks = 0;
  (function hookMic() {
    const md = navigator.mediaDevices;
    if (!md || typeof md.getUserMedia !== 'function' || md.getUserMedia.__owpHooked) return;
    const original = md.getUserMedia.bind(md);
    const wrapped = async constraints => {
      const stream = await original(constraints);
      try {
        for (const track of stream.getAudioTracks()) {
          micTracks++;
          let live = true;
          const done = () => { if (live) { live = false; micTracks = Math.max(0, micTracks - 1); } };
          track.addEventListener('ended', done);
          const stop = track.stop.bind(track);
          track.stop = () => { done(); stop(); };
        }
      } catch {}
      return stream;
    };
    Object.defineProperty(wrapped, '__owpHooked', { value: true });
    try { md.getUserMedia = wrapped; } catch {}
  })();

  /* ================================================================
     Words
     ================================================================ */
  const lang = (() => {
    if (CONFIG.language === 'fr' || CONFIG.language === 'en') return CONFIG.language;
    let loc = '';
    try { loc = localStorage.getItem('locale') || ''; } catch {}
    loc = (loc || navigator.language || 'en').toLowerCase();
    return loc.startsWith('fr') ? 'fr' : 'en';
  })();
  const STRINGS = {
    fr: {
      pick_title: 'Choisissez votre compagnon', pick_sub: 'Choisissez un compagnon ou créez le vôtre.',
      adopt: 'Adopter', adopt_named: 'Adopter {name}', cancel: 'Annuler', close: 'Fermer', none: 'Pas de compagnon', back: 'Retour',
      add_own: 'Créer le mien', add_own_sub: 'Votre nom, votre couleur, votre photo.',
      create_title: 'Créez votre compagnon', create_sub: 'Donnez-lui un nom, un look et un rôle.',
      f_name: 'Nom', f_name_ph: 'Biscotte', f_animal: 'Animal', f_color: 'Couleur', f_role: 'Ce qu’il fait pour vous', f_role_hint: 'Facultatif. Ses raccourcis sont ceux de l’animal choisi.',
      f_photo: 'Photo', f_photo_btn: 'Choisir une photo', f_photo_remove: 'Retirer la photo', f_photo_hint: 'PNG, JPEG, WebP ou GIF, 5 Mo maximum. Elle reste dans votre compte.',
      original: 'Couleur d’origine', err_name: 'Donnez-lui un nom (24 caractères au maximum).', err_photo_type: 'Ce format d’image n’est pas accepté.', err_photo_size: 'Cette image est trop lourde.', err_photo_read: 'Impossible de lire cette image.',
      shortcuts: 'Raccourcis', change: 'Changer de compagnon…', settings: 'Réglages du compagnon…',
      nap: 'Le cacher pendant 1 h', remove: 'Retirer mon compagnon', paw: 'Choisir un compagnon', pet_label: '{name}, votre compagnon. Ouvrir son menu.',
      menu_label: 'Menu de {name}', picker_label: 'Choix du compagnon', settings_label: 'Réglages du compagnon',
      hello_morning: 'Bonjour{name} !', hello_afternoon: 'Bon après-midi{name} !', hello_evening: 'Bonsoir{name} !',
      adopted: 'Coucou, je suis {name} ! Cliquez sur moi pour mes raccourcis.', removed: 'Votre compagnon est parti se reposer. La patte en bas à droite le fait revenir.',
      custom_role: 'Compagnon', snoozed: 'À tout à l’heure !',
      s_title: 'Réglages de {name}', s_sub: 'Choisissez comment il se comporte. Les changements sont enregistrés tout de suite.',
      s_look: 'Apparence', s_behaviour: 'Comportement', s_reactions: 'Réactions', s_nick: 'Surnom', s_nick_hint: 'Vide : son nom d’origine.',
      s_size: 'Taille', s_small: 'Petit', s_medium: 'Moyen', s_large: 'Grand',
      s_place: 'Place', s_composer: 'Barre de message', s_corner: 'Coin',
      s_talk: 'Bulles', s_often: 'Souvent', s_normal: 'Normal', s_rare: 'Rarement', s_never: 'Jamais',
      s_walk: 'Se promener', s_walk_hint: 'Il marche le long de la barre de temps en temps.',
      s_sleep: 'Faire la sieste', s_sleep_hint: 'Il s’endort quand vous vous absentez.',
      s_motion: 'Animations', s_motion_hint: 'Désactivé : il reste calme, sans sauts ni marche.',
      s_activity: 'Suivre l’IA', s_activity_hint: 'Recherche web, notes, mémoire, code, images, outils…',
      s_errors: 'Signaler les problèmes', s_errors_hint: 'Erreurs, outils en échec, connexion perdue.',
      s_notices: 'Annonces', s_notices_hint: 'Automatisations, rappels d’agenda, fichiers, notes et souvenirs enregistrés.',
      s_reset: 'Réglages par défaut', s_done: 'Terminé',
      a_think: 'Je réfléchis…', a_ponder: 'Le modèle raisonne…', a_write: 'Il écrit la réponse…', a_search: 'Je cherche…', a_web: 'Je fouille le web…', a_read: 'Je lis vos documents…',
      a_note: 'Je prends des notes…', a_memory: 'Je consulte ma mémoire…', a_tasks: 'Je fais une liste de tâches…', a_calendar: 'Je regarde l’agenda…', a_automation: 'Je règle une automatisation…',
      a_timer: 'Je surveille l’heure…', a_code: 'J’exécute du code…', a_image: 'Je dessine…', a_agents: 'J’envoie des renforts…', a_skill: 'J’utilise une compétence…', a_tool: 'J’utilise un outil…',
      a_plug: 'J’appelle un service externe…', a_chats: 'Je relis vos conversations…', a_mail: 'Je regarde vos emails…', a_ask: 'L’IA attend votre réponse.', a_listen: 'Je vous écoute…', a_compact: 'Je résume le début de la conversation.',
      r_done: 'C’est prêt !', r_sources: 'Réponse prête, avec ses sources.', r_image: 'Votre image est prête !', r_tasks_done: 'Toutes les tâches sont faites !', r_slow: 'Ouf, c’était long !',
      r_stopped: 'D’accord, j’arrête.', r_empty: 'Hmm, la réponse est vide.', r_tool_failed: 'L’outil « {tool} » n’a pas marché.', r_tools_failed: '{n} outils n’ont pas marché.',
      r_upload: 'Fichier reçu !', r_uploads: '{n} fichiers reçus !', r_offline: 'Connexion perdue… je patiente.', r_online: 'Re-connecté !', r_toast_warn: 'Attention : {text}',
      e_rate: 'Trop de demandes en même temps. Réessayez dans un instant.', e_context: 'La conversation est trop longue pour ce modèle. Ouvrez une nouvelle conversation.',
      e_auth: 'Le modèle a refusé l’accès (clé ou droits).', e_model: 'Ce modèle est introuvable ou indisponible.', e_network: 'Le modèle ne répond pas (réseau).',
      e_timeout: 'Le modèle a mis trop de temps à répondre.', e_generic: 'Oups, une erreur : {text}', e_unknown: 'Oups, quelque chose s’est mal passé.',
      n_auto_start: 'L’automatisation « {name} » démarre.', n_auto_done: 'L’automatisation « {name} » est terminée.', n_auto_fail: 'L’automatisation « {name} » a échoué.',
      n_calendar: '« {name} » commence {when}.', n_now: 'maintenant', n_file_fail: 'Le fichier « {name} » n’a pas pu être lu.', n_note: 'Note enregistrée : « {name} »',
      n_memory: 'C’est noté, je m’en souviendrai.', n_image: 'Une image vient d’être créée.', n_open: 'Ouvrir', n_background: 'Réponse prête dans « {name} ».'
    },
    en: {
      pick_title: 'Choose your pet', pick_sub: 'Pick a companion or add one of your own.',
      adopt: 'Choose', adopt_named: 'Choose {name}', cancel: 'Cancel', close: 'Close', none: 'No pet', back: 'Back',
      add_own: 'Add your own', add_own_sub: 'Your name, your colour, your photo.',
      create_title: 'Make your pet', create_sub: 'Give it a name, a look and a job.',
      f_name: 'Name', f_name_ph: 'Biscuit', f_animal: 'Animal', f_color: 'Colour', f_role: 'What it helps you with', f_role_hint: 'Optional. Its shortcuts come from the animal you pick.',
      f_photo: 'Photo', f_photo_btn: 'Choose a photo', f_photo_remove: 'Remove photo', f_photo_hint: 'PNG, JPEG, WebP or GIF, up to 5 MB. It stays in your account.',
      original: 'Original colour', err_name: 'Give it a name (up to 24 characters).', err_photo_type: 'This image format isn’t supported.', err_photo_size: 'This image is too large.', err_photo_read: 'This image couldn’t be read.',
      shortcuts: 'Shortcuts', change: 'Change pet…', settings: 'Pet settings…',
      nap: 'Hide for 1 hour', remove: 'Remove my pet', paw: 'Choose a pet', pet_label: '{name}, your pet. Open its menu.',
      menu_label: '{name}’s menu', picker_label: 'Pet picker', settings_label: 'Pet settings',
      hello_morning: 'Good morning{name}!', hello_afternoon: 'Good afternoon{name}!', hello_evening: 'Good evening{name}!',
      adopted: 'Hi, I’m {name}! Click me for shortcuts.', removed: 'Your pet went to rest. The paw at the bottom right brings it back.',
      custom_role: 'Companion', snoozed: 'See you soon!',
      s_title: '{name}’s settings', s_sub: 'Choose how it behaves. Changes are saved right away.',
      s_look: 'Look', s_behaviour: 'Behaviour', s_reactions: 'Reactions', s_nick: 'Nickname', s_nick_hint: 'Empty: its own name.',
      s_size: 'Size', s_small: 'Small', s_medium: 'Medium', s_large: 'Large',
      s_place: 'Place', s_composer: 'Message box', s_corner: 'Corner',
      s_talk: 'Speech bubbles', s_often: 'Often', s_normal: 'Normal', s_rare: 'Rarely', s_never: 'Never',
      s_walk: 'Wander', s_walk_hint: 'It walks along the box now and then.',
      s_sleep: 'Nap', s_sleep_hint: 'It falls asleep when you step away.',
      s_motion: 'Animations', s_motion_hint: 'Off: it stays calm, no hops or walks.',
      s_activity: 'Follow the AI', s_activity_hint: 'Web search, notes, memory, code, images, tools…',
      s_errors: 'Report problems', s_errors_hint: 'Errors, failed tools, lost connection.',
      s_notices: 'Notices', s_notices_hint: 'Automations, calendar reminders, files, notes and memories saved.',
      s_reset: 'Reset to defaults', s_done: 'Done',
      a_think: 'Thinking…', a_ponder: 'The model is reasoning…', a_write: 'Writing the answer…', a_search: 'Searching…', a_web: 'Digging through the web…', a_read: 'Reading your documents…',
      a_note: 'Taking notes…', a_memory: 'Checking my memory…', a_tasks: 'Making a task list…', a_calendar: 'Looking at the calendar…', a_automation: 'Setting up an automation…',
      a_timer: 'Keeping an eye on the time…', a_code: 'Running code…', a_image: 'Drawing…', a_agents: 'Calling in helpers…', a_skill: 'Using a skill…', a_tool: 'Using a tool…',
      a_plug: 'Calling an outside service…', a_chats: 'Reading your past chats…', a_mail: 'Looking at your emails…', a_ask: 'The AI is waiting for your answer.', a_listen: 'Listening…', a_compact: 'Summarising the start of the chat.',
      r_done: 'All done!', r_sources: 'Answer ready, with sources.', r_image: 'Your image is ready!', r_tasks_done: 'Every task is done!', r_slow: 'Phew, that was a long one!',
      r_stopped: 'Okay, stopping.', r_empty: 'Hmm, the answer is empty.', r_tool_failed: 'The “{tool}” tool didn’t work.', r_tools_failed: '{n} tools didn’t work.',
      r_upload: 'File received!', r_uploads: '{n} files received!', r_offline: 'Connection lost… waiting.', r_online: 'Back online!', r_toast_warn: 'Heads up: {text}',
      e_rate: 'Too many requests at once. Try again in a moment.', e_context: 'This chat is too long for the model. Start a new chat.',
      e_auth: 'The model refused access (key or permissions).', e_model: 'This model can’t be found or isn’t available.', e_network: 'The model isn’t answering (network).',
      e_timeout: 'The model took too long to answer.', e_generic: 'Oops, an error: {text}', e_unknown: 'Oops, something went wrong.',
      n_auto_start: 'Automation “{name}” is starting.', n_auto_done: 'Automation “{name}” has finished.', n_auto_fail: 'Automation “{name}” failed.',
      n_calendar: '“{name}” starts {when}.', n_now: 'now', n_file_fail: 'The file “{name}” couldn’t be read.', n_note: 'Note saved: “{name}”',
      n_memory: 'Noted, I’ll remember that.', n_image: 'An image was just created.', n_open: 'Open', n_background: 'Answer ready in “{name}”.'
    }
  };
  const T = (key, vars) => {
    let s = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
    if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  };
  const L = obj => (obj && (obj[lang] || obj.en)) || '';

  /* ================================================================
     Pets
     ================================================================ */
  const SPECIES = {
    hibou: { name: 'Plume', colors: { main: '#8a5a3b', belly: '#f0ddc2', dark: '#3a281c', accent: '#f0a234' },
      kind: { fr: 'le hibou', en: 'the owl' }, role: { fr: 'Courrier', en: 'Mail' },
      blurb: { fr: 'Trie vos emails et prépare vos réponses.', en: 'Sorts your inbox and drafts your replies.' },
      lines: { fr: ['Hou hou ! Aucun email ne m’échappe.', 'Réponse livrée, plume au vent.', 'Je garde un œil sur votre boîte.'],
               en: ['Hoo hoo! No email gets past me.', 'Answer delivered, feathers ruffled.', 'I’m keeping an eye on your inbox.'] },
      actions: [
        { fr: 'Résume mes emails non lus', en: 'Summarise my unread emails' },
        { fr: 'Quels emails importants attendent une réponse ?', en: 'Which important emails are waiting for a reply?' },
        { fr: 'Aide-moi à répondre au dernier email que j’ai reçu', en: 'Help me reply to the last email I received' },
        { fr: 'Analyse ma boîte mail des 7 derniers jours', en: 'Analyse my mailbox for the last 7 days' } ] },
    ecureuil: { name: 'Noisette', colors: { main: '#c0692a', belly: '#f5dfc3', dark: '#4a2612', accent: '#ffffff' },
      kind: { fr: 'l’écureuil', en: 'the squirrel' }, role: { fr: 'Notes et mémoire', en: 'Notes & memory' },
      blurb: { fr: 'Range vos idées et n’oublie rien.', en: 'Stores your ideas and forgets nothing.' },
      lines: { fr: ['Hop, rangé dans ma réserve !', 'Une noisette de plus pour l’hiver.', 'Je retiens tout, promis.'],
               en: ['Tucked away in my stash!', 'One more acorn for winter.', 'I remember everything, promise.'] },
      actions: [
        { fr: 'Prends une note : ', en: 'Take a note: ' },
        { fr: 'Retrouve mes notes sur ', en: 'Find my notes about ' },
        { fr: 'Résume cette conversation dans une note', en: 'Summarise this conversation in a note' },
        { fr: 'Qu’as-tu retenu sur moi ?', en: 'What do you remember about me?' } ] },
    berger: { name: 'Filou', colors: { main: '#c98b4c', belly: '#f7ead8', dark: '#3b2a1d', accent: '#ef8aa2' },
      kind: { fr: 'le chien de berger', en: 'the sheepdog' }, role: { fr: 'Tâches', en: 'Tasks' },
      blurb: { fr: 'Rassemble vos tâches et vous rappelle les échéances.', en: 'Herds your tasks and nudges you on deadlines.' },
      lines: { fr: ['Wouf ! Tout le troupeau est rentré.', 'Une tâche de moins, bravo !', 'Je surveille vos échéances.'],
               en: ['Woof! The whole flock is in.', 'One task down, well done!', 'I’m watching your deadlines.'] },
      actions: [
        { fr: 'Quelles tâches sont en retard ?', en: 'Which tasks are overdue?' },
        { fr: 'Ajoute une tâche : ', en: 'Add a task: ' },
        { fr: 'Fais-moi un plan pour cette semaine', en: 'Make me a plan for this week' },
        { fr: 'Rappelle-moi dans une heure de ', en: 'Remind me in an hour to ' } ] },
    renard: { name: 'Roux', colors: { main: '#e2702a', belly: '#fff3e6', dark: '#33241b', accent: '#ffffff' },
      kind: { fr: 'le renard', en: 'the fox' }, role: { fr: 'Recherche', en: 'Research' },
      blurb: { fr: 'Fouille le web et vos documents pour vous.', en: 'Digs through the web and your documents.' },
      lines: { fr: ['Flair infaillible, résultat trouvé.', 'J’ai suivi la piste jusqu’au bout.', 'Encore une énigme résolue.'],
               en: ['Sharp nose, answer found.', 'I followed the trail all the way.', 'Another puzzle solved.'] },
      actions: [
        { fr: 'Fais une recherche sur ', en: 'Research ' },
        { fr: 'Compare ces deux options : ', en: 'Compare these two options: ' },
        { fr: 'Explique-moi simplement ', en: 'Explain simply ' },
        { fr: 'Trouve des sources fiables sur ', en: 'Find reliable sources about ' } ] },
    chat: { name: 'Minuit', colors: { main: '#6f7682', belly: '#e9ebee', dark: '#22262c', accent: '#f39bb4' },
      kind: { fr: 'le chat', en: 'the cat' }, role: { fr: 'Agenda', en: 'Calendar' },
      blurb: { fr: 'Veille sur votre agenda et vos réunions.', en: 'Keeps watch over your calendar and meetings.' },
      lines: { fr: ['Miaou. Rien ne m’échappe dans votre agenda.', 'Ronron… c’est fait.', 'Je retombe toujours sur mes pattes.'],
               en: ['Meow. Nothing in your calendar escapes me.', 'Purr… done.', 'I always land on my feet.'] },
      actions: [
        { fr: 'Qu’est-ce que j’ai demain ?', en: 'What do I have tomorrow?' },
        { fr: 'Trouve un créneau d’une heure cette semaine avec ', en: 'Find a one-hour slot this week with ' },
        { fr: 'Prépare ma prochaine réunion', en: 'Prepare my next meeting' },
        { fr: 'Suis-je libre jeudi après-midi ?', en: 'Am I free on Thursday afternoon?' } ] },
    tortue: { name: 'Sage', colors: { main: '#4f9a5c', belly: '#a9d08b', dark: '#24452a', accent: '#d8ecb8' },
      kind: { fr: 'la tortue', en: 'the turtle' }, role: { fr: 'Révisions', en: 'Study' },
      blurb: { fr: 'Vous fait réviser pas à pas, sans stress.', en: 'Helps you revise step by step, no stress.' },
      lines: { fr: ['Lentement mais sûrement !', 'Une notion de plus dans la carapace.', 'Rien ne sert de courir.'],
               en: ['Slow and steady!', 'One more idea safely in my shell.', 'No need to rush.'] },
      actions: [
        { fr: 'Fais-moi réviser ', en: 'Help me revise ' },
        { fr: 'Pose-moi 5 questions sur ', en: 'Ask me 5 questions about ' },
        { fr: 'Explique-moi pas à pas ', en: 'Explain step by step ' },
        { fr: 'Fais un plan de révision pour mon examen du ', en: 'Make a revision plan for my exam on ' } ] }
  };
  const ORDER = ['hibou', 'ecureuil', 'berger', 'renard', 'chat', 'tortue'];
  const AVAILABLE = ORDER.filter(id => (CONFIG.pets || ORDER).includes(id));
  if (CONFIG.actions) for (const id in CONFIG.actions) if (SPECIES[id]) SPECIES[id].actions = CONFIG.actions[id];
  const SWATCHES = ['#e2702a', '#c0692a', '#8a5a3b', '#c98b4c', '#d4a017', '#4f9a5c', '#3f8f8a', '#4a7bd0', '#7a5cc4', '#d65d8a', '#6f7682', '#2f3640'];

  /* ---------------- colours ---------------- */
  const HEX = /^#[0-9a-f]{6}$/i;
  const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const hex = a => '#' + a.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
  const mix = (a, b, t) => { const x = rgb(a), y = rgb(b); return hex(x.map((v, i) => v + (y[i] - v) * t)); };
  function palette(pet) {
    const base = SPECIES[pet.base || pet.id] || SPECIES.renard;
    if (!pet.color || !HEX.test(pet.color)) return base.colors;
    return { main: pet.color, belly: mix(pet.color, '#ffffff', 0.74), dark: mix(pet.color, '#101014', 0.72), accent: base.colors.accent };
  }

  /* ---------------- drawings (64 x 64, feet on y = 62) ---------------- */
  const eyesDot = (y, c) => `<g class="owp-eyes" style="transform-origin:32px ${y}px"><g class="owp-pupils"><ellipse cx="26" cy="${y}" rx="2.3" ry="2.9" fill="${c.dark}"/><ellipse cx="38" cy="${y}" rx="2.3" ry="2.9" fill="${c.dark}"/><circle cx="26.8" cy="${y - 1}" r=".8" fill="#fff"/><circle cx="38.8" cy="${y - 1}" r=".8" fill="#fff"/></g></g>`;
  const legs = (c, y = 52) => `<g><rect class="owp-leg-a" x="22" y="${y}" width="6.5" height="${62 - y}" rx="3.2" fill="${c.dark}"/><rect class="owp-leg-b" x="35.5" y="${y}" width="6.5" height="${62 - y}" rx="3.2" fill="${c.dark}"/></g>`;
  const DRAW = {
    hibou: c => `<g class="owp-body"><ellipse cx="32" cy="40" rx="19" ry="21" fill="${c.main}"/><ellipse cx="32" cy="45" rx="12" ry="13" fill="${c.belly}"/><path d="M27 41q5 3 10 0M26 47q6 3 12 0M28 53q4 2 8 0" stroke="${c.main}" stroke-width="1.2" fill="none" opacity=".5"/><path class="owp-wing" d="M13.5 36c-3 9 0 17 7 20 0-9-2-15-7-20z" fill="${c.dark}" opacity=".35"/><path class="owp-wing" d="M50.5 36c3 9 0 17-7 20 0-9 2-15 7-20z" fill="${c.dark}" opacity=".35"/></g>`
      + `<rect class="owp-leg-a" x="25" y="58" width="5" height="4.5" rx="2" fill="${c.accent}"/><rect class="owp-leg-b" x="34" y="58" width="5" height="4.5" rx="2" fill="${c.accent}"/>`
      + `<g class="owp-head" style="transform-origin:32px 34px"><path d="M15 21l3-11 8 8z" fill="${c.main}"/><path d="M49 21l-3-11-8 8z" fill="${c.main}"/><ellipse cx="32" cy="27" rx="18" ry="14" fill="${c.main}"/>`
      + `<g class="owp-eyes" style="transform-origin:32px 26px"><circle cx="24.5" cy="26" r="7.2" fill="#fff"/><circle cx="39.5" cy="26" r="7.2" fill="#fff"/><g class="owp-pupils"><circle cx="24.5" cy="26.6" r="3.6" fill="${c.dark}"/><circle cx="39.5" cy="26.6" r="3.6" fill="${c.dark}"/><circle cx="25.6" cy="25.4" r="1.1" fill="#fff"/><circle cx="40.6" cy="25.4" r="1.1" fill="#fff"/></g></g>`
      + `<path d="M30 31.5l2 5 2-5z" fill="${c.accent}"/></g>`,
    ecureuil: c => `<path class="owp-tail" style="transform-origin:42px 54px" d="M40 56c19 3 25-19 14-31-6-6-13 0-8 6 6 6 3 15-7 17z" fill="${c.main}"/><path d="M52 27c4 4 5 9 3 13" stroke="${c.belly}" stroke-width="2" fill="none" opacity=".55" stroke-linecap="round"/>`
      + legs(c, 54) + `<g class="owp-body"><ellipse cx="31" cy="47" rx="13" ry="11.5" fill="${c.main}"/><ellipse cx="31" cy="50" rx="7.5" ry="8" fill="${c.belly}"/></g>`
      + `<g class="owp-head" style="transform-origin:31px 36px"><ellipse cx="21.5" cy="13" rx="3.6" ry="6.2" fill="${c.main}" transform="rotate(-18 21.5 13)"/><ellipse cx="40.5" cy="13" rx="3.6" ry="6.2" fill="${c.main}" transform="rotate(18 40.5 13)"/><path d="M19 7.5l1.6-3.5 1.6 3.4zM43 7.5l-1.6-3.5-1.6 3.4z" fill="${c.dark}" opacity=".7"/><ellipse cx="21.5" cy="14" rx="1.6" ry="3.4" fill="${c.dark}" opacity=".35" transform="rotate(-18 21.5 14)"/><ellipse cx="40.5" cy="14" rx="1.6" ry="3.4" fill="${c.dark}" opacity=".35" transform="rotate(18 40.5 14)"/>`
      + `<ellipse cx="31" cy="26" rx="14" ry="12.5" fill="${c.main}"/><ellipse cx="31" cy="31" rx="8" ry="6" fill="${c.belly}"/>` + eyesDot(24, c).replace(/cx="26"/g, 'cx="25"').replace(/cx="38"/g, 'cx="37"').replace(/cx="26.8"/g, 'cx="25.8"').replace(/cx="38.8"/g, 'cx="37.8"')
      + `<ellipse cx="31" cy="29.5" rx="1.9" ry="1.4" fill="${c.dark}"/><rect x="29.6" y="32.5" width="2.8" height="2.6" rx=".6" fill="#fff"/></g>`,
    berger: c => `<path class="owp-tail" style="transform-origin:45px 49px" d="M45 49q10-3 9-13" stroke="${c.main}" stroke-width="5" fill="none" stroke-linecap="round"/>`
      + legs(c) + `<g class="owp-body"><ellipse cx="32" cy="46" rx="15" ry="11.5" fill="${c.main}"/><ellipse cx="32" cy="49" rx="9" ry="8" fill="${c.belly}"/></g>`
      + `<g class="owp-head" style="transform-origin:32px 36px"><ellipse cx="32" cy="25" rx="15" ry="13.5" fill="${c.main}"/><path d="M32 13c-4 6-4 16 0 22 4-6 4-16 0-22z" fill="${c.belly}"/><ellipse cx="32" cy="31.5" rx="8" ry="5.5" fill="${c.belly}"/>`
      + `<ellipse cx="17.5" cy="25" rx="5" ry="9.5" fill="${c.dark}" transform="rotate(18 17.5 25)"/><ellipse cx="46.5" cy="25" rx="5" ry="9.5" fill="${c.dark}" transform="rotate(-18 46.5 25)"/>` + eyesDot(24, c)
      + `<ellipse cx="32" cy="29.5" rx="2.6" ry="1.9" fill="${c.dark}"/><path d="M30 33.5h4v2.3a2 2 0 01-4 0z" fill="${c.accent}"/></g>`,
    renard: c => `<path class="owp-tail" style="transform-origin:44px 52px" d="M43 53c14 2 20-11 15-23-1 9-6 14-16 15z" fill="${c.main}"/><path d="M58 30c2 4 2 8 0 11-2-3-2-7 0-11z" fill="${c.accent}"/>`
      + legs(c) + `<g class="owp-body"><ellipse cx="32" cy="46" rx="14.5" ry="11.5" fill="${c.main}"/><ellipse cx="32" cy="49" rx="8" ry="8" fill="${c.belly}"/></g>`
      + `<g class="owp-head" style="transform-origin:32px 36px"><path d="M18.5 23L21 5l10 12z" fill="${c.main}"/><path d="M45.5 23L43 5 33 17z" fill="${c.main}"/><path d="M21.5 18l1-8 5 6z" fill="${c.dark}" opacity=".55"/><path d="M42.5 18l-1-8-5 6z" fill="${c.dark}" opacity=".55"/>`
      + `<ellipse cx="32" cy="25" rx="15.5" ry="12.5" fill="${c.main}"/><path d="M16.5 26c5 10 11 12 15.5 12s10.5-2 15.5-12c-5 4-10 5-15.5 5s-10.5-1-15.5-5z" fill="${c.belly}"/>` + eyesDot(24, c)
      + `<ellipse cx="32" cy="31.5" rx="2.2" ry="1.6" fill="${c.dark}"/></g>`,
    chat: c => `<path class="owp-tail" style="transform-origin:44px 56px" d="M44 56c11 0 14-9 9-17" stroke="${c.main}" stroke-width="4.2" fill="none" stroke-linecap="round"/>`
      + legs(c, 53) + `<g class="owp-body"><ellipse cx="32" cy="47" rx="13.5" ry="11" fill="${c.main}"/><ellipse cx="32" cy="50" rx="7.5" ry="7.5" fill="${c.belly}"/></g>`
      + `<g class="owp-head" style="transform-origin:32px 37px"><path d="M19 21l1.5-13 9 8z" fill="${c.main}"/><path d="M45 21l-1.5-13-9 8z" fill="${c.main}"/><path d="M21.5 17l1-6 4.5 4z" fill="${c.accent}"/><path d="M42.5 17l-1-6-4.5 4z" fill="${c.accent}"/>`
      + `<ellipse cx="32" cy="26" rx="14.5" ry="12.5" fill="${c.main}"/><ellipse cx="32" cy="31" rx="6.5" ry="4.5" fill="${c.belly}"/>` + eyesDot(25, c)
      + `<path d="M30.6 29.6h2.8l-1.4 1.8z" fill="${c.accent}"/><path d="M15 29h8M15.5 32.5l7.5-1.5M49 29h-8M48.5 32.5l-7.5-1.5" stroke="${c.dark}" stroke-width=".8" opacity=".45" stroke-linecap="round"/></g>`,
    tortue: c => `<rect class="owp-leg-a" x="15" y="53" width="7" height="9" rx="3.4" fill="${c.belly}"/><rect class="owp-leg-b" x="38" y="53" width="7" height="9" rx="3.4" fill="${c.belly}"/><path class="owp-tail" style="transform-origin:12px 52px" d="M12 52l-6 2 6 2z" fill="${c.belly}"/>`
      + `<g class="owp-head" style="transform-origin:48px 46px"><path d="M44 48c2-6 6-9 10-9 5 0 7 4 7 7s-3 6-8 6c-3 0-6-1-9-4z" fill="${c.belly}"/><g class="owp-eyes" style="transform-origin:55px 44px"><g class="owp-pupils"><ellipse cx="55.5" cy="44" rx="1.9" ry="2.3" fill="${c.dark}"/><circle cx="56.1" cy="43.3" r=".6" fill="#fff"/></g></g><path d="M56 48.5q2 1 4-.5" stroke="${c.dark}" stroke-width=".9" fill="none" stroke-linecap="round"/></g>`
      + `<g class="owp-body"><path d="M9 52c0-15 10-24 22-24s22 9 22 24z" fill="${c.main}"/><path d="M31 30v22M18 36l6 7-2 9M44 36l-6 7 2 9M24 43h14" stroke="${c.accent}" stroke-width="1.6" fill="none" opacity=".75" stroke-linejoin="round"/><rect x="7" y="50" width="48" height="5" rx="2.5" fill="${c.dark}"/></g>`
  };
  let svgSeq = 0;
  function sprite(pet) {
    const c = palette(pet);
    if (pet.kind === 'custom' && pet.photo) {
      const id = 'owpclip' + (++svgSeq);
      return `<svg viewBox="0 0 64 64" aria-hidden="true" focusable="false"><defs><clipPath id="${id}"><circle cx="32" cy="33" r="21"/></clipPath></defs>`
        + legs(c, 50) + `<g class="owp-body"><circle cx="32" cy="33" r="23.5" fill="${c.main}"/><image class="owp-photo-img" x="11" y="12" width="42" height="42" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/></g></svg>`;
    }
    const draw = DRAW[pet.base || pet.id] || DRAW.renard;
    return `<svg viewBox="0 0 64 64" aria-hidden="true" focusable="false">${draw(c)}</svg>`;
  }
  function paint(host, pet) {
    host.innerHTML = sprite(pet);
    const img = host.querySelector('.owp-photo-img');
    if (img && pet.photo) img.setAttribute('href', pet.photo);
  }


  /* ================================================================
     Emote icons (24 px, stroke) shown in a badge above the pet's head
     ================================================================ */
  const EMOTE = {
    bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0012 3z"/>',
    pen: '<path d="M4 20l4-1L19 8l-3-3L5 16z"/><path d="M14 7l3 3"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 3 2.5 14 0 17M12 3.5c-2.5 3-2.5 14 0 17"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 016.5 3H20v15H6.5A2.5 2.5 0 004 20.5z"/><path d="M4 20.5A2.5 2.5 0 016.5 23H20"/>',
    note: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 12h7M9 16h5"/>',
    brain: '<path d="M9 4a3 3 0 00-3 3 3 3 0 00-2 5 3 3 0 002 5 3 3 0 003 3h1V4z"/><path d="M15 4a3 3 0 013 3 3 3 0 012 5 3 3 0 01-2 5 3 3 0 01-3 3h-1V4z"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    hourglass: '<path d="M7 3h10M7 21h10M8 3c0 5 8 5 8 9s-8 4-8 9M16 3c0 5-8 5-8 9s8 4 8 9"/>',
    code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
    brush: '<path d="M14 4l6 6-7 7-6-6z"/><path d="M7 11l-3 3c-1 1-1 4-1 6 2 0 5 0 6-1l3-3"/>',
    users: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3 3-4.8 5.5-4.8S13.9 16 14.5 19"/><path d="M16 5.2a3 3 0 010 5.6M18 14.6c1.4.8 2.3 2.4 2.5 4.4"/>',
    wand: '<path d="M4 20L15 9M14 4v2M19 9h2M18 5l-1.4 1.4M10 5l1 1M19 14l-1-1"/>',
    wrench: '<path d="M14.5 6a4 4 0 00-5 5L4 16.5 7.5 20 13 14.5a4 4 0 005-5L15.5 12 12 8.5z"/>',
    plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0zM12 17v4"/>',
    chat: '<path d="M5 5h14v10H9l-4 4z"/>',
    mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="M4 7l8 6 8-6"/>',
    help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 1.7-2.5 2-2.5 3.5M12 17h.01"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21"/>',
    cloudoff: '<path d="M7 18h10a4 4 0 00.5-8 6 6 0 00-11-1.5A4.5 4.5 0 007 18z"/><path d="M4 4l16 16"/>',
    squeeze: '<path d="M4 9l4 3-4 3M20 9l-4 3 4 3M12 4v16"/>',
    clip: '<path d="M20 11.5l-8 8a5 5 0 01-7-7l8.5-8.5a3.5 3.5 0 015 5L10 17.5a2 2 0 01-3-3L14.5 7"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
    error: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5M12 16.5h.01"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    bell: '<path d="M6 16V11a6 6 0 0112 0v5l2 2H4zM10 20a2 2 0 004 0"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="M4 18l5-5 4 4 3-3 4 4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
    sweat: '<path d="M12 4c3 4 5 7 5 9.5a5 5 0 01-10 0C7 11 9 8 12 4z"/>'
  };
  const emoteSvg = name => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (EMOTE[name] || EMOTE.sparkle) + '</svg>';

  /* What each activity looks like: animation state, badge icon, tone, words key. */
  const ACT = {
    think: { anim: 'think', icon: '', tone: 'info', key: 'a_think', rank: 10 },
    write: { anim: 'write', icon: 'pen', tone: 'info', key: 'a_write', rank: 12 },
    ponder: { anim: 'ponder', icon: 'bulb', tone: 'info', key: 'a_ponder', rank: 14 },
    search: { anim: 'sniff', icon: 'search', tone: 'info', key: 'a_search', rank: 20 },
    web: { anim: 'sniff', icon: 'globe', tone: 'info', key: 'a_web', rank: 20 },
    read: { anim: 'read', icon: 'book', tone: 'info', key: 'a_read', rank: 20 },
    note: { anim: 'write', icon: 'note', tone: 'info', key: 'a_note', rank: 20 },
    memory: { anim: 'ponder', icon: 'brain', tone: 'info', key: 'a_memory', rank: 20 },
    tasks: { anim: 'write', icon: 'list', tone: 'info', key: 'a_tasks', rank: 20 },
    calendar: { anim: 'read', icon: 'calendar', tone: 'info', key: 'a_calendar', rank: 20 },
    automation: { anim: 'type', icon: 'clock', tone: 'info', key: 'a_automation', rank: 20 },
    timer: { anim: 'ponder', icon: 'hourglass', tone: 'info', key: 'a_timer', rank: 20 },
    code: { anim: 'type', icon: 'code', tone: 'info', key: 'a_code', rank: 20 },
    image: { anim: 'paint', icon: 'brush', tone: 'info', key: 'a_image', rank: 20 },
    agents: { anim: 'think', icon: 'users', tone: 'info', key: 'a_agents', rank: 20 },
    skill: { anim: 'ponder', icon: 'wand', tone: 'info', key: 'a_skill', rank: 20 },
    tool: { anim: 'type', icon: 'wrench', tone: 'info', key: 'a_tool', rank: 18 },
    plug: { anim: 'type', icon: 'plug', tone: 'info', key: 'a_plug', rank: 18 },
    chats: { anim: 'read', icon: 'chat', tone: 'info', key: 'a_chats', rank: 20 },
    mail: { anim: 'read', icon: 'mail', tone: 'info', key: 'a_mail', rank: 20 },
    ask: { anim: 'wait', icon: 'help', tone: 'warn', key: 'a_ask', rank: 40, pulse: true },
    listen: { anim: 'listen', icon: 'mic', tone: 'info', key: 'a_listen', rank: 45, pulse: true },
    offline: { anim: 'offline', icon: 'cloudoff', tone: 'err', key: 'r_offline', rank: 60 }
  };
  /* Tool or status name -> activity. First match wins; names are lower-cased. */
  const TOOL_RULES = [
    [/^(ask_user|request_user_input)$/, 'ask'],
    [/(search_web|web_search|fetch_url|browse|websearch|search_the_web|google|bing|duckduckgo|tavily|exa_)/, 'web'],
    [/(knowledge|chat_files|view_file|grep_.*files|query_.*files|list_.*files|retriev|document|rag)/, 'read'],
    [/note/, 'note'],
    [/memor/, 'memory'],
    [/^(create_tasks|update_task)$/, 'tasks'],
    [/automation/, 'automation'],
    [/(calendar|availability|meeting|slot|room|invitation)/, 'calendar'],
    [/^timer$/, 'timer'],
    [/(execute_code|code_interpreter|run_code|python|terminal|shell|jupyter|execute)/, 'code'],
    [/(image|video|draw|paint|picture|photo)/, 'image'],
    [/^(delegate_task|send_message)$|agent/, 'agents'],
    [/skill/, 'skill'],
    [/(create_tool|search_tools|update_tool)/, 'tool'],
    [/(search_chats|view_chat|channel)/, 'chats'],
    [/(email|mail|inbox|draft|reply_to|forward|contact|identit|out_of_office|sender)/, 'mail'],
    [/task/, 'tasks'],
    [/(timestamp|time|date)/, 'timer'],
    [/(search|find|lookup|query)/, 'search']
  ];
  function classify(name, type) {
    const n = String(name || '').toLowerCase();
    for (const [rx, act] of TOOL_RULES) if (rx.test(n)) return act;
    return /external|mcp|openapi|server/.test(String(type || '')) ? 'plug' : 'tool';
  }

  /* ================================================================
     SHA-256 (tiny, synchronous). Used to compare the signed-in person
     with the admin's allow-lists, which are published only as hashes.
     ================================================================ */
  function sha256(text) {
    const K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
    const bytes = new TextEncoder().encode(text);
    const len = bytes.length, words = new Uint32Array(((len + 9 + 63) >> 6) << 4);
    for (let i = 0; i < len; i++) words[i >> 2] |= bytes[i] << (24 - (i % 4) * 8);
    words[len >> 2] |= 0x80 << (24 - (len % 4) * 8);
    words[words.length - 1] = len * 8;
    const H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const W = new Uint32Array(64);
    const r = (x, n) => (x >>> n) | (x << (32 - n));
    for (let o = 0; o < words.length; o += 16) {
      for (let t = 0; t < 64; t++) {
        if (t < 16) W[t] = words[o + t];
        else {
          const a = W[t - 15], b = W[t - 2];
          W[t] = (W[t - 16] + (r(a, 7) ^ r(a, 18) ^ (a >>> 3)) + W[t - 7] + (r(b, 17) ^ r(b, 19) ^ (b >>> 10))) | 0;
        }
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let t = 0; t < 64; t++) {
        const t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[t] + W[t]) | 0;
        const t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }
    return H.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('');
  }
  const idHash = value => sha256('owui-pets:v2:' + String(value || '').trim().toLowerCase());
  function accessOf(me, groups) {
    const A = CONFIG.access || { mode: 'everyone' };
    const mine = new Set([idHash(me.id), idHash(me.email)]);
    for (const g of groups) { mine.add(idHash(g.id)); mine.add(idHash(g.name)); }
    const hit = list => (list || []).some(h => mine.has(h));
    if (hit(A.blocked)) return { pets: false, custom: false };
    const allowed = A.mode !== 'selected' || (A.roles || []).includes(String(me.role || '')) || hit(A.users) || hit(A.groups);
    const custom = allowed && CONFIG.allowCustom && (!(A.custom || []).length || hit(A.custom));
    return { pets: allowed, custom };
  }

  /* ================================================================
     State
     ================================================================ */
  const PHOTO = /^data:image\/(png|webp|jpeg);base64,[A-Za-z0-9+/]+=*$/;
  const clean = (s, n) => String(s == null ? '' : s).replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
  const TALK = ['often', 'normal', 'rare', 'never'];
  const SIZES = { small: 46, medium: 58, large: 70 };
  const DEFAULT_PREFS = () => ({ bubbles: CONFIG.bubbles ? 'normal' : 'never', motion: true, place: CONFIG.place === 'corner' ? 'corner' : 'composer', x: 0.86,
    size: 'medium', walk: true, sleep: true, activity: true, errors: true, notices: true, nickname: '' });
  let access = { pets: false, custom: false };
  function sanitizePet(p) {
    if (!p || typeof p !== 'object') return null;
    if (p.kind === 'builtin' && AVAILABLE.includes(p.id)) return { kind: 'builtin', id: p.id };
    if (p.kind === 'custom' && access.custom) {
      const base = AVAILABLE.includes(p.base) ? p.base : (AVAILABLE[0] || 'renard');
      const name = clean(p.name, 24);
      if (!name) return null;
      const out = { kind: 'custom', base, name, role: clean(p.role, 40) };
      if (typeof p.color === 'string' && HEX.test(p.color)) out.color = p.color.toLowerCase();
      if (CONFIG.allowPhoto && typeof p.photo === 'string' && p.photo.length <= 70000 && PHOTO.test(p.photo)) out.photo = p.photo;
      return out;
    }
    return null;
  }
  function sanitize(raw) {
    const s = raw && typeof raw === 'object' ? raw : {};
    const prefs = DEFAULT_PREFS();
    const rp = s.prefs && typeof s.prefs === 'object' ? s.prefs : {};
    if (rp.bubbles === true) prefs.bubbles = 'normal';
    else if (rp.bubbles === false) prefs.bubbles = 'never';
    else if (TALK.includes(rp.bubbles)) prefs.bubbles = rp.bubbles;
    for (const k of ['motion', 'walk', 'sleep', 'activity', 'errors', 'notices']) if (typeof rp[k] === 'boolean') prefs[k] = rp[k];
    if (rp.place === 'composer' || rp.place === 'corner') prefs.place = rp.place;
    if (typeof rp.x === 'number' && rp.x >= 0 && rp.x <= 1) prefs.x = rp.x;
    if (SIZES[rp.size]) prefs.size = rp.size;
    prefs.nickname = clean(rp.nickname, 24);
    return {
      v: 2,
      updatedAt: Number.isFinite(s.updatedAt) ? s.updatedAt : 0,
      chosen: s.chosen === true,
      pet: sanitizePet(s.pet),
      prefs,
      lastGreet: typeof s.lastGreet === 'string' ? s.lastGreet.slice(0, 10) : ''
    };
  }
  let state = sanitize(null);
  let uid = '', firstName = '', token = '';
  const localKey = () => 'owui-pets:' + uid;
  const getToken = () => { try { return localStorage.getItem('token') || ''; } catch { return ''; } };
  function effectivePet() {
    if (state.chosen) return state.pet;
    if (CONFIG.defaultPet && AVAILABLE.includes(CONFIG.defaultPet)) return { kind: 'builtin', id: CONFIG.defaultPet };
    return null;
  }
  function petInfo(pet) {
    const sp = SPECIES[pet.kind === 'custom' ? pet.base : pet.id] || SPECIES.renard;
    const own = pet.kind === 'custom' ? pet.name : sp.name;
    return {
      name: (pet === effectivePet() || JSON.stringify(pet) === JSON.stringify(effectivePet())) && state.prefs.nickname ? state.prefs.nickname : own,
      role: pet.kind === 'custom' ? (pet.role || L(sp.role)) : L(sp.role),
      kind: L(sp.kind), actions: sp.actions || [], lines: L(sp.lines) || []
    };
  }

  async function api(method, path, body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 9000);
    try {
      const res = await fetch(path, { method, credentials: 'same-origin', signal: ctrl.signal,
        headers: Object.assign({ Accept: 'application/json' }, token ? { Authorization: 'Bearer ' + token } : {}, body ? { 'Content-Type': 'application/json' } : {}),
        body: body ? JSON.stringify(body) : undefined });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json().catch(() => null);
    } finally { clearTimeout(timer); }
  }
  function readLocal() { try { return sanitize(JSON.parse(localStorage.getItem(localKey()) || 'null')); } catch { return sanitize(null); } }
  function writeLocal() { try { localStorage.setItem(localKey(), JSON.stringify(state)); } catch {} }
  let pushTimer = 0, dirty = false;
  function push() {
    clearTimeout(pushTimer);
    pushTimer = setTimeout(async () => {
      try { await api('POST', '/api/v1/users/user/settings/update', { owui_pets: state }); dirty = false; }
      catch { dirty = true; }
    }, 600);
  }
  function save(change) {
    if (change) change(state);
    state.updatedAt = Math.max(now(), state.updatedAt + 1);
    writeLocal();
    push();
    render();
  }
  async function loadState() {
    const local = readLocal();
    let remote = null;
    try { const s = await api('GET', '/api/v1/users/user/settings?raw=true'); remote = s && s.owui_pets ? sanitize(s.owui_pets) : null; } catch {}
    if (remote && remote.updatedAt >= local.updatedAt) { state = remote; writeLocal(); }
    else { state = local; if (local.updatedAt && (!remote || local.updatedAt > remote.updatedAt)) push(); }
  }
  window.addEventListener('storage', e => {
    if (uid && access.pets && e.key === localKey() && e.newValue) {
      try { state = sanitize(JSON.parse(e.newValue)); render(); } catch {}
    }
  });
  window.addEventListener('online', () => { if (dirty) push(); });

  /* ================================================================
     DOM
     ================================================================ */
  const el = (tag, attrs, text) => {
    const e = D.createElement(tag);
    if (attrs) for (const k in attrs) { if (k === 'class') e.className = attrs[k]; else e.setAttribute(k, attrs[k]); }
    if (text != null) e.textContent = text;
    return e;
  };
  const ICON = {
    paw: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><ellipse cx="6.5" cy="10" rx="2.2" ry="2.8"/><ellipse cx="17.5" cy="10" rx="2.2" ry="2.8"/><ellipse cx="10" cy="6" rx="2.1" ry="2.7"/><ellipse cx="14" cy="6" rx="2.1" ry="2.7"/><path d="M12 11.5c-3 0-6 3.6-6 6.2 0 1.6 1.3 2.3 2.7 2.3 1.2 0 2-.6 3.3-.6s2.1.6 3.3.6c1.4 0 2.7-.7 2.7-2.3 0-2.6-3-6.2-6-6.2z"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    swap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7h11l-3-3M17 17H6l3 3"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M5 5h14v10H9l-4 4z"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M20 14A8 8 0 1110 4a6.5 6.5 0 0010 10z"/></svg>',
    bye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 12h4M17 12h4M12 3v4M12 17v4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/></svg>'
  };
  const icon = name => { const s = el('span'); s.innerHTML = ICON[name]; return s.firstChild; };

  let petEl, hitEl, jumpEl, flipEl, fxEl, emoteEl, bubbleEl, menuEl, pawEl, liveEl, modalEl = null, built = false;
  function buildDom() {
    if (built) return;
    built = true;
    const style = el('style', { id: 'owp-style' }); style.textContent = CSS; D.head.appendChild(style);
    petEl = el('div', { id: 'owp-pet', hidden: '' });
    flipEl = el('div', { class: 'owp-flip' }); jumpEl = el('div', { class: 'owp-jump' });
    flipEl.appendChild(jumpEl); petEl.appendChild(flipEl);
    fxEl = el('div', { class: 'owp-fx', 'aria-hidden': 'true' });
    emoteEl = el('div', { class: 'owp-emote', hidden: '', 'aria-hidden': 'true' });
    petEl.appendChild(fxEl); petEl.appendChild(emoteEl);
    hitEl = el('button', { class: 'owp-hit', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false' });
    petEl.appendChild(hitEl);
    bubbleEl = el('div', { id: 'owp-bubble', hidden: '' });
    menuEl = el('div', { id: 'owp-menu', hidden: '', role: 'menu' });
    pawEl = el('button', { id: 'owp-paw', type: 'button', hidden: '', 'aria-label': T('paw'), title: T('paw') });
    pawEl.innerHTML = ICON.paw;
    liveEl = el('div', { class: 'owp-sr', 'aria-live': 'polite' });
    for (const n of [petEl, bubbleEl, menuEl, pawEl, liveEl]) D.body.appendChild(n);
    pawEl.addEventListener('click', () => openPicker());
    bubbleEl.addEventListener('click', () => { const go = bubbleEl._go; hideBubble(); if (go) location.assign(go); });
    wirePointer();
    menuKeys();
  }
  function teardown() {
    if (!built) return;
    for (const n of [petEl, bubbleEl, menuEl, pawEl, liveEl, D.getElementById('owp-style')]) if (n) n.remove();
    if (modalEl) { modalEl.remove(); modalEl = null; }
    built = false;
  }

  /* ================================================================
     Placement
     ================================================================ */
  let place = { x: -200, y: -200, size: 58, perch: null, mode: 'composer' };
  let route = location.pathname;
  const narrow = () => innerWidth < 640;
  function snoozed() { try { return Number(sessionStorage.getItem('owui-pets-snooze') || 0) > now(); } catch { return false; } }
  function composerBox() {
    const box = D.getElementById('message-input-container');
    if (!box) return null;
    const r = box.getBoundingClientRect();
    if (r.width < 120 || r.height < 20 || r.bottom < 0 || r.top > innerHeight) return null;
    return r;
  }
  function perchRange(r, size) { return { min: r.left + 10, max: r.right - size - 10, center: r.left + r.width / 2 }; }
  function xFromRatio(range, ratio, size) {
    let x = range.min + (range.max - range.min) * ratio;
    const mid = x + size / 2, dead = 70;
    if (Math.abs(mid - range.center) < dead) x = mid < range.center ? range.center - dead - size / 2 : range.center + dead - size / 2;
    return Math.max(range.min, Math.min(range.max, x));
  }
  function petSize(usePerch, r) {
    let size = SIZES[state.prefs.size] || 58;
    if (!usePerch) size -= 12;
    if (narrow() || (r && r.width < 520)) size -= 12;
    return Math.max(34, size);
  }
  function layout() {
    if (!built) return;
    const t = getToken();
    if (t !== token) { token = t; boot(); return; }
    if (location.pathname !== route) { route = location.pathname; resetWatch(); pendingChat = ''; adoptOpen = false; }
    const pet = effectivePet();
    const blocked = !uid || !access.pets || /^\/(auth|s\/|error|watch)/.test(location.pathname) || (CONFIG.hideOnMobile && narrow()) || !!D.fullscreenElement;
    const r = composerBox();
    const usePerch = !!r && state.prefs.place === 'composer';
    const size = petSize(usePerch, r);
    const showPet = !blocked && !!pet && !snoozed();
    const showPaw = !blocked && !showPet && CONFIG.paw && !modalEl;
    petEl.hidden = !showPet;
    pawEl.hidden = !showPaw;
    if (!showPet) { hideBubble(); closeMenu(); }
    if (showPaw) {
      const px = r ? Math.min(innerWidth - 40, r.right - 30) : innerWidth - 46;
      const py = r ? r.top - 38 : innerHeight - 50;
      pawEl.style.left = Math.round(px) + 'px'; pawEl.style.top = Math.round(py) + 'px';
      pawEl.classList.toggle('owp-invite', !state.chosen && CONFIG.invite);
    }
    if (!showPet) return;
    petEl.classList.toggle('owp-corner', !usePerch);
    place.size = size;
    petEl.style.setProperty('--owp-size', size + 'px');
    if (usePerch) {
      const range = perchRange(r, size);
      place.perch = range; place.mode = 'composer';
      if (!walking && !dragging) place.x = xFromRatio(range, state.prefs.x, size);
      place.y = r.top - size + Math.round(size * 0.07);
    } else {
      place.perch = null; place.mode = 'corner';
      place.x = innerWidth - size - 18;
      place.y = (r ? r.top - size - 6 : innerHeight - size - 18);
    }
    if (!walking) apply();
    if (!bubbleEl.hidden) placeBubble();
    if (!menuEl.hidden) placeMenu();
  }
  function apply() {
    petEl.style.setProperty('--owp-x', Math.round(place.x) + 'px');
    petEl.style.setProperty('--owp-y', Math.round(place.y) + 'px');
  }

  /* ================================================================
     Activities and reactions
     ================================================================ */
  const acts = new Map();
  let reaction = null;
  let walking = false, dragging = false, typingUntil = 0, lastActivity = now(), asleep = false;
  let lastWalk = now(), nextWalkIn = 15000, lastBubble = 0, draggedAt = 0;
  const calm = () => !state.prefs.motion || matchMedia('(prefers-reduced-motion: reduce)').matches;
  const allowActivity = name => {
    if (name === 'offline') return CONFIG.reactErrors && state.prefs.errors;
    if (name === 'think' || name === 'listen' || name === 'ask') return true;
    return CONFIG.reactActivity && state.prefs.activity;
  };
  function setActivity(key, name, ttl) { if (ACT[name]) acts.set(key, { name, until: now() + (ttl || 120000) }); show(); }
  function clearActivity(key) { if (acts.delete(key)) show(); }
  function clearActivities(prefix) { for (const k of [...acts.keys()]) if (k.startsWith(prefix)) acts.delete(k); show(); }
  function react(anim, iconName, tone, ms, opts) {
    reaction = { anim, icon: iconName || '', tone: tone || 'info', until: now() + (ms || 1800), pulse: !!(opts && opts.pulse) };
    show();
  }
  function current() {
    const t = now();
    for (const [k, a] of acts) if (a.until < t) acts.delete(k);
    if (reaction && reaction.until > t) return reaction;
    let best = null;
    for (const a of acts.values()) {
      if (!allowActivity(a.name)) continue;
      const def = ACT[a.name];
      if (!best || def.rank > best.rank) best = def;
    }
    if (best) return { anim: best.anim, icon: best.icon, tone: best.tone, pulse: !!best.pulse };
    if (walking) return { anim: 'walk' };
    if (t < typingUntil) return { anim: 'watch' };
    if (asleep) return { anim: 'sleep' };
    return { anim: 'idle' };
  }
  let shownKey = '';
  function show() {
    if (!built) return;
    const c = current();
    const key = c.anim + '|' + (c.icon || '') + '|' + (c.tone || '');
    if (key !== shownKey) {
      shownKey = key;
      petEl.setAttribute('data-state', c.anim);
      fxEl.textContent = '';
      const glyphs = c.anim === 'sleep' ? ['z', 'z', 'Z'] : c.anim === 'think' ? ['•', '•', '•'] : (c.anim === 'happy' || c.anim === 'celebrate') ? ['✦', '✧', '✦'] : [];
      for (const g of glyphs) fxEl.appendChild(el('span', null, g));
      if (c.icon) {
        emoteEl.innerHTML = emoteSvg(c.icon);
        emoteEl.setAttribute('data-tone', c.tone || 'info');
        emoteEl.classList.toggle('owp-pulse', !!c.pulse);
        emoteEl.hidden = false;
        emoteEl.style.animation = 'none'; void emoteEl.offsetWidth; emoteEl.style.animation = '';
      } else emoteEl.hidden = true;
    }
    petEl.classList.toggle('owp-calm', calm());
  }

  /* ---- the answer being written in the chat the person is looking at ---- */
  const answer = { chat: '', active: false, start: 0, tools: {}, failed: [], failReason: '', sources: 0, image: false, wrote: false, error: false, tasksDone: false };
  const startedHere = new Set(), titles = new Map();
  let sentAt = 0, stoppedAt = 0, errorAt = 0, everSocket = false, pendingChat = '', adoptOpen = false;
  const routeChat = () => { const m = /^\/c\/([^/?#]+)/.exec(location.pathname); return m ? decodeURIComponent(m[1]) : ''; };
  /* Is this chat the one on screen? A new chat has no address for its first moments,
     so a chat that starts right after a send on a page without one is adopted until
     an address appears; after that only the address counts. */
  function ours(chatId) {
    if (!chatId) return false;
    const cur = routeChat();
    if (cur) { pendingChat = ''; adoptOpen = false; return chatId === cur; }
    if (pendingChat) return chatId === pendingChat;
    if (adoptOpen && now() - sentAt < 15000) { pendingChat = chatId; adoptOpen = false; return true; }
    return false;
  }
  function markSent() { sentAt = now(); adoptOpen = !routeChat(); }
  function startAnswer(chatId) {
    Object.assign(answer, { chat: chatId, active: true, start: now(), tools: {}, failed: [], failReason: '', sources: 0, image: false, wrote: false, error: false, tasksDone: false });
    if (now() - sentAt < 15000) startedHere.add(chatId);
    reaction = null;
    setActivity('answer', 'think', 600000);
  }
  function settle() { clearActivity('answer'); clearActivity('reason'); clearActivities('tool:'); clearActivities('status:'); }
  function endAnswer(chatId) {
    if (!answer.active || (chatId && answer.chat && chatId !== answer.chat)) return;
    answer.active = false;
    settle();
    if (answer.error) return;
    const dur = now() - answer.start;
    const info = petInfo(effectivePet() || { kind: 'builtin', id: 'renard' });
    if (now() - stoppedAt < 5000) { react('shrug', 'stop', 'muted', 1500); return maybeSay(T('r_stopped'), 0.5); }
    if (answer.failed.length && CONFIG.reactErrors && state.prefs.errors) {
      react('puzzled', 'alert', 'warn', 2600);
      const line = answer.failed.length === 1 ? T('r_tool_failed', { tool: answer.failed[0] }) : T('r_tools_failed', { n: answer.failed.length });
      return say(line + (answer.failReason ? ' ' + clean(answer.failReason, 110) : ''), { tone: 'warn', force: true });
    }
    if (answer.image) { react('celebrate', 'image', 'ok', 2400); return maybeSay(T('r_image'), 0.9); }
    if (answer.tasksDone) { react('celebrate', 'list', 'ok', 2200); return maybeSay(T('r_tasks_done'), 0.8); }
    if (!answer.wrote && !Object.keys(answer.tools).length && dur > 400) { react('puzzled', 'help', 'muted', 1800); return maybeSay(T('r_empty'), 0.8); }
    if (answer.sources) react('happy', 'book', 'ok', 1900);
    else react('happy', 'check', 'ok', 1900);
    if (dur > 30000) return maybeSay(T('r_slow'), 0.7);
    maybeSay(answer.sources ? T('r_sources') : pick(info.lines), 0.3);
  }
  function errorText(err) {
    if (!err) return '';
    if (typeof err === 'string') return err;
    return String(err.content || err.message || err.detail || (err.error && (err.error.message || err.error)) || '');
  }
  function explain(text) {
    const t = String(text || '').toLowerCase();
    if (/rate.?limit|429|too many requests|quota/.test(t)) return T('e_rate');
    if (/context.?(length|window)|too long|maximum.*tokens|token limit|max_tokens|reduce the length/.test(t)) return T('e_context');
    if (/401|403|unauthori[sz]ed|forbidden|api.?key|permission|not allowed/.test(t)) return T('e_auth');
    if (/model.*(not found|does not exist|unavailable)|no such model|404/.test(t)) return T('e_model');
    if (/time.?out|timed out/.test(t)) return T('e_timeout');
    if (/network|connect|econn|fetch failed|502|503|504|bad gateway|unreachable/.test(t)) return T('e_network');
    const c = clean(text, 120);
    return c ? T('e_generic', { text: c }) : T('e_unknown');
  }
  function onError(text, fromToast) {
    if (now() - errorAt < 3500) return;
    errorAt = now();
    if (!fromToast) { answer.error = true; answer.active = false; settle(); }
    if (!CONFIG.reactErrors || !state.prefs.errors) return;
    react('worried', 'error', 'err', 3400);
    say(explain(text), { tone: 'err', force: true, ms: 8000 });
  }
  function onResponse(ev) {
    if (!ev || typeof ev !== 'object') return;
    const item = ev.item || {};
    if (ev.type === 'response.output_item.added') {
      if (item.type === 'function_call' && !item.hidden) {
        answer.tools[item.call_id || item.id || item.name] = item.name;
        setActivity('tool:' + (item.call_id || item.id || item.name), classify(item.name, item.tool_type), 180000);
      } else if (item.type === 'reasoning') setActivity('reason', 'ponder', 180000);
      else if (item.type === 'message') answer.wrote = true;
    } else if (ev.type === 'response.output_item.done') {
      if (item.type === 'function_call') {
        clearActivity('tool:' + (item.call_id || item.id || item.name));
        if (item.status === 'failed' && !item.hidden) { answer.failed.push(clean(item.name, 40)); react('puzzled', 'alert', 'warn', 1300); }
      } else if (item.type === 'function_call_output' && item.status === 'failed') {
        try {
          const raw = item.output && item.output[0] && item.output[0].text;
          const parsed = JSON.parse(raw);
          answer.failReason = clean(parsed && parsed.error, 120);
        } catch {}
      } else if (item.type === 'reasoning') clearActivity('reason');
    }
  }
  function onStatus(s) {
    if (!s || typeof s !== 'object' || s.hidden) return;
    const key = 'status:' + clean(s.action || s.description || 'status', 40);
    if (s.done) clearActivity(key);
    else setActivity(key, classify(s.action || s.description, ''), 120000);
    if (s.error && CONFIG.reactErrors && state.prefs.errors) {
      react('puzzled', 'alert', 'warn', 2000);
      maybeSay(T('r_toast_warn', { text: clean(s.description, 100) }), 1);
    }
  }
  function hasImage(files) {
    const list = Array.isArray(files) ? files : (files && Array.isArray(files.files) ? files.files : []);
    return list.some(f => f && (f.type === 'image' || /^image\//.test(String(f.content_type || (f.meta && f.meta.content_type) || ''))));
  }
  function onEvent(p) {
    const chatId = typeof p.chat_id === 'string' ? p.chat_id : '';
    const d = p.data && typeof p.data === 'object' ? p.data : {};
    const type = d.type, inner = d.data;
    if (type === 'chat:title' && chatId && typeof inner === 'string') titles.set(chatId, clean(inner, 80));
    if (!ours(chatId)) {
      if (type === 'chat:active' && inner && inner.active === false && startedHere.has(chatId)) {
        startedHere.delete(chatId);
        if (CONFIG.notices.includes('chats') && state.prefs.notices && /^[0-9a-fA-F-]{36}$/.test(chatId)) {
          react('happy', 'chat', 'ok', 1800);
          say(T('n_background', { name: titles.get(chatId) || '…' }), { force: true, go: '/c/' + chatId });
        }
      }
      return;
    }
    switch (type) {
      case 'chat:active': if (inner && inner.active) startAnswer(chatId); else endAnswer(chatId); break;
      case 'response:completion': onResponse(inner); break;
      case 'chat:completion':
        if (inner && inner.error) onError(errorText(inner.error));
        else if (inner) {
          if (typeof inner.content === 'string' && inner.content.trim()) answer.wrote = true;
          if (Array.isArray(inner.sources)) answer.sources += inner.sources.length;
          if (inner.done && answer.active && !socketOpen) endAnswer(chatId);
        }
        break;
      case 'chat:message:error': onError(errorText(inner && inner.error)); break;
      case 'status': onStatus(inner); break;
      case 'source': case 'citation': answer.sources++; break;
      case 'chat:message:files': case 'files': if (hasImage(inner)) answer.image = true; break;
      case 'chat:message:tasks': {
        const tasks = inner && Array.isArray(inner.tasks) ? inner.tasks : [];
        setActivity('tasks', 'tasks', 2500);
        answer.tasksDone = tasks.length > 0 && tasks.every(t => t && (t.status === 'completed' || t.status === 'cancelled'));
        break;
      }
      case 'request:user_input': case 'request:elicitation': case 'confirmation': case 'input':
        setActivity('ask', 'ask', 240000);
        if (state.prefs.bubbles !== 'never') say(T('a_ask'), { tone: 'warn', force: true });
        break;
      case 'request:interaction:done': clearActivity('ask'); break;
      case 'open_webui:context_compaction': case 'context_compaction': react('squish', 'squeeze', 'muted', 1600); maybeSay(T('a_compact'), 1); break;
    }
  }
  const RELAYED = new Set(['automation.run_started', 'automation.run_completed', 'automation.run_failed', 'calendar.alert', 'retrieval.content.process_failed',
    'note.created', 'memory.created', 'image.generated', 'image.edited', 'video.generated']);
  function onRelay(p) {
    if (!p || typeof p !== 'object' || !RELAYED.has(p.e) || !state.prefs.notices || !effectivePet()) return;
    const name = clean(p.t, 60) || '…';
    const go = typeof p.chat === 'string' && /^[0-9a-fA-F-]{36}$/.test(p.chat) ? '/c/' + p.chat : '';
    const toolBusy = answer.active && Object.keys(answer.tools).length > 0;
    switch (p.e) {
      case 'automation.run_started': react('greet', 'clock', 'info', 1500); say(T('n_auto_start', { name }), { force: true }); break;
      case 'automation.run_completed': react('happy', 'clock', 'ok', 2000); say(T('n_auto_done', { name }), { force: true, go }); break;
      case 'automation.run_failed': react('worried', 'alert', 'err', 2600); say(T('n_auto_fail', { name }), { tone: 'err', force: true, go }); break;
      case 'calendar.alert': {
        const m = Number.isFinite(p.m) ? Math.max(0, Math.round(p.m)) : -1;
        const when = m > 0 ? (lang === 'fr' ? 'dans ' + m + ' min' : 'in ' + m + ' min') : T('n_now');
        react('alarm', 'bell', 'warn', 2600, { pulse: true });
        say(T('n_calendar', { name, when }), { tone: 'warn', force: true, ms: 12000 });
        break;
      }
      case 'retrieval.content.process_failed': react('worried', 'clip', 'err', 2400); say(T('n_file_fail', { name }), { tone: 'err', force: true }); break;
      case 'note.created': react('happy', 'note', 'ok', 1600); if (!toolBusy) maybeSay(T('n_note', { name }), 1); break;
      case 'memory.created': react('happy', 'brain', 'ok', 1600); maybeSay(T('n_memory'), 1); break;
      default: if (!answer.active) { react('celebrate', 'image', 'ok', 2000); maybeSay(T('n_image'), 1); }
    }
  }
  let offlineTimer = 0, wasOffline = false;
  function handleFrame(f) {
    if (!access.pets) return;
    if (f && f.kind === 'open') {
      everSocket = true;
      clearTimeout(offlineTimer);
      if (acts.has('socket')) { clearActivity('socket'); if (wasOffline && state.prefs.errors && CONFIG.reactErrors) { react('happy', 'check', 'ok', 1500); maybeSay(T('r_online'), 1); } }
      wasOffline = false;
      return;
    }
    if (f && f.kind === 'close') {
      clearTimeout(offlineTimer);
      offlineTimer = setTimeout(() => { if (!socketOpen) { wasOffline = true; setActivity('socket', 'offline', 3600000); if (state.prefs.errors && CONFIG.reactErrors) maybeSay(T('r_offline'), 1); } }, 5000);
      return;
    }
    if (typeof f !== 'string') return;
    const m = /^42\d*\[/.exec(f);
    if (!m) return;
    const quick = f.slice(0, 400);
    if (f.indexOf('output_text.delta') > 0 || quick.indexOf('chat:message:delta') > 0) {
      const id = (/"chat_id":"([^"]{1,80})"/.exec(quick) || [])[1];
      if (ours(id)) { answer.wrote = true; if (answer.active && !acts.has('reason')) setActivity('write', 'write', 2500); }
      return;
    }
    if (f.indexOf('reasoning_text.delta') > 0) {
      const id = (/"chat_id":"([^"]{1,80})"/.exec(quick) || [])[1];
      if (ours(id)) setActivity('reason', 'ponder', 8000);
      return;
    }
    let arr;
    try { arr = JSON.parse(f.slice(m[0].length - 1)); } catch { return; }
    if (!Array.isArray(arr)) return;
    if (arr[0] === 'owui-pets') return onRelay(arr[1]);
    if (arr[0] === 'events' && arr[1] && typeof arr[1] === 'object') onEvent(arr[1]);
  }

  /* ---- page signals: toasts, uploads, microphone, network, stop, send ---- */
  let toastHost = null, toastObserver = null;
  function watchToasts() {
    const host = D.querySelector('[data-sonner-toaster]');
    if (!host || host === toastHost) return;
    toastHost = host;
    if (toastObserver) toastObserver.disconnect();
    toastObserver = new MutationObserver(list => {
      for (const m of list) for (const n of m.addedNodes) {
        if (!(n instanceof Element) || !n.matches('[data-sonner-toast]')) continue;
        const type = n.getAttribute('data-type'), text = clean(n.textContent, 160);
        if (type === 'error') onError(text, true);
        else if (type === 'warning' && CONFIG.reactErrors && state.prefs.errors) { react('puzzled', 'alert', 'warn', 1800); maybeSay(T('r_toast_warn', { text }), 1); }
        else if (type === 'success') react('happy', 'check', 'ok', 1200);
      }
    });
    toastObserver.observe(host, { childList: true, subtree: true });
  }
  function onFiles(count) {
    if (!count || !effectivePet() || !access.pets) return;
    react('sniff', 'clip', 'info', 1800);
    maybeSay(count === 1 ? T('r_upload') : T('r_uploads', { n: count }), 0.6);
  }
  D.addEventListener('change', e => {
    const t = e.target;
    if (t && t.type === 'file' && t.files && t.files.length && !(t.closest && t.closest('.owp-modal'))) onFiles(t.files.length);
  }, true);
  D.addEventListener('drop', e => {
    const n = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files.length : 0;
    if (n && !(e.target.closest && e.target.closest('.owp-modal'))) onFiles(n);
  }, true);
  D.addEventListener('paste', e => {
    const n = e.clipboardData && e.clipboardData.files ? e.clipboardData.files.length : 0;
    if (n && e.target.closest && e.target.closest('#chat-input')) onFiles(n);
  }, true);
  window.addEventListener('offline', () => { if (!access.pets) return; wasOffline = true; setActivity('net', 'offline', 3600000); if (state.prefs.errors && CONFIG.reactErrors) maybeSay(T('r_offline'), 1); });
  window.addEventListener('online', () => { if (!access.pets) return; if (acts.has('net')) { clearActivity('net'); if (state.prefs.errors && CONFIG.reactErrors) { react('happy', 'check', 'ok', 1500); maybeSay(T('r_online'), 1); } } });
  const isStop = b => b && (b.getAttribute('aria-label') === 'Stop' || !!b.querySelector('path[d*="h4.874"]'));
  D.addEventListener('click', e => {
    const b = e.target && e.target.closest ? e.target.closest('button') : null;
    if (!b) return;
    if (isStop(b)) stoppedAt = now();
    if (b.id === 'send-message-button') markSent();
  }, true);
  D.addEventListener('keydown', e => {
    const inBox = e.target && e.target.closest && e.target.closest('#chat-input, textarea');
    if (!inBox) return;
    typingUntil = now() + 2400;
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) markSent();
  }, true);
  D.addEventListener('input', e => { if (e.target && e.target.closest && e.target.closest('#chat-input, textarea')) typingUntil = now() + 2400; }, true);

  /* ---- fallback when Open WebUI's socket isn't visible (polling transport, or opened before this script) ---- */
  let lastLen = -1, lastGrow = 0, lastMsgId = '', domAnswering = false, watchRoute = location.pathname;
  function resetWatch() { lastLen = -1; lastMsgId = ''; lastGrow = 0; domAnswering = false; }
  function stopButton(box) { if (!box) return false; for (const b of box.querySelectorAll('button')) if (isStop(b)) return true; return false; }
  function lastMessage() {
    const all = D.querySelectorAll('[id^="message-"]');
    for (let i = all.length - 1; i >= 0; i--) { const id = all[i].id; if (!id.startsWith('message-input') && id !== 'messages-container') return all[i]; }
    return null;
  }
  function watchDom() {
    if (socketOpen && everSocket) return;
    if (location.pathname !== watchRoute) { watchRoute = location.pathname; resetWatch(); return; }
    const msg = lastMessage(), t = now();
    if (msg) {
      const len = msg.textContent.length;
      if (msg.id === lastMsgId && lastLen >= 0 && len !== lastLen) lastGrow = t;
      if (msg.id !== lastMsgId && lastMsgId && lastLen >= 0) lastGrow = t;
      lastMsgId = msg.id; lastLen = len;
    } else { lastMsgId = ''; lastLen = -1; }
    const busy = stopButton(D.getElementById('message-input-container')) || (lastGrow > 0 && t - lastGrow < 1600);
    if (busy && !domAnswering) { domAnswering = true; startAnswer(routeChat() || 'dom'); answer.wrote = true; }
    else if (!busy && domAnswering) { domAnswering = false; endAnswer(''); }
  }

  /* ================================================================
     Waking, sleeping, walking
     ================================================================ */
  const pick = arr => arr && arr.length ? arr[Math.floor(Math.random() * arr.length)] : '';
  function wake() {
    lastActivity = now();
    if (asleep) { asleep = false; if (built && !petEl.hidden) react('greet', '', '', 900); }
  }
  for (const type of ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart']) window.addEventListener(type, wake, { passive: true, capture: true });
  function tick() {
    if (!built || D.hidden || !uid || !access.pets) return;
    watchDom();
    if (micTracks > 0) setActivity('mic', 'listen', 3000); else if (acts.has('mic')) clearActivity('mic');
    if (!asleep && state.prefs.sleep && now() - lastActivity > CONFIG.sleepAfter * 60000 && !answer.active && !acts.size) asleep = true;
    if (asleep && !state.prefs.sleep) asleep = false;
    show(); maybeWalk(); flushPending(); watchToasts();
  }
  function maybeWalk() {
    if (petEl.hidden || walking || dragging || !menuEl.hidden || modalEl || calm() || narrow() || !state.prefs.walk) return;
    if (current().anim !== 'idle') return;
    if (!place.perch || now() - lastWalk < nextWalkIn || now() - draggedAt < 60000) return;
    lastWalk = now(); nextWalkIn = 14000 + Math.random() * 18000;
    if (Math.random() < 0.45) return;
    const range = place.perch;
    let target = state.prefs.x;
    for (let i = 0; i < 4 && Math.abs(target - state.prefs.x) < 0.12; i++) target = Math.random() < 0.5 ? 0.04 + Math.random() * 0.3 : 0.66 + Math.random() * 0.3;
    const toX = xFromRatio(range, target, place.size);
    const dist = Math.abs(toX - place.x);
    if (dist < 20) return;
    const ms = Math.round(dist / 42 * 1000);
    petEl.classList.toggle('owp-left', toX < place.x);
    walking = true; show();
    petEl.style.setProperty('--owp-walk', ms + 'ms');
    petEl.classList.add('owp-walking');
    place.x = toX; apply();
    setTimeout(() => { petEl.classList.remove('owp-walking'); walking = false; state.prefs.x = target; writeLocal(); show(); }, ms + 40);
  }

  /* ================================================================
     Speech bubbles
     ================================================================ */
  let bubbleTimer = 0;
  const talkFactor = () => ({ often: 0.35, normal: 1, rare: 3 })[state.prefs.bubbles] || 1;
  function say(text, opts) {
    const o = opts || {};
    if (!text || !built || petEl.hidden || modalEl) return;
    if (state.prefs.bubbles === 'never' && !o.always) return;
    if (!CONFIG.bubbles && !o.force && !o.always) return;
    bubbleEl.textContent = text;
    bubbleEl._go = '';
    if (o.go && /^\/c\/[0-9a-fA-F-]{36}$/.test(o.go)) {
      bubbleEl._go = o.go;
      bubbleEl.appendChild(el('span', { class: 'owp-bact' }, T('n_open') + ' →'));
    }
    bubbleEl.classList.toggle('owp-action', !!bubbleEl._go);
    bubbleEl.setAttribute('data-tone', o.tone || 'info');
    bubbleEl.setAttribute('role', o.tone === 'err' ? 'alert' : 'status');
    bubbleEl.hidden = false;
    announce(text);
    lastBubble = now();
    placeBubble();
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(hideBubble, o.ms || 6500);
  }
  function maybeSay(text, chance) {
    if (!CONFIG.bubbles || state.prefs.bubbles === 'never' || !menuEl || !menuEl.hidden || modalEl) return;
    if (now() - lastBubble < CONFIG.bubbleInterval * 60000 * talkFactor()) return;
    if (Math.random() > chance) return;
    say(text);
  }
  function hideBubble() { if (bubbleEl && !bubbleEl.hidden) { bubbleEl.hidden = true; clearTimeout(bubbleTimer); } }
  function placeBubble() {
    const w = bubbleEl.offsetWidth, h = bubbleEl.offsetHeight;
    let x = place.x + place.size / 2 - 26;
    x = Math.max(8, Math.min(innerWidth - w - 8, x));
    const y = Math.max(8, place.y - h - 10);
    bubbleEl.style.left = Math.round(x) + 'px'; bubbleEl.style.top = Math.round(y) + 'px';
    bubbleEl.style.setProperty('--owp-tail-x', Math.max(10, Math.min(w - 22, place.x + place.size / 2 - x - 5)) + 'px');
  }
  function greet() {
    const pet = effectivePet();
    if (!pet || !CONFIG.bubbles || state.prefs.bubbles === 'never') return;
    const today = new Date().toLocaleDateString('sv-SE');
    if (state.lastGreet === today) return;
    state.lastGreet = today; writeLocal(); push();
    const hr = new Date().getHours();
    const key = hr < 12 ? 'hello_morning' : hr < 18 ? 'hello_afternoon' : 'hello_evening';
    const who = firstName ? ' ' + firstName : '';
    setTimeout(() => { react('greet', '', '', 900); say(T(key, { name: who }) + ' ' + pick(petInfo(pet).lines)); }, 900);
  }
  function announce(text) { if (!liveEl) return; liveEl.textContent = ''; setTimeout(() => { liveEl.textContent = text; }, 50); }

  /* ================================================================
     Pointer and keyboard on the pet
     ================================================================ */
  function wirePointer() {
    let startX = 0, startPetX = 0, moved = false, pid = null;
    hitEl.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      pid = e.pointerId; startX = e.clientX; startPetX = place.x; moved = false;
      try { hitEl.setPointerCapture(pid); } catch {}
    });
    hitEl.addEventListener('pointermove', e => {
      if (pid !== e.pointerId) return;
      const dx = e.clientX - startX;
      if (!moved && Math.abs(dx) < 5) return;
      if (!place.perch) return;
      moved = true; dragging = true; petEl.classList.add('owp-dragging'); hideBubble(); closeMenu();
      const range = place.perch;
      place.x = Math.max(range.min, Math.min(range.max, startPetX + dx));
      petEl.classList.toggle('owp-left', dx < 0);
      apply();
    });
    const end = e => {
      if (pid !== e.pointerId) return;
      pid = null;
      petEl.classList.remove('owp-dragging');
      if (moved && place.perch) {
        const range = place.perch;
        const ratio = range.max > range.min ? (place.x - range.min) / (range.max - range.min) : 0.5;
        draggedAt = now(); dragging = false;
        save(s => { s.prefs.x = Math.max(0, Math.min(1, ratio)); });
      } else if (e.type === 'pointerup') { dragging = false; toggleMenu(); }
      dragging = false;
    };
    hitEl.addEventListener('pointerup', end);
    hitEl.addEventListener('pointercancel', end);
    hitEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleMenu(true); }
      else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && place.perch) {
        e.preventDefault();
        save(s => { s.prefs.x = Math.max(0, Math.min(1, s.prefs.x + (e.key === 'ArrowLeft' ? -0.08 : 0.08))); });
        petEl.classList.toggle('owp-left', e.key === 'ArrowLeft');
        draggedAt = now();
      }
    });
  }

  /* ================================================================
     Menu
     ================================================================ */
  function toggleMenu(fromKeyboard) { if (menuEl.hidden) openMenu(fromKeyboard); else closeMenu(); }
  function menuItem(label, iconName, onClick) {
    const b = el('button', { class: 'owp-mi', type: 'button', role: 'menuitem' });
    if (iconName) b.appendChild(icon(iconName));
    b.appendChild(el('span', null, label));
    b.addEventListener('click', e => { e.stopPropagation(); onClick(); });
    return b;
  }
  function openMenu(fromKeyboard) {
    const pet = effectivePet();
    if (!pet) return;
    hideBubble();
    const info = petInfo(pet);
    menuEl.textContent = '';
    menuEl.setAttribute('aria-label', T('menu_label', { name: info.name }));
    const head = el('div', { class: 'owp-mhead' });
    const av = el('div', { class: 'owp-av' }); paint(av, pet);
    const txt = el('div', { style: 'min-width:0' });
    txt.appendChild(el('div', { class: 'owp-mname' }, info.name));
    txt.appendChild(el('div', { class: 'owp-mrole' }, info.role + (pet.kind === 'custom' ? '' : ' · ' + info.kind)));
    head.appendChild(av); head.appendChild(txt); menuEl.appendChild(head);
    if (CONFIG.quickActions && info.actions.length) {
      menuEl.appendChild(el('div', { class: 'owp-mlabel' }, T('shortcuts')));
      for (const a of info.actions) {
        const text = L(a);
        if (text) menuEl.appendChild(menuItem(text.trim(), 'chat', () => { closeMenu(); insertPrompt(text); }));
      }
      menuEl.appendChild(el('div', { class: 'owp-sep' }));
    }
    menuEl.appendChild(menuItem(T('settings'), 'gear', () => { closeMenu(); openSettings(); }));
    menuEl.appendChild(menuItem(T('change'), 'swap', () => { closeMenu(); openPicker(); }));
    menuEl.appendChild(menuItem(T('nap'), 'moon', () => { closeMenu(); say(T('snoozed'), { ms: 1400, always: true }); setTimeout(() => { try { sessionStorage.setItem('owui-pets-snooze', String(now() + 3600000)); } catch {} layout(); }, 1200); }));
    menuEl.appendChild(menuItem(T('remove'), 'bye', () => { closeMenu(); save(s => { s.chosen = true; s.pet = null; }); announce(T('removed')); }));
    menuEl.hidden = false;
    hitEl.setAttribute('aria-expanded', 'true');
    placeMenu();
    if (fromKeyboard) { const first = menuEl.querySelector('.owp-mi'); if (first) first.focus(); }
  }
  function closeMenu() {
    if (!menuEl || menuEl.hidden) return;
    const hadFocus = menuEl.contains(D.activeElement);
    menuEl.hidden = true;
    hitEl.setAttribute('aria-expanded', 'false');
    if (hadFocus) { try { hitEl.focus({ preventScroll: true }); } catch {} }
  }
  function placeMenu() {
    const w = menuEl.offsetWidth, h = menuEl.offsetHeight;
    let x = place.x + place.size / 2 - w / 2;
    x = Math.max(8, Math.min(innerWidth - w - 8, x));
    let y = place.y - h - 8;
    if (y < 8) y = Math.min(innerHeight - h - 8, place.y + place.size + 8);
    menuEl.style.left = Math.round(x) + 'px'; menuEl.style.top = Math.round(Math.max(8, y)) + 'px';
  }
  function menuKeys() {
    D.addEventListener('keydown', e => {
      if (!menuEl || menuEl.hidden) return;
      const items = [...menuEl.querySelectorAll('.owp-mi')];
      const i = items.indexOf(D.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); closeMenu(); hitEl.focus(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === 'Tab' && menuEl.contains(D.activeElement)) closeMenu();
    }, true);
    D.addEventListener('pointerdown', e => {
      if (menuEl && !menuEl.hidden && !menuEl.contains(e.target) && !petEl.contains(e.target)) closeMenu();
    }, true);
  }

  /* ================================================================
     Prompts into the message box (never sent)
     ================================================================ */
  function insertPrompt(text) {
    const ed = D.getElementById('chat-input');
    if (!ed) {
      try { sessionStorage.setItem('owui-pets-pending', text); } catch {}
      location.assign('/');
      return;
    }
    ed.focus();
    if (ed.tagName === 'TEXTAREA') {
      const v = ed.value;
      ed.value = (v.trim() ? v.replace(/\s*$/, ' ') : '') + text;
      ed.dispatchEvent(new Event('input', { bubbles: true }));
      ed.setSelectionRange(ed.value.length, ed.value.length);
      return;
    }
    const sel = getSelection();
    const range = D.createRange();
    range.selectNodeContents(ed); range.collapse(false);
    sel.removeAllRanges(); sel.addRange(range);
    const prefix = ed.textContent.trim() ? ' ' : '';
    if (!D.execCommand('insertText', false, prefix + text)) {
      ed.appendChild(D.createTextNode(prefix + text));
      ed.dispatchEvent(new InputEvent('input', { bubbles: true }));
    }
  }
  function flushPending() {
    let text = '';
    try { text = sessionStorage.getItem('owui-pets-pending') || ''; } catch {}
    if (!text || !D.getElementById('chat-input')) return;
    try { sessionStorage.removeItem('owui-pets-pending'); } catch {}
    setTimeout(() => insertPrompt(text), 300);
  }

  /* ================================================================
     Dialogs (picker and settings share one modal frame)
     ================================================================ */
  function openModal(label) {
    if (modalEl) return null;
    closeMenu(); hideBubble();
    const returnTo = D.activeElement;
    modalEl = el('div', { class: 'owp-modal', role: 'presentation' });
    const dlg = el('div', { class: 'owp-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': label });
    modalEl.appendChild(dlg);
    D.body.appendChild(modalEl);
    layout();
    const close = () => {
      if (!modalEl) return;
      modalEl.remove(); modalEl = null;
      D.removeEventListener('keydown', onKey, true);
      layout();
      try { (returnTo && returnTo.isConnected ? returnTo : (petEl.hidden ? pawEl : hitEl)).focus({ preventScroll: true }); } catch {}
    };
    const onKey = e => {
      if (!modalEl) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
      if (e.key === 'Tab') {
        const f = [...dlg.querySelectorAll('button,input,[tabindex="0"]')].filter(n => !n.disabled && n.offsetParent !== null);
        if (!f.length) return;
        const i = f.indexOf(D.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && (i === f.length - 1 || i < 0)) { e.preventDefault(); f[0].focus(); }
      }
    };
    D.addEventListener('keydown', onKey, true);
    modalEl.addEventListener('pointerdown', e => { if (e.target === modalEl) close(); });
    const header = (title, sub) => {
      const h = el('div', { class: 'owp-dhead' });
      const t = el('div', { style: 'min-width:0' });
      t.appendChild(el('h2', { id: 'owp-dtitle' }, title)); t.appendChild(el('p', null, sub));
      const x = el('button', { class: 'owp-x', type: 'button', 'aria-label': T('close') }); x.innerHTML = ICON.x;
      x.addEventListener('click', close);
      h.appendChild(t); h.appendChild(x);
      dlg.setAttribute('aria-labelledby', 'owp-dtitle');
      return h;
    };
    return { dlg, close, header };
  }

  function openSettings() {
    const pet = effectivePet();
    if (!pet) return;
    const m = openModal(T('settings_label'));
    if (!m) return;
    const { dlg, close, header } = m;
    const draw = () => {
      dlg.textContent = '';
      dlg.appendChild(header(T('s_title', { name: petInfo(pet).name }), T('s_sub')));
      const wrap = el('div', { class: 'owp-settings' });
      const group = title => { const g = el('div', { class: 'owp-sgroup' }); g.appendChild(el('h3', null, title)); wrap.appendChild(g); return g; };
      const row = (g, label, hint, control) => {
        const r = el('div', { class: 'owp-srow' });
        const t = el('span'); t.appendChild(el('b', null, label)); if (hint) t.appendChild(el('small', null, hint));
        r.appendChild(t); r.appendChild(control); g.appendChild(r); return r;
      };
      const toggle = (key, label) => {
        const b = el('button', { class: 'owp-switch', type: 'button', role: 'switch', 'aria-checked': String(!!state.prefs[key]), 'aria-label': label, 'data-pref': key });
        b.addEventListener('click', () => { save(s => { s.prefs[key] = !s.prefs[key]; }); b.setAttribute('aria-checked', String(!!state.prefs[key])); });
        return b;
      };
      const seg = (key, options, label) => {
        const s = el('div', { class: 'owp-seg', role: 'radiogroup', 'aria-label': label, 'data-pref': key });
        for (const [value, text] of options) {
          const b = el('button', { type: 'button', role: 'radio', 'aria-checked': String(state.prefs[key] === value), 'data-value': value }, text);
          b.addEventListener('click', () => { save(st => { st.prefs[key] = value; }); for (const n of s.children) n.setAttribute('aria-checked', String(n.dataset.value === value)); });
          s.appendChild(b);
        }
        return s;
      };
      const look = group(T('s_look'));
      const nick = el('input', { type: 'text', maxlength: '24', autocomplete: 'off', placeholder: pet.kind === 'custom' ? pet.name : (SPECIES[pet.id] || SPECIES.renard).name, 'aria-label': T('s_nick'), style: 'width:min(220px,100%);height:32px;padding:0 10px;border-radius:9px;border:1px solid var(--owp-line);background:var(--owp-surface);color:var(--owp-text);font:inherit;font-size:13.5px' });
      nick.value = state.prefs.nickname || '';
      nick.addEventListener('change', () => save(s => { s.prefs.nickname = clean(nick.value, 24); }));
      row(look, T('s_nick'), T('s_nick_hint'), nick);
      row(look, T('s_size'), '', seg('size', [['small', T('s_small')], ['medium', T('s_medium')], ['large', T('s_large')]], T('s_size')));
      row(look, T('s_place'), '', seg('place', [['composer', T('s_composer')], ['corner', T('s_corner')]], T('s_place')));
      const beh = group(T('s_behaviour'));
      if (CONFIG.bubbles) row(beh, T('s_talk'), '', seg('bubbles', [['often', T('s_often')], ['normal', T('s_normal')], ['rare', T('s_rare')], ['never', T('s_never')]], T('s_talk')));
      row(beh, T('s_walk'), T('s_walk_hint'), toggle('walk', T('s_walk')));
      row(beh, T('s_sleep'), T('s_sleep_hint'), toggle('sleep', T('s_sleep')));
      row(beh, T('s_motion'), T('s_motion_hint'), toggle('motion', T('s_motion')));
      const rx = group(T('s_reactions'));
      if (CONFIG.reactActivity) row(rx, T('s_activity'), T('s_activity_hint'), toggle('activity', T('s_activity')));
      if (CONFIG.reactErrors) row(rx, T('s_errors'), T('s_errors_hint'), toggle('errors', T('s_errors')));
      if (CONFIG.notices.length) row(rx, T('s_notices'), T('s_notices_hint'), toggle('notices', T('s_notices')));
      if (!rx.querySelector('.owp-srow')) rx.remove();
      dlg.appendChild(wrap);
      const foot = el('div', { class: 'owp-dfoot' });
      const reset = el('button', { class: 'owp-link', type: 'button' }, T('s_reset'));
      reset.addEventListener('click', () => { save(s => { const x = s.prefs.x; s.prefs = DEFAULT_PREFS(); s.prefs.x = x; }); draw(); });
      const done = el('button', { class: 'owp-btn owp-primary', type: 'button' }, T('s_done'));
      done.addEventListener('click', close);
      foot.appendChild(reset); foot.appendChild(el('div', { class: 'owp-spacer' })); foot.appendChild(done);
      dlg.appendChild(foot);
      const first = dlg.querySelector('input,button.owp-switch,.owp-seg button');
      if (first) first.focus({ preventScroll: true });
    };
    draw();
  }

  /* ================================================================
     Picker
     ================================================================ */
  function openPicker() {
    const current = effectivePet();
    let selected = current && current.kind === 'builtin' ? current.id : (current ? 'custom' : '');
    let draft = current && current.kind === 'custom' ? Object.assign({}, current) : { kind: 'custom', base: AVAILABLE[0] || 'renard', name: '', role: '', color: '' };
    const m = openModal(T('picker_label'));
    if (!m) return;
    const { dlg, close, header } = m;
    const adopt = pet => {
      const isNew = JSON.stringify(pet) !== JSON.stringify(effectivePet());
      try { sessionStorage.removeItem('owui-pets-snooze'); } catch {}
      save(s => { s.chosen = true; s.pet = pet; s.lastGreet = new Date().toLocaleDateString('sv-SE'); });
      close();
      if (pet && isNew) { setTimeout(() => { react('greet', 'sparkle', 'ok', 1200); say(T('adopted', { name: petInfo(pet).name })); }, 250); }
    };
    function gridView() {
      dlg.textContent = '';
      dlg.appendChild(header(T('pick_title'), T('pick_sub')));
      const grid = el('div', { class: 'owp-grid', role: 'radiogroup', 'aria-label': T('pick_title') });
      const cards = [];
      const adoptBtn = el('button', { class: 'owp-btn owp-primary', type: 'button' });
      const refresh = () => {
        for (const c of cards) c.setAttribute('aria-checked', String(c.dataset.id === selected));
        const pet = selected === 'custom' ? sanitizePet(draft) : (selected ? { kind: 'builtin', id: selected } : null);
        adoptBtn.disabled = !pet;
        adoptBtn.textContent = pet ? T('adopt_named', { name: petInfo(pet).name }) : T('adopt');
      };
      for (const id of AVAILABLE) {
        const sp = SPECIES[id];
        const card = el('button', { class: 'owp-card', type: 'button', role: 'radio', 'data-id': id });
        const cv = el('div', { class: 'owp-cv' }); paint(cv, { kind: 'builtin', id });
        card.appendChild(cv);
        card.appendChild(el('div', { class: 'owp-cname' }, sp.name));
        card.appendChild(el('div', { class: 'owp-crole' }, L(sp.role)));
        card.appendChild(el('div', { class: 'owp-cblurb' }, L(sp.blurb)));
        card.addEventListener('click', () => { selected = id; refresh(); });
        card.addEventListener('dblclick', () => adopt({ kind: 'builtin', id }));
        cards.push(card); grid.appendChild(card);
      }
      if (access.custom && AVAILABLE.length) {
        const card = el('button', { class: 'owp-card', type: 'button', role: 'radio', 'data-id': 'custom' });
        const mine = sanitizePet(draft);
        if (mine && selected === 'custom') {
          const cv = el('div', { class: 'owp-cv' }); paint(cv, mine); card.appendChild(cv);
          card.appendChild(el('div', { class: 'owp-cname' }, mine.name));
          card.appendChild(el('div', { class: 'owp-crole' }, mine.role || T('custom_role')));
          card.appendChild(el('div', { class: 'owp-cblurb' }, T('add_own_sub')));
        } else {
          const plus = el('div', { class: 'owp-plus' }); plus.innerHTML = ICON.plus; card.appendChild(plus);
          card.appendChild(el('div', { class: 'owp-cname' }, T('add_own')));
          card.appendChild(el('div', { class: 'owp-cblurb' }, T('add_own_sub')));
        }
        card.addEventListener('click', () => formView());
        cards.push(card); grid.appendChild(card);
      }
      dlg.appendChild(grid);
      const foot = el('div', { class: 'owp-dfoot' });
      const none = el('button', { class: 'owp-link', type: 'button' }, T('none'));
      none.addEventListener('click', () => adopt(null));
      const cancel = el('button', { class: 'owp-btn', type: 'button' }, T('cancel'));
      cancel.addEventListener('click', close);
      adoptBtn.addEventListener('click', () => {
        const pet = selected === 'custom' ? sanitizePet(draft) : (selected ? { kind: 'builtin', id: selected } : null);
        if (pet) adopt(pet);
      });
      foot.appendChild(none); foot.appendChild(el('div', { class: 'owp-spacer' })); foot.appendChild(cancel); foot.appendChild(adoptBtn);
      dlg.appendChild(foot);
      refresh();
      const focus = cards.find(c => c.dataset.id === selected) || cards[0];
      if (focus) focus.focus({ preventScroll: true });
    }

    function formView() {
      dlg.textContent = '';
      dlg.appendChild(header(T('create_title'), T('create_sub')));
      const form = el('div', { class: 'owp-form' });
      const prev = el('div', { class: 'owp-preview' });
      const cv = el('div', { class: 'owp-cv' });
      const pname = el('div', { class: 'owp-cname' });
      prev.appendChild(cv); prev.appendChild(pname);
      const fields = el('div', { class: 'owp-fields' });
      const redraw = () => {
        const p = Object.assign({}, draft, { name: clean(draft.name, 24) || T('f_name_ph') });
        paint(cv, sanitizePet(p) || { kind: 'builtin', id: draft.base });
        pname.textContent = clean(draft.name, 24) || T('f_name_ph');
      };
      const field = (labelText, input, hint) => {
        const f = el('div', { class: 'owp-field' });
        const id = 'owp-f-' + Math.random().toString(36).slice(2, 8);
        if (input.tagName === 'INPUT') { input.id = id; f.appendChild(el('label', { for: id }, labelText)); }
        else f.appendChild(el('div', { class: 'owp-fl' }, labelText));
        f.appendChild(input);
        if (hint) f.appendChild(el('div', { class: 'owp-hint' }, hint));
        fields.appendChild(f);
        return f;
      };
      const nameIn = el('input', { type: 'text', maxlength: '24', autocomplete: 'off', placeholder: T('f_name_ph') });
      nameIn.value = draft.name || '';
      const nameErr = el('div', { class: 'owp-err', hidden: '' }, T('err_name'));
      nameIn.addEventListener('input', () => { draft.name = nameIn.value; nameErr.hidden = true; redraw(); });
      field(T('f_name'), nameIn).appendChild(nameErr);

      const radio = (cls, items, isOn, onPick, label) => {
        const row = el('div', { class: 'owp-row', role: 'radiogroup', 'aria-label': label });
        const nodes = items.map(it => {
          const b = el('button', { class: cls, type: 'button', role: 'radio' }, it.text || null);
          if (it.color) { b.style.background = it.color; b.setAttribute('aria-label', it.label); b.title = it.label; }
          b.addEventListener('click', () => { onPick(it); for (const n of nodes) n.setAttribute('aria-checked', String(isOn(n._it))); redraw(); });
          b._it = it; b.setAttribute('aria-checked', String(isOn(it)));
          row.appendChild(b); return b;
        });
        return row;
      };
      field(T('f_animal'), radio('owp-chip', AVAILABLE.map(id => ({ id, text: SPECIES[id].name + ' · ' + L(SPECIES[id].kind) })), it => it.id === draft.base, it => { draft.base = it.id; }, T('f_animal')));
      const swItems = [{ color: '', label: T('original'), text: '' }].concat(SWATCHES.map(c => ({ color: c, label: c })));
      const swRow = radio('owp-sw', swItems, it => (it.color || '') === (draft.color || ''), it => { draft.color = it.color; }, T('f_color'));
      swRow.firstChild.style.background = 'conic-gradient(#e2702a,#4f9a5c,#4a7bd0,#d65d8a,#e2702a)';
      field(T('f_color'), swRow);
      const roleIn = el('input', { type: 'text', maxlength: '40', autocomplete: 'off', placeholder: L(SPECIES[draft.base] ? SPECIES[draft.base].role : SPECIES.renard.role) });
      roleIn.value = draft.role || '';
      roleIn.addEventListener('input', () => { draft.role = roleIn.value; });
      field(T('f_role'), roleIn, T('f_role_hint'));
      if (CONFIG.allowPhoto) {
        const box = el('div', { class: 'owp-row' });
        const file = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', class: 'owp-sr', tabindex: '-1' });
        const pickB = el('button', { class: 'owp-btn', type: 'button' }, T('f_photo_btn'));
        const rm = el('button', { class: 'owp-btn', type: 'button' }, T('f_photo_remove'));
        const err = el('div', { class: 'owp-err', hidden: '' });
        rm.hidden = !draft.photo;
        pickB.addEventListener('click', () => file.click());
        rm.addEventListener('click', () => { delete draft.photo; rm.hidden = true; redraw(); });
        file.addEventListener('change', async () => {
          const f = file.files && file.files[0]; file.value = '';
          if (!f) return;
          err.hidden = true;
          try { draft.photo = await toPhoto(f); rm.hidden = false; redraw(); }
          catch (x) { err.textContent = T(x && x.message === 'type' ? 'err_photo_type' : x && x.message === 'size' ? 'err_photo_size' : 'err_photo_read'); err.hidden = false; }
        });
        box.appendChild(pickB); box.appendChild(rm); box.appendChild(file);
        const f = field(T('f_photo'), box, T('f_photo_hint'));
        f.appendChild(err);
      }
      form.appendChild(prev); form.appendChild(fields);
      dlg.appendChild(form);
      const foot = el('div', { class: 'owp-dfoot' });
      const back = el('button', { class: 'owp-btn', type: 'button' }, T('back'));
      back.addEventListener('click', gridView);
      const ok = el('button', { class: 'owp-btn owp-primary', type: 'button' }, T('adopt'));
      ok.addEventListener('click', () => {
        const pet = sanitizePet(draft);
        if (!pet) { nameErr.hidden = false; nameIn.focus(); return; }
        selected = 'custom'; adopt(pet);
      });
      foot.appendChild(back); foot.appendChild(el('div', { class: 'owp-spacer' })); foot.appendChild(ok);
      dlg.appendChild(foot);
      redraw();
      nameIn.focus({ preventScroll: true });
    }
    gridView();
  }

  async function toPhoto(file) {
    if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type || '')) throw new Error('type');
    if (file.size > 5 * 1024 * 1024) throw new Error('size');
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, ko) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ko(new Error('read')); i.src = url; });
      if (!img.naturalWidth || !img.naturalHeight) throw new Error('read');
      const c = D.createElement('canvas'); c.width = c.height = 128;
      const g = c.getContext('2d');
      const s = Math.min(img.naturalWidth, img.naturalHeight);
      g.drawImage(img, (img.naturalWidth - s) / 2, (img.naturalHeight - s) / 2, s, s, 0, 0, 128, 128);
      let out = c.toDataURL('image/webp', 0.82);
      if (!out.startsWith('data:image/webp')) out = c.toDataURL('image/png');
      if (out.length > 60000) out = c.toDataURL('image/jpeg', 0.72);
      if (out.length > 60000 || !PHOTO.test(out)) throw new Error('size');
      return out;
    } finally { URL.revokeObjectURL(url); }
  }


  /* ================================================================
     Render, loops, boot
     ================================================================ */
  let lastPetKey = '';
  function render() {
    if (!built) return;
    const pet = effectivePet();
    const key = JSON.stringify(pet) + '|' + state.prefs.nickname;
    if (pet && key !== lastPetKey) {
      paint(jumpEl, pet);
      hitEl.setAttribute('aria-label', T('pet_label', { name: petInfo(pet).name }));
      hitEl.title = petInfo(pet).name;
    }
    lastPetKey = key;
    layout();
    show();
  }
  let loopTimer = 0, layoutTimer = 0;
  function startLoops() {
    clearInterval(loopTimer); clearInterval(layoutTimer);
    loopTimer = setInterval(tick, 700);
    layoutTimer = setInterval(() => { if (!D.hidden && built) layout(); else if (!built && getToken() !== token) { token = getToken(); boot(); } }, 400);
  }
  D.addEventListener('visibilitychange', () => { if (!built) return; petEl.classList.toggle('owp-paused', D.hidden); if (!D.hidden) { wake(); layout(); } });
  window.addEventListener('resize', () => { if (built) layout(); }, { passive: true });
  window.addEventListener('keydown', e => {
    if (e.altKey && e.shiftKey && (e.key === 'P' || e.key === 'p' || e.code === 'KeyP') && uid && access.pets) { e.preventDefault(); openPicker(); }
  }, true);
  const needsGroups = () => {
    const A = CONFIG.access || {};
    return A.mode === 'selected' || (A.blocked || []).length > 0 || (A.custom || []).length > 0;
  };
  let booting = 0;
  async function boot() {
    const run = ++booting;
    uid = ''; firstName = ''; access = { pets: false, custom: false };
    frameSink = null;
    if (built) { petEl.hidden = true; pawEl.hidden = true; hideBubble(); closeMenu(); }
    if (!token) return;
    let me;
    try { me = await api('GET', '/api/v1/auths/'); } catch { return; }
    if (run !== booting || !me || !me.id) return;
    let groups = [];
    if (needsGroups()) {
      try { const g = await api('GET', '/api/v1/users/groups?include_inherited=true'); groups = Array.isArray(g) ? g.filter(x => x && x.id) : []; } catch {}
      if (run !== booting) return;
    }
    access = accessOf(me, groups);
    if (!access.pets) { teardown(); frameQueue.length = 0; return; }
    uid = String(me.id);
    firstName = clean(String(me.name || '').split(' ')[0], 24);
    buildDom();
    await loadState();
    if (run !== booting) return;
    lastPetKey = '';
    render();
    frameSink = handleFrame;
    while (frameQueue.length) handleFrame(frameQueue.shift());
    greet();
  }
  function start() {
    if (!D.body) { D.addEventListener('DOMContentLoaded', start, { once: true }); return; }
    token = getToken();
    startLoops();
    boot();
  }
  window.__owuiChatPetsDebug = {
    get state() { return JSON.parse(JSON.stringify(state)); },
    get mode() { return built ? petEl.getAttribute('data-state') : null; },
    get emote() { return built && !emoteEl.hidden ? emoteEl.getAttribute('data-tone') + ':' + [...emoteEl.querySelectorAll('path,circle,rect')].length : ''; },
    get access() { return Object.assign({}, access); },
    get activities() { return [...acts.values()].map(a => a.name); },
    get socket() { return { open: socketOpen, ever: everSocket }; },
    openPicker: () => openPicker(), openSettings: () => openSettings(),
    frame: f => handleFrame(f), react: (a, i, t) => react(a, i, t, 3000)
  };
  start();
})();
"""


# ===========================================================================
# Chat Pets: event function
# ===========================================================================

PET_IDS = ("hibou", "ecureuil", "berger", "renard", "chat", "tortue")
NOTICE_KINDS = ("automations", "calendar", "files", "notes", "memories", "images", "chats")
# Server events forwarded to the person they concern, and the notice kind each belongs to.
RELAYED_EVENTS = {
    "automation.run_started": "automations",
    "automation.run_completed": "automations",
    "automation.run_failed": "automations",
    "calendar.alert": "calendar",
    "retrieval.content.process_failed": "files",
    "note.created": "notes",
    "memory.created": "memories",
    "image.generated": "images",
    "image.edited": "images",
    "video.generated": "images",
}
UUID_RE = _re.compile(r"^[0-9a-fA-F-]{36}$")


def _clean_text(value: Any, limit: int) -> str:
    text = _re.sub(r"[\x00-\x1f\x7f\u2028\u2029]", " ", str(value or ""))
    return _re.sub(r"\s+", " ", text).strip()[:limit]


def _items(raw: Any) -> list:
    """Comma- or line-separated values, trimmed and lower-cased, without duplicates."""
    out = []
    for part in _re.split(r"[,\n;]+", str(raw or "")):
        part = part.strip().lower()
        if part and part not in out:
            out.append(part)
    return out[:500]


def _hash_id(value: str) -> str:
    """The browser compares the signed-in person against these hashes, so the
    admin's lists of people and groups are never published in clear."""
    return _hashlib.sha256(("owui-pets:v2:" + str(value).strip().lower()).encode("utf-8")).hexdigest()


def _parse_actions(raw: str) -> dict:
    """Admin shortcut overrides: {"hibou": [{"fr": "...", "en": "..."}]}. Invalid parts are dropped."""
    if not str(raw or "").strip():
        return {}
    try:
        data = _json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for pet_id, items in data.items():
        if pet_id not in PET_IDS or not isinstance(items, list):
            continue
        clean = []
        for item in items[:8]:
            if isinstance(item, str):
                item = {"fr": item, "en": item}
            if not isinstance(item, dict):
                continue
            fr = _clean_text(item.get("fr") or item.get("en"), 160)
            en = _clean_text(item.get("en") or item.get("fr"), 160)
            if fr or en:
                clean.append({"fr": fr or en, "en": en or fr})
        if clean:
            out[pet_id] = clean
    return out


class Event:
    class Valves(BaseModel):
        available_pets: str = Field(
            default=",".join(PET_IDS),
            description="Pets people can choose, separated by commas: hibou (owl, mail), ecureuil (squirrel, notes), berger (sheepdog, tasks), renard (fox, research), chat (cat, calendar), tortue (turtle, study).",
        )
        allow_custom_pets: bool = Field(default=True, description="Let people make their own pet: a name, an animal, a colour and a short job description.")
        allow_photo_pets: bool = Field(default=True, description="Let people use a photo for their own pet. It is shrunk to 128 px in the browser and kept in their account settings (about 10 to 40 KB).")
        default_pet: str = Field(default="", description="Pet shown to people who haven't chosen yet, for example 'hibou'. Empty: nobody has a pet until they pick one.")
        invite_new_users: bool = Field(default=True, description="Show a wiggling paw button that invites people without a pet to choose one.")
        show_paw_when_off: bool = Field(default=True, description="Keep a small paw button on screen when someone has no pet or hid it, so they can bring one back. Alt+Shift+P always opens the picker.")
        speech_bubbles: bool = Field(default=True, description="Let pets say short things: a greeting once a day, and now and then a word when an answer is ready. Each person can also turn them off.")
        bubble_interval_minutes: int = Field(default=20, ge=1, le=1440, description="Least time between two spontaneous speech bubbles.")
        sleep_after_minutes: int = Field(default=3, ge=1, le=120, description="Minutes without mouse or keyboard activity before the pet falls asleep.")
        quick_actions: bool = Field(default=True, description="Show each pet's shortcuts in its menu. A shortcut only fills the message box; nothing is sent until the person presses Enter.")
        quick_actions_json: str = Field(default="", description="Optional shortcuts per pet, replacing the built-in ones, as JSON: {\"hibou\": [{\"fr\": \"Résume mes emails\", \"en\": \"Summarise my emails\"}]}. Invalid entries are ignored.")
        default_position: Literal["composer", "corner"] = Field(default="composer", description="Where pets sit by default: on top of the message box, or in the bottom-right corner. Each person can change it.")
        hide_on_mobile: bool = Field(default=False, description="Hide pets on screens narrower than 640 px.")
        language: Literal["auto", "en", "fr"] = Field(default="auto", description="Language of the pet's words. 'auto' follows each person's Open WebUI language.")
        access_mode: Literal["everyone", "selected"] = Field(default="everyone", description="Who gets a pet: 'everyone', or only the roles, groups and people listed below ('selected').")
        allowed_roles: str = Field(default="user,admin", description="With 'selected': roles that get pets (user, admin, pending), separated by commas.")
        allowed_groups: str = Field(default="", description="With 'selected': groups that get pets, by name or ID, separated by commas. Members of sub-groups count.")
        allowed_users: str = Field(default="", description="With 'selected': people who get pets, by email or user ID, separated by commas.")
        blocked_users: str = Field(default="", description="People or groups who never get a pet, by email, user ID, group name or group ID. Wins over everything else.")
        custom_pets_for: str = Field(default="", description="Who may make their own pet, by email, user ID, group name or group ID. Empty: everyone who has a pet (if allow_custom_pets is on).")
        react_to_activity: bool = Field(default=True, description="Pets show what the AI is doing: web search, documents, notes, memory, tasks, calendar, automations, code, images, skills, sub-agents and other tools.")
        react_to_errors: bool = Field(default=True, description="Pets react to errors, failed tools, warnings and lost connections, and explain them in plain words.")
        notices: str = Field(default=",".join(NOTICE_KINDS), description="What pets announce on their own: automations, calendar (reminders), files (unreadable uploads), notes, memories, images, chats (an answer ready in another chat). Empty: nothing.")

    LOADER_BLOCK_START = "// owui-chat-pets:start"
    LOADER_BLOCK_END = "// owui-chat-pets:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment_key = None
        self._fragment = ""
        self._access_cache: dict = {}
        reload_bootstrap()

    def _config(self) -> dict:
        v = self.valves
        pets = [p.strip().lower() for p in str(v.available_pets or "").split(",")]
        pets = [p for p in PET_IDS if p in pets] or list(PET_IDS)
        default = str(v.default_pet or "").strip().lower()
        return {
            "pets": pets,
            "allowCustom": bool(v.allow_custom_pets),
            "allowPhoto": bool(v.allow_custom_pets and v.allow_photo_pets),
            "defaultPet": default if default in pets else "",
            "invite": bool(v.invite_new_users),
            "paw": bool(v.show_paw_when_off),
            "bubbles": bool(v.speech_bubbles),
            "bubbleInterval": int(v.bubble_interval_minutes),
            "sleepAfter": int(v.sleep_after_minutes),
            "quickActions": bool(v.quick_actions),
            "actions": _parse_actions(v.quick_actions_json),
            "place": v.default_position,
            "hideOnMobile": bool(v.hide_on_mobile),
            "language": v.language,
            "reactActivity": bool(v.react_to_activity),
            "reactErrors": bool(v.react_to_errors),
            "notices": [k for k in NOTICE_KINDS if k in _items(v.notices)],
            "access": {
                "mode": v.access_mode,
                "roles": [r for r in ("user", "admin", "pending") if r in _items(v.allowed_roles)],
                "groups": sorted(_hash_id(x) for x in _items(v.allowed_groups)),
                "users": sorted(_hash_id(x) for x in _items(v.allowed_users)),
                "blocked": sorted(_hash_id(x) for x in _items(v.blocked_users)),
                "custom": sorted(_hash_id(x) for x in _items(v.custom_pets_for)),
            },
        }

    def _loader_fragment(self) -> str:
        # Runs for every page load: rebuild only when a valve changed.
        key = _json.dumps(self._config(), sort_keys=True, ensure_ascii=True)
        if key != self._fragment_key:
            # JSON inside a script: "</" is escaped so no value can end the script early.
            config = key.replace("</", "<\\/")
            css = _json.dumps(PETS_CSS).replace("</", "<\\/")
            script = LOADER_SCRIPT.replace("__CSS__", css).replace("__CONFIG__", config)
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
        try:
            await self._relay(event or {}, __event_name__ or (event or {}).get("event", ""))
        except Exception as exc:  # a pet must never break Open WebUI's event dispatch
            logging.getLogger("chat_pets").debug("relay skipped: %s", type(exc).__name__)

    # ---------------------------------------------------------------- relay

    @staticmethod
    def _user_of(event: dict) -> str:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        uid = data.get("user_id") or (actor.get("id") if actor.get("type") == "user" else "")
        return str(uid or "")

    @staticmethod
    def _payload(name: str, event: dict) -> dict:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        title = data.get("name") or data.get("title") or data.get("filename") or data.get("file_name") or ""
        out = {"e": name, "t": _clean_text(title, 60)}
        chat = str(data.get("chat_id") or "")
        if UUID_RE.match(chat):
            out["chat"] = chat
        if name == "calendar.alert" and isinstance(data.get("minutes_until"), (int, float)):
            out["m"] = max(0, min(int(data["minutes_until"]), 1440))
        return out

    async def _allowed(self, user_id: str) -> bool:
        """The server's own access check: notices only reach people who may have a pet."""
        cached = self._access_cache.get(user_id)
        if cached and cached[0] > _time.monotonic():
            return cached[1]
        from open_webui.models.groups import Groups
        from open_webui.models.users import Users

        user = await Users.get_user_by_id(user_id)
        allowed = False
        if user is not None:
            groups = await Groups.get_groups_by_member_id(user_id, include_inherited=True)
            mine = {str(user_id).lower(), str(getattr(user, "email", "") or "").lower()}
            for group in groups or []:
                mine.add(str(getattr(group, "id", "")).lower())
                mine.add(str(getattr(group, "name", "")).lower())
            v = self.valves
            if mine & set(_items(v.blocked_users)):
                allowed = False
            elif v.access_mode == "everyone":
                allowed = True
            else:
                allowed = (
                    str(getattr(user, "role", "")).lower() in _items(v.allowed_roles)
                    or bool(mine & set(_items(v.allowed_users)))
                    or bool(mine & set(_items(v.allowed_groups)))
                )
        if len(self._access_cache) > 5000:
            self._access_cache.clear()
        self._access_cache[user_id] = (_time.monotonic() + 60, allowed)
        return allowed

    async def _relay(self, event: dict, name: str) -> None:
        kind = RELAYED_EVENTS.get(name)
        if not kind or kind not in _items(self.valves.notices):
            return
        user_id = self._user_of(event)
        if not user_id or not await self._allowed(user_id):
            return
        from open_webui.socket.main import sio

        await sio.emit("owui-pets", self._payload(name, event), room=f"user:{user_id}")


def register(app: Any) -> None:
    Event.instance._publish(app)
