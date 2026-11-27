"""
title: Chat Pets
description: A companion that lives on Open WebUI's message box, the way ChatGPT's pets do. Each person picks one of six animals or makes their own (name, colour, photo). It watches you type, thinks while the model answers, cheers when it is done, naps when you are away, and offers shortcuts for its job: mail, notes, tasks, research, calendar or revision.
author: Muhammad Sohail
version: 1.0.0
license: MIT
required_open_webui_version: 0.13.0
"""

# The two protocol blocks below are Classic298's Shared Assets Protocol and its
# live reload add-on (BSD 3-Clause), copied byte for byte from Chat Inspector.

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
@media (prefers-reduced-motion:reduce){.owp-card .owp-cv *,.owp-preview .owp-cv *,.owp-card:hover .owp-cv,#owp-paw.owp-invite,#owp-bubble,#owp-menu,.owp-modal{animation:none!important}}
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

  /* ---------------- language ---------------- */
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
      shortcuts: 'Raccourcis', change: 'Changer de compagnon…', bubbles_on: 'Bulles : activées', bubbles_off: 'Bulles : désactivées',
      motion_on: 'Animations : activées', motion_off: 'Animations : réduites', place_composer: 'Place : sur la barre de message', place_corner: 'Place : dans le coin',
      nap: 'Le cacher pendant 1 h', remove: 'Retirer mon compagnon', paw: 'Choisir un compagnon', pet_label: '{name}, votre compagnon. Ouvrir son menu.',
      menu_label: 'Menu de {name}', picker_label: 'Choix du compagnon', invite: 'Psst… un compagnon pour vous accompagner ?',
      hello_morning: 'Bonjour{name} !', hello_afternoon: 'Bon après-midi{name} !', hello_evening: 'Bonsoir{name} !',
      adopted: 'Coucou, je suis {name} ! Cliquez sur moi pour mes raccourcis.', woke: 'Hmm ? Je suis là !', removed: 'Votre compagnon est parti se reposer. La patte en bas à droite le fait revenir.',
      custom_role: 'Compagnon', on_pet: 'Vous', snoozed: 'À tout à l’heure !'
    },
    en: {
      pick_title: 'Choose your pet', pick_sub: 'Pick a companion or add one of your own.',
      adopt: 'Choose', adopt_named: 'Choose {name}', cancel: 'Cancel', close: 'Close', none: 'No pet', back: 'Back',
      add_own: 'Add your own', add_own_sub: 'Your name, your colour, your photo.',
      create_title: 'Make your pet', create_sub: 'Give it a name, a look and a job.',
      f_name: 'Name', f_name_ph: 'Biscuit', f_animal: 'Animal', f_color: 'Colour', f_role: 'What it helps you with', f_role_hint: 'Optional. Its shortcuts come from the animal you pick.',
      f_photo: 'Photo', f_photo_btn: 'Choose a photo', f_photo_remove: 'Remove photo', f_photo_hint: 'PNG, JPEG, WebP or GIF, up to 5 MB. It stays in your account.',
      original: 'Original colour', err_name: 'Give it a name (up to 24 characters).', err_photo_type: 'This image format isn’t supported.', err_photo_size: 'This image is too large.', err_photo_read: 'This image couldn’t be read.',
      shortcuts: 'Shortcuts', change: 'Change pet…', bubbles_on: 'Speech bubbles: on', bubbles_off: 'Speech bubbles: off',
      motion_on: 'Animations: on', motion_off: 'Animations: reduced', place_composer: 'Place: on the message box', place_corner: 'Place: in the corner',
      nap: 'Hide for 1 hour', remove: 'Remove my pet', paw: 'Choose a pet', pet_label: '{name}, your pet. Open its menu.',
      menu_label: '{name}’s menu', picker_label: 'Pet picker', invite: 'Psst… want a companion to keep you company?',
      hello_morning: 'Good morning{name}!', hello_afternoon: 'Good afternoon{name}!', hello_evening: 'Good evening{name}!',
      adopted: 'Hi, I’m {name}! Click me for shortcuts.', woke: 'Hm? I’m here!', removed: 'Your pet went to rest. The paw at the bottom right brings it back.',
      custom_role: 'Companion', on_pet: 'You', snoozed: 'See you soon!'
    }
  };
  const T = (key, vars) => {
    let s = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
    if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  };
  const L = obj => (obj && (obj[lang] || obj.en)) || '';

  /* ---------------- catalogue ---------------- */
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

  /* ---------------- state ---------------- */
  const PHOTO = /^data:image\/(png|webp|jpeg);base64,[A-Za-z0-9+/]+=*$/;
  const clean = (s, n) => String(s == null ? '' : s).replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
  const DEFAULT_PREFS = () => ({ bubbles: true, motion: true, place: CONFIG.place === 'corner' ? 'corner' : 'composer', x: 0.86 });
  function sanitizePet(p) {
    if (!p || typeof p !== 'object') return null;
    if (p.kind === 'builtin' && AVAILABLE.includes(p.id)) return { kind: 'builtin', id: p.id };
    if (p.kind === 'custom' && CONFIG.allowCustom) {
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
    const prefs = Object.assign(DEFAULT_PREFS(), {});
    const rp = s.prefs && typeof s.prefs === 'object' ? s.prefs : {};
    if (typeof rp.bubbles === 'boolean') prefs.bubbles = rp.bubbles;
    if (typeof rp.motion === 'boolean') prefs.motion = rp.motion;
    if (rp.place === 'composer' || rp.place === 'corner') prefs.place = rp.place;
    if (typeof rp.x === 'number' && rp.x >= 0 && rp.x <= 1) prefs.x = rp.x;
    return {
      v: 1,
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
    return {
      name: pet.kind === 'custom' ? pet.name : sp.name,
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
    if (uid && e.key === localKey() && e.newValue) {
      try { state = sanitize(JSON.parse(e.newValue)); render(); } catch {}
    }
  });
  window.addEventListener('online', () => { if (dirty) push(); });

  /* ---------------- DOM ---------------- */
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
    spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>',
    swap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7h11l-3-3M17 17H6l3 3"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M5 5h14v10H9l-4 4z"/></svg>',
    move: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 18h16M7 14l5-8 5 8"/></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M20 14A8 8 0 1110 4a6.5 6.5 0 0010 10z"/></svg>',
    wave: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 12c3-4 6 4 9 0s6 4 9 0"/></svg>',
    bye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/></svg>'
  };
  const icon = name => { const s = el('span'); s.innerHTML = ICON[name]; return s.firstChild; };

  let petEl, hitEl, jumpEl, flipEl, fxEl, bubbleEl, menuEl, pawEl, liveEl, modalEl = null;
  function buildDom() {
    const style = el('style', { id: 'owp-style' }); style.textContent = CSS; D.head.appendChild(style);
    petEl = el('div', { id: 'owp-pet', hidden: '' });
    flipEl = el('div', { class: 'owp-flip' }); jumpEl = el('div', { class: 'owp-jump' });
    flipEl.appendChild(jumpEl); petEl.appendChild(flipEl);
    fxEl = el('div', { class: 'owp-fx', 'aria-hidden': 'true' });
    petEl.appendChild(fxEl);
    hitEl = el('button', { class: 'owp-hit', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false' });
    petEl.appendChild(hitEl);
    bubbleEl = el('div', { id: 'owp-bubble', hidden: '', role: 'status' });
    menuEl = el('div', { id: 'owp-menu', hidden: '', role: 'menu' });
    pawEl = el('button', { id: 'owp-paw', type: 'button', hidden: '', 'aria-label': T('paw'), title: T('paw') });
    pawEl.innerHTML = ICON.paw;
    liveEl = el('div', { class: 'owp-sr', 'aria-live': 'polite' });
    for (const n of [petEl, bubbleEl, menuEl, pawEl, liveEl]) D.body.appendChild(n);
    pawEl.addEventListener('click', () => openPicker());
    bubbleEl.addEventListener('click', hideBubble);
    wirePointer();
  }

  /* ---------------- placement ---------------- */
  let place = { x: -200, y: -200, size: 58, perch: null, mode: 'composer' };
  let route = location.pathname, hiddenReason = '';
  const narrow = () => innerWidth < 640;
  function snoozed() { try { return Number(sessionStorage.getItem('owui-pets-snooze') || 0) > now(); } catch { return false; } }
  function composerBox() {
    const box = D.getElementById('message-input-container');
    if (!box) return null;
    const r = box.getBoundingClientRect();
    if (r.width < 120 || r.height < 20 || r.bottom < 0 || r.top > innerHeight) return null;
    return r;
  }
  function perchRange(r, size) {
    return { min: r.left + 10, max: r.right - size - 10, center: r.left + r.width / 2 };
  }
  function xFromRatio(range, ratio, size) {
    let x = range.min + (range.max - range.min) * ratio;
    const mid = x + size / 2, dead = 70;
    if (Math.abs(mid - range.center) < dead) x = mid < range.center ? range.center - dead - size / 2 : range.center + dead - size / 2;
    return Math.max(range.min, Math.min(range.max, x));
  }
  function layout() {
    const t = getToken();
    if (t !== token) { token = t; boot(); return; }
    if (location.pathname !== route) { route = location.pathname; resetWatch(); }
    const pet = effectivePet();
    const blocked = !uid || /^\/(auth|s\/|error|watch)/.test(location.pathname) || (CONFIG.hideOnMobile && narrow()) || !!D.fullscreenElement;
    const r = composerBox();
    const usePerch = !!r && state.prefs.place === 'composer';
    const size = usePerch ? (r.width < 520 || narrow() ? 46 : 58) : 46;
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
      place.y = r.top - size + 4;
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

  /* ---------------- behaviour ---------------- */
  let base = 'idle', transient = '', transientUntil = 0, walking = false, dragging = false;
  let typingUntil = 0, lastActivity = now(), asleep = false, generating = false;
  let lastLen = -1, lastGrow = 0, lastMsgId = '', lastWalk = now(), nextWalkIn = 15000, lastBubble = 0, draggedAt = 0;
  const calm = () => !state.prefs.motion || matchMedia('(prefers-reduced-motion: reduce)').matches;
  function resetWatch() { lastLen = -1; lastMsgId = ''; lastGrow = 0; generating = false; }
  function setTransient(name, ms) { transient = name; transientUntil = now() + ms; show(); }
  function show() {
    const s = transient && now() < transientUntil ? transient : (walking ? 'walk' : base);
    if (petEl.getAttribute('data-state') !== s) {
      petEl.setAttribute('data-state', s);
      fxEl.textContent = '';
      const glyphs = s === 'sleep' ? ['z', 'z', 'Z'] : s === 'think' ? ['•', '•', '•'] : s === 'happy' ? ['✦', '✧', '✦'] : [];
      for (const g of glyphs) fxEl.appendChild(el('span', null, g));
    }
    petEl.classList.toggle('owp-calm', calm());
  }
  function stopButton(box) {
    if (!box) return false;
    for (const b of box.querySelectorAll('button')) {
      if (b.getAttribute('aria-label') === 'Stop' || b.querySelector('path[d*="h4.874"]')) return true;
    }
    return false;
  }
  function lastMessage() {
    const all = D.querySelectorAll('[id^="message-"]');
    for (let i = all.length - 1; i >= 0; i--) {
      const id = all[i].id;
      if (id !== 'message-input-container' && !id.startsWith('message-input') && id !== 'messages-container') return all[i];
    }
    return null;
  }
  let watchRoute = location.pathname;
  function watchChat() {
    if (location.pathname !== watchRoute) { watchRoute = location.pathname; resetWatch(); return; }
    const box = D.getElementById('message-input-container');
    const msg = lastMessage();
    const t = now();
    if (msg) {
      const len = msg.textContent.length;
      if (msg.id === lastMsgId && lastLen >= 0 && len !== lastLen) lastGrow = t;
      if (msg.id !== lastMsgId && lastMsgId && lastLen >= 0) lastGrow = t;
      lastMsgId = msg.id; lastLen = len;
    } else { lastMsgId = ''; lastLen = -1; }
    const was = generating;
    generating = stopButton(box) || (t - lastGrow < 1600 && lastGrow > 0);
    if (was && !generating) {
      setTransient('happy', 1900);
      maybeSay(pick(petInfo(effectivePet() || { kind: 'builtin', id: 'renard' }).lines), 0.3);
    }
    if (!asleep && t - lastActivity > CONFIG.sleepAfter * 60000 && !generating) asleep = true;
    base = generating ? 'think' : t < typingUntil ? 'watch' : asleep ? 'sleep' : 'idle';
  }
  const pick = arr => arr && arr.length ? arr[Math.floor(Math.random() * arr.length)] : '';
  function wake() {
    lastActivity = now();
    if (asleep) { asleep = false; base = 'idle'; if (!petEl.hidden) { setTransient('greet', 900); } }
  }
  for (const type of ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart']) {
    window.addEventListener(type, wake, { passive: true, capture: true });
  }
  D.addEventListener('keydown', e => { if (e.target && e.target.closest && e.target.closest('#chat-input, textarea')) typingUntil = now() + 2400; }, true);
  D.addEventListener('input', e => { if (e.target && e.target.closest && e.target.closest('#chat-input, textarea')) typingUntil = now() + 2400; }, true);

  function maybeWalk() {
    if (petEl.hidden || walking || dragging || !menuEl.hidden || modalEl || calm() || narrow()) return;
    if (base !== 'idle' || (transient && now() < transientUntil)) return;
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
    setTimeout(() => {
      petEl.classList.remove('owp-walking');
      walking = false; state.prefs.x = target; writeLocal(); show();
    }, ms + 40);
  }

  /* ---------------- bubbles ---------------- */
  let bubbleTimer = 0;
  function say(text, ms = 6500) {
    if (!text || petEl.hidden) return;
    bubbleEl.textContent = text;
    bubbleEl.hidden = false;
    liveEl.textContent = text;
    lastBubble = now();
    placeBubble();
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(hideBubble, ms);
  }
  function maybeSay(text, chance) {
    if (!CONFIG.bubbles || !state.prefs.bubbles || !menuEl.hidden || modalEl) return;
    if (now() - lastBubble < CONFIG.bubbleInterval * 60000) return;
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
    if (!pet || !CONFIG.bubbles || !state.prefs.bubbles) return;
    const today = new Date().toLocaleDateString('sv-SE');
    if (state.lastGreet === today) return;
    state.lastGreet = today; writeLocal(); push();
    const hr = new Date().getHours();
    const key = hr < 12 ? 'hello_morning' : hr < 18 ? 'hello_afternoon' : 'hello_evening';
    const who = firstName ? ' ' + firstName : '';
    setTimeout(() => { setTransient('greet', 900); say(T(key, { name: who }) + ' ' + pick(petInfo(pet).lines)); }, 900);
  }

  /* ---------------- pointer, keyboard ---------------- */
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

  /* ---------------- menu ---------------- */
  let menuReturn = null;
  function toggleMenu(fromKeyboard) { if (menuEl.hidden) openMenu(fromKeyboard); else closeMenu(); }
  function menuItem(label, iconName, onClick, extra) {
    const b = el('button', { class: 'owp-mi', type: 'button', role: 'menuitem' });
    if (iconName) b.appendChild(icon(iconName));
    b.appendChild(el('span', null, label));
    if (extra) b.appendChild(el('span', { class: 'owp-k' }, extra));
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
    menuEl.appendChild(menuItem(T('change'), 'swap', () => { closeMenu(); openPicker(); }));
    if (CONFIG.bubbles) menuEl.appendChild(menuItem(state.prefs.bubbles ? T('bubbles_on') : T('bubbles_off'), 'wave', () => { save(s => { s.prefs.bubbles = !s.prefs.bubbles; }); openMenu(true); }));
    menuEl.appendChild(menuItem(state.prefs.motion ? T('motion_on') : T('motion_off'), 'spark', () => { save(s => { s.prefs.motion = !s.prefs.motion; }); openMenu(true); }));
    menuEl.appendChild(menuItem(state.prefs.place === 'composer' ? T('place_composer') : T('place_corner'), 'move', () => { save(s => { s.prefs.place = s.prefs.place === 'composer' ? 'corner' : 'composer'; }); openMenu(true); }));
    menuEl.appendChild(menuItem(T('nap'), 'moon', () => { closeMenu(); say(T('snoozed'), 1400); setTimeout(() => { try { sessionStorage.setItem('owui-pets-snooze', String(now() + 3600000)); } catch {} layout(); }, 1200); }));
    menuEl.appendChild(menuItem(T('remove'), 'bye', () => { closeMenu(); save(s => { s.chosen = true; s.pet = null; }); announce(T('removed')); }));
    if (menuEl.hidden) menuReturn = D.activeElement;
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
    menuReturn = null;
  }
  function placeMenu() {
    const w = menuEl.offsetWidth, h = menuEl.offsetHeight;
    let x = place.x + place.size / 2 - w / 2;
    x = Math.max(8, Math.min(innerWidth - w - 8, x));
    let y = place.y - h - 8;
    if (y < 8) y = Math.min(innerHeight - h - 8, place.y + place.size + 8);
    menuEl.style.left = Math.round(x) + 'px'; menuEl.style.top = Math.round(Math.max(8, y)) + 'px';
  }
  menuKeys();
  function menuKeys() {
    D.addEventListener('keydown', e => {
      if (!menuEl || menuEl.hidden) return;
      const items = [...menuEl.querySelectorAll('.owp-mi')];
      const i = items.indexOf(D.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); closeMenu(); hitEl.focus(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === 'Tab' && menuEl.contains(D.activeElement)) { closeMenu(); }
    }, true);
    D.addEventListener('pointerdown', e => {
      if (menuEl && !menuEl.hidden && !menuEl.contains(e.target) && !petEl.contains(e.target)) closeMenu();
    }, true);
  }
  function announce(text) { liveEl.textContent = ''; setTimeout(() => { liveEl.textContent = text; }, 50); }

  /* ---------------- prompts into the message box ---------------- */
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

  /* ---------------- picker ---------------- */
  function openPicker() {
    if (modalEl) return;
    closeMenu(); hideBubble();
    const returnTo = D.activeElement;
    const current = effectivePet();
    let selected = current && current.kind === 'builtin' ? current.id : (current ? 'custom' : '');
    let draft = current && current.kind === 'custom' ? Object.assign({}, current) : { kind: 'custom', base: AVAILABLE[0] || 'renard', name: '', role: '', color: '' };
    modalEl = el('div', { class: 'owp-modal', role: 'presentation' });
    const dlg = el('div', { class: 'owp-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': T('picker_label') });
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
        if (e.shiftKey && (i <= 0)) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && (i === f.length - 1 || i < 0)) { e.preventDefault(); f[0].focus(); }
      }
    };
    D.addEventListener('keydown', onKey, true);
    modalEl.addEventListener('pointerdown', e => { if (e.target === modalEl) close(); });

    const adopt = pet => {
      const isNew = JSON.stringify(pet) !== JSON.stringify(effectivePet());
      try { sessionStorage.removeItem('owui-pets-snooze'); } catch {}
      save(s => { s.chosen = true; s.pet = pet; s.lastGreet = new Date().toLocaleDateString('sv-SE'); });
      close();
      if (pet && isNew) { setTimeout(() => { setTransient('greet', 900); if (CONFIG.bubbles && state.prefs.bubbles) say(T('adopted', { name: petInfo(pet).name })); }, 250); }
    };
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
      if (CONFIG.allowCustom && AVAILABLE.length) {
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

  /* ---------------- render & loop ---------------- */
  let lastPetKey = '';
  function render() {
    const pet = effectivePet();
    const key = JSON.stringify(pet);
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
    loopTimer = setInterval(() => {
      if (D.hidden || !uid) return;
      watchChat(); show(); maybeWalk(); flushPending();
    }, 700);
    layoutTimer = setInterval(() => { if (!D.hidden) layout(); }, 400);
  }
  D.addEventListener('visibilitychange', () => { if (petEl) petEl.classList.toggle('owp-paused', D.hidden); if (!D.hidden) { wake(); layout(); } });
  window.addEventListener('resize', () => { if (petEl) layout(); }, { passive: true });
  window.addEventListener('keydown', e => {
    if (e.altKey && e.shiftKey && (e.key === 'P' || e.key === 'p' || e.code === 'KeyP') && uid) { e.preventDefault(); openPicker(); }
  }, true);

  let booting = 0;
  async function boot() {
    const run = ++booting;
    uid = ''; firstName = '';
    if (petEl) { petEl.hidden = true; pawEl.hidden = true; hideBubble(); closeMenu(); }
    if (!token) return;
    try {
      const me = await api('GET', '/api/v1/auths/');
      if (run !== booting || !me || !me.id) return;
      uid = String(me.id);
      firstName = clean(String(me.name || '').split(' ')[0], 24);
    } catch { return; }
    await loadState();
    if (run !== booting) return;
    lastPetKey = '';
    render();
    greet();
    if (!state.chosen && !effectivePet() && CONFIG.invite && CONFIG.paw) {
      setTimeout(() => { if (!state.chosen && !modalEl) { pawEl.classList.add('owp-invite'); } }, 1200);
    }
  }
  function start() {
    if (!D.body) { D.addEventListener('DOMContentLoaded', start, { once: true }); return; }
    buildDom();
    token = getToken();
    startLoops();
    boot();
  }
  window.__owuiChatPetsDebug = { get state() { return JSON.parse(JSON.stringify(state)); }, get mode() { return petEl && petEl.getAttribute('data-state'); }, openPicker: () => openPicker(), say: t => say(String(t)) };
  start();
})();
"""


# ===========================================================================
# Chat Pets: event function
# ===========================================================================

PET_IDS = ("hibou", "ecureuil", "berger", "renard", "chat", "tortue")


def _clean_text(value: Any, limit: int) -> str:
    text = _re.sub(r"[\x00-\x1f\x7f\u2028\u2029]", " ", str(value or ""))
    return _re.sub(r"\s+", " ", text).strip()[:limit]


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

    LOADER_BLOCK_START = "// owui-chat-pets:start"
    LOADER_BLOCK_END = "// owui-chat-pets:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment_key = None
        self._fragment = ""
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


def register(app: Any) -> None:
    Event.instance._publish(app)
