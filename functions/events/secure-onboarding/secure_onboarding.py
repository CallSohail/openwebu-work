"""
title: Secure Dynamic Onboarding Rich UI
author: CallSohail
author_url: https://github.com/CallSohail/openwebu-work
funding_url: https://github.com/CallSohail/openwebu-work
version: 9.1.0
required_open_webui_version: 0.11.3
description: Bilingual (FR/EN), role-aware interactive onboarding guide and tutorial for Open WebUI. Delivered once per user (new sign-ups, first login, or pushed to everyone), updated in place when content or permissions change, and never shows a feature the user is not allowed to use.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field


log = logging.getLogger("openwebui.secure_onboarding")
log.setLevel(logging.INFO)

ONBOARDING_VERSION = 5
TEMPLATE_REVISION = 9
SETTINGS_KEY = "secure_onboarding"
TEST_SETTINGS_KEY = "secure_onboarding_test"

FALLBACK_TEXT_FR = """## Bienvenue

Votre guide interactif et personnalisé s'affiche au-dessus de ce message. Il est
construit uniquement à partir des modèles, prompts, outils, compétences, bases de
connaissances et fonctions auxquels votre compte peut accéder.

Si le guide ne s'affiche pas, actualisez la page ou contactez votre équipe de support.
"""

FALLBACK_TEXT_EN = """## Welcome

Your personalized interactive guide appears above this message. It is built only
from models, prompts, tools, skills, knowledge bases, and functions your account
is allowed to access.

If the guide does not appear, refresh the page or contact your support team.
"""


# The iframe is intentionally self-contained. It loads no scripts, fonts, images,
# or data from third-party origins. Dynamic values are serialized as inert JSON.
ONBOARDING_HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none';">
<title>Guide</title>
<style>
:root{
  color-scheme:light;
  --bleu:#1F4E79;--turq:#0EA5B7;--jaune:#F0B429;--orange:#E8710A;--nuit:#0B1F33;
  --primary:var(--bleu);--primary-hover:#082F50;--on-primary:#fff;--accent:var(--turq);
  --ring:rgba(0,179,195,.28);--bleu-tint:#EAF1F7;--turq-tint:#E3F7F9;--jaune-tint:#FFF6DB;--orange-tint:#FFF0E0;
  --bg:#fff;--stage:#F2F5F8;--surface:#fff;--subtle:#F3F4F6;--hover:#F5F5F5;
  --text:#111827;--muted:#6B7280;--faint:#9CA3AF;--border:#E5E7EB;--border-strong:#D1D5DB;
  --send:#000;--send-fg:#fff;--head:var(--bleu);--head-text:#fff;--head-muted:rgba(255,255,255,.72);
  --font:Arial,"Helvetica Neue",Helvetica,sans-serif
}
@media(prefers-color-scheme:dark){:root{
  color-scheme:dark;--primary:var(--turq);--primary-hover:#2FC2D4;--on-primary:var(--nuit);
  --bleu-tint:#12293D;--turq-tint:#07333A;--jaune-tint:#2B2409;--orange-tint:#2E1B08;
  --bg:#141414;--stage:#0C1B29;--surface:#1E1E1E;--subtle:#262626;--hover:#2A2A2A;
  --text:#ECECEC;--muted:#A3A3A3;--faint:#737373;--border:#2E2E2E;--border-strong:#404040;
  --send:#ECECEC;--send-fg:#111;--head:var(--nuit)
}}
*{box-sizing:border-box}
html,body{margin:0}
body{padding:2px;background:transparent;color:var(--text);font:14px/1.55 var(--font);-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;text-align:left}
button:focus-visible{outline:2px solid var(--turq);outline-offset:2px;border-radius:6px}
.i{width:16px;height:16px;flex:0 0 auto;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}

/* ===== Guide chrome ===== */
.g{position:relative;max-width:900px;margin:0 auto;border:1px solid var(--border);border-radius:16px;background:var(--bg);overflow:hidden}
.g-head{display:flex;align-items:center;gap:14px;padding:12px 18px;background:var(--head);color:var(--head-text)}
.g-logo{display:grid;width:28px;height:28px;flex:0 0 auto;place-items:center;border-radius:50%;background:#fff;color:var(--bleu);font-weight:800;font-size:13px}
.g-name{font-size:14px;font-weight:650;white-space:nowrap}
.g-name span{font-weight:400;color:var(--head-muted)}
.g-tabs{display:flex;gap:4px;margin-left:8px}
.g-tab{padding:6px 10px;border-radius:8px;font-size:13px;font-weight:600;color:var(--head-muted);transition:all 160ms ease-out}
.g-tab:hover{color:#fff;background:rgba(255,255,255,.08)}
.g-tab[aria-selected="true"]{color:#fff;box-shadow:inset 0 -2px 0 var(--jaune);border-radius:8px 8px 0 0}
.g-right{display:flex;align-items:center;gap:6px;margin-left:auto}
.g-lang{display:flex;padding:2px;border-radius:8px;background:rgba(255,255,255,.12)}
.g-lang button{padding:2px 8px;border-radius:6px;font-size:12px;font-weight:700;color:var(--head-muted)}
.g-lang button[aria-pressed="true"]{background:#fff;color:var(--bleu)}
.g-close{display:flex;align-items:center;gap:4px;padding:5px 8px;border-radius:8px;font-size:13px;color:var(--head-muted);transition:all 160ms ease-out}
.g-close:hover{color:#fff;background:rgba(255,255,255,.1)}

.g-chapters{display:flex;gap:6px;padding:12px 18px;overflow-x:auto;border-bottom:1px solid var(--border);scrollbar-width:none}
.g-chapters::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;padding:5px 11px;border:1px solid var(--border);border-radius:999px;font-size:12.5px;font-weight:600;color:var(--muted);white-space:nowrap;transition:all 160ms ease-out}
.chip:hover{color:var(--text);border-color:var(--border-strong)}
.chip.done{color:var(--bleu);border-color:transparent;background:var(--bleu-tint)}
.chip.on{color:#fff;border-color:var(--bleu);background:var(--bleu)}
@media(prefers-color-scheme:dark){.chip.done{color:var(--turq)}.chip.on{color:var(--nuit);background:var(--turq);border-color:var(--turq)}}
.g-bar{height:3px;background:var(--border)}
.g-bar span{display:block;height:100%;background:var(--turq);transition:width 220ms ease-out}

/* ===== Step ===== */
.step{padding:22px 18px 8px;animation:fade 200ms ease-out both}
.kicker{display:flex;align-items:center;gap:8px;margin:0 0 4px;font-size:12px;font-weight:700;letter-spacing:.02em;color:var(--primary)}
.kicker b{padding:1px 7px;border-radius:999px;background:var(--jaune);color:var(--nuit);font-size:11px}
.step h1{margin:0;font-size:22px;line-height:1.3;font-weight:700;letter-spacing:-.01em}
.desc{max-width:720px;margin:6px 0 0;color:var(--muted);font-size:14.5px}
.hint{display:inline-flex;align-items:center;gap:6px;margin-top:10px;font-size:12.5px;color:var(--muted)}
.hint .i{color:var(--turq)}

.stage{position:relative;display:flex;justify-content:center;align-items:flex-start;min-height:300px;margin-top:16px;padding:24px 20px;border:1px solid var(--border);border-radius:14px;background:var(--stage);overflow:hidden}
.stage.center{align-items:center;min-height:220px}
.stage.tall{min-height:470px}
.stage.flush{padding:14px}

.notes{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:14px}
.note{display:flex;gap:10px;padding:10px 12px;border:1px solid transparent;border-radius:12px;transition:all 160ms ease-out}
.note:hover{background:var(--hover)}
.note.on{border-color:var(--turq);background:var(--turq-tint)}
.note-n{display:grid;width:20px;height:20px;flex:0 0 auto;place-items:center;border-radius:50%;background:var(--bleu);color:#fff;font-size:11px;font-weight:700}
@media(prefers-color-scheme:dark){.note-n{background:var(--turq);color:var(--nuit)}}
.note-b{min-width:0}
.note-k{display:flex;align-items:center;gap:6px;font-size:13.5px;font-weight:650}
.note-k .i{width:15px;height:15px;color:var(--muted)}
.note-t{margin-top:2px;font-size:13px;color:var(--muted)}

.callout{display:flex;gap:10px;margin-top:12px;padding:11px 14px;border-radius:12px;font-size:13.5px}
.callout .i{margin-top:2px}
.callout.tip{background:var(--jaune-tint);border-left:3px solid var(--jaune)}
.callout.tip .i{color:#B38300}
.callout.warn{background:var(--orange-tint);border-left:3px solid var(--orange)}
.callout.warn .i{color:var(--orange)}
.callout.info{background:var(--bleu-tint);border-left:3px solid var(--bleu)}
.callout.info .i{color:var(--primary)}
.example{margin-top:12px;padding:12px 14px;border:1px solid var(--border);border-radius:12px}
.example small{display:block;margin-bottom:4px;font-size:11.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.example p{margin:0;font-size:13.5px}
.try{display:inline-flex;align-items:center;gap:6px;margin-top:8px;font-size:13px;font-weight:650;color:var(--primary)}
.try:hover{text-decoration:underline}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.compare div{padding:12px 14px;border-radius:12px;font-size:13.5px}
.compare .bad{background:var(--orange-tint)}
.compare .good{background:var(--turq-tint)}
.compare b{display:flex;align-items:center;gap:6px;margin-bottom:4px;font-size:12px}

.g-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:16px;padding:12px 18px;border-top:1px solid var(--border)}
.g-count{font-size:12.5px;color:var(--muted)}
.btn{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:0 14px;border-radius:10px;font-size:14px;font-weight:650;transition:all 160ms ease-out}
.btn.ghost{color:var(--muted)}
.btn.ghost:hover{color:var(--text);background:var(--hover)}
.btn.ghost:disabled{visibility:hidden}
.btn.primary{background:var(--primary);color:var(--on-primary)}
.btn.primary:hover{background:var(--primary-hover)}

/* ===== Library ===== */
.lib{position:relative;padding:22px 18px 18px;min-height:420px}
.lib-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:16px}
.tile{display:flex;flex-direction:column;gap:4px;padding:14px;border:1px solid var(--border);border-radius:12px;transition:all 160ms ease-out}
.tile:hover{border-color:var(--turq);background:var(--turq-tint)}
.tile-k{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:650}
.tile-k .i{width:18px;height:18px;color:var(--primary)}
.tile-t{font-size:12.5px;color:var(--muted)}
.tile-w{margin-top:4px;font-size:11.5px;color:var(--faint)}
.lib-group{margin:18px 0 0;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.overlay{position:absolute;inset:0;display:flex;justify-content:center;align-items:flex-start;padding:18px;background:rgba(6,30,51,.45);animation:fadeonly 160ms ease-out both;z-index:20}
.sheet{width:100%;max-width:560px;border-radius:16px;background:var(--bg);box-shadow:0 20px 60px rgba(6,30,51,.3);overflow:hidden;animation:pop 200ms ease-out both}
.sheet-h{display:flex;align-items:flex-start;gap:12px;padding:16px 18px;border-bottom:1px solid var(--border)}
.sheet-h .i{width:22px;height:22px;margin-top:2px;color:var(--primary)}
.sheet-h h2{margin:0;font-size:18px}
.sheet-h p{margin:2px 0 0;color:var(--muted);font-size:13.5px}
.sheet-x{margin-left:auto;padding:4px;border-radius:8px;color:var(--muted)}
.sheet-x:hover{background:var(--hover);color:var(--text)}
.sheet-b{padding:14px 18px 18px}
.sheet-l{margin:14px 0 6px;font-size:11.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.sheet-l:first-child{margin-top:0}
.path{display:flex;flex-wrap:wrap;align-items:center;gap:4px;font-size:12.5px}
.path span{padding:3px 8px;border-radius:6px;background:var(--subtle);font-weight:600}
.path .i{width:13px;height:13px;color:var(--faint)}
.steps{margin:0;padding-left:18px;display:grid;gap:5px;font-size:13.5px}
.steps li::marker{color:var(--primary);font-weight:700}

/* ===== Mock primitives (mirror Open WebUI) ===== */
.mk{font-size:13px;color:var(--text)}
.hl{position:relative;border-radius:8px;box-shadow:0 0 0 2px var(--turq),0 0 0 6px var(--ring)!important;z-index:2}
[data-a]{cursor:pointer}
.m-home{display:flex;flex-direction:column;align-items:center;width:100%;max-width:640px}
.m-title{display:flex;align-items:center;gap:10px;margin:4px 0 18px;padding:2px 6px;font-size:22px}
.m-oi{display:grid;width:30px;height:30px;place-items:center;border:1px solid var(--border);border-radius:8px;background:var(--surface);font-size:11px;font-weight:800}
.m-comp{position:relative;width:100%;max-width:640px}
.m-box{padding:12px 10px 8px 14px;border:1px solid var(--border);border-radius:24px;background:var(--surface);box-shadow:0 2px 10px rgba(0,0,0,.06)}
.m-ph{padding:0 2px;font-size:15px;color:var(--muted)}
.m-ph.typed{color:var(--text)}
.m-row{display:flex;align-items:center;gap:2px;margin-top:10px}
.m-ib{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;color:var(--muted)}
.m-ib .i{width:17px;height:17px}
.m-div{width:1px;height:18px;margin:0 4px;background:var(--border)}
.m-grow{flex:1}
.m-model{display:flex;align-items:center;gap:6px;padding:4px 8px;font-size:13.5px;color:var(--muted)}
.m-model .i{width:13px;height:13px}
.m-send{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;background:var(--send);color:var(--send-fg);margin-left:2px}
.m-send .i{width:16px;height:16px}
.m-menu{position:absolute;top:calc(100% + 6px);min-width:270px;padding:4px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 8px 28px rgba(0,0,0,.10);z-index:3;animation:pop 180ms ease-out both}
.m-menu.left{left:6px}.m-menu.right{right:6px}
.m-it{display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:9px;font-size:13.5px;white-space:nowrap}
.m-it:hover{background:var(--hover)}
.m-it .i{color:var(--text)}
.m-it .m-end{display:flex;align-items:center;gap:8px;margin-left:auto;padding-left:14px;color:var(--faint);font-size:12.5px}
.m-it .m-count{color:var(--faint)}
.m-sep{height:1px;margin:4px 8px;background:var(--border)}
.m-sw{position:relative;width:28px;height:16px;border-radius:999px;background:var(--border-strong)}
.m-sw:after{content:"";position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#fff;transition:all 160ms ease-out}
.m-sw.on{background:var(--turq)}.m-sw.on:after{left:14px}
.m-sub{position:absolute;top:calc(100% + 6px);min-width:240px;padding:4px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 8px 28px rgba(0,0,0,.12);z-index:4;animation:pop 180ms ease-out both}
.m-sub .m-back{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:13.5px}
.m-subsearch{display:flex;align-items:center;gap:8px;margin:2px 4px 4px;padding:6px 8px;border-bottom:1px solid var(--border);color:var(--faint);font-size:13px}
.m-sugg{align-self:flex-start;display:grid;gap:10px;margin:18px 0 0 60px;border-radius:10px;padding:4px 8px}
.m-sugg .m-sh{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)}
.m-sugg b{display:block;font-weight:500;font-size:14px}
.m-sugg span{font-size:12px;color:var(--muted)}

/* response + feedback */
.m-resp{width:100%;max-width:640px}
.m-lines{display:grid;gap:8px;margin-bottom:12px}
.m-l{height:8px;border-radius:4px;background:var(--border)}
.m-acts{display:flex;flex-wrap:wrap;align-items:center;gap:1px}
.m-acts .m-ib{width:28px;height:28px;border-radius:8px}
.m-acts .m-ib .i{width:16px;height:16px}
.m-acts .m-ib.sel{background:var(--subtle);color:var(--text)}
.m-time{margin-left:6px;font-size:12px;color:var(--faint)}
.m-fb{margin-top:10px;padding:14px 16px;border:1px solid var(--border);border-radius:16px;background:var(--surface)}
.m-fbh{display:flex;justify-content:space-between;font-size:13.5px}
.m-scale{display:flex;justify-content:center;gap:6px;margin:10px 0 2px;padding:3px}
.m-scale span{display:grid;width:26px;height:26px;place-items:center;border:1px solid var(--border);border-radius:50%;font-size:12.5px}
.m-scale span.off{color:var(--faint)}
.m-scale span.sel{background:var(--bleu);border-color:var(--bleu);color:#fff}
.m-scalel{display:flex;justify-content:space-between;max-width:330px;margin:0 auto;font-size:11.5px;color:var(--muted)}
.m-why{margin:8px 0 6px;font-size:13.5px}
.m-chips{display:flex;flex-wrap:wrap;gap:6px;padding:3px}
.m-chips span{padding:4px 11px;border:1px solid var(--border);border-radius:999px;font-size:12.5px}
.m-chips span.sel{border-color:var(--bleu);background:var(--bleu-tint);color:var(--bleu)}
@media(prefers-color-scheme:dark){.m-chips span.sel{color:var(--turq);border-color:var(--turq)}}
.m-details{margin-top:8px;padding:6px 4px;min-height:44px;font-size:13px;color:var(--faint)}
.m-fbf{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
.m-tags{display:flex;align-items:center;gap:8px;padding:3px;font-size:12px;color:var(--faint)}
.m-tags b{padding:2px 8px;border:1px solid var(--border);border-radius:999px;background:var(--subtle);font-weight:500;color:var(--text)}
.m-save{padding:6px 16px;border-radius:999px;background:var(--send);color:var(--send-fg);font-size:13px;font-weight:600}

/* sidebar */
.m-app{display:grid;grid-template-columns:230px 1fr;width:100%;max-width:720px;min-height:340px;overflow:hidden;border:1px solid var(--border);border-radius:14px;background:var(--surface)}
.m-side{display:flex;flex-direction:column;gap:1px;padding:10px 8px;background:var(--subtle)}
.m-sh1{display:flex;align-items:center;gap:8px;padding:4px 8px 10px;font-size:14px;font-weight:500}
.m-sh1 .m-oi{width:24px;height:24px;font-size:9px}
.m-sh1 .m-ib{margin-left:auto;width:26px;height:26px}
.m-si{display:flex;align-items:center;gap:10px;padding:6px 8px;border-radius:9px;font-size:14px}
.m-si .i{width:17px;height:17px}
.m-sg{display:flex;align-items:center;gap:4px;margin-top:10px;padding:5px 8px;border-radius:8px;font-size:13.5px;color:var(--faint)}
.m-sg .m-end{margin-left:auto;display:flex;gap:6px}
.m-sg .i{width:14px;height:14px}
.m-sc{padding:5px 8px 5px 12px;font-size:13px;border-radius:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m-mainpane{display:flex;align-items:center;justify-content:center;padding:20px;color:var(--faint);font-size:13px}

/* notes */
.m-page{width:100%;max-width:760px;border:1px solid var(--border);border-radius:14px;background:var(--surface);padding:14px 16px;overflow:hidden}
.m-nh{display:flex;align-items:center;justify-content:space-between}
.m-nh .t{padding:2px 4px;font-size:14px}.m-nh .t span{color:var(--faint);margin-left:6px}
.m-create{display:flex;align-items:center;border:1px solid var(--border);border-radius:9px;font-size:12.5px}
.m-create span{padding:4px 10px}.m-create i{display:grid;place-items:center;padding:4px 6px;border-left:1px solid var(--border)}
.m-ns{display:flex;align-items:center;justify-content:space-between;margin-top:10px}
.m-ns .s{display:flex;align-items:center;gap:8px;padding:4px;color:var(--faint);font-size:14px}
.m-ns .f{display:flex;gap:12px;padding:4px;font-size:13px}
.m-ns .f span{display:flex;align-items:center;gap:3px}
.m-ns .f .i{width:13px;height:13px}
.m-th{display:flex;justify-content:space-between;margin-top:10px;padding:4px;font-size:12.5px;color:var(--faint)}
.m-tg{margin:8px 4px 2px;font-size:12.5px;color:var(--faint)}
.m-tr{display:flex;align-items:center;gap:8px;padding:7px 4px;border-radius:8px;font-size:13.5px}
.m-tr small{color:var(--faint);font-size:11.5px}
.m-tr .r{margin-left:auto;display:flex;gap:14px;color:var(--faint);font-size:12px}
.m-editor{display:grid;grid-template-columns:1fr 250px;width:100%;max-width:780px;height:350px;border:1px solid var(--border);border-radius:14px;background:var(--surface);overflow:hidden}
.m-ed{position:relative;padding:12px 14px;border-right:1px solid var(--border)}
.m-edt{display:flex;align-items:flex-start;justify-content:space-between}
.m-edt .d{padding:2px 4px;font-size:15px}
.m-edt .tools{display:flex;align-items:center;gap:2px}
.m-edt .tools .m-ib{width:26px;height:26px}.m-edt .tools .m-ib .i{width:15px;height:15px}
.m-acc{display:flex;align-items:center;gap:4px;margin-left:4px;padding:3px 8px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-acc .i{width:12px;height:12px}
.m-meta{padding:2px 4px;font-size:12px;color:var(--faint)}
.m-body{margin-top:8px;padding:4px;color:var(--muted);font-size:14px}
.m-edchat{display:flex;flex-direction:column;padding:10px 10px 8px}
.m-edchat .h{display:flex;justify-content:space-between;font-size:13px;color:var(--muted)}
.m-sp{margin-top:auto;display:grid;gap:9px;padding:6px;border-radius:8px;font-size:12px;color:var(--muted)}
.m-sp small{color:var(--faint)}
.m-mini{margin-top:10px;padding:8px 8px 6px 10px;border:1px solid var(--border);border-radius:16px;box-shadow:0 2px 8px rgba(0,0,0,.05)}
.m-mini .ph{font-size:13px;color:var(--muted)}
.m-mini .m-row{margin-top:6px}
.m-mini .m-ib{width:24px;height:24px}.m-mini .m-ib .i{width:14px;height:14px}
.m-mini .m-model{font-size:11.5px;padding:2px 4px}
.m-mini .up{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;background:var(--border);color:#fff}

/* folder modal */
.m-modal{width:100%;max-width:560px;padding:18px 20px;border-radius:26px;background:var(--surface);box-shadow:0 10px 40px rgba(0,0,0,.15)}
.m-modal .h{display:flex;justify-content:space-between;font-size:15px;margin-bottom:12px}
.m-fl{padding:3px 4px;border-radius:8px}
.m-fl label{display:block;font-size:12.5px;color:var(--muted)}
.m-fl .v{font-size:14px;color:var(--faint)}
.m-flr{display:flex;justify-content:space-between;align-items:center}
.m-hr{height:1px;margin:10px 0;background:var(--border)}
.m-kn{display:flex;gap:10px;font-size:12.5px;color:var(--muted)}
.m-kn span:first-child{color:var(--muted)}
.m-knnote{margin-top:6px;font-size:12.5px}

/* channels */
.m-chan{display:grid;grid-template-columns:180px 1fr;width:100%;max-width:760px;height:350px;border:1px solid var(--border);border-radius:14px;background:var(--surface);overflow:hidden}
.m-cmain{display:flex;flex-direction:column;padding:10px 14px}
.m-chead{display:flex;align-items:center;justify-content:space-between;padding:2px 4px 8px;border-bottom:1px solid var(--border);font-size:14px;font-weight:600}
.m-msg{display:flex;gap:10px;margin-top:10px;padding:4px;border-radius:10px}
.m-av{display:grid;width:28px;height:28px;flex:0 0 auto;place-items:center;border-radius:50%;background:var(--bleu-tint);color:var(--bleu);font-size:11px;font-weight:700}
.m-av.bot{background:var(--bleu);color:#fff}
.m-msg .n{font-size:13px;font-weight:600}.m-msg .n small{margin-left:6px;color:var(--faint);font-weight:400}
.m-msg .x{font-size:13px}
.m-mention{color:var(--bleu);font-weight:600;background:var(--bleu-tint);padding:0 4px;border-radius:4px}
@media(prefers-color-scheme:dark){.m-mention{color:var(--turq)}}
.m-under{display:flex;gap:8px;margin-top:4px}
.m-pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border:1px solid var(--border);border-radius:999px;font-size:11.5px;color:var(--muted)}
.m-pill .i{width:12px;height:12px}
.m-cin{margin-top:auto;padding:8px 10px;border:1px solid var(--border);border-radius:16px;display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}

/* do / don't */
.m-cols{display:grid;grid-template-columns:1fr 1fr;gap:10px;width:100%;max-width:640px}
.m-card{padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
.m-card h3{margin:0 0 8px;font-size:13px}
.m-rule{display:flex;gap:8px;margin-top:8px;font-size:13px}
.m-rule.ok .i{color:var(--turq)}.m-rule.no .i{color:var(--orange)}

/* cover */
.cover{margin-top:18px;padding:22px;border-radius:14px;background:var(--bleu);color:#fff}
@media(prefers-color-scheme:dark){.cover{background:#122C42}}
.cover h2{margin:0 0 6px;font-size:18px}
.cover p{margin:0;color:rgba(255,255,255,.82);font-size:14px;max-width:640px}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.fact{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:rgba(255,255,255,.12);font-size:12.5px;font-weight:600}
.fact .i{width:14px;height:14px;color:var(--jaune)}
.cover-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.cbtn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:10px;font-size:14px;font-weight:700;transition:all 160ms ease-out}
.cbtn.main{background:var(--jaune);color:var(--nuit)}.cbtn.main:hover{filter:brightness(.95)}
.cbtn.alt{color:#fff;border:1px solid rgba(255,255,255,.4)}.cbtn.alt:hover{background:rgba(255,255,255,.1)}
.toc-h{margin:20px 0 8px;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.toc{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.toc button{display:flex;gap:10px;padding:12px;border:1px solid var(--border);border-radius:12px;transition:all 160ms ease-out}
.toc button:hover{border-color:var(--turq);background:var(--turq-tint)}
.toc .i{width:18px;height:18px;margin-top:1px;color:var(--primary)}
.toc b{display:block;font-size:13.5px}.toc span{display:block;font-size:12px;color:var(--muted)}
.toc small{display:block;margin-top:4px;font-size:11px;color:var(--faint)}
/* user menu */
.m-user{position:relative;width:250px;padding:4px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 8px 28px rgba(0,0,0,.10)}
.m-uh{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:13.5px}
.m-uh .dot{width:7px;height:7px;border-radius:50%;background:#22C55E;margin-left:auto}
.m-uh small{color:var(--faint);font-size:12px}
.m-face{display:grid;width:20px;height:20px;place-items:center;border-radius:50%;background:var(--bleu);color:#fff;font-size:10px;font-weight:700}
/* calendar */
.m-calapp{display:grid;grid-template-columns:180px 1fr;width:100%;max-width:780px;min-height:380px;border:1px solid var(--border);border-radius:14px;background:var(--surface);overflow:hidden}
.m-calside{display:flex;flex-direction:column;gap:10px;padding:12px;border-right:1px solid var(--border)}
.m-newev{display:flex;align-items:center;justify-content:center;gap:6px;padding:7px;border:1px solid var(--border);border-radius:10px;font-size:13px;font-weight:600}
.m-mini{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;padding:4px;border-radius:8px;font-size:10px;text-align:center;color:var(--muted)}
.m-mini span.t{background:var(--bleu);color:#fff;border-radius:50%}
.m-calh{display:flex;align-items:center;justify-content:space-between;padding:3px 4px;font-size:12px;color:var(--muted)}
.m-calrow{display:flex;align-items:center;gap:8px;padding:4px;border-radius:8px;font-size:12.5px}
.m-sq{width:10px;height:10px;border-radius:3px}
.m-calmain{display:flex;flex-direction:column;padding:10px 12px}
.m-calbar{display:flex;align-items:center;gap:8px;padding-bottom:8px}
.m-calbar .m-btn2{padding:4px 10px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-calbar .nav{display:flex;padding:2px;border-radius:8px}
.m-calbar .nav .i{width:16px;height:16px;color:var(--muted)}
.m-calbar h4{margin:0;font-size:15px;font-weight:600}
.m-calbar .view{margin-left:auto;display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-grid7{display:grid;grid-template-columns:repeat(7,1fr);flex:1;border-top:1px solid var(--border);border-left:1px solid var(--border)}
.m-grid7 div{min-height:52px;padding:3px;border-right:1px solid var(--border);border-bottom:1px solid var(--border);font-size:10.5px;color:var(--muted)}
.m-grid7 .hd{min-height:0;padding:4px;font-weight:600;text-align:center}
.m-ev{display:block;margin-top:2px;padding:1px 4px;border-radius:4px;font-size:10px;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m-ev.b{background:#3B82F6}.m-ev.v{background:#8B5CF6}.m-ev.g{background:#10B981}
.m-form{width:100%;max-width:520px;padding:16px 18px;border-radius:20px;background:var(--surface);box-shadow:0 10px 40px rgba(0,0,0,.15)}
.m-form .h{display:flex;justify-content:space-between;margin-bottom:8px;font-size:15px;font-weight:600}
.m-f{display:flex;align-items:center;gap:10px;padding:6px 4px;border-radius:8px;font-size:13px}
.m-f .i{color:var(--muted)}
.m-f .lbl{width:84px;flex:0 0 auto;color:var(--muted);font-size:12.5px}
.m-f .val{flex:1;padding:5px 8px;border:1px solid var(--border);border-radius:8px;color:var(--text)}
.m-f .val.ph{color:var(--faint)}
.m-seg{display:flex;flex-wrap:wrap;gap:4px}
.m-seg span{padding:3px 9px;border:1px solid var(--border);border-radius:999px;font-size:12px}
.m-seg span.sel{background:var(--bleu);border-color:var(--bleu);color:#fff}
/* automations */
.m-rowx{display:flex;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--border);border-radius:8px;font-size:13px}
.m-rowx small{display:block;color:var(--faint);font-size:11.5px}
.m-rowx .m-sw{margin-left:auto}
.m-split{display:flex;align-items:center;border-radius:9px;background:var(--send);color:var(--send-fg);font-size:12.5px}
.m-split span{padding:4px 10px}.m-split i{display:grid;place-items:center;padding:4px 6px;border-left:1px solid rgba(255,255,255,.25)}
.m-split .i{width:13px;height:13px}
.m-tbtns{display:flex;gap:6px}
.m-tbtns span{display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-tbtns .i{width:13px;height:13px}
.m-tbtns .run{background:var(--bleu);border-color:var(--bleu);color:#fff}
.m-log{display:flex;align-items:center;gap:8px;padding:5px 4px;font-size:12px;color:var(--muted)}
.m-log .ok{color:#10B981}.m-log .err{color:var(--orange)}
.m-log a{margin-left:auto;color:var(--primary);font-weight:600}
/* built-in tools */
.m-tc{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--border);border-radius:999px;background:var(--subtle);font-size:12px;color:var(--muted)}
.m-tc .i{width:13px;height:13px;color:#10B981}
.m-bt{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:14px;padding:8px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
.m-bt .m-it{white-space:normal;font-size:12.5px;padding:6px 8px}
.m-bt .m-it .i{color:var(--primary)}
@media(max-width:760px){.toc{grid-template-columns:1fr 1fr}.m-calapp{grid-template-columns:1fr}.m-calside{display:none}.m-bt{grid-template-columns:1fr 1fr}}
@media(max-width:520px){.toc{grid-template-columns:1fr}}
/* dismissed / toast */
.g.slim .g-chapters,.g.slim .g-bar,.g.slim .g-body,.g.slim .g-tabs{display:none}
.slim-msg{font-size:13.5px}
.toast{position:fixed;left:50%;bottom:14px;padding:9px 14px;border-radius:10px;background:var(--nuit);color:#fff;font-size:13px;opacity:0;transform:translate(-50%,8px);pointer-events:none;transition:all 160ms ease-out;z-index:50}
.toast.show{opacity:1;transform:translate(-50%,0)}

@keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
@keyframes fadeonly{from{opacity:0}to{opacity:1}}
@keyframes pop{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}

@media(max-width:760px){
  .notes,.compare{grid-template-columns:1fr}
  .lib-grid{grid-template-columns:1fr 1fr}
  .m-app{grid-template-columns:200px 1fr}
  .m-editor{grid-template-columns:1fr}.m-edchat{display:none}
  .m-chan{grid-template-columns:1fr}.m-chan .m-side{display:none}
  .g-name span{display:none}
}
@media(max-width:520px){
  .g-head{flex-wrap:wrap;gap:8px}.g-tabs{order:3;margin-left:0;width:100%}
  .lib-grid,.m-cols{grid-template-columns:1fr}
  .stage{padding:14px 10px;overflow-x:auto;justify-content:flex-start}
  .m-app{grid-template-columns:1fr}.m-app .m-mainpane{display:none}
  .m-ns .f{display:none}
  .m-sub{left:36px!important;top:calc(100% + 40px)!important;box-shadow:0 12px 36px rgba(0,0,0,.22)}
}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<section class="g" id="g" aria-label="Guide">
  <header class="g-head">
    <span class="g-logo" id="gLogo" aria-hidden="true"></span>
    <span class="g-name" id="gName"></span>
    <nav class="g-tabs" role="tablist">
      <button class="g-tab" role="tab" data-tab="tour" type="button"></button>
      <button class="g-tab" role="tab" data-tab="lib" type="button"></button>
    </nav>
    <div class="g-right">
      <div class="g-lang" role="group" aria-label="Langue / Language"><button type="button" data-lang="fr">FR</button><button type="button" data-lang="en">EN</button></div>
      <button class="g-close" id="close" type="button"></button>
    </div>
  </header>
  <div class="g-body" id="gBody"></div>
</section>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script type="application/json" id="snapshot">__SNAPSHOT_JSON__</script>
<script>
(function(){
'use strict';
var data;try{data=JSON.parse(document.getElementById('snapshot').textContent);}catch(e){document.body.textContent='Onboarding data could not be loaded.';return;}
var ui=data.ui||{},brand=data.brand||{},res=data.resources||{},acc=data.access||{};
var PRODUCT=brand.product||'AI Assistant',CHD={};
var updated=false,REV=Number(data.guide_revision||1)+'.'+Number(data.template_revision||0),lang=ui.default_language==='en'?'en':'fr',tab='tour',cur=0,active=null,steps=[],chapters=[],sheet=null,toastT;
var KEY='openwebui-onboarding-v7-'+String(data.progress_scope||'local'),mem={};

function L(fr,en){return lang==='fr'?fr:en;}
function E(t,c,x){var n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=String(x);return n;}
function add(p){for(var i=1;i<arguments.length;i++){var a=arguments[i];if(a===null||a===undefined||a===false)continue;p.appendChild(typeof a==='string'?document.createTextNode(a):a);}return p;}
function on(k){return !!ui[k];}
var ADMIN=!!ui.is_admin;function ft(k){var a=res.features||[];for(var i=0;i<a.length;i++)if(a[i].key===k)return !!a[i].enabled;return false;}
function av(k){return !!(data.available&&data.available[k]);}
function ws(k){return !!(acc.workspace&&acc.workspace[k]);}
function anyWs(){return ws('models')||ws('knowledge')||ws('prompts')||ws('tools')||ws('skills');}
var TOOLS=res.tools||[],TOGGLES=res.toggles||[],ACTIONS=res.actions||[];
var hasTools=TOOLS.length>0||av('tools');
function load(){try{var r=localStorage.getItem(KEY);if(r)return JSON.parse(r);}catch(e){}return mem;}
function save(p){mem=Object.assign({},load(),p);try{localStorage.setItem(KEY,JSON.stringify(mem));}catch(e){}}
function height(){try{parent.postMessage({type:'iframe:height',height:Math.ceil(document.documentElement.scrollHeight)},'*');}catch(e){}}
function sendPrompt(t){try{parent.postMessage({type:'input:prompt',text:String(t).slice(0,4000)},'*');}catch(e){}var n=document.getElementById('toast');n.textContent=L('Exemple ajouté dans la zone de saisie.','Example added to the message box.');n.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){n.classList.remove('show');},2200);}

var P={
 plus:'M12 5v14|M5 12h14',integ:'M12 3.5l2.2 2.2L12 7.9 9.8 5.7z|M5.7 9.8l2.2 2.2-2.2 2.2L3.5 12z|M18.3 9.8l2.2 2.2-2.2 2.2-2.2-2.2z|M12 16.1l2.2 2.2-2.2 2.2-2.2-2.2z',
 cloud:'M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 1 1 0 9z',down:'M6 9l6 6 6-6',right:'M9 6l6 6-6 6',left:'M15 6l-6 6 6 6',arrowr:'M5 12h14|M13 6l6 6-6 6',arrowl:'M19 12H5|M11 6l-6 6 6 6',
 x:'M18 6 6 18|M6 6l12 12',check:'M20 6 9 17l-5-5',mic:'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z|M19 10v2a7 7 0 0 1-14 0v-2|M12 19v3',wave:'M4 10v4|M8 7v10|M12 4v16|M16 7v10|M20 10v4',
 shield:'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',clip:'M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5',
 camera:'M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z|M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
 globe:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M2 12h20|M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20z',fileplus:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5|M12 11v6|M9 14h6',
 notefile:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5|M9 13h6|M9 17h4',db:'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3z|M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5|M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
 history:'M3 12a9 9 0 1 0 3-6.7L3 8|M3 3v5h5|M12 7v5l3 2',wrench:'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z',
 sliders:'M4 6h9|M17 6h3|M4 12h3|M11 12h9|M4 18h11|M19 18h1|M15 4v4|M9 10v4|M17 16v4',terminal:'M4 4h16v16H4z|M8 9l3 3-3 3|M13 15h3',image:'M3 3h18v18H3z|M21 15l-5-5L5 21|M9 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
 pencil:'M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5z',clipboard:'M9 4h6v3H9z|M15 5h3v16H6V5h3',speaker:'M11 5 6 9H2v6h4l5 4z|M15.5 8.5a5 5 0 0 1 0 7|M19 5a10 10 0 0 1 0 14',
 info:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 16v-4|M12 8h.01',tup:'M7 10v11|M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 7A2 2 0 0 1 18.4 21H7V10l4-8a3 3 0 0 1 4 3.9z',
 tdown:'M17 14V3|M9 18.1 10 14H4.2a2 2 0 0 1-2-2.3l1.4-7A2 2 0 0 1 5.6 3H17v11l-4 8a3 3 0 0 1-4-3.9z',play:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M10 8l6 4-6 4z',
 refresh:'M3 12a9 9 0 0 1 15-6.7L21 8|M21 3v5h-5|M21 12a9 9 0 0 1-15 6.7L3 16|M3 21v-5h5',doc:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5|M9 13h6|M9 17h6',
 spark:'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z|M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',edit:'M12 20h9|M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
 search:'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z|M21 21l-4.3-4.3',book:'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z|M4 19.5V21h16',
 grid:'M4 4h6v6H4z|M14 4h6v6h-6z|M4 14h6v6H4z|M17 14v6|M14 17h6',panel:'M3 4h18v16H3z|M9 4v16',dots:'M5 12h.01|M12 12h.01|M19 12h.01',
 folder:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',hash:'M4 9h16|M4 15h16|M10 3 8 21|M16 3l-2 18',users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2|M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z|M22 21v-2a4 4 0 0 0-3-3.9|M16 3.1a4 4 0 0 1 0 7.8',
 undo:'M9 14 4 9l5-5|M4 9h11a5 5 0 0 1 0 10h-3',redo:'M15 14l5-5-5-5|M20 9H9a5 5 0 0 0 0 10h3',bubble:'M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z',
 lock:'M5 11h14v10H5z|M8 11V7a4 4 0 0 1 8 0v4',download:'M12 3v12|M7 10l5 5 5-5|M5 21h14',upcloud:'M12 13v8|M8 17l4-4 4 4|M20 16.6A5 5 0 0 0 18 7h-1.3A8 8 0 1 0 4 15.3',
 share:'M12 3v12|M7 8l5-5 5 5|M5 14v6h14v-6',pin:'M12 17v5|M9 3h6l-1 7 4 3v2H6v-2l4-3z',trash:'M3 6h18|M8 6V4h8v2|M6 6l1 15h10l1-15',
 bulb:'M9 18h6|M10 22h4|M12 2a7 7 0 0 0-4 12.7V16h8v-1.3A7 7 0 0 0 12 2z',warn:'M12 3 2 21h20z|M12 10v5|M12 18h.01',bolt:'M13 2 4 14h7l-1 8 9-12h-7z',
 okc:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M8 12l3 3 5-6',noc:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M15 9l-6 6|M9 9l6 6',
 brain:'M12 5a3 3 0 1 0-5.9.9A4 4 0 0 0 4 12a4 4 0 0 0 2 3.5A3.5 3.5 0 0 0 12 18z|M12 5a3 3 0 1 1 5.9.9A4 4 0 0 1 20 12a4 4 0 0 1-2 3.5A3.5 3.5 0 0 1 12 18z|M12 5v13',
 layers:'M12 2 2 7l10 5 10-5z|M2 17l10 5 10-5|M2 12l10 5 10-5',slash:'M16 3 8 21',smile:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M8 14s1.5 2 4 2 4-2 4-2|M9 9h.01|M15 9h.01',reply:'M9 17l-5-5 5-5|M20 18v-2a4 4 0 0 0-4-4H4',
 imageup:'M21 12v7H3V5h9|M16 5h6|M19 2v6|M3 16l5-5 5 5',calendar:'M3 5h18v16H3z|M16 3v4|M8 3v4|M3 10h18',clock:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 6v6l4 2',code:'M8 8l-4 4 4 4|M16 8l4 4-4 4',user:'M20 21a8 8 0 0 0-16 0|M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z',gear:'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z|M19.4 15l1.5 1.2-2 3.4-1.8-.6a7 7 0 0 1-2.1 1.2L14.6 22h-4l-.4-1.8a7 7 0 0 1-2.1-1.2l-1.8.6-2-3.4L5.8 15a7 7 0 0 1 0-2.4L4.3 11.4l2-3.4 1.8.6a7 7 0 0 1 2.1-1.2L10.6 5.6h4l.4 1.8a7 7 0 0 1 2.1 1.2l1.8-.6 2 3.4-1.5 1.2a7 7 0 0 1 0 2.4z',logout:'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|M16 17l5-5-5-5|M21 12H9',pause:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M10 9v6|M14 9v6',repeat:'M17 2l4 4-4 4|M3 11v-1a4 4 0 0 1 4-4h14|M7 22l-4-4 4-4|M21 13v1a4 4 0 0 1-4 4H3',bell:'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9|M13.7 21a2 2 0 0 1-3.4 0',mappin:'M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0z|M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',tasks:'M9 6h11|M9 12h11|M9 18h11|M4 6l1 1 2-2|M4 12l1 1 2-2|M4 18l1 1 2-2',flask:'M9 3h6|M10 3v6L4 20h16L14 9V3',map:'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z|M9 4v14|M15 6v14'
};
function I(n){var s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('aria-hidden','true');s.setAttribute('class','i');String(P[n]||P.info).split('|').forEach(function(d){var p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',d);s.appendChild(p);});return s;}

/* Register an annotated element in the mock. Clicking it selects the matching explanation. */
function A(id,el){el.setAttribute('data-a',id);if(active===id)el.classList.add('hl');return el;}

/* ============ Mock builders (mirror the real screens) ============ */
function mItem(id,icon,label,end){var r=E('div','m-it');if(icon)add(r,I(icon));add(r,E('span','',label));if(end){var e=E('span','m-end');end.forEach(function(x){add(e,x);});add(r,e);}return id?A(id,r):r;}
function sw(onn){return E('span','m-sw'+(onn?' on':''));}
function composer(o){
  o=o||{};var c=E('div','m-comp'),b=E('div','m-box'),row=E('div','m-row');
  add(b,A('input',E('div','m-ph'+(o.text?' typed':''),o.text||'How can I help you today?')));
  add(row,A('plus',add(E('span','m-ib'),I('plus'))),E('span','m-div'),A('integ',add(E('span','m-ib'),I('integ'))),E('span','m-grow'),
      A('model',add(E('span','m-model'),E('span','',o.model||PRODUCT),I('down'))));
  if(ft('stt'))add(row,A('mic',add(E('span','m-ib'),I('mic'))));
  add(row,A('voice',add(E('span','m-send'),I('wave'))));
  add(b,row);add(c,b);if(o.menu)add(c,o.menu);return c;
}
function home(o){o=o||{};var h=E('div','m-home');add(h,A('title',add(E('div','m-title'),E('span','m-oi','OI'),E('span','',PRODUCT))),composer(o));
  if(o.sugg!==false){var s=A('sugg',E('div','m-sugg'));add(s,add(E('div','m-sh'),I('bolt'),E('span','','Suggested')));
    [[L('Résumer un document','Summarize a document'),L('En 5 points','In 5 points')],[L('Comparer deux options','Compare two options'),L('Avantages et limites','Pros and cons')],[L('Rédiger un e-mail','Draft an email'),L('Clair et professionnel','Clear and professional')]].forEach(function(x){add(s,add(E('div'),E('b','',x[0]),E('span','',x[1])));});add(h,s);}
  return h;}

function plusItems(){var it=[];
  if(hasTools)it.push({id:'toolperm',i:'shield',l:'Tool Permissions',end:'Full access',arrow:1,sep:1});
  if(ft('file_upload')){it.push({id:'upload',i:'clip',l:'Upload Files'});it.push({id:'capture',i:'camera',l:'Capture'});}
  it.push({id:'webpage',i:'globe',l:'Attach Webpage'});
  if(ft('file_upload'))it.push({id:'attfiles',i:'fileplus',l:'Attach Files',arrow:1});
  if(ft('notes'))it.push({id:'attnotes',i:'notefile',l:'Attach Notes',arrow:1});
  if(av('knowledge')||ws('knowledge'))it.push({id:'attknow',i:'db',l:'Attach Knowledge',arrow:1});
  it.push({id:'refchats',i:'history',l:'Reference Chats',arrow:1});
  return it;}
function subFor(id){var s=E('div','m-sub');s.style.left='284px';
  if(id==='toolperm'){add(s,add(E('div','m-back'),I('left'),E('span','','Tool Permissions')),mItem('perm-full',null,'Full access',[I('check')]),mItem('perm-ask',null,'Ask for approval'));return s;}
  var map={attfiles:[L('Rechercher des fichiers','Search files'),['rapport-2026.pdf','planning.xlsx','note-interne.docx'],'doc'],
    attnotes:[L('Rechercher des notes','Search notes'),[L('Notes de réunion','Meeting notes'),L('Idées de projet','Project ideas')],'notefile'],
    attknow:[L('Rechercher','Search'),[L('Règlement intérieur','Internal rules'),L('Guide des outils','Tools handbook')],'db'],
    refchats:[L('Rechercher des conversations','Search chats'),[L('Plan de projet','Project plan'),L('E-mail au fournisseur','Email to supplier')],'history']};
  var m=map[id];if(!m)return null;add(s,add(E('div','m-subsearch'),I('search'),E('span','',m[0])));m[1].forEach(function(n){add(s,mItem(null,m[2],n));});return s;}
function plusMenu(){var m=E('div','m-menu left');plusItems().forEach(function(x){
  var end=[];if(x.end)end.push(E('span','',x.end));if(x.arrow)end.push(I('right'));add(m,mItem(x.id,x.i,x.l,end.length?end:null));if(x.sep)add(m,E('div','m-sep'));});
  var wrap=E('div');wrap.style.position='relative';add(wrap,m);return m;}
function integItems(){var it=[];
  if(hasTools)it.push({id:'tools',i:'wrench',l:'Tools',count:TOOLS.length||'',arrow:1});
  TOGGLES.slice(0,4).forEach(function(t,k){it.push({id:'toggle-'+k,i:'spark',l:t.name,valves:1,sw:1,desc:t.description});});
  if(ft('web_search'))it.push({id:'web',i:'globe',l:'Web Search',sw:1});
  if(ft('image_generation'))it.push({id:'imagegen',i:'image',l:'Image',sw:1});
  if(ft('code_interpreter'))it.push({id:'code',i:'terminal',l:'Code Interpreter',sw:1});
  return it;}
function integMenu(){var m=E('div','m-menu left');m.style.left='44px';integItems().forEach(function(x){var end=[];
  if(x.count!==undefined){var r=mItem(x.id,x.i,'',null);r.lastChild.remove();add(r,add(E('span'),x.l+' ',E('span','m-count',String(x.count))));add(r,add(E('span','m-end'),I('right')));add(m,r);return;}
  if(x.valves)end.push(I('sliders'));if(x.sw)end.push(sw(active===x.id));add(m,mItem(x.id,x.i,x.l,end));});return m;}
function toolsSub(){var s=E('div','m-sub');s.style.left='330px';add(s,add(E('div','m-back'),I('left'),E('span','','Tools')));
  var list=TOOLS.length?TOOLS.slice(0,7):[{name:L('Outil 1','Tool 1')},{name:L('Outil 2','Tool 2')}];
  list.forEach(function(t,k){add(s,mItem(k===0?'tool-row':null,'wrench',t.name,[sw(k===0&&active==='tool-row')]));});return s;}

function actionsRow(sel){var r=E('div','m-acts');
  [['edit','pencil'],['copy','clipboard'],['speak','speaker'],['ginfo','info'],['good','tup'],['bad','tdown'],['continue','play'],['regen','refresh']].forEach(function(x){var b=A(x[0],add(E('span','m-ib'+(sel===x[0]?' sel':'')),I(x[1])));add(r,b);});
  ACTIONS.slice(0,4).forEach(function(a,k){add(r,A('act-'+k,add(E('span','m-ib'),I(k%2?'spark':'doc'))));});
  add(r,E('span','m-time','Sep 16, 11:31 AM'));return r;}
function feedback(kind){var f=E('div','m-fb');add(f,add(E('div','m-fbh'),E('span','','How would you rate this response?'),I('x')));
  var sc=A('score',E('div','m-scale'));for(var i=1;i<=10;i++){var sp=E('span','',i);if(kind==='good'&&i<6||kind==='bad'&&i>5)sp.className='off';if(kind==='good'&&i===8||kind==='bad'&&i===3)sp.className='sel';add(sc,sp);}
  add(f,sc,add(E('div','m-scalel'),E('span','','1 - Awful'),E('span','','10 - Amazing')),E('div','m-why','Why?'));
  var reasons=kind==='good'?['Accurate information','Followed instructions perfectly','Showcased creativity','Positive attitude','Attention to detail','Thorough explanation','Other']:['Don’t like the style','Too verbose','Not helpful','Not factually correct','Didn’t fully follow instructions','Refused when it shouldn’t have','Being lazy','Other'];
  var ch=A('reasons',E('div','m-chips'));reasons.forEach(function(r,k){add(ch,E('span',(kind==='good'&&k===0)||(kind==='bad'&&k===3)?'sel':'',r));});add(f,ch);
  add(f,A('details',E('div','m-details','Feel free to add specific details')));
  add(f,add(E('div','m-fbf'),A('tags',add(E('div','m-tags'),E('b','','General ×'),E('span','','Add a tag...'))),A('save',E('span','m-save','Save'))));return f;}

function sidebar(extraChats){var s=E('div','m-side');
  add(s,add(E('div','m-sh1'),E('span','m-oi','OI'),E('span','',PRODUCT),A('collapse',add(E('span','m-ib'),I('panel')))));
  add(s,A('new',add(E('div','m-si'),I('edit'),E('span','','New Chat'))),A('search',add(E('div','m-si'),I('search'),E('span','','Search'))));
  if(ft('notes'))add(s,A('notes',add(E('div','m-si'),I('book'),E('span','','Notes'))));
  if(anyWs())add(s,A('workspace',add(E('div','m-si'),I('grid'),E('span','','Workspace'))));
  add(s,A('models',add(E('div','m-sg'),E('span','','Models'))));
  if(ft('channels'))add(s,A('channels',add(E('div','m-sg'),E('span','','Channels'),add(E('span','m-end'),I('plus')))));
  if(ft('folders'))add(s,A('folders',add(E('div','m-sg'),E('span','','Folders'),add(E('span','m-end'),I('plus')))));
  add(s,A('chats',add(E('div','m-sg'),E('span','','Chats'),I('right'),add(E('span','m-end'),I('dots')))));
  if(extraChats)[L('Plan de projet','Project plan'),L('E-mail au fournisseur','Email to supplier')].forEach(function(t){add(s,E('div','m-sc',t));});
  return s;}

function notesList(){var p=E('div','m-page');
  add(p,add(E('div','m-nh'),A('count',add(E('span','t'),'Notes',E('span','','5'))),A('create',add(E('span','m-create'),E('span','','Create'),add(E('i'),I('down'))))));
  add(p,add(E('div','m-ns'),A('nsearch',add(E('span','s'),I('search'),E('span','','Search Notes'))),A('filters',add(E('span','f'),add(E('span'),'All',I('down')),add(E('span'),'Write',I('down')),add(E('span'),'List',I('down'))))));
  add(p,A('sort',add(E('div','m-th'),E('span','','Title'),E('span','','Updated at'))),E('div','m-tg','Previous 30 days'));
  [[L('Idées et inspiration','Ideas & inspiration'),'12 days ago'],[L('Compte rendu de réunion','Meeting minutes'),'12 days ago'],[L('Plan du projet','Project outline'),'a month ago']].forEach(function(r,k){
    var row=add(E('div','m-tr'),E('span','',r[0]),E('small','',r[1]),add(E('span','r'),E('span','',L('Vous','You')),add(E('span'),I('dots'))));add(p,k===0?A('row',row):row);});
  return p;}
function noteEditor(menu){var g=E('div','m-editor'),ed=E('div','m-ed'),top=E('div','m-edt'),tools=E('div','tools');
  add(tools,A('undo',add(E('span','m-ib'),I('undo'))),add(E('span','m-ib'),I('redo')),A('nchat',add(E('span','m-ib'),I('bubble'))),A('nrec',add(E('span','m-ib'),I('mic'))),A('nmore',add(E('span','m-ib'),I('dots'))),A('access',add(E('span','m-acc'),I('lock'),E('span','','Access'))));
  add(top,A('ntitle',E('span','d','2026-09-16')),tools);add(ed,top,A('nmeta',E('div','m-meta','Today at 11:30 AM   0 words 0 characters')),A('nbody',E('div','m-body','Write something...')));
  if(menu){var m=E('div','m-menu right');m.style.top='42px';m.style.right='80px';m.style.minWidth='170px';
    [['m-download','download','Download'],['m-upload','upcloud','Upload files'],['m-share','share','Share'],['m-pin','pin','Pin to Sidebar'],['m-delete','trash','Delete']].forEach(function(x){add(m,mItem(x[0],x[1],x[2]));});add(ed,m);}
  var c=E('div','m-edchat');add(c,add(E('div','h'),E('span','','Chat'),I('x')));
  var sp=A('nprompts',E('div','m-sp'));add(sp,E('small','','Suggested prompts'),E('span','','Enhance this note and update it.'),E('span','','Summarize this note.'),E('span','','Extract action items from this note.'),E('span','','Rewrite the selected text.'));
  var mini=A('ncomposer',E('div','m-mini'));add(mini,E('div','ph','Send a Message'),add(E('div','m-row'),add(E('span','m-ib'),I('plus')),add(E('span','m-ib'),I('integ')),add(E('span','m-model'),E('span','',PRODUCT),I('down')),E('span','m-grow'),add(E('span','m-ib'),I('mic')),add(E('span','up'),I('arrowr'))));
  add(c,sp,mini);add(g,ed,c);return g;}

function folderModal(){var m=E('div','m-modal');add(m,add(E('div','h'),E('span','','Create Folder'),I('x')));
  add(m,A('fname',add(E('div','m-fl'),E('label','','Folder Name'),E('div','v','Enter folder name'))));
  add(m,A('fbg',add(E('div','m-fl m-flr'),E('label','','Folder Background Image'),E('span','','Upload'))),E('div','m-hr'));
  add(m,A('fprompt',add(E('div','m-fl'),E('label','','System Prompt'),E('div','v',L('Ex. : Tu es un tuteur de statistiques. Réponds avec des exemples simples.','e.g. You are a statistics tutor. Answer with simple examples.')))));
  add(m,A('fknow',add(E('div','m-fl'),add(E('div','m-kn'),E('span','','Knowledge'),E('span','','Select Knowledge'),E('span','','Upload')),E('div','m-knnote','To attach knowledge base here, add them to the "Knowledge" workspace first.'))));
  add(m,add(E('div','m-fbf'),E('span'),A('fsave',E('span','m-save','Save'))));return m;}

function channelMock(){var g=E('div','m-chan'),s=E('div','m-side');
  add(s,add(E('div','m-sh1'),E('span','m-oi','OI'),E('span','',PRODUCT)));
  add(s,A('cplus',add(E('div','m-sg'),E('span','','Channels'),add(E('span','m-end'),I('plus')))));
  add(s,A('clist',add(E('div','m-si'),I('hash'),E('span','',L('projet-alpha','project-alpha')))),add(E('div','m-si'),I('hash'),E('span','',L('annonces','announcements'))));
  var mn=E('div','m-cmain');add(mn,add(E('div','m-chead'),add(E('span'),'# ',L('projet-alpha','project-alpha')),A('cpin',add(E('span','m-ib'),I('pin')))));
  var m1=add(E('div','m-msg'),E('span','m-av','AL'),add(E('div'),add(E('div','n'),'Alice',E('small','','10:02')),E('div','x',L('J’ai déposé le plan du rapport, vos avis ?','I shared the report outline, thoughts?')),A('creact',add(E('div','m-under'),add(E('span','m-pill'),'👍 3'),add(E('span','m-pill'),I('smile'))))));
  var m2=add(E('div','m-msg'),E('span','m-av','YB'),add(E('div'),add(E('div','n'),'Yanis',E('small','','10:05')),A('cmention',add(E('div','x'),E('span','m-mention','@'+PRODUCT),L(' résume les décisions de ce fil en 3 points.',' summarize the decisions in this thread in 3 points.'))),A('cthread',add(E('div','m-under'),add(E('span','m-pill'),I('reply'),E('span','',L('2 réponses','2 replies')))))));
  add(mn,m1,m2,A('cinput',add(E('div','m-cin'),I('plus'),E('span','m-grow','Send a Message'),I('arrowr'))));
  add(g,s,mn);return g;}

function doDont(){var c=E('div','m-cols'),a=E('div','m-card'),b=E('div','m-card');
  add(a,E('h3','',L('À faire','Do')));[L('Vérifier chiffres, dates et citations','Check figures, dates and quotes'),L('Ouvrir les sources affichées','Open the cited sources'),L('Préciser votre demande si besoin','Refine your request when needed'),L('Donner votre avis sur les réponses','Rate the answers')].forEach(function(t){add(a,add(E('div','m-rule ok'),I('okc'),E('span','',t)));});
  add(b,E('h3','',L('À éviter','Avoid')));[L('Mots de passe, codes, identifiants','Passwords, codes, credentials'),L('Données personnelles de tiers','Other people’s personal data'),L('Documents confidentiels non autorisés','Unauthorized confidential documents'),L('Copier une réponse sans la relire','Copying an answer without reading it')].forEach(function(t){add(b,add(E('div','m-rule no'),I('noc'),E('span','',t)));});
  add(c,a,b);return c;}

function userMenu(){var m=E('div','m-user');
  var h=A('uhead',add(E('div','m-uh'),E('span','m-face',L('V','Y')),E('span','',L('Votre nom','Your name')),E('span','dot'),ADMIN?E('small','','5'):null));add(m,h);
  add(m,mItem('ustatus','smile','Update your status'),E('div','m-sep'));
  if(anyWs())add(m,mItem('uworkspace','grid','Workspace'));
  if(ft('notes'))add(m,mItem('unotes','book','Notes'));
  if(ft('calendar'))add(m,mItem('ucalendar','calendar','Calendar'));
  if(ft('automations'))add(m,mItem('uauto','clock','Automations'));
  if(ADMIN)add(m,mItem('uplay','code','Playground'));
  add(m,E('div','m-sep'));
  if(ADMIN)add(m,mItem('uadmin','user','Admin Panel'));
  add(m,mItem('usettings','gear','Settings'),mItem('usignout','logout','Sign Out'));return m;}
function calendarPage(){var g=E('div','m-calapp'),sd=E('div','m-calside'),mn=E('div','m-calmain');
  add(sd,A('newevent',add(E('div','m-newev'),I('plus'),E('span','','New Event'))));
  var mini=A('mini',E('div','m-mini'));['M','T','W','T','F','S','S'].forEach(function(d){add(mini,E('span','',d));});for(var d=1;d<=30;d++){var c=E('span','',d);if(d===16)c.className='t';add(mini,c);}add(sd,mini);
  add(sd,A('calplus',add(E('div','m-calh'),E('span','','Calendars'),I('plus'))));
  add(sd,A('personal',add(E('div','m-calrow'),add(E('span','m-sq'),null),E('span','','Personal'))));sd.lastChild.firstChild.style.background='#3B82F6';
  if(ft('automations')){add(sd,A('sched',add(E('div','m-calrow'),E('span','m-sq'),E('span','','Scheduled Tasks'))));sd.lastChild.firstChild.style.background='#8B5CF6';}
  add(mn,add(E('div','m-calbar'),A('today',E('span','m-btn2','Today')),A('nav',add(E('span','nav'),I('left'),I('right'))),E('h4','','September 2026'),A('view',add(E('span','view'),E('span','','Month'),I('down')))));
  var gr=E('div','m-grid7');['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(function(d){add(gr,E('div','hd',d));});
  for(var i=0;i<28;i++){var day=i+1,cell=E('div','',day);
    if(day%7===1)add(cell,E('span','m-ev g',L('Point équipe 9:00','Team standup 9:00')));
    if(day===16)add(cell,A('event',E('span','m-ev b',L('Réunion projet 10:00','Project meeting 10:00'))));
    if(ft('automations')&&(day===18||day===25))add(cell,A('autoev',E('span','m-ev v',L('Résumé hebdo 8:00','Weekly digest 8:00'))));
    add(gr,cell);}
  add(mn,gr);add(g,sd,mn);return g;}
function eventForm(){var f=E('div','m-form');add(f,add(E('div','h'),E('span','','New Event'),I('x')));
  function row(id,icon,lbl,val,ph){return A(id,add(E('div','m-f'),I(icon),E('span','lbl',lbl),E('span','val'+(ph?' ph':''),val)));}
  add(f,row('etitle','edit','Title',L('Réunion de projet','Project meeting')),row('ecal','calendar','Calendar','Personal'),
    A('ewhen',add(E('div','m-f'),I('clock'),E('span','lbl','When'),E('span','val','16/09/2026  10:00 – 11:00'),E('span','',L('All day','All day')),E('span','m-sw'))),
    row('eloc','mappin','Location',L('Salle 204','Room 204')),row('erepeat','repeat','Repeat','Weekly'),row('eremind','bell','Reminder','10 minutes before'),row('edesc','notefile','Description',L('Ordre du jour…','Agenda…'),true));
  add(f,add(E('div','m-fbf'),E('span'),A('ecreate',E('span','m-save','Create'))));return f;}
function autoList(){var p=E('div','m-page');
  add(p,add(E('div','m-nh'),add(E('span','t'),'Automations',E('span','','3')),A('acreate',add(E('span','m-split'),E('span','','Create'),add(E('i'),I('down'))))));
  add(p,add(E('div','m-ns'),A('asearch',add(E('span','s'),I('search'),E('span','','Search'))),A('afilter',add(E('span','f'),add(E('span'),'All',I('down'))))));
  [[L('Résumé hebdomadaire des actualités','Weekly news digest'),L('Chaque lundi à 08:00 · prochaine : 21 sept.','Every Monday at 08:00 · next: 21 Sep'),true],[L('Rappel quotidien','Daily reminder'),L('Chaque jour à 18:00','Every day at 18:00'),true],[L('Veille concurrentielle','Market watch'),L('En pause','Paused'),false]].forEach(function(r,k){
    var row=add(E('div','m-rowx'),I('clock'),add(E('span'),r[0],E('small','',r[1])),k===0?A('aswitch',E('span','m-sw'+(r[2]?' on':''))):E('span','m-sw'+(r[2]?' on':'')));add(p,k===0?A('arow',row):row);});
  return p;}
function autoEditor(){var p=E('div','m-page');
  add(p,add(E('div','m-nh'),E('span','t',L('Résumé hebdomadaire des actualités','Weekly news digest')),add(E('div','m-tbtns'),A('arun',add(E('span','run'),I('play'),E('span','','Run now'))),A('apause',add(E('span'),I('pause'),E('span','','Pause'))),A('adelete',add(E('span'),I('trash'),E('span','','Delete'))))));
  function row(id,icon,lbl,node){return A(id,add(E('div','m-f'),I(icon),E('span','lbl',lbl),node));}
  add(p,row('atitle','edit','Title',E('span','val',L('Résumé hebdomadaire des actualités','Weekly news digest'))),
    row('ainstr','notefile','Instructions',E('span','val',L('Recherche les actualités du secteur de la semaine et résume-les en 5 points avec les liens.','Search this week’s industry news and summarize it in 5 points with links.'))),
    row('amodel','layers','Model',E('span','val',PRODUCT)));
  var seg=E('span','m-seg');['Once','Hourly','Daily','Weekly','Monthly','Custom'].forEach(function(x){add(seg,E('span',x==='Weekly'?'sel':'',x));});add(p,row('asched','repeat','Schedule',seg));
  if(ft('folders'))add(p,row('afolder','folder','Folder',E('span','val',L('Veille','Watch'))));
  var logs=A('alogs',E('div'));add(logs,E('div','m-tg','Execution logs'),add(E('div','m-log'),add(E('span','ok'),I('okc')),E('span','','success · 14 Sep 08:00'),E('a','',L('Ouvrir le chat','Open chat'))),add(E('div','m-log'),add(E('span','err'),I('noc')),E('span','','error · 7 Sep 08:00'),E('a','',L('Détails','Details'))));add(p,logs);
  return p;}
function builtinTools(){var w=E('div','m-resp');
  add(w,E('div','m-bubble',L('Rappelle-moi vendredi à 15h de relancer le prestataire.','Remind me on Friday at 3pm to follow up with the supplier.')));w.firstChild.style.cssText='margin-left:auto;width:max-content;max-width:80%;padding:8px 12px;border:1px solid var(--border);border-radius:16px;background:var(--surface);font-size:13px';
  add(w,A('toolcall',add(E('div','m-tc'),I('check'),E('span','','create_calendar_event'),I('down'))));w.lastChild.style.marginTop='12px';
  add(w,E('p','',L('C’est noté : événement créé vendredi à 15:00, rappel 10 minutes avant.','Done: event created on Friday at 15:00 with a reminder 10 minutes before.')));w.lastChild.style.cssText='margin:8px 0 0;font-size:13.5px';
  var bt=E('div','m-bt');builtinCats().forEach(function(c){add(bt,mItem(c.a,c.i,c.k));});add(w,bt);return w;}
function builtinCats(){var c=[];
  c.push({a:'bt-time',i:'clock',k:L('Date et heure','Date & time'),t:L('Connaît la date et calcule des délais : « dans 3 semaines », « lundi prochain ».','Knows the date and computes delays: “in 3 weeks”, “next Monday”.')});
  if(ft('web_search'))c.push({a:'bt-web',i:'globe',k:L('Recherche web','Web search'),t:L('Cherche et lit des pages quand Web Search est activé.','Searches and reads pages when Web Search is on.')});
  if(av('knowledge')||ws('knowledge'))c.push({a:'bt-know',i:'db',k:L('Documents','Knowledge'),t:L('Parcourt et lit les bases de documents accessibles.','Browses and reads accessible document collections.')});
  if(ft('memories'))c.push({a:'bt-mem',i:'brain',k:L('Mémoire','Memory'),t:L('Retient ou retrouve une préférence : « souviens-toi que je travaille sur le projet Alpha ».','Saves or recalls a preference: “remember I work on Project Alpha”.')});
  if(ft('notes'))c.push({a:'bt-notes',i:'book',k:'Notes',t:L('Recherche, lit, crée et met à jour vos notes.','Searches, reads, creates and updates your notes.')});
  c.push({a:'bt-chats',i:'history',k:L('Conversations','Chats'),t:L('Retrouve une ancienne conversation : « qu’avions-nous décidé sur le budget ? ».','Finds a past chat: “what did we decide about the budget?”.')});
  if(ft('channels'))c.push({a:'bt-chan',i:'hash',k:L('Canaux','Channels'),t:L('Cherche dans les canaux dont vous êtes membre.','Searches channels you belong to.')});
  if(ft('calendar'))c.push({a:'bt-cal',i:'calendar',k:L('Calendrier','Calendar'),t:L('Crée, déplace ou supprime des événements et consulte votre semaine.','Creates, moves or deletes events and checks your week.')});
  if(ft('automations'))c.push({a:'bt-auto',i:'repeat',k:L('Automatisations','Automations'),t:L('Crée, liste, met en pause ou supprime une tâche planifiée.','Creates, lists, pauses or deletes a scheduled task.')});
  if(ft('image_generation'))c.push({a:'bt-img',i:'image',k:L('Images','Images'),t:L('Génère une image à partir de la conversation.','Generates an image from the conversation.')});
  if(ft('code_interpreter'))c.push({a:'bt-code',i:'terminal',k:L('Exécution de code','Code execution'),t:L('Exécute du code pour calculer ou analyser des données.','Runs code to calculate or analyze data.')});
  c.push({a:'bt-tasks',i:'tasks',k:L('Liste de tâches','Task list'),t:L('Pour une demande en plusieurs étapes, l’assistant tient une liste de tâches et la coche au fur et à mesure.','For multi-step requests, the assistant keeps a task list and ticks items off.')});
  return c;}

/* ============ Steps ============ */
function N(a,i,k,t){return {a:a,i:i,k:k,t:t};}
function build(){
 steps=[];var CH=null;
 function S(o){o.ch=CH;steps.push(o);}
 function chapter(name,icon,d){CH=name;if(!CHD[name])CHD[name]={i:icon,d:d};}

 chapter(L('Accueil','Home'),'map',L('Présentation du guide','About this guide'));
 if(on('show_cover'))S({cover:true,title:L('Guide d’utilisation de ','How to use ')+PRODUCT,desc:L('Bienvenue ! Ce guide interactif vous apprend à utiliser la plateforme, écran par écran. Il montre uniquement les fonctions disponibles pour votre compte.','Welcome! This interactive guide teaches you how to use the platform, screen by screen. It only shows the features available on your account.'),notes:[]});
 chapter(L('Démarrer','Start'),'bolt',L('L’écran d’accueil et les suggestions','Home screen and suggestions'));
 if(on('show_welcome'))S({title:L('Bienvenue sur ','Welcome to ')+PRODUCT,desc:L('Ce guide vous montre l’interface réelle, élément par élément. Cliquez sur un élément de l’écran ou sur son explication pour le mettre en évidence.','This guide walks you through the real interface, one element at a time. Click any element on the screen or its explanation to highlight it.'),
   mock:function(){return home({});},stage:'',
   notes:[N('title','layers',L('Assistant actif','Active assistant'),L('Le nom en haut indique l’assistant qui va répondre.','The name at the top shows which assistant will answer.')),
     N('input','edit',L('Zone de saisie','Message box'),L('Écrivez votre demande ici. Entrée pour envoyer, Maj + Entrée pour aller à la ligne.','Type your request here. Enter sends, Shift + Enter adds a new line.')),
     N('sugg','bolt','Suggested',L('Des idées de questions prêtes à l’emploi. Cliquez sur l’une d’elles pour la placer dans la zone de saisie.','Ready-made question ideas. Click one to place it in the message box.'))],
   tip:L('Les suggestions changent selon l’assistant choisi : elles montrent ce qu’il sait bien faire.','Suggestions change with the selected assistant: they show what it does best.')});

 chapter(L('Zone de saisie','Message box'),'edit',L('Le bouton + et ses menus','The + button and its menus'));
 var cn=[N('plus','plus','+',L('Ajouter du contexte : fichiers, capture, page web, notes, documents, anciennes conversations.','Add context: files, capture, web page, notes, documents, past chats.')),
   N('integ','integ',L('Intégrations','Integrations'),L('Activer des outils et fonctions pour cette conversation, comme la recherche web.','Turn on tools and features for this chat, such as web search.')),
   N('model','layers',L('Sélecteur d’assistant','Assistant selector'),L('Choisir l’assistant ou le modèle qui répond.','Choose the assistant or model that answers.'))];
 if(ft('stt'))cn.push(N('mic','mic',L('Dictée','Dictation'),L('Parlez : votre voix est transcrite en texte, que vous relisez avant d’envoyer.','Speak: your voice becomes text you can review before sending.')));
 cn.push(N('voice','wave',L('Envoyer / mode vocal','Send / voice mode'),L('Quand la zone est vide, ce bouton lance le mode vocal. Dès que vous écrivez, il devient le bouton d’envoi.','When the box is empty, this button starts voice mode. Once you type, it becomes the send button.')));
 if(on('show_composer'))S({title:L('Les commandes de la zone de saisie','The message box controls'),desc:L('Tout part de cette zone. Voici à quoi sert chaque bouton.','Everything starts here. Here is what each button does.'),mock:function(){return composer({});},stage:'center',notes:cn});

 var pi=plusItems(),pn=[];
 pi.forEach(function(x){var t={
   toolperm:L('Définit si l’assistant peut utiliser les outils librement (Full access) ou doit vous demander avant chaque action (Ask for approval). La flèche ouvre ce choix.','Sets whether the assistant can use tools freely (Full access) or must ask before each action (Ask for approval). The arrow opens this choice.'),
   upload:L('Envoyer un fichier depuis votre ordinateur : PDF, Word, Excel, image. Vous pouvez aussi le glisser dans la zone de saisie.','Send a file from your computer: PDF, Word, Excel, image. You can also drag it into the message box.'),
   capture:L('Prendre une capture d’écran d’une fenêtre ou de l’écran et la joindre au message.','Take a screenshot of a window or your screen and attach it.'),
   webpage:L('Coller l’adresse d’une page web : son texte est lu et ajouté comme contexte.','Paste a web page address: its text is read and added as context.'),
   attfiles:L('La flèche ouvre la liste des fichiers que vous avez déjà envoyés, pour les réutiliser sans les téléverser à nouveau.','The arrow opens files you already uploaded, so you can reuse them without uploading again.'),
   attnotes:L('La flèche liste vos Notes. Le contenu complet de la note est ajouté au message.','The arrow lists your Notes. The full note content is added to the message.'),
   attknow:L('La flèche liste les bases de documents auxquelles vous avez accès. L’assistant cherche la réponse dedans.','The arrow lists document collections you can access. The assistant searches them for the answer.'),
   refchats:L('La flèche liste vos anciennes conversations pour vous appuyer sur un échange précédent.','The arrow lists your past chats so you can build on an earlier exchange.')}[x.id];
   pn.push(N(x.id,x.i,x.l+(x.arrow?'  ›':''),t));});
 if(on('show_plus_menu'))S({title:L('Le bouton + : ajouter du contexte','The + button: add context'),desc:L('Cliquez sur + pour ouvrir ce menu. Les lignes avec une flèche › ouvrent une seconde liste : cliquez dessus ici pour la voir.','Click + to open this menu. Rows with an arrow › open a second list: click them here to see it.'),
   stage:'tall',notes:pn,
   mock:function(){var c=composer({menu:plusMenu()});var sub=active&&subFor(active==='perm-full'||active==='perm-ask'?'toolperm':active);if(sub)add(c,sub);return c;},
   tip:L('Plus le contexte est précis, meilleure est la réponse. N’ajoutez que ce qui est utile à la question.','Precise context gives better answers. Only add what the question needs.')});

 if(hasTools&&on('show_tool_permissions'))S({title:L('Permissions des outils','Tool permissions'),desc:L('Dans + › Tool Permissions, décidez du niveau de contrôle quand l’assistant utilise un outil.','In + › Tool Permissions, decide how much control you keep when the assistant uses a tool.'),
   stage:'tall',start:'perm-full',
   mock:function(){var c=composer({menu:plusMenu()});add(c,subFor('toolperm'));return c;},
   notes:[N('perm-full','check','Full access',L('L’assistant lance les outils dont il a besoin sans vous interrompre. Idéal pour les recherches et lectures.','The assistant runs the tools it needs without interrupting you. Best for searches and reading.')),
     N('perm-ask','shield','Ask for approval',L('Avant chaque action, l’assistant affiche ce qu’il veut faire et attend votre accord. Recommandé pour tout ce qui envoie, modifie ou supprime.','Before each action, the assistant shows what it wants to do and waits for your approval. Recommended for anything that sends, changes or deletes.'))],
   warn:L('En cas de doute, choisissez Ask for approval : vous gardez la main sur chaque action.','If in doubt, choose Ask for approval: you stay in control of every action.')});

 chapter(L('Intégrations','Integrations'),'integ',L('Outils, recherche web, fonctions','Tools, web search, features'));
 var ii=integItems(),inn=[];
 ii.forEach(function(x){var t;
   if(x.id==='tools')t=L('La flèche › ouvre la liste de vos outils. Activez uniquement ceux utiles à la conversation en cours.','The arrow › opens your tools. Turn on only the ones this conversation needs.');
   else if(x.id==='web')t=L('Cherche sur Internet et cite ses sources. À activer pour toute information récente : actualités, dates, règlements.','Searches the internet and cites sources. Turn on for anything recent: news, dates, regulations.');
   else if(x.id==='code')t=L('Exécute du code pour calculer, analyser un fichier de données ou produire un graphique.','Runs code to calculate, analyze a data file or build a chart.');
   else if(x.id==='imagegen')t=L('Crée une image à partir de votre description.','Creates an image from your description.');
   else t=(x.desc||L('Fonction ajoutée par votre organisation. L’interrupteur l’active pour cette conversation.','A feature added by your organization. The switch turns it on for this chat.'))+' '+L('L’icône réglages permet d’ajuster ses options personnelles.','The settings icon adjusts its personal options.');
   inn.push(N(x.id,x.i,x.l+(x.arrow?'  ›':''),t));});
 if(inn.length&&on('show_integrations'))S({title:L('Le bouton Intégrations','The Integrations button'),desc:L('Le bouton à côté de + regroupe les outils et fonctions. Chaque interrupteur s’applique à la conversation en cours.','The button next to + groups tools and features. Each switch applies to the current conversation.'),
   stage:'tall',notes:inn,mock:function(){var c=composer({menu:integMenu()});if(active==='tools'||active==='tool-row')add(c,toolsSub());return c;},
   tip:L('N’activez que ce dont vous avez besoin : trop d’outils actifs ralentit et disperse les réponses.','Only turn on what you need: too many active tools slows and scatters answers.')});
 if(hasTools&&on('show_tools'))S({title:L('Utiliser un outil','Using a tool'),desc:L('Intégrations › Tools › ouvre la liste. Activez l’outil, puis demandez simplement ce que vous voulez : l’assistant l’utilisera au bon moment.','Integrations › Tools › opens the list. Turn the tool on, then just ask: the assistant uses it when needed.'),
   stage:'tall',start:'tool-row',mock:function(){var c=composer({menu:integMenu()});add(c,toolsSub());return c;},
   notes:[N('tool-row','wrench',L('Interrupteur d’outil','Tool switch'),L('Activé pour cette conversation seulement. Désactivez-le quand vous n’en avez plus besoin.','On for this conversation only. Turn it off when you no longer need it.')),
     N('tools','left',L('Retour','Back'),L('La flèche à gauche revient au menu Intégrations.','The left arrow returns to the Integrations menu.'))]});
 if(ft('web_search')&&on('show_web_search'))S({title:L('Recherche web','Web search'),desc:L('Intégrations › Web Search. Posez ensuite votre question : la réponse affiche les sources consultées.','Integrations › Web Search. Then ask your question: the answer shows the sources it used.'),
   stage:'tall',start:'web',mock:function(){return composer({text:L('Quelles sont les règles en vigueur en 2026 ? Cite la source officielle.','What rules apply in 2026? Cite the official source.'),menu:integMenu()});},
   notes:[N('web','globe','Web Search',L('Activez-le avant d’envoyer. Il reste actif pour la conversation.','Turn it on before sending. It stays on for the conversation.')),N('input','edit',L('Votre question','Your question'),L('Précisez la période, le pays et le type de source souhaité.','Specify the period, country and type of source you want.'))],
   example:L('Trouve les informations officielles les plus récentes sur ce sujet et donne le lien de chaque source.','Find the latest official information on this topic and give the link to each source.'),
   warn:L('Sans recherche web, l’assistant répond avec des connaissances qui peuvent être anciennes.','Without web search, the assistant answers from knowledge that may be outdated.')});

 chapter(L('Assistants','Assistants'),'layers',L('Choisir le bon assistant','Choosing the right assistant'));
 if(on('show_models'))S({title:L('Choisir l’assistant','Choosing the assistant'),desc:L('Cliquez sur le nom à droite de la zone de saisie pour ouvrir la liste.','Click the name on the right of the message box to open the list.'),stage:'tall',start:'minfo',
   mock:function(){var c=composer({}),m=E('div','m-menu right');m.style.minWidth='300px';
     add(m,A('msearch',add(E('div','m-subsearch'),I('search'),E('span','m-grow','Search a model'),E('span','','All'),I('down'))));
     add(m,A('minfo',mItem(null,'layers',PRODUCT,[I('info'),I('check')])),mItem(null,'layers',L('Modèle généraliste','General model')),mItem(null,'layers',L('Modèle raisonnement','Reasoning model')));
     add(m,A('mdefault',add(E('div','m-it'),add(E('span','m-end'),E('span','','Set as default')))));add(c,m);return c;},
   notes:[N('msearch','search','Search a model',L('Tapez un nom pour filtrer. « All » filtre par catégorie.','Type a name to filter. “All” filters by category.')),
     N('minfo','info',L('Icône d’information','Info icon'),L('Survolez-la pour lire le rôle de l’assistant avant de le choisir.','Hover it to read what the assistant is for before choosing it.')),
     N('mdefault','check','Set as default',L('Utiliser cet assistant par défaut pour vos nouvelles conversations.','Use this assistant by default for new chats.'))],
   tip:L('Un assistant spécialisé répond à partir de ses documents. Pour résumer vos fichiers ou chercher sur Internet, prenez un modèle généraliste.','A specialized assistant answers from its own documents. To summarize your files or search the web, pick a general model.')});
 if(on('show_suggestions'))S({title:L('Les questions suggérées','Suggested questions'),desc:L('Sous la zone de saisie d’une nouvelle conversation, des suggestions montrent des usages typiques de l’assistant.','Below the message box of a new chat, suggestions show typical uses of the assistant.'),start:'sugg',
   mock:function(){return home({});},notes:[N('sugg','bolt','Suggested',L('Cliquez sur une suggestion : elle remplit la zone de saisie. Complétez-la avec vos détails avant d’envoyer.','Click a suggestion: it fills the message box. Add your details before sending.')),N('title','layers',L('Liées à l’assistant','Tied to the assistant'),L('Changez d’assistant pour voir d’autres suggestions.','Switch assistants to see other suggestions.'))]});

 chapter(L('Réponses','Answers'),'refresh',L('Les actions sous chaque réponse','Actions under each answer'));
 var an=[N('edit','pencil',L('Modifier','Edit'),L('Corrige le texte de la réponse, par exemple avant de la copier.','Edit the answer text, for example before copying it.')),
   N('copy','clipboard',L('Copier','Copy'),L('Copie la réponse avec sa mise en forme.','Copies the answer with its formatting.')),
   N('speak','speaker',L('Lire à voix haute','Read aloud'),L('Écoutez la réponse.','Listen to the answer.')),
   N('ginfo','info',L('Informations','Info'),L('Détails de génération : durée, longueur.','Generation details: time, length.')),
   N('good','tup',L('Bonne réponse','Good response'),L('Ouvre le formulaire d’avis positif. Voir chapitre Avis.','Opens the positive rating form. See the Feedback chapter.')),
   N('bad','tdown',L('Mauvaise réponse','Bad response'),L('Ouvre le formulaire pour signaler un problème.','Opens the form to report a problem.')),
   N('continue','play',L('Continuer','Continue'),L('Si la réponse s’arrête en cours de route, l’assistant reprend là où il s’était arrêté.','If the answer stops midway, the assistant picks up where it left off.')),
   N('regen','refresh',L('Régénérer','Regenerate'),L('Produit une nouvelle version. Vous pouvez naviguer entre les versions.','Produces a new version. You can switch between versions.'))];
 ACTIONS.slice(0,4).forEach(function(a,k){an.push(N('act-'+k,k%2?'spark':'doc',a.name,(a.description||L('Action ajoutée par votre organisation.','Action added by your organization.'))+' '+L('Cliquez pour l’appliquer à cette réponse.','Click to apply it to this answer.')));});
 if(on('show_response_actions'))S({title:L('Les actions sous chaque réponse','Actions under each answer'),desc:L('Survolez une réponse : cette barre apparaît. Chaque icône a un rôle précis.','Hover an answer: this bar appears. Each icon has a specific role.'),stage:'center',
   mock:function(){var r=E('div','m-resp'),ls=E('div','m-lines');['100%','92%','70%'].forEach(function(w){var l=E('div','m-l');l.style.width=w;add(ls,l);});add(r,ls,actionsRow());return r;},
   notes:an,info:ACTIONS.length?L('Les dernières icônes sont des actions propres à votre compte. Survolez-les pour voir leur nom.','The last icons are actions specific to your account. Hover them to see their name.'):null});

 chapter(L('Avis','Feedback'),'tup',L('Noter les bonnes et mauvaises réponses','Rating good and bad answers'));
 if(on('show_feedback'))S({title:L('Donner un avis positif','Giving positive feedback'),desc:L('👍 ouvre ce formulaire. Vos avis aident votre organisation à choisir et améliorer les assistants.','👍 opens this form. Your ratings help your organization choose and improve assistants.'),stage:'tall',start:'score',
   mock:function(){var r=E('div','m-resp');add(r,actionsRow('good'),feedback('good'));return r;},
   notes:[N('score','tup',L('Note de 6 à 10','Score 6 to 10'),L('Après 👍, seules les notes hautes sont disponibles. 10 = parfaite.','After 👍, only high scores are available. 10 = perfect.')),
     N('reasons','check','Why?',L('Choisissez ce qui était vraiment bien : exactitude, respect des consignes, clarté…','Pick what was actually good: accuracy, following instructions, clarity…')),
     N('details','edit',L('Détails','Details'),L('Une phrase suffit pour dire ce qui vous a aidé.','One sentence is enough to say what helped.')),
     N('tags','hash',L('Étiquettes','Tags'),L('Classez l’avis par sujet, par exemple « recherche » ou « rédaction ».','Categorize by topic, e.g. “search” or “writing”.')),
     N('save','check','Save',L('Enregistre l’avis. Il n’est pas visible par les autres utilisateurs.','Saves the rating. It is not visible to other users.'))]});
 if(on('show_feedback'))S({title:L('Signaler une mauvaise réponse','Reporting a bad answer'),desc:L('👎 ouvre la version négative. Un avis précis est bien plus utile qu’une simple note.','👎 opens the negative version. A precise rating is far more useful than a score alone.'),stage:'tall',start:'reasons',
   mock:function(){var r=E('div','m-resp');add(r,actionsRow('bad'),feedback('bad'));return r;},
   notes:[N('score','tdown',L('Note de 1 à 5','Score 1 to 5'),L('1 = inutilisable, 5 = moyen.','1 = unusable, 5 = average.')),
     N('reasons','warn','Why?',L('Not factually correct : erreur de fait. Didn’t fully follow instructions : consigne ignorée. Refused when it shouldn’t have : refus injustifié. Too verbose : trop long.','Not factually correct: wrong facts. Didn’t fully follow instructions: ignored a request. Refused when it shouldn’t have: unjustified refusal. Too verbose: too long.')),
     N('details','edit',L('Détails','Details'),L('Indiquez ce qui était faux et, si possible, la bonne information.','Say what was wrong and, if you can, the correct information.')),
     N('save','check','Save',L('Enregistrez, puis reformulez ou régénérez pour obtenir une meilleure réponse.','Save, then rephrase or regenerate for a better answer.'))],
   compare:[L('« Nul. »','“Bad.”'),L('« La date limite indiquée est le 15 mars, mais le site officiel indique le 31 mars. »','“It says the deadline is 15 March, but the official site says 31 March.”')]});

 chapter(L('Barre latérale','Sidebar'),'panel',L('Naviguer dans vos espaces','Moving between spaces'));
 var sn=[N('collapse','panel',L('Masquer la barre','Hide sidebar'),L('Replie la barre pour gagner de la place. Cliquez à nouveau pour la rouvrir.','Collapses the sidebar for more room. Click again to reopen.')),
   N('new','edit','New Chat',L('Démarre une conversation vide. Changez de conversation quand vous changez de sujet.','Starts an empty chat. Start a new one when you change topic.')),
   N('search','search','Search',L('Retrouve une conversation par mot-clé.','Finds a chat by keyword.'))];
 if(ft('notes'))sn.push(N('notes','book','Notes',L('Vos documents personnels, avec un assistant intégré. Voir chapitre Notes.','Your personal documents with a built-in assistant. See the Notes chapter.')));
 if(anyWs())sn.push(N('workspace','grid','Workspace',L('Créer et gérer des assistants, documents ou modèles de demandes, selon vos droits.','Create and manage assistants, documents or prompt templates, depending on your rights.')));
 sn.push(N('models','layers','Models',L('Vos assistants épinglés, pour les ouvrir en un clic.','Your pinned assistants, one click away.')));
 if(ft('channels'))sn.push(N('channels','hash','Channels  +',L('Espaces de discussion partagés. Le + crée un canal si vous en avez le droit.','Shared discussion spaces. The + creates a channel if you are allowed.')));
 if(ft('folders'))sn.push(N('folders','folder','Folders  +',L('Regroupez vos conversations par projet. Le + crée un dossier.','Group chats by project. The + creates a folder.')));
 sn.push(N('chats','history','Chats  ›  ⋯',L('L’historique. La flèche replie la liste ; le menu ⋯ propose d’archiver ou supprimer. Clic droit sur une conversation pour l’épingler, la renommer ou la déplacer.','Your history. The arrow collapses the list; ⋯ offers archive or delete. Right-click a chat to pin, rename or move it.')));
 if(on('show_sidebar_navigation'))S({title:L('La barre latérale','The sidebar'),desc:L('À gauche, tout pour naviguer entre vos conversations et espaces.','On the left, everything to move between chats and spaces.'),stage:'flush center',
   mock:function(){var a=E('div','m-app');add(a,sidebar(true),E('div','m-mainpane',L('Conversation','Conversation')));return a;},notes:sn});

 chapter(L('Menu utilisateur','User menu'),'user',L('Votre nom en bas à gauche','Your name, bottom left'));
 var un=[N('uhead','user',L('Votre compte','Your account'),L('Cliquez sur votre nom en bas de la barre latérale pour ouvrir ce menu. Le point vert indique que vous êtes en ligne.','Click your name at the bottom of the sidebar to open this menu. The green dot means you are online.')+(ADMIN?L(' Le nombre, visible par les administrateurs, indique les utilisateurs actifs.',' The number, shown to administrators, is the count of active users.'):'')),
   N('ustatus','smile','Update your status',L('Ajoutez un emoji et un message court (« en réunion », « en cours »), visible par les autres dans les canaux.','Set an emoji and a short message (“in a meeting”, “in class”), shown to others in channels.'))];
 if(anyWs())un.push(N('uworkspace','grid','Workspace',L('Créer et gérer assistants, bases de documents, modèles de demandes, selon vos droits.','Create and manage assistants, document collections and prompt templates, depending on your rights.')));
 if(ft('notes'))un.push(N('unotes','book','Notes',L('Ouvre vos Notes.','Opens your Notes.')));
 if(ft('calendar'))un.push(N('ucalendar','calendar','Calendar',L('Votre agenda personnel, avec rappels. Voir chapitre Calendrier.','Your personal calendar with reminders. See the Calendar chapter.')));
 if(ft('automations'))un.push(N('uauto','clock','Automations',L('Des demandes qui s’exécutent toutes seules à heure fixe. Voir chapitre Automatisations.','Requests that run on their own on a schedule. See the Automations chapter.')));
 if(ADMIN)un.push(N('uplay','code','Playground',L('Réservé aux administrateurs : tester un modèle avec ses paramètres bruts, hors conversation.','Admins only: test a model with raw parameters, outside a chat.')));
 if(ADMIN)un.push(N('uadmin','user','Admin Panel',L('Réservé aux administrateurs : utilisateurs, groupes et permissions, réglages, évaluations.','Admins only: users, groups and permissions, settings, evaluations.')));
 un.push(N('usettings','gear','Settings',L('Langue, thème, notifications, voix, personnalisation et mémoire.','Language, theme, notifications, voice, personalization and memory.')),N('usignout','logout','Sign Out',L('Déconnexion. Indispensable sur un ordinateur partagé.','Signs you out. Essential on a shared computer.')));
 if(on('show_user_menu'))S({title:L('Le menu utilisateur','The user menu'),desc:L('Il donne accès à vos espaces personnels et à vos réglages. Son contenu dépend de votre rôle.','It opens your personal spaces and settings. What you see depends on your role.'),stage:'center',start:'uhead',mock:userMenu,notes:un,
   tip:(ft('calendar')||ft('automations'))?L('Astuce : maintenez Maj (Shift) dans ce menu pour épingler Calendar ou Automations dans la barre latérale.','Tip: hold Shift in this menu to pin Calendar or Automations to the sidebar.'):null});

 if(on('show_notes')&&ft('notes')){
  chapter('Notes','book',L('Rédiger avec l’assistant','Writing with the assistant'));
  S({title:L('La page Notes','The Notes page'),desc:L('Barre latérale › Notes. Un espace pour rédiger des textes qui durent : comptes rendus, plans, idées.','Sidebar › Notes. A place for lasting writing: minutes, outlines, ideas.'),stage:'center',
    mock:notesList,notes:[N('count','book','Notes 5',L('Nombre de notes accessibles.','Number of notes you can access.')),N('create','plus','Create  ⌄',L('Crée une note. La flèche propose d’autres options de création.','Creates a note. The arrow offers other creation options.')),
      N('nsearch','search','Search Notes',L('Recherche dans les titres et le contenu.','Searches titles and content.')),N('filters','sliders','All · Write · List',L('Filtrer (toutes, les vôtres…), le mode d’ouverture et l’affichage liste ou grille.','Filter (all, yours…), open mode, and list or grid view.')),
      N('sort','down','Title · Updated at',L('Cliquez sur une colonne pour trier.','Click a column to sort.')),N('row','dots',L('Une note','A note'),L('Cliquez pour ouvrir. ⋯ pour les options.','Click to open. ⋯ for options.'))]});
  S({title:L('L’éditeur de note','The note editor'),desc:L('Rédigez à gauche, travaillez avec l’assistant à droite.','Write on the left, work with the assistant on the right.'),stage:'flush center',
    mock:function(){return noteEditor(false);},notes:[N('ntitle','edit',L('Titre','Title'),L('Cliquez pour renommer la note.','Click to rename the note.')),N('nmeta','info',L('Compteur','Counter'),L('Date de modification, nombre de mots et de caractères.','Last edit, word and character count.')),
      N('nbody','notefile','Write something...',L('Écrivez librement. Sélectionnez un passage pour le faire réécrire par l’assistant.','Write freely. Select a passage to have the assistant rewrite it.')),N('undo','undo',L('Annuler / rétablir','Undo / redo'),L('Revenez sur une modification, y compris celles faites par l’assistant.','Undo any change, including those made by the assistant.')),
      N('nchat','bubble','Chat',L('Ouvre ou ferme le panneau assistant de la note.','Opens or closes the note’s assistant panel.')),N('nrec','mic',L('Enregistrer','Record'),L('Dictez ou enregistrez de l’audio : le texte est ajouté à la note.','Dictate or record audio: the text is added to the note.')),
      N('nmore','dots','⋯',L('Télécharger, partager, épingler, supprimer. Voir étape suivante.','Download, share, pin, delete. See next step.')),N('access','lock','Access',L('Qui peut voir ou modifier la note. Par défaut elle est privée.','Who can view or edit the note. Private by default.')),
      N('nprompts','bolt','Suggested prompts',L('Améliorer, résumer, extraire les actions, réécrire la sélection : l’assistant modifie la note directement.','Enhance, summarize, extract action items, rewrite selection: the assistant edits the note directly.')),N('ncomposer','edit','Send a Message',L('Posez votre propre demande sur la note.','Ask your own request about the note.'))],
    example:L('Transforme cette note en compte rendu : décisions, responsables, échéances.','Turn this note into minutes: decisions, owners, deadlines.')});
  S({title:L('Le menu ⋯ d’une note','The note ⋯ menu'),desc:L('Toutes les actions sur la note.','All actions for the note.'),stage:'flush center',start:'m-pin',
    mock:function(){return noteEditor(true);},notes:[N('m-download','download','Download',L('Exporter la note (texte, Markdown ou PDF).','Export the note (text, Markdown or PDF).')),N('m-upload','upcloud','Upload files',L('Joindre des fichiers à la note comme contexte pour l’assistant.','Attach files to the note as context for the assistant.')),
      N('m-share','share','Share',L('Copier un lien ou partager la note.','Copy a link or share the note.')),N('m-pin','pin','Pin to Sidebar',L('Affiche la note dans la barre latérale pour la retrouver vite et la glisser dans une conversation.','Shows the note in the sidebar to find it fast and drag it into a chat.')),
      N('m-delete','trash','Delete',L('Supprime définitivement la note.','Permanently deletes the note.'))],
    info:L('Pour utiliser une note dans une conversation : + › Attach Notes.','To use a note in a chat: + › Attach Notes.')});
 }

 if(on('show_folders')&&ft('folders')){
  chapter(L('Dossiers','Folders'),'folder',L('Organiser vos projets','Organizing your projects'));
  S({title:L('Pourquoi utiliser des dossiers','Why use folders'),desc:L('Un dossier est un projet : il range vos conversations ET donne à l’assistant les mêmes consignes et documents pour chaque conversation qu’il contient.','A folder is a project: it groups your chats AND gives the assistant the same instructions and documents for every chat inside.'),stage:'flush center',start:'folders',
    mock:function(){var a=E('div','m-app');add(a,sidebar(false),E('div','m-mainpane',''));return a;},
    notes:[N('folders','folder','Folders  +',L('Cliquez sur + pour créer un dossier. Glissez-déposez ensuite des conversations dedans.','Click + to create a folder. Then drag chats into it.')),N('chats','history',L('Nouvelle conversation dans un dossier','New chat in a folder'),L('Ouvrez le dossier puis New Chat : la conversation hérite de ses réglages.','Open the folder then New Chat: the chat inherits its settings.'))],
    compare:null,info:L('Exemples : « Projet Alpha » avec ses documents, « Rapport annuel » avec le guide de rédaction, « Candidatures » avec votre CV.','Examples: “Project Alpha” with its documents, “Annual report” with the writing guide, “Applications” with your CV.')});
  S({title:L('Créer un dossier pas à pas','Create a folder step by step'),desc:L('Folders › + ouvre cette fenêtre.','Folders › + opens this window.'),stage:'center',start:'fname',mock:folderModal,
    notes:[N('fname','edit','Folder Name',L('Un nom clair, sans donnée sensible.','A clear name, no sensitive data.')),N('fbg','imageup','Folder Background Image',L('Optionnel : une image pour reconnaître le dossier.','Optional: an image to recognize the folder.')),
      N('fprompt','edit','System Prompt',L('Consignes appliquées à toutes les conversations du dossier : rôle, ton, format. Visible seulement si votre compte l’autorise.','Instructions applied to every chat in the folder: role, tone, format. Only shown if your account allows it.')),
      N('fknow','db','Knowledge',L('Select Knowledge ajoute une base existante, Upload envoie vos fichiers. Ils servent de contexte à chaque conversation du dossier.','Select Knowledge adds an existing collection, Upload sends your files. They become context for every chat in the folder.')),
      N('fsave','check','Save',L('Crée le dossier. Vous pourrez modifier ces réglages plus tard.','Creates the folder. You can change these settings later.'))],
    example:L('Tu es mon tuteur en statistiques. Explique avec des exemples concrets, vérifie mes calculs et pose-moi une question pour valider ma compréhension.','You are my statistics tutor. Explain with concrete examples, check my calculations and ask me a question to confirm I understood.'),exampleLabel:L('Exemple de System Prompt','Example System Prompt')});
 }

 if(on('show_channels')&&ft('channels')){
  chapter(L('Canaux','Channels'),'hash',L('Travailler à plusieurs','Working together'));
  S({title:L('Les canaux : travailler à plusieurs avec l’IA','Channels: working together with AI'),desc:L('Un canal est un espace partagé en temps réel. L’IA n’intervient que si vous la mentionnez avec @.','A channel is a real-time shared space. AI only joins in when you mention it with @.'),stage:'flush center',start:'cmention',mock:channelMock,
    notes:[N('cplus','plus','Channels  +',L('Créer un canal (selon vos droits) et choisir qui y a accès.','Create a channel (if allowed) and choose who can access it.')),N('clist','hash',L('Liste des canaux','Channel list'),L('Les canaux dont vous êtes membre. Un point signale les messages non lus.','Channels you belong to. A dot marks unread messages.')),
      N('cmention','users','@'+PRODUCT,L('Tapez @ puis choisissez un assistant : il répond dans un fil sous votre message, sans encombrer le canal.','Type @ then pick an assistant: it replies in a thread under your message, keeping the channel tidy.')),
      N('cthread','reply',L('Fils de discussion','Threads'),L('Répondez à un message précis pour garder les sujets séparés.','Reply to a specific message to keep topics separate.')),
      N('creact','smile',L('Réactions','Reactions'),L('Un emoji pour approuver sans ajouter de message.','An emoji to agree without adding a message.')),N('cpin','pin',L('Messages épinglés','Pinned messages'),L('Gardez les décisions et liens importants en haut.','Keep key decisions and links at the top.')),
      N('cinput','edit','Send a Message',L('Écrivez, joignez un fichier avec +, mentionnez une personne ou un assistant.','Write, attach a file with +, mention a person or an assistant.'))],
    warn:L('Tous les membres du canal lisent ce que vous publiez. Vérifiez qui y a accès avant de partager un document.','Every channel member reads what you post. Check who has access before sharing a document.')});
 }

 if(on('show_calendar')&&ft('calendar')){
  chapter(L('Calendrier','Calendar'),'calendar',L('Événements, rappels, IA','Events, reminders, AI'));
  var cn2=[N('newevent','plus','New Event',L('Crée un événement. Vous pouvez aussi cliquer directement sur un jour de la grille.','Creates an event. You can also click a day on the grid.')),
    N('mini','calendar',L('Mini calendrier','Mini calendar'),L('Aller rapidement à une date.','Jump quickly to a date.')),
    N('calplus','plus','Calendars  +',L('Créez d’autres calendriers (« Équipe », « Projet ») avec leur couleur.','Create more calendars (“Team”, “Project”) with their own colour.')),
    N('personal','calendar','Personal',L('Créé automatiquement : votre calendrier par défaut. Cliquez pour l’afficher ou le masquer.','Created automatically: your default calendar. Click to show or hide it.'))];
  if(ft('automations'))cn2.push(N('sched','clock','Scheduled Tasks',L('Affiche vos automatisations prévues et passées. En lecture seule : cliquez un événement pour ouvrir l’automatisation ou le chat produit.','Shows your planned and past automations. Read-only: click an event to open the automation or the chat it produced.')));
  cn2.push(N('today','calendar','Today',L('Revient à aujourd’hui. Les flèches passent à la période précédente ou suivante.','Returns to today. Arrows move to the previous or next period.')),N('view','down','Month / Week / Day',L('Change la vue : mois, semaine ou jour.','Switch view: month, week or day.')),
    N('event','calendar',L('Un événement','An event'),L('Cliquez pour le modifier ou le supprimer.','Click to edit or delete it.')));
  if(ft('automations'))cn2.push(N('autoev','repeat',L('Exécution automatique','Automation run'),L('En violet : une automatisation planifiée.','In purple: a scheduled automation.')));
  S({title:L('Le Calendrier','The Calendar'),desc:L('Menu utilisateur › Calendar. Un agenda personnel avec rappels, que l’assistant peut aussi gérer pour vous.','User menu › Calendar. A personal calendar with reminders that the assistant can also manage for you.'),stage:'flush center',start:'newevent',mock:calendarPage,notes:cn2,
    info:L('Le calendrier ne se synchronise pas avec Outlook ou Google Agenda.','The calendar does not sync with Outlook or Google Calendar.')});
  S({title:L('Créer un événement','Creating an event'),desc:L('New Event ouvre ce formulaire, prérempli à la date du jour.','New Event opens this form, prefilled with today’s date.'),stage:'center',start:'etitle',mock:eventForm,
    notes:[N('etitle','edit','Title',L('Obligatoire. Un nom clair.','Required. A clear name.')),N('ecal','calendar','Calendar',L('Dans quel calendrier ranger l’événement.','Which calendar to put it in.')),
      N('ewhen','clock','When · All day',L('Date et heures, ou journée entière.','Date and times, or all day.')),N('eloc','mappin','Location',L('Optionnel : salle ou lien de visio.','Optional: room or video link.')),
      N('erepeat','repeat','Repeat',L('No Repeat, Daily, Monday – Friday, Weekly, Monthly ou Yearly. Pas plus souvent qu’une fois par jour : pour cela, utilisez une automatisation.','No Repeat, Daily, Monday – Friday, Weekly, Monthly or Yearly. No more than once a day: use an automation for that.')),
      N('eremind','bell','Reminder',L('Alerte avant l’événement (10 minutes par défaut) : notification dans la plateforme et dans le navigateur si vous l’avez autorisé.','Alert before the event (10 minutes by default): in-app notification, and browser notification if allowed.')),
      N('edesc','notefile','Description',L('Ordre du jour, liens, documents à préparer.','Agenda, links, documents to prepare.')),N('ecreate','check','Create',L('Enregistre l’événement.','Saves the event.'))]});
 }
 if(on('show_automations')&&ft('automations')){
  chapter(L('Automatisations','Automations'),'clock',L('Demandes planifiées','Scheduled requests'));
  S({title:L('Les automatisations','Automations'),desc:L('Menu utilisateur › Automations. Une automatisation envoie une demande à heure fixe : chaque exécution crée une conversation avec la réponse.','User menu › Automations. An automation sends a request on a schedule: each run creates a chat with the answer.'),stage:'center',start:'acreate',mock:autoList,
    notes:[N('acreate','plus','Create  ⌄',L('New Automation. La flèche propose Import JSON et Export JSON pour copier vos automatisations.','New Automation. The arrow offers Import JSON and Export JSON to copy your automations.')),
      N('asearch','search','Search',L('Retrouver une automatisation par son nom.','Find an automation by name.')),N('afilter','sliders',L('Filtre de statut','Status filter'),L('Toutes, actives ou en pause.','All, active or paused.')),
      N('arow','clock',L('Une automatisation','An automation'),L('Nom, fréquence et prochaine exécution. Cliquez pour ouvrir l’éditeur.','Name, schedule and next run. Click to open the editor.')),
      N('aswitch','check',L('Interrupteur','Switch'),L('Met en pause ou relance sans supprimer.','Pauses or resumes without deleting.'))],
    info:L('Vos automatisations sont privées : personne d’autre ne peut les voir ni les lancer.','Your automations are private: nobody else can see or run them.')});
  S({title:L('Créer et suivre une automatisation','Creating and monitoring an automation'),desc:L('Remplissez les champs puis Create. L’éditeur permet ensuite de lancer, suspendre et suivre les exécutions.','Fill in the fields then Create. The editor then lets you run, pause and monitor executions.'),stage:'center',start:'ainstr',mock:autoEditor,
    notes:[N('atitle','edit','Title',L('Un nom qui dit ce que produit l’automatisation.','A name saying what it produces.')),N('ainstr','notefile','Instructions',L('La demande envoyée à chaque exécution. Soyez complet : elle doit fonctionner sans vous.','The request sent on each run. Be complete: it must work without you.')),
      N('amodel','layers','Model',L('L’assistant utilisé. Ses outils et réglages s’appliquent.','The assistant used. Its tools and settings apply.')),N('asched','repeat','Schedule',L('Once, Hourly, Daily, Weekly, Monthly ou Custom (règle avancée).','Once, Hourly, Daily, Weekly, Monthly or Custom (advanced rule).'))]
      .concat(ft('folders')?[N('afolder','folder','Folder',L('Range automatiquement les conversations produites dans un de vos dossiers.','Files the chats it creates into one of your folders.'))]:[])
      .concat([N('arun','play','Run now',L('Lance immédiatement pour tester avant d’attendre l’horaire.','Runs immediately to test before the schedule.')),N('apause','pause','Pause',L('Suspend sans perdre les réglages.','Suspends without losing settings.')),
      N('adelete','trash','Delete',L('Supprime l’automatisation et son historique.','Deletes the automation and its history.')),N('alogs','tasks','Execution logs',L('Chaque exécution avec son statut (success ou error) et le lien vers la conversation créée.','Each run with its status (success or error) and a link to the chat created.'))]),
    example:L('Chaque lundi, recherche les nouveautés publiées sur ce sujet et présente-les dans un tableau avec lien, source et date.','Every Monday, find what was published on this topic and list it in a table with link, source and date.'),exampleLabel:L('Exemple d’instructions','Example instructions'),
    tip:L('Testez toujours avec Run now. Vous pouvez aussi demander dans une conversation : « programme un résumé tous les jours à 9h ».','Always test with Run now. You can also ask in a chat: “schedule a summary every day at 9am”.')});
 }
 chapter(L('Outils intégrés','Built-in tools'),'spark',L('Ce que l’assistant fait seul','What the assistant does on its own'));
 if(on('show_builtin_tools'))S({title:L('Les outils intégrés de l’assistant','The assistant’s built-in tools'),desc:L('Selon sa configuration, l’assistant peut agir directement : consulter la date, chercher dans vos notes, créer un événement… Vous le demandez en langage naturel, il choisit l’outil.','Depending on its setup, the assistant can act directly: check the date, search your notes, create an event… You ask in plain language and it picks the tool.'),stage:'center',start:'toolcall',mock:builtinTools,
   notes:[N('toolcall','check',L('Outil utilisé','Tool used'),L('Cette ligne montre l’outil lancé. Cliquez dessus pour voir ce qui a été envoyé et reçu.','This line shows the tool that ran. Click it to see what was sent and received.'))].concat(builtinCats().map(function(c){return N(c.a,c.i,c.k,c.t);})),
   info:L('Ces outils ne fonctionnent qu’avec les assistants configurés pour eux et selon vos permissions. Si rien ne se passe, choisissez un autre assistant.','These tools only work with assistants set up for them and within your permissions. If nothing happens, choose another assistant.'),
   warn:L('Pour les actions qui modifient quelque chose, réglez + › Tool Permissions sur Ask for approval.','For actions that change something, set + › Tool Permissions to Ask for approval.')});
 if(ADMIN&&on('show_admin_section')){chapter(L('Administration','Administration'),'shield',L('Réservé aux administrateurs','Admins only'));
  S({title:L('Espace administrateur','Administrator area'),desc:L('Vous voyez ce chapitre car votre compte est administrateur.','You see this chapter because your account is an administrator.'),stage:'center',start:'uadmin',mock:userMenu,
   notes:[N('uadmin','user','Admin Panel',L('Utilisateurs, groupes et permissions (qui voit Notes, Calendar, Automations…), réglages généraux, évaluations des modèles à partir des avis.','Users, groups and permissions (who sees Notes, Calendar, Automations…), general settings, model evaluations from feedback.')),
     N('uplay','code','Playground',L('Tester un modèle en direct avec prompt système et paramètres, sans créer de conversation.','Test a model live with system prompt and parameters, without creating a chat.'))],
   warn:L('Modifiez les permissions sur un groupe de test avant de les appliquer à tous.','Change permissions on a test group before applying them to everyone.')});}
 chapter(L('Bon usage','Good practice'),'shield',L('Règles essentielles','Essential rules'));
 if(on('show_safety'))S({title:L('Utiliser l’IA de façon responsable','Using AI responsibly'),desc:L('L’assistant peut se tromper avec assurance. Vous restez responsable de ce que vous en faites.','The assistant can be confidently wrong. You stay responsible for what you do with it.'),stage:'center',mock:doDont,notes:[],
   warn:L('Respectez les règles de votre organisation sur l’usage de l’IA.','Follow your organization’s rules on using AI.')});
 S({title:L('Vous êtes prêt','You’re ready'),desc:L('Fermez le guide et posez votre première question. L’onglet Fonctions reste disponible pour revoir chaque fonctionnalité.','Close the guide and ask your first question. The Features tab stays available to revisit every feature.'),stage:'center',mock:function(){return composer({});},notes:[],links:true});

 chapters=[];steps.forEach(function(s,i){var l=chapters[chapters.length-1];if(l&&l.name===s.ch)l.end=i;else chapters.push({name:s.ch,start:i,end:i});});
}

/* ============ Library of built-in features ============ */
function libItems(){var x=[];
 var GATE={files:'show_file_upload',webpage:'show_plus_menu',refchats:'show_plus_menu',knowledge:'show_knowledge',web:'show_web_search',code:'show_code_interpreter',image:'show_image_generation',tools:'show_tools',voice:'show_voice',multi:'show_multiple_models',prompts:'show_prompts',notes:'show_notes',folders:'show_folders',channels:'show_channels',feedback:'show_feedback',memory:'show_memory',settings:'show_user_menu',status:'show_user_menu',calendar:'show_calendar',automations:'show_automations',builtin:'show_builtin_tools',playground:'show_admin_section',admin:'show_admin_section'};
 function F(g,id,i,name,short,where,what,how,example,note){if(GATE[id]&&!on(GATE[id]))return;if(!on('show_try_buttons'))example=null;x.push({g:g,id:id,i:i,name:name,short:short,where:where,what:what,how:how,example:example,note:note});}
 var G1=L('Ajouter du contexte','Add context'),G2=L('Fonctions intégrées','Built-in features'),G3=L('Organiser et collaborer','Organize and collaborate'),G4=L('Votre compte','Your account');
 if(ft('file_upload'))F(G1,'files','clip',L('Fichiers','Files'),L('PDF, Word, Excel, images','PDF, Word, Excel, images'),['+','Upload Files'],L('Analysez vos documents : résumé, extraction, traduction, comparaison.','Analyze your documents: summary, extraction, translation, comparison.'),[L('Cliquez sur + › Upload Files, ou glissez le fichier dans la zone de saisie.','Click + › Upload Files, or drag the file into the message box.'),L('Attendez la fin du chargement.','Wait for the upload to finish.'),L('Demandez ce que vous voulez en citant le fichier.','Ask what you need, referring to the file.')],L('Compare ces deux documents et liste les différences dans un tableau.','Compare these two documents and list the differences in a table.'),L('Ne joignez que des documents que vous avez le droit d’utiliser.','Only attach documents you are allowed to use.'));
 F(G1,'webpage','globe','Attach Webpage',L('Lire une page web','Read a web page'),['+','Attach Webpage'],L('Ajoute le texte d’une page à partir de son adresse.','Adds a page’s text from its address.'),[L('+ › Attach Webpage.','+ › Attach Webpage.'),L('Collez l’adresse complète (https://…).','Paste the full address (https://…).'),L('Posez votre question sur la page.','Ask about the page.')],L('Résume cette page en 5 points pour quelqu’un qui découvre le sujet.','Summarize this page in 5 points for someone new to the topic.'));
 if(av('knowledge')||ws('knowledge'))F(G1,'knowledge','db',L('Bases de documents','Knowledge'),L('Interroger des documents','Ask document collections'),['+','Attach Knowledge','›'],L('L’assistant cherche la réponse dans une collection de documents et cite les passages.','The assistant searches a document collection and cites passages.'),[L('+ › Attach Knowledge › choisissez la base. Raccourci : tapez # dans la zone de saisie.','+ › Attach Knowledge › pick the collection. Shortcut: type # in the message box.'),L('Posez une question précise.','Ask a precise question.'),L('Demandez les sources utilisées.','Ask for the sources used.')],L('Réponds uniquement à partir de ces documents et indique la section utilisée.','Answer only from these documents and name the section used.'));
 F(G1,'refchats','history','Reference Chats',L('Réutiliser une conversation','Reuse a conversation'),['+','Reference Chats','›'],L('Ajoute une conversation passée comme contexte.','Adds a past chat as context.'),[L('+ › Reference Chats › choisissez la conversation.','+ › Reference Chats › pick the chat.'),L('Demandez de continuer ou de réutiliser ce qui a été produit.','Ask to continue or reuse what was produced.')],L('À partir de la conversation jointe, rédige la version finale de l’e-mail.','Using the attached chat, write the final version of the email.'));
 if(ft('web_search'))F(G2,'web','globe','Web Search',L('Informations à jour avec sources','Up-to-date info with sources'),[L('Intégrations','Integrations'),'Web Search'],L('Recherche sur Internet et cite les pages consultées.','Searches the internet and cites the pages used.'),[L('Intégrations › activez Web Search.','Integrations › turn on Web Search.'),L('Précisez période et sources souhaitées.','Specify the period and sources you want.'),L('Ouvrez les sources pour vérifier.','Open the sources to check.')],L('Quelles sont les nouveautés officielles sur ce sujet en 2026 ? Donne les liens.','What was officially published on this topic in 2026? Give the links.'),L('Désactivez-la quand l’information récente n’est pas nécessaire.','Turn it off when recent information is not needed.'));
 if(ft('code_interpreter'))F(G2,'code','terminal','Code Interpreter',L('Calculs, données, graphiques','Calculations, data, charts'),[L('Intégrations','Integrations'),'Code Interpreter'],L('L’assistant écrit et exécute du code pour obtenir un résultat exact.','The assistant writes and runs code to get an exact result.'),[L('Intégrations › activez Code Interpreter.','Integrations › turn on Code Interpreter.'),L('Joignez un fichier CSV ou Excel si besoin.','Attach a CSV or Excel file if needed.'),L('Décrivez le calcul ou le graphique attendu.','Describe the calculation or chart you want.')],L('Calcule la moyenne et l’écart-type par groupe et trace un histogramme.','Compute mean and standard deviation per group and plot a histogram.'));
 if(ft('image_generation'))F(G2,'image','image',L('Génération d’images','Image generation'),L('Créer une illustration','Create an illustration'),[L('Intégrations','Integrations'),'Image'],L('Crée une image à partir d’une description.','Creates an image from a description.'),[L('Activez la génération d’images.','Turn on image generation.'),L('Décrivez sujet, style, cadrage et format.','Describe subject, style, framing and format.')],L('Illustration simple et moderne d’une équipe en réunion, format 16:9, sans texte.','Simple modern illustration of a team in a meeting, 16:9, no text.'));
 if(hasTools)F(G2,'tools','wrench','Tools',L('Outils de votre compte','Tools on your account'),[L('Intégrations','Integrations'),'Tools','›'],L('Des outils connectés permettent à l’assistant de consulter ou agir sur des services.','Connected tools let the assistant look up or act on services.'),[L('Intégrations › Tools › activez l’outil utile.','Integrations › Tools › turn on the tool you need.'),L('Réglez + › Tool Permissions sur Ask for approval pour valider chaque action.','Set + › Tool Permissions to Ask for approval to confirm each action.')],null,L('Ne collez jamais de mot de passe dans la conversation.','Never paste a password into the chat.'));
 if(ft('stt')||ft('call'))F(G2,'voice','mic',L('Voix','Voice'),L('Dicter, écouter, parler','Dictate, listen, talk'),[L('Zone de saisie','Message box'),'🎙'],L('Dictée avec le micro, mode vocal avec le bouton rond, lecture des réponses avec l’icône haut-parleur.','Dictation with the mic, voice mode with the round button, read-aloud with the speaker icon.'),[L('Micro : parlez, relisez le texte, envoyez.','Mic: speak, review the text, send.'),L('Bouton rond (zone vide) : conversation orale.','Round button (empty box): spoken conversation.'),L('Sous une réponse : haut-parleur pour l’écouter.','Under an answer: speaker to listen.')],null);
 if(ft('multiple_models'))F(G2,'multi','layers',L('Comparer des modèles','Compare models'),L('Plusieurs réponses côte à côte','Several answers side by side'),[L('Sélecteur d’assistant','Assistant selector'),'+'],L('Posez la même question à plusieurs modèles et comparez.','Ask several models the same question and compare.'),[L('Ajoutez un second modèle à côté du sélecteur.','Add a second model next to the selector.'),L('Envoyez une seule fois : chaque modèle répond.','Send once: each model answers.')],null,L('Plusieurs réponses identiques peuvent partager la même erreur.','Matching answers can share the same mistake.'));
 if(av('prompts'))F(G2,'prompts','slash',L('Modèles de demande','Saved prompts'),L('Tapez /','Type /'),[L('Zone de saisie','Message box'),'/'],L('Insère une demande préparée à l’avance.','Inserts a request prepared in advance.'),[L('Tapez / au début du message.','Type / at the start of the message.'),L('Choisissez la commande et complétez les champs.','Pick the command and fill in the fields.')],null);
 if(ft('notes'))F(G3,'notes','book','Notes',L('Rédiger avec l’assistant','Write with the assistant'),[L('Barre latérale','Sidebar'),'Notes'],L('Des documents persistants avec un assistant qui peut les modifier directement.','Lasting documents with an assistant that can edit them directly.'),[L('Notes › Create.','Notes › Create.'),L('Ouvrez le panneau Chat et utilisez une suggestion.','Open the Chat panel and use a suggestion.'),L('⋯ › Pin to Sidebar pour la garder à portée.','⋯ › Pin to Sidebar to keep it handy.')],L('Extrais les actions de cette note avec responsable et échéance.','Extract action items from this note with owner and deadline.'));
 if(ft('folders'))F(G3,'folders','folder',L('Dossiers','Folders'),L('Projets avec consignes et documents','Projects with instructions and documents'),[L('Barre latérale','Sidebar'),'Folders','+'],L('Rangez les conversations d’un projet et partagez consignes et documents entre elles.','Group a project’s chats and share instructions and documents between them.'),[L('Folders › +, donnez un nom.','Folders › +, give a name.'),L('Ajoutez un System Prompt et des documents.','Add a System Prompt and documents.'),L('Démarrez vos conversations depuis le dossier.','Start chats from the folder.')],null);
 if(ft('channels'))F(G3,'channels','hash',L('Canaux','Channels'),L('Discussions d’équipe avec l’IA','Team discussions with AI'),[L('Barre latérale','Sidebar'),'Channels'],L('Espaces partagés où l’IA répond quand on la mentionne.','Shared spaces where AI answers when mentioned.'),[L('Ouvrez un canal.','Open a channel.'),L('Tapez @ et choisissez un assistant.','Type @ and pick an assistant.'),L('Répondez en fil pour séparer les sujets.','Reply in threads to separate topics.')],null,L('Tous les membres voient vos messages.','All members see your messages.'));
 F(G3,'feedback','tup',L('Avis sur les réponses','Rating answers'),L('👍 / 👎 sous chaque réponse','👍 / 👎 under each answer'),[L('Sous une réponse','Under an answer'),'👍 👎'],L('Notez les réponses pour aider à améliorer les assistants.','Rate answers to help improve assistants.'),[L('Cliquez 👍 ou 👎.','Click 👍 or 👎.'),L('Choisissez une note et une raison.','Pick a score and a reason.'),L('Ajoutez une phrase précise, puis Save.','Add one precise sentence, then Save.')],null);
 if(ft('memories'))F(G4,'memory','brain',L('Mémoire','Memory'),L('Préférences retenues','Remembered preferences'),[L('Votre nom','Your name'),'Settings','Personalization'],L('L’assistant retient des informations utiles d’une conversation à l’autre.','The assistant keeps useful details across chats.'),[L('Ouvrez Settings › Personalization › Memory.','Open Settings › Personalization › Memory.'),L('Ajoutez, corrigez ou supprimez ce qui est retenu.','Add, edit or delete what is remembered.')],null,L('N’y enregistrez aucune donnée sensible.','Do not store sensitive data there.'));
 F(G4,'settings','sliders',L('Paramètres','Settings'),L('Langue, thème, compte','Language, theme, account'),[L('Votre nom (en bas à gauche)','Your name (bottom left)'),'Settings'],L('Réglez l’interface selon vos préférences.','Adjust the interface to your preferences.'),[L('Cliquez sur votre nom en bas de la barre latérale.','Click your name at the bottom of the sidebar.'),L('Settings › General pour la langue et le thème.','Settings › General for language and theme.')],null,L('Déconnectez-vous sur un ordinateur partagé.','Sign out on a shared computer.'));
 if(ft('calendar'))F(G3,'calendar','calendar',L('Calendrier','Calendar'),L('Événements et rappels','Events and reminders'),[L('Menu utilisateur','User menu'),'Calendar'],L('Agenda personnel avec vues mois/semaine/jour, événements récurrents, rappels et partage.','Personal calendar with month/week/day views, recurring events, reminders and sharing.'),[L('Menu utilisateur › Calendar.','User menu › Calendar.'),L('New Event : titre, date, lieu, répétition, rappel.','New Event: title, date, location, repeat, reminder.'),L('Ou demandez à l’assistant : « ajoute le partiel de stats jeudi 14h ».','Or ask the assistant: “add the stats exam on Thursday at 2pm”.')],L('Qu’est-ce que j’ai dans mon calendrier cette semaine ?','What is on my calendar this week?'),L('Aucune synchronisation avec Outlook ou Google Agenda.','No sync with Outlook or Google Calendar.'));
 if(ft('automations'))F(G3,'automations','clock',L('Automatisations','Automations'),L('Demandes planifiées','Scheduled requests'),[L('Menu utilisateur','User menu'),'Automations'],L('Exécute une demande automatiquement (une fois, chaque heure, jour, semaine, mois). Chaque exécution crée une conversation.','Runs a request automatically (once, hourly, daily, weekly, monthly). Each run creates a chat.'),[L('Automations › Create.','Automations › Create.'),L('Title, Instructions, Model, Schedule, Folder.','Title, Instructions, Model, Schedule, Folder.'),L('Testez avec Run now, suivez les Execution logs.','Test with Run now, check Execution logs.')],L('Programme un résumé des actualités du secteur chaque lundi à 8h.','Schedule an industry news digest every Monday at 8am.'),L('Relisez les résultats : l’automatisation s’exécute sans vous.','Review results: the automation runs without you.'));
 F(G2,'builtin','spark',L('Outils intégrés','Built-in tools'),L('L’assistant agit pour vous','The assistant acts for you'),[L('Dans la conversation','In the chat')],L('Date et heure, recherche web, documents, mémoire, notes, anciennes conversations, calendrier, automatisations, images, code, listes de tâches : selon l’assistant et vos droits.','Date & time, web search, documents, memory, notes, past chats, calendar, automations, images, code, task lists: depending on the assistant and your rights.'),[L('Demandez en langage naturel.','Ask in plain language.'),L('Repérez la ligne « outil utilisé » et ouvrez-la pour vérifier.','Look for the “tool used” line and open it to check.'),L('Utilisez Ask for approval pour valider les actions.','Use Ask for approval to confirm actions.')],L('Retrouve dans mes notes ce que j’ai écrit sur le projet Alpha.','Find what I wrote about Project Alpha in my notes.'));
 F(G4,'status','smile','Update your status',L('Statut visible par les autres','Status shown to others'),[L('Menu utilisateur','User menu'),'Update your status'],L('Un emoji et un court message pour indiquer votre disponibilité.','An emoji and a short message to show your availability.'),[L('Cliquez sur votre nom.','Click your name.'),L('Update your status, choisissez un emoji et un texte.','Update your status, pick an emoji and text.')],null);
 if(ADMIN){F(G4,'playground','code','Playground',L('Administrateurs','Administrators'),[L('Menu utilisateur','User menu'),'Playground'],L('Tester un modèle avec ses paramètres, hors conversation.','Test a model with its parameters, outside a chat.'),[L('Ouvrez Playground.','Open Playground.'),L('Choisissez un modèle, un prompt système et envoyez.','Pick a model, a system prompt and send.')],null);
  F(G4,'admin','shield','Admin Panel',L('Administrateurs','Administrators'),[L('Menu utilisateur','User menu'),'Admin Panel'],L('Utilisateurs, groupes, permissions, réglages et évaluations.','Users, groups, permissions, settings and evaluations.'),[L('Admin Panel › Users › Groups pour les permissions.','Admin Panel › Users › Groups for permissions.'),L('Testez chaque changement sur un compte non administrateur.','Test every change with a non-admin account.')],null);}
 ACTIONS.slice(0,6).forEach(function(a,k){F(G4,'action-'+k,k%2?'spark':'doc',a.name,L('Action sous les réponses','Action under answers'),[L('Sous une réponse','Under an answer'),L('icône d’action','action icon')],a.description||L('Action ajoutée par votre organisation.','Action added by your organization.'),[L('Survolez une réponse.','Hover an answer.'),L('Cliquez sur l’icône de l’action.','Click the action icon.')],null);});
 return x;}

/* ============ Render ============ */
function header(){
 if(!on('show_features_tab')){tab='tour';var lt=document.querySelector('.g-tabs');if(lt)lt.style.display='none';}
 if(!on('show_language_switch')){var lg=document.querySelector('.g-lang');if(lg)lg.style.display='none';}
 document.getElementById('gLogo').textContent=PRODUCT.charAt(0).toUpperCase();
 document.getElementById('gName').replaceChildren(document.createTextNode(PRODUCT+' '),E('span','',L('· Guide et tutoriels','· Guide & tutorials')));
 document.querySelectorAll('[data-tab]').forEach(function(b){b.textContent=b.dataset.tab==='tour'?L('Visite guidée','Guided tour'):L('Fonctions','Features');b.setAttribute('aria-selected',String(b.dataset.tab===tab));});
 document.querySelectorAll('[data-lang]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.lang===lang));});
 document.getElementById('close').replaceChildren(E('span','',L('Fermer','Close')),I('x'));
 document.documentElement.lang=lang;
}
function render(){header();var body=document.getElementById('gBody');body.replaceChildren();var ub=updateBanner();if(ub)add(body,ub);if(tab==='lib')renderLib(body);else renderTour(body);save({index:cur,lang:lang,tab:tab,status:'active'});requestAnimationFrame(height);}

function renderTour(body){
 var s=steps[cur];if(active===null)active=s.start||(s.notes[0]&&s.notes[0].a)||'';
 var chips=E('nav','g-chapters');chips.setAttribute('aria-label',L('Chapitres','Chapters'));
 chapters.forEach(function(c){var b=E('button','chip'+(cur>=c.start&&cur<=c.end?' on':cur>c.end?' done':''),c.name);b.type='button';b.addEventListener('click',function(){go(c.start);});add(chips,b);});
 var bar=E('div','g-bar'),fill=E('span');fill.style.width=((cur+1)/steps.length*100)+'%';add(bar,fill);
 var st=E('div','step'),ch=chapters.filter(function(c){return cur>=c.start&&cur<=c.end;})[0];
 add(st,add(E('p','kicker'),E('span','',s.ch),ch&&ch.end>ch.start?E('b','',(cur-ch.start+1)+'/'+(ch.end-ch.start+1)):null),E('h1','',s.title),E('p','desc',s.desc));
 if(s.notes.length)add(st,add(E('div','hint'),I('bolt'),E('span','',L('Cliquez sur l’écran ou sur une explication','Click the screen or an explanation'))));
 var stage=E('div','stage '+(s.stage||''));if(s.cover)renderCover(st);else add(stage,s.mock());
 stage.addEventListener('click',function(e){var t=e.target.closest('[data-a]');if(!t)return;active=t.getAttribute('data-a');renderStage(stage,s);syncNotes(st);});
 if(!s.cover)add(st,stage);
 if(s.notes.length){var ns=E('div','notes');s.notes.forEach(function(n,k){var b=E('button','note'+(active===n.a?' on':''));b.type='button';b.dataset.n=n.a;add(b,E('span','note-n',k+1),add(E('span','note-b'),add(E('span','note-k'),I(n.i),E('span','',n.k)),E('span','note-t',n.t)));
   b.addEventListener('click',function(){active=n.a;renderStage(stage,s);syncNotes(st);});add(ns,b);});add(st,ns);}
 if(s.compare)add(st,add(E('div','compare'),add(E('div','bad'),add(E('b'),I('noc'),E('span','',L('Avis peu utile','Unhelpful feedback'))),E('span','',s.compare[0])),add(E('div','good'),add(E('b'),I('okc'),E('span','',L('Avis utile','Helpful feedback'))),E('span','',s.compare[1]))));
 if(s.example&&on('show_try_buttons')){var ex=E('div','example'),tb=E('button','try');tb.type='button';add(tb,E('span','',L('Essayer dans le chat','Try it in the chat')),I('arrowr'));tb.addEventListener('click',function(){sendPrompt(s.example);});add(ex,E('small','',s.exampleLabel||L('Exemple','Example')),E('p','',s.example),tb);add(st,ex);}
 if(s.tip)add(st,add(E('div','callout tip'),I('bulb'),E('span','',s.tip)));
 if(s.info)add(st,add(E('div','callout info'),I('info'),E('span','',s.info)));
 if(s.warn)add(st,add(E('div','callout warn'),I('warn'),E('span','',s.warn)));
 if(s.links){var lk=data.links||{},wrap=E('div','callout info');var any=false;add(wrap,I('info'));var sp=E('span');[['support_url',L('Support','Support')],['acceptable_use_url',L('Règles d’utilisation','Acceptable use')],['privacy_url',L('Confidentialité','Privacy')],['feedback_url',L('Donner votre avis','Give feedback')]].forEach(function(x){if(lk[x[0]]){var a=E('a','',x[1]);a.href=lk[x[0]];a.target='_blank';a.rel='noopener noreferrer';a.style.cssText='margin-right:14px;font-weight:650;color:inherit';add(sp,a);any=true;}});add(wrap,sp);if(any)add(st,wrap);}
 var ft_=E('div','g-foot'),back=E('button','btn ghost'),next=E('button','btn primary'),last=cur===steps.length-1;
 back.type=next.type='button';add(back,I('arrowl'),E('span','',L('Précédent','Back')));back.disabled=cur===0;
 add(next,E('span','',last?L('Commencer','Get started'):L('Suivant','Next')),last?null:I('arrowr'));
 back.addEventListener('click',function(){go(cur-1);});next.addEventListener('click',function(){last?finish():go(cur+1);});
 add(ft_,back,E('span','g-count',L('Étape ','Step ')+(cur+1)+L(' sur ',' of ')+steps.length),next);
 add(body,chips,bar,st,ft_);
 setTimeout(function(){var c=chips.querySelector('.on');if(c&&c.scrollIntoView)c.scrollIntoView({block:'nearest',inline:'center'});},0);
}
function renderCover(st){
 var c=E('div','cover');add(c,E('h2','',L('Ce que vous allez apprendre','What you will learn')),E('p','',L('Écrire une bonne demande, ajouter des documents, activer les outils, organiser vos projets, noter les réponses et utiliser l’IA de façon responsable.','Writing good requests, adding documents, turning on tools, organizing projects, rating answers and using AI responsibly.')));
 var facts=E('div','facts');[['clock',L('Environ 10 minutes','About 10 minutes')],['map',chapters.length-1+' '+L('chapitres','chapters')],['user',L('Adapté à votre compte','Tailored to your account')],['check',L('Reprenez quand vous voulez','Resume anytime')]].forEach(function(f){add(facts,add(E('span','fact'),I(f[0]),E('span','',f[1])));});add(c,facts);
 var a=E('div','cover-actions'),b1=E('button','cbtn main'),b2=E('button','cbtn alt');b1.type=b2.type='button';add(b1,E('span','',L('Commencer la visite','Start the tour')),I('arrowr'));add(b2,E('span','',L('Voir toutes les fonctions','See all features')));
 b1.addEventListener('click',function(){go(1);});b2.addEventListener('click',function(){tab='lib';render();});add(a,b1,on('show_features_tab')?b2:null);add(c,a);add(st,c);
 add(st,E('p','toc-h',L('Sommaire','Contents')));var toc=E('div','toc');
 chapters.slice(1).forEach(function(ch){var d=CHD[ch.name]||{},b=E('button');b.type='button';var n=ch.end-ch.start+1;add(b,I(d.i||'info'),add(E('span'),E('b','',ch.name),E('span','',d.d||''),E('small','',n+' '+(n>1?L('écrans','screens'):L('écran','screen')))));b.addEventListener('click',function(){go(ch.start);});add(toc,b);});
 add(st,toc);
}
function updateBanner(){
 if(!updated)return null;var n=data.update_notes||{},txt=(lang==='fr'?n.fr:n.en)||L('Le guide a été enrichi. Parcourez le sommaire pour voir les nouveautés.','The guide has been updated. Browse the contents to see what is new.');
 var b=E('div','callout info');b.style.margin='14px 18px 0';var x=E('button','sheet-x');x.type='button';x.setAttribute('aria-label',L('Fermer','Close'));add(x,I('x'));
 add(b,I('spark'),add(E('span'),E('b','',L('Guide mis à jour. ','Guide updated. ')),txt),x);x.addEventListener('click',function(){updated=false;b.remove();requestAnimationFrame(height);});return b;}
function renderStage(stage,s){stage.replaceChildren(s.mock());requestAnimationFrame(height);}
function syncNotes(st){st.querySelectorAll('.note').forEach(function(b){b.classList.toggle('on',b.dataset.n===active);});}

function renderLib(body){
 var wrap=E('div','lib');add(wrap,add(E('p','kicker'),E('span','',L('Tutoriels','Tutorials'))),E('h1','',L('Toutes les fonctions disponibles pour vous','Every feature available to you')),E('p','desc',L('Cette liste s’adapte à votre compte. Ouvrez une fiche pour savoir où la trouver et comment bien l’utiliser.','This list adapts to your account. Open a card to see where to find it and how to use it well.')));
 var items=libItems(),groups=[];items.forEach(function(x){if(groups.indexOf(x.g)<0)groups.push(x.g);});
 groups.forEach(function(g){add(wrap,E('p','lib-group',g));var grid=E('div','lib-grid');items.filter(function(x){return x.g===g;}).forEach(function(x){var t=E('button','tile');t.type='button';add(t,add(E('span','tile-k'),I(x.i),E('span','',x.name)),E('span','tile-t',x.short),E('span','tile-w',x.where.join(' › ')));t.addEventListener('click',function(){sheet=x.id;renderSheet(wrap,x);});add(grid,t);});add(wrap,grid);});
 add(body,wrap);var open=items.filter(function(x){return x.id===sheet;})[0];if(open)renderSheet(wrap,open);
}
function renderSheet(wrap,x){var old=wrap.querySelector('.overlay');if(old)old.remove();
 var ov=E('div','overlay'),sh=E('div','sheet'),h=E('div','sheet-h'),close=E('button','sheet-x');close.type='button';close.setAttribute('aria-label',L('Fermer','Close'));add(close,I('x'));
 add(h,I(x.i),add(E('div'),E('h2','',x.name),E('p','',x.what)),close);
 var b=E('div','sheet-b'),path=E('div','path');x.where.forEach(function(w,k){if(k)add(path,I('right'));add(path,E('span','',w));});
 add(b,E('p','sheet-l',L('Où la trouver','Where to find it')),path,E('p','sheet-l',L('Comment l’utiliser','How to use it')));
 var ol=E('ol','steps');x.how.forEach(function(t){add(ol,E('li','',t));});add(b,ol);
 if(x.example){var ex=E('div','example'),tb=E('button','try');tb.type='button';add(tb,E('span','',L('Essayer dans le chat','Try it in the chat')),I('arrowr'));tb.addEventListener('click',function(){sendPrompt(x.example);});add(ex,E('small','',L('Exemple','Example')),E('p','',x.example),tb);add(b,ex);}
 if(x.note)add(b,add(E('div','callout warn'),I('warn'),E('span','',x.note)));
 add(sh,h,b);add(ov,sh);
 function shut(){sheet=null;ov.remove();requestAnimationFrame(height);}
 close.addEventListener('click',shut);ov.addEventListener('click',function(e){if(e.target===ov)shut();});
 add(wrap,ov);wrap.style.minHeight=Math.max(420,sh.offsetHeight+60)+'px';setTimeout(function(){wrap.style.minHeight=Math.max(420,sh.offsetHeight+60)+'px';requestAnimationFrame(height);close.focus();},0);
}

function go(i){cur=Math.max(0,Math.min(steps.length-1,i));active=null;render();}
function finish(){save({status:'completed',index:cur});var g=document.getElementById('g');g.classList.add('slim');
 var r=document.querySelector('.g-right');r.replaceChildren();var again=E('button','g-close',L('Rouvrir le guide','Reopen guide'));again.type='button';again.addEventListener('click',function(){g.classList.remove('slim');save({status:'active'});location.reload();});
 add(r,E('span','slim-msg',L('Guide fermé','Guide closed')),again);requestAnimationFrame(height);}

document.querySelectorAll('[data-tab]').forEach(function(b){b.addEventListener('click',function(){tab=b.dataset.tab;sheet=null;render();});});
document.querySelectorAll('[data-lang]').forEach(function(b){b.addEventListener('click',function(){if(lang===b.dataset.lang)return;lang=b.dataset.lang;build();active=null;render();});});
document.getElementById('close').addEventListener('click',finish);
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&sheet){sheet=null;render();return;}if(tab!=='tour'||document.getElementById('g').classList.contains('slim'))return;if(e.key==='ArrowRight')go(cur+1);if(e.key==='ArrowLeft')go(cur-1);});

(function start(){var st=load();
 if(st.rev&&st.rev!==REV){updated=true;st.status='active';st.index=0;save({status:'active',index:0});}
 save({rev:REV});if(st.lang==='fr'||st.lang==='en')lang=st.lang;build();
 cur=Number.isInteger(st.index)?Math.max(0,Math.min(steps.length-1,st.index)):0;if(st.tab==='lib')tab='lib';
 render();if(st.status==='completed')finish();
 window.addEventListener('load',height);if('ResizeObserver' in window)new ResizeObserver(height).observe(document.body);})();
}());
</script>
</body>
</html>"""

FEATURE_DEFINITIONS = {
    "web_search": ("Recherche web", "Web search"),
    "image_generation": ("Génération d’images", "Image generation"),
    "code_interpreter": ("Interpréteur de code", "Code interpreter"),
    "file_upload": ("Téléversement de fichiers", "File upload"),
    "notes": ("Notes", "Notes"),
    "folders": ("Dossiers", "Folders"),
    "channels": ("Canaux", "Channels"),
    "memories": ("Mémoire", "Memory"),
    "automations": ("Automatisations", "Automations"),
    "calendar": ("Calendrier", "Calendar"),
    "multiple_models": ("Comparaison de modèles", "Multiple models"),
    "stt": ("Transcription vocale", "Speech to text"),
    "tts": ("Lecture vocale", "Text to speech"),
    "call": ("Conversation vocale", "Voice call"),
}

# Permissions that live under "chat" instead of "features" in Open WebUI.
CHAT_PERMISSION_KEYS = {"file_upload", "multiple_models", "stt", "tts", "call"}

# Instance-wide switches. If the administrator turned a feature off for the whole
# platform, the guide hides it for everyone, admins included.
GLOBAL_FEATURE_FLAGS = {
    "web_search": ("ENABLE_WEB_SEARCH", "ENABLE_RAG_WEB_SEARCH"),
    "image_generation": ("ENABLE_IMAGE_GENERATION",),
    "code_interpreter": ("ENABLE_CODE_INTERPRETER",),
    "notes": ("ENABLE_NOTES",),
    "channels": ("ENABLE_CHANNELS",),
    "calendar": ("ENABLE_CALENDAR",),
    "automations": ("ENABLE_AUTOMATIONS",),
    "folders": ("ENABLE_FOLDERS",),
    "memories": ("ENABLE_MEMORIES",),
}

# Every section switch exposed as a valve and forwarded to the page.
SECTION_FLAGS = [
    "show_cover", "show_welcome", "show_composer", "show_plus_menu", "show_tool_permissions",
    "show_integrations", "show_tools", "show_web_search", "show_models", "show_suggestions",
    "show_response_actions", "show_feedback", "show_sidebar_navigation", "show_user_menu",
    "show_notes", "show_folders", "show_channels", "show_calendar", "show_automations",
    "show_builtin_tools", "show_admin_section", "show_safety", "show_file_upload", "show_knowledge",
    "show_prompts", "show_memory", "show_image_generation", "show_code_interpreter", "show_voice",
    "show_multiple_models", "show_features_tab", "show_language_switch", "show_try_buttons",
]


class Event:
    class Valves(BaseModel):
        # ----------------------------------------------------------------- Master
        enabled: bool = Field(
            True,
            description="MASTER SWITCH. When OFF, the function does nothing at all: no guide is created, updated or deployed. Existing welcome chats stay as they are.",
        )
        production_enabled: bool = Field(
            False,
            description="SAFETY SWITCH FOR REAL USERS. Keep OFF while you validate the guide with the test account (see Testing valves). When OFF, sign-up, login and deployment events are ignored for everyone except the test user.",
        )

        # --------------------------------------------------------------- Delivery
        create_on_signup: bool = Field(
            True,
            description="NEW USERS. Create the guide as soon as an account is created (self sign-up, admin-created account, SSO/LDAP first login). Pending accounts are handled by 'skip_pending_users'.",
        )
        create_on_first_login: bool = Field(
            True,
            description="EXISTING USERS. If a user has never received the guide, create it at their next login. Covers accounts created before this function was installed and pending users who were approved later.",
        )
        skip_pending_users: bool = Field(
            True,
            description="Do not create a guide for accounts still in 'pending' role (they cannot use the platform yet). They receive it at their first login after approval.",
        )
        include_admins: bool = Field(
            True,
            description="Also deliver the guide to administrators. Admins see an extra 'Administration' chapter.",
        )
        deploy_to_all_users: bool = Field(
            False,
            description="SEND TO EVERYONE. When ON and you increase 'deployment_revision', every eligible user who does not have the guide yet receives it immediately in their sidebar. When OFF, a deployment only updates guides that already exist.",
        )
        deployment_revision: int = Field(
            0,
            ge=0,
            description="PUSH NOW. Increase this number and save to run a background deployment: existing guides are updated in place (no duplicate chat) and, if 'deploy_to_all_users' is ON, missing guides are created. Users already processed for this number are skipped, so saving twice or restarting the server is safe.",
        )
        deployment_batch_size: int = Field(
            50, ge=1, le=500, description="How many users are processed before pausing during a deployment. Lower it on small servers."
        )
        deployment_batch_delay_seconds: float = Field(
            0.5, ge=0, le=30, description="Pause between two batches during a deployment, to keep the server responsive."
        )
        recreate_deleted_guides: bool = Field(
            False,
            description="If a user deleted their welcome chat: OFF = respect their choice and never recreate it. ON = recreate it at the next login or deployment.",
        )

        # ---------------------------------------------------------------- Updates
        guide_revision: int = Field(
            1,
            ge=1,
            description="CONTENT VERSION. Increase it when you change the guide (texts, links, sections). Each user's existing guide is updated in place at their next login (or immediately with a deployment), and the guide shows a 'Guide updated' banner once, even if they had already finished it.",
        )
        update_notes_en: str = Field(
            "",
            max_length=500,
            description="Optional English text shown in the 'Guide updated' banner after a revision change, e.g. 'New: Calendar and Automations chapters.' Leave empty for a generic message.",
        )
        update_notes_fr: str = Field(
            "",
            max_length=500,
            description="Optional French text for the 'Guide updated' banner, e.g. 'Nouveau : chapitres Calendrier et Automatisations.'",
        )
        refresh_on_login: bool = Field(
            True,
            description="At login, update the user's guide when their permissions, tools or your valves have changed. A new 'guide_revision' is always applied at login, whatever the interval.",
        )
        refresh_interval_minutes: int = Field(
            60,
            ge=0,
            le=10080,
            description="Minimum time between two permission checks for the same user at login. 0 = check at every login.",
        )

        # ------------------------------------------------------------ Welcome chat
        title_emoji: str = Field("👋", max_length=8, description="Emoji placed in the chat title. Leave empty for no emoji.")
        welcome_title_en: str = Field(
            "{emoji} Welcome to {product}",
            max_length=120,
            description="English chat title shown in the sidebar. Placeholders: {emoji}, {product}.",
        )
        welcome_title_fr: str = Field(
            "{emoji} Bienvenue sur {product}",
            max_length=120,
            description="French chat title shown in the sidebar. Placeholders: {emoji}, {product}.",
        )
        update_title_on_refresh: bool = Field(
            True, description="When a guide is updated, also apply the current title (useful after changing the emoji or product name)."
        )
        pin_welcome_chat: bool = Field(
            True, description="Pin the welcome chat at the top of the user's sidebar when it is created. Users can unpin it; it is never re-pinned afterwards."
        )
        default_language: str = Field(
            "fr", pattern="^(fr|en)$", description="Language used when the user's own interface language is unknown: 'fr' or 'en'."
        )
        use_user_interface_language: bool = Field(
            True, description="Use each user's Open WebUI interface language (Settings > General) when it is French or English."
        )
        preferred_welcome_model_id: str = Field(
            "", description="Model ID attached to the welcome chat. Only used if the user is allowed to access it; otherwise their first accessible model is used."
        )
        default_group_id: str = Field(
            "", description="Optional group ID added to brand-new accounts before their permissions are read. Leave empty to disable. Never applied to existing users."
        )

        # ---------------------------------------------------------------- Branding
        product_name: str = Field("AI Assistant", max_length=60, description="Platform name shown in the guide and title.")
        organization_name: str = Field(
            "", max_length=120, description="Organization name shown under the product name. Leave empty to show none."
        )
        primary_color: str = Field("#1F4E79", description="Main brand colour (#RRGGBB): header, primary buttons. Set it to your own brand colour.")
        secondary_color: str = Field("#0EA5B7", description="Accent colour (#RRGGBB): highlights, progress, dark-mode buttons.")
        support_url: str = Field("", description="HTTPS link to your support page, shown on the last step. Non-HTTPS links are ignored.")
        privacy_url: str = Field("", description="HTTPS link to your privacy policy.")
        acceptable_use_url: str = Field("", description="HTTPS link to your AI acceptable-use rules.")
        feedback_url: str = Field("", description="HTTPS link to a feedback form about the guide.")

        # ------------------------------------------------------ Sections (on / off)
        show_cover: bool = Field(True, description="Cover page: what the guide is, key facts and clickable table of contents.")
        show_welcome: bool = Field(True, description="'Start' chapter: home screen, message box and suggested questions.")
        show_composer: bool = Field(True, description="Message box controls: +, Integrations, model selector, dictation, voice/send.")
        show_plus_menu: bool = Field(True, description="The + menu with every item and its sub-lists (only items the user can use).")
        show_tool_permissions: bool = Field(True, description="Full access vs Ask for approval. Only shown to users who have tools.")
        show_integrations: bool = Field(True, description="Integrations button: tools, toggle functions, web search, code interpreter.")
        show_tools: bool = Field(True, description="Tools sub-menu with the user's own tools.")
        show_web_search: bool = Field(True, description="Web search step and card. Hidden anyway if web search is disabled for the user.")
        show_models: bool = Field(True, description="Model selector: search, info icon, Set as default.")
        show_suggestions: bool = Field(True, description="Suggested questions step.")
        show_response_actions: bool = Field(True, description="Icons under each answer, including the user's action functions.")
        show_feedback: bool = Field(True, description="Positive and negative feedback steps.")
        show_sidebar_navigation: bool = Field(True, description="Sidebar chapter (only items the user has).")
        show_user_menu: bool = Field(True, description="User menu chapter: status, Workspace, Notes, Calendar, Automations, Settings, Sign Out.")
        show_notes: bool = Field(True, description="Notes chapter (list, editor, ⋯ menu). Requires Notes permission.")
        show_folders: bool = Field(True, description="Folders chapter. Requires Folders to be enabled.")
        show_channels: bool = Field(True, description="Channels chapter. Requires Channels permission.")
        show_calendar: bool = Field(True, description="Calendar chapter. Requires Calendar permission.")
        show_automations: bool = Field(True, description="Automations chapter. Requires Automations permission.")
        show_builtin_tools: bool = Field(True, description="Built-in tools chapter (date, notes, calendar, memory… only the ones the user has).")
        show_admin_section: bool = Field(True, description="Administration chapter (Admin Panel, Playground). Only ever shown to admins.")
        show_safety: bool = Field(True, description="Responsible use chapter.")
        show_file_upload: bool = Field(True, description="File upload card in the Features tab.")
        show_knowledge: bool = Field(True, description="Knowledge card and # shortcut. Requires access to at least one knowledge base.")
        show_prompts: bool = Field(True, description="Saved prompts (/) card. Requires access to at least one prompt.")
        show_memory: bool = Field(True, description="Memory card. Requires Memory permission.")
        show_image_generation: bool = Field(True, description="Image generation card. Requires permission.")
        show_code_interpreter: bool = Field(True, description="Code interpreter card. Requires permission.")
        show_voice: bool = Field(True, description="Voice card (dictation, voice mode, read aloud).")
        show_multiple_models: bool = Field(True, description="Compare models card. Requires permission.")
        show_features_tab: bool = Field(True, description="'Features' tab with every available feature as a card and pop-up tutorial.")
        show_language_switch: bool = Field(True, description="FR / EN switch in the guide header.")
        show_try_buttons: bool = Field(True, description="'Try it in the chat' buttons that place an example in the message box (never sends it).")

        # --------------------------------------------------------------- Privacy
        expose_tool_names: bool = Field(
            True, description="Show the names of the user's own tools in the guide. Only tools this user can already access are listed; never schemas, valves or secrets."
        )
        expose_function_names: bool = Field(
            True, description="Show the names of the user's toggle functions and response actions. Only from models this user can access."
        )
        max_items_per_section: int = Field(8, ge=1, le=30, description="Maximum tools, functions or actions listed per menu.")
        catalog_timeout_seconds: int = Field(
            20, ge=3, le=60, description="Maximum time to read one permission source. On timeout that source is treated as empty (hidden), never as allowed."
        )

        # ---------------------------------------------------------------- Testing
        test_user: str = Field("", description="Email or user ID of a test account. Works even when 'production_enabled' is OFF.")
        test_revision: int = Field(
            0, ge=0, description="Increase and save to (re)build the guide for the test user. Each increase replaces their previous test guide instead of piling up chats."
        )
        test_assign_group: bool = Field(False, description="Also add the test user to 'default_group_id' during the test.")

    def __init__(self):
        self.valves = self.Valves()
        self._redis = self._connect_redis()
        self._prefix = self._redis_prefix()
        self._local_locks: set[str] = set()
        self._tasks: set = set()

    # ================================================================ Events
    async def event(
        self,
        event: dict,
        __event_id__: str = None,
        __event_name__: str = None,
        __id__: str = None,
        __app__=None,
        __request__=None,
        **kwargs,
    ):
        try:
            if not self.valves.enabled:
                return

            if __event_name__ in ("auth.signup", "user.created"):
                user_id = self._user_id_from_event(__event_name__, event)
                if user_id and self.valves.create_on_signup and await self._allowed_target(user_id):
                    await self._ensure_guide(user_id, __app__, __request__, source=__event_name__, allow_create=True, assign_group=True)
                return

            if __event_name__ == "auth.login":
                user_id = self._user_id_from_event(__event_name__, event)
                if user_id and await self._allowed_target(user_id):
                    await self._handle_login(user_id, __app__, __request__)
                return

            if __event_name__ == "function.valves_updated":
                subject_id = str((event.get("subject") or {}).get("id") or "")
                if __id__ and subject_id == str(__id__):
                    await self._maybe_run_test(__app__, __request__)
                    if self.valves.production_enabled and int(self.valves.deployment_revision) > 0:
                        self._spawn(self._deploy(__app__, None if __app__ is not None else __request__, int(self.valves.deployment_revision)))
        except Exception:
            log.exception("[onboarding] unhandled event=%s id=%s", __event_name__, __event_id__)

    async def _allowed_target(self, user_id: str) -> bool:
        """Production users need production_enabled; the test user is always allowed."""
        if self.valves.production_enabled:
            return True
        target = (self.valves.test_user or "").strip()
        return bool(target) and (await self._resolve_user(target)) == user_id

    async def _handle_login(self, user_id: str, app, request) -> None:
        marker = await self._get_marker(user_id)
        if not self._marker_valid(marker):
            if self.valves.create_on_first_login:
                await self._ensure_guide(user_id, app, request, source="auth.login", allow_create=True)
            return
        if not self.valves.refresh_on_login:
            return
        content_changed = (
            int(marker.get("guide_revision", 0)) != int(self.valves.guide_revision)
            or int(marker.get("template_revision", 0)) != TEMPLATE_REVISION
        )
        age = int(time.time()) - int(marker.get("refreshed_at", marker.get("created_at", 0)) or 0)
        if content_changed or age >= int(self.valves.refresh_interval_minutes) * 60:
            await self._ensure_guide(user_id, app, request, source="auth.login", allow_create=self.valves.recreate_deleted_guides)

    # ============================================================ Deployment
    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deploy(self, app, request, revision: int) -> None:
        job_key = f"{self._prefix}:secure-onboarding:deploy:{revision}"
        token = self._acquire_key(job_key, ttl=6 * 3600)
        if token is None:
            log.info("[onboarding] deployment %s already running", revision)
            return
        created = updated = skipped = failed = 0
        try:
            from open_webui.models.users import Users

            batch = int(self.valves.deployment_batch_size)
            skip = 0
            while True:
                page = await self._maybe_await(Users.get_users(skip=skip, limit=batch))
                users = page.get("users", []) if isinstance(page, dict) else list(page or [])
                if not users:
                    break
                for user in users:
                    user_id = str(self._value(user, "id", ""))
                    if not user_id:
                        continue
                    try:
                        marker = await self._get_marker(user_id)
                        if marker and int(marker.get("deployment_revision", 0)) >= revision:
                            skipped += 1
                            continue
                        allow_create = bool(self.valves.deploy_to_all_users) and (
                            not (marker or {}).get("deleted_by_user") or self.valves.recreate_deleted_guides
                        )
                        result = await self._ensure_guide(
                            user_id, app, request, source=f"deploy:{revision}", allow_create=allow_create, deployment_revision=revision
                        )
                        if result == "created":
                            created += 1
                        elif result == "updated":
                            updated += 1
                        else:
                            skipped += 1
                    except Exception:
                        failed += 1
                        log.exception("[onboarding] deployment %s failed for user=%s", revision, user_id)
                if len(users) < batch:
                    break
                skip += batch
                await asyncio.sleep(float(self.valves.deployment_batch_delay_seconds))
            log.info(
                "[onboarding] deployment %s done: created=%s updated=%s skipped=%s failed=%s",
                revision, created, updated, skipped, failed,
            )
        finally:
            self._release_key(job_key, token)

    # ======================================================== Core delivery
    async def _ensure_guide(
        self,
        user_id: str,
        app,
        request,
        source: str,
        allow_create: bool,
        assign_group: bool = False,
        deployment_revision: Optional[int] = None,
        force: bool = False,
    ) -> str:
        """Guarantee at most one guide per user, create or update it. Returns created|updated|unchanged|skipped."""
        from open_webui.models.chats import Chats
        from open_webui.models.users import Users

        token = self._acquire_key(self._lock_key(user_id), ttl=300)
        if token is None:
            return "skipped"
        try:
            user = await Users.get_user_by_id(user_id)
            if user is None:
                return "skipped"
            role = str(getattr(user, "role", "user") or "user")
            if role == "pending" and self.valves.skip_pending_users:
                return "skipped"
            if role == "admin" and not self.valves.include_admins:
                return "skipped"

            marker = await self._get_marker(user_id)
            chat = None
            if self._marker_valid(marker):
                chat = await Chats.get_chat_by_id_and_user_id(str(marker["chat_id"]), user_id)
                if chat is None:
                    if not (self.valves.recreate_deleted_guides and allow_create):
                        new_marker = {**marker, "deleted_by_user": True}
                        if deployment_revision is not None:
                            new_marker["deployment_revision"] = deployment_revision
                        await self._save_marker(user_id, new_marker)
                        return "skipped"
                    marker = None

            if chat is None:
                if not allow_create:
                    return "skipped"
                if assign_group and (self.valves.default_group_id or "").strip():
                    await self._assign_group(user_id)
                    user = await Users.get_user_by_id(user_id) or user
                snapshot = await self._build_snapshot(user, app, request)
                created = await self._create_welcome_chat(user, snapshot)
                if not created:
                    return "skipped"
                created.update({"version": ONBOARDING_VERSION, "source": source})
                if deployment_revision is not None:
                    created["deployment_revision"] = deployment_revision
                await self._save_marker(user_id, created)
                log.info("[onboarding] created user=%s source=%s", user_id, source)
                return "created"

            snapshot = await self._build_snapshot(user, app, request)
            new_hash = self._snapshot_hash(snapshot)
            now = int(time.time())
            new_marker = {
                **marker,
                "refreshed_at": now,
                "catalog_hash": new_hash,
                "guide_revision": int(self.valves.guide_revision),
                "template_revision": TEMPLATE_REVISION,
                "deleted_by_user": False,
            }
            if deployment_revision is not None:
                new_marker["deployment_revision"] = deployment_revision
            changed = force or new_hash != marker.get("catalog_hash")
            if changed:
                message_id = str(marker["message_id"])
                stored = (getattr(chat, "chat", None) or {}).get("history", {}).get("messages", {}).get(message_id, {})
                message = {**stored, "content": self._fallback_text(snapshot), "embeds": [self._render_html(snapshot)]}
                result = await Chats.upsert_message_to_chat_by_id_and_message_id(str(marker["chat_id"]), message_id, message, touch=False)
                if result is None:
                    log.warning("[onboarding] update failed user=%s", user_id)
                    return "skipped"
                if self.valves.update_title_on_refresh:
                    await self._set_title(str(marker["chat_id"]), self._title(snapshot))
            await self._save_marker(user_id, new_marker)
            return "updated" if changed else "unchanged"
        finally:
            self._release_key(self._lock_key(user_id), token)

    async def _create_welcome_chat(self, user, snapshot: dict) -> Optional[dict]:
        from open_webui.models.chats import ChatForm, Chats

        now = int(time.time())
        chat_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        model_id = snapshot.get("selected_model_id") or ""
        message = {
            "id": message_id,
            "parentId": None,
            "childrenIds": [],
            "role": "assistant",
            "content": self._fallback_text(snapshot),
            "embeds": [self._render_html(snapshot)],
            "files": [],
            "sources": [],
            "model": model_id,
            "modelName": model_id,
            "modelIdx": 0,
            "timestamp": now,
            "done": True,
        }
        chat = {
            "id": "",
            "title": self._title(snapshot),
            "models": [model_id] if model_id else [],
            "params": {},
            "history": {"messages": {message_id: message}, "currentId": message_id},
            "messages": [message],
            "tags": [],
            "timestamp": now * 1000,
            "meta": {"secure_onboarding": {"version": ONBOARDING_VERSION, "created_at": now}},
        }
        result = await Chats.insert_new_chat(chat_id, str(user.id), ChatForm(chat=chat))
        if result is None:
            return None
        if self.valves.pin_welcome_chat:
            await self._pin(chat_id, str(user.id))
        return {
            "chat_id": chat_id,
            "message_id": message_id,
            "created_at": now,
            "refreshed_at": now,
            "catalog_hash": self._snapshot_hash(snapshot),
            "guide_revision": int(self.valves.guide_revision),
            "template_revision": TEMPLATE_REVISION,
        }

    async def _set_title(self, chat_id: str, title: str) -> None:
        try:
            from open_webui.models.chats import Chats

            method = getattr(Chats, "update_chat_title_by_id", None)
            if method:
                await self._maybe_await(method(chat_id, title))
        except Exception:
            log.warning("[onboarding] title update not supported", exc_info=True)

    async def _pin(self, chat_id: str, user_id: str) -> None:
        try:
            from open_webui.models.chats import Chats

            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            method = getattr(Chats, "toggle_chat_pinned_by_id", None)
            if chat is not None and method and not getattr(chat, "pinned", False):
                await self._maybe_await(method(chat_id))
        except Exception:
            log.warning("[onboarding] pinning not supported", exc_info=True)

    # ============================================================== Snapshot
    async def _build_snapshot(self, user, app, request) -> dict:
        req = self._usable_request(app, request)
        timeout = int(self.valves.catalog_timeout_seconds)
        role = str(getattr(user, "role", "user") or "user")
        is_admin = role == "admin"

        async def safe(name: str, coroutine, fallback):
            # Any failure hides the resource. We never fall back to "allowed".
            try:
                return await asyncio.wait_for(coroutine, timeout=timeout)
            except Exception as exc:
                log.warning("[onboarding] catalog source=%s user=%s hidden: %s", name, user.id, type(exc).__name__)
                return fallback

        permissions, models, prompts, tools, knowledge = await asyncio.gather(
            safe("permissions", self._load_permissions(user), {}),
            safe("models", self._load_models(req, user), []),
            safe("prompts", self._load_prompts(user), []),
            safe("tools", self._load_tools(req, user), []),
            safe("knowledge", self._load_knowledge(user), []),
        )

        features = self._effective_features(permissions, is_admin, req)
        enabled = {f["key"] for f in features}
        workspace_perms = (permissions.get("workspace") or {}) if isinstance(permissions, dict) else {}
        workspace = {k: bool(is_admin or workspace_perms.get(k, False)) for k in ("models", "prompts", "skills", "tools", "knowledge")}
        max_items = int(self.valves.max_items_per_section)

        resources = {
            "features": features,
            "tools": [self._named(x) for x in tools[:max_items]] if self.valves.expose_tool_names else [],
            "toggles": self._unique_functions(models, "filters", max_items),
            "actions": self._unique_functions(models, "actions", max_items),
        }

        preferred = (self.valves.preferred_welcome_model_id or "").strip()
        model_ids = [str(self._value(m, "id", "")) for m in models if self._value(m, "id", "")]
        selected_model_id = preferred if preferred in model_ids else (model_ids[0] if model_ids else "")

        ui = {flag: bool(getattr(self.valves, flag)) for flag in SECTION_FLAGS}
        ui["default_language"] = self._language_for(user)
        ui["is_admin"] = is_admin
        # A section switch can never reveal something the user has no right to.
        ui["show_admin_section"] = ui["show_admin_section"] and is_admin

        now = int(time.time())
        return {
            "schema": 2,
            "template_revision": TEMPLATE_REVISION,
            "guide_revision": int(self.valves.guide_revision),
            "update_notes": {
                "fr": self._plain(self.valves.update_notes_fr, 500),
                "en": self._plain(self.valves.update_notes_en, 500),
            },
            "generated_at": now,
            "brand": {
                "product": self._plain(self.valves.product_name, 60) or "AI Assistant",
                "organization": self._plain(self.valves.organization_name, 120),
                "primary": self._safe_color(self.valves.primary_color, "#1F4E79"),
                "secondary": self._safe_color(self.valves.secondary_color, "#0EA5B7"),
            },
            "progress_scope": hashlib.sha256(f"{user.id}:{ONBOARDING_VERSION}".encode("utf-8")).hexdigest()[:20],
            "ui": ui,
            "links": {
                "support_url": self._safe_http_url(self.valves.support_url),
                "privacy_url": self._safe_http_url(self.valves.privacy_url),
                "acceptable_use_url": self._safe_http_url(self.valves.acceptable_use_url),
                "feedback_url": self._safe_http_url(self.valves.feedback_url),
            },
            "selected_model_id": selected_model_id,
            "available": {
                "models": bool(models),
                "prompts": bool(prompts),
                "tools": bool(tools),
                "knowledge": bool(knowledge),
            },
            "access": {"workspace": workspace},
            "resources": resources,
            "_enabled": sorted(enabled),
        }

    def _effective_features(self, permissions: dict, is_admin: bool, request) -> list[dict]:
        """A feature is shown only if it is ON for the platform AND allowed for this user."""
        features = permissions.get("features", {}) if isinstance(permissions, dict) else {}
        chat = permissions.get("chat", {}) if isinstance(permissions, dict) else {}
        config = getattr(getattr(getattr(request, "app", None), "state", None), "config", None)
        results = []
        for key, labels in FEATURE_DEFINITIONS.items():
            if not self._globally_enabled(config, key):
                continue
            source = chat if key in CHAT_PERMISSION_KEYS else features
            allowed = is_admin or (bool(source.get(key, False)) if isinstance(source, dict) else False)
            if allowed:
                results.append({"key": key, "enabled": True, "label": {"fr": labels[0], "en": labels[1]}})
        return results

    @staticmethod
    def _globally_enabled(config, key: str) -> bool:
        names = GLOBAL_FEATURE_FLAGS.get(key)
        if not names or config is None:
            return True
        for name in names:
            try:
                value = getattr(config, name)
            except Exception:
                continue
            value = getattr(value, "value", value)
            if isinstance(value, bool):
                return value
        return True

    async def _load_permissions(self, user) -> dict:
        from open_webui.models.config import Config
        from open_webui.utils.access_control import get_permissions

        defaults = await Config.get("user.permissions")
        return await get_permissions(str(user.id), defaults or {})

    async def _load_models(self, request, user) -> list[dict]:
        from open_webui.utils.models import get_all_models, get_filtered_models

        models = await get_all_models(request, refresh=False, user=user)
        models = [m for m in models if not (isinstance(m.get("pipeline"), dict) and m["pipeline"].get("type") == "filter")]
        models = list({str(m.get("id")): m for m in models if m.get("id")}.values())
        return await get_filtered_models(models, user)

    async def _load_prompts(self, user) -> list:
        from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
        from open_webui.models.prompts import Prompts

        if getattr(user, "role", None) == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
            return await Prompts.get_prompts()
        return await Prompts.get_prompts_by_user_id(str(user.id), "read")

    async def _load_tools(self, request, user) -> list:
        from open_webui.routers.tools import get_tools

        return await get_tools(request=request, query=None, user=user, db=None)

    async def _load_knowledge(self, user) -> list:
        from open_webui.routers.knowledge import get_knowledge_bases

        result = await get_knowledge_bases(page=1, user=user, db=None)
        return list(getattr(result, "items", None) or self._value(result, "items", []) or [])

    def _named(self, item) -> dict:
        meta = self._value(item, "meta", {}) or {}
        return {
            "name": self._plain(self._value(item, "name", ""), 80),
            "description": self._plain(self._value(meta, "description", "") or self._value(item, "description", ""), 160),
        }

    def _unique_functions(self, models: list, key: str, limit: int) -> list[dict]:
        if not self.valves.expose_function_names:
            return []
        seen, out = set(), []
        for model in models:
            for fn in (model.get(key) or []) if isinstance(model, dict) else []:
                fid = str((fn or {}).get("id") or (fn or {}).get("name") or "")
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                out.append({"name": self._plain(fn.get("name") or fid, 80), "description": self._plain(fn.get("description", ""), 160)})
                if len(out) >= limit:
                    return out
        return out

    def _language_for(self, user) -> str:
        if self.valves.use_user_interface_language:
            settings = self._settings_dict(user)
            language = str(((settings.get("ui") or {}) if isinstance(settings, dict) else {}).get("language") or "").lower()
            if language.startswith("fr"):
                return "fr"
            if language.startswith("en"):
                return "en"
        return self.valves.default_language

    def _title(self, snapshot: dict) -> str:
        lang = (snapshot.get("ui") or {}).get("default_language", self.valves.default_language)
        template = self.valves.welcome_title_en if lang == "en" else self.valves.welcome_title_fr
        title = template.replace("{emoji}", self.valves.title_emoji or "").replace("{product}", snapshot["brand"]["product"])
        return self._plain(title, 120) or "Welcome"

    def _render_html(self, snapshot: dict) -> str:
        public = {k: v for k, v in snapshot.items() if not k.startswith("_")}
        payload = json.dumps(public, ensure_ascii=False, separators=(",", ":"))
        # Prevent script termination and HTML parser ambiguity inside the JSON script block.
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return ONBOARDING_HTML.replace("__SNAPSHOT_JSON__", payload)

    def _snapshot_hash(self, snapshot: dict) -> str:
        stable = {k: v for k, v in snapshot.items() if k not in {"generated_at"}}
        raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _fallback_text(self, snapshot: dict) -> str:
        lang = (snapshot.get("ui") or {}).get("default_language", self.valves.default_language)
        return FALLBACK_TEXT_EN if lang == "en" else FALLBACK_TEXT_FR

    # ================================================================ Testing
    async def _maybe_run_test(self, app, request) -> None:
        target = (self.valves.test_user or "").strip()
        revision = int(self.valves.test_revision or 0)
        if not target or revision <= 0:
            return
        user_id = await self._resolve_user(target)
        if not user_id:
            log.warning("[onboarding] test user not found target=%s", target)
            return
        from open_webui.models.users import Users

        user = await Users.get_user_by_id(user_id)
        state = self._settings_dict(user).get(TEST_SETTINGS_KEY) or {}
        if isinstance(state, dict) and revision <= int(state.get("revision", 0) or 0):
            return
        if self.valves.test_assign_group:
            await self._assign_group(user_id)
        result = await self._ensure_guide(user_id, app, request, source=f"test:{revision}", allow_create=True, force=True)
        await self._update_user_settings(user_id, {TEST_SETTINGS_KEY: {"revision": revision, "run_at": int(time.time()), "result": result}})

    # ============================================================== Storage
    @staticmethod
    def _marker_valid(marker: Optional[dict]) -> bool:
        return bool(marker) and int(marker.get("version", 0)) >= ONBOARDING_VERSION and bool(marker.get("chat_id")) and bool(marker.get("message_id"))

    async def _get_marker(self, user_id: str) -> Optional[dict]:
        try:
            from open_webui.models.users import Users

            user = await Users.get_user_by_id(user_id)
            marker = self._settings_dict(user).get(SETTINGS_KEY)
            return dict(marker) if isinstance(marker, dict) else None
        except Exception:
            log.warning("[onboarding] marker read failed user=%s", user_id, exc_info=True)
            return None

    async def _save_marker(self, user_id: str, marker: dict) -> None:
        await self._update_user_settings(user_id, {SETTINGS_KEY: marker})

    async def _assign_group(self, user_id: str) -> None:
        group_id = (self.valves.default_group_id or "").strip()
        if not group_id:
            return
        try:
            from open_webui.models.groups import Groups

            if await Groups.add_users_to_group(group_id, [user_id]) is None:
                log.warning("[onboarding] default group not found id=%s", group_id)
        except Exception:
            log.exception("[onboarding] default group assignment failed user=%s", user_id)

    def _redis_call(self, action, what: str):
        """Run a Redis command, reconnecting once if the connection was dropped.

        Idle connections are closed by Redis or by the proxy in front of it, so the
        first command after a quiet period can fail with a broken pipe. That is
        routine, not an error: we reconnect and retry, and only fall back to
        process-local locking if the second attempt also fails.
        """
        if self._redis is None:
            return None, False
        for attempt in (1, 2):
            try:
                return action(self._redis), True
            except Exception as exc:
                if attempt == 1:
                    log.info("[onboarding] redis %s retry after %s", what, type(exc).__name__)
                    self._redis = self._connect_redis()
                    if self._redis is None:
                        return None, False
                    continue
                log.warning("[onboarding] redis %s unavailable (%s), using process-local locking", what, type(exc).__name__)
                return None, False
        return None, False

    def _acquire_key(self, key: str, ttl: int) -> Optional[str]:
        token = str(uuid.uuid4())
        result, ok = self._redis_call(lambda r: r.set(key, token, nx=True, ex=ttl), "lock")
        if ok:
            return token if result else None
        if key in self._local_locks:
            return None
        self._local_locks.add(key)
        return token

    def _release_key(self, key: str, token: str) -> None:
        self._redis_call(
            lambda r: r.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1, key, token,
            ),
            "unlock",
        )
        self._local_locks.discard(key)

    def _lock_key(self, user_id: str) -> str:
        return f"{self._prefix}:secure-onboarding:lock:user:{user_id}"

    # ============================================================== Helpers
    @staticmethod
    async def _maybe_await(value):
        if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future):
            return await value
        return value

    @staticmethod
    def _user_id_from_event(event_name: str, event: dict) -> Optional[str]:
        actor = event.get("actor") if isinstance(event, dict) else None
        if event_name in ("auth.signup", "auth.login") and isinstance(actor, dict) and actor.get("id"):
            return str(actor["id"])
        for key in ("subject", "data", "user", "object", "actor"):
            value = event.get(key) if isinstance(event, dict) else None
            if isinstance(value, dict) and value.get("id"):
                return str(value["id"])
        return None

    @staticmethod
    async def _resolve_user(value: str) -> Optional[str]:
        from open_webui.models.users import Users

        user = await Users.get_user_by_email(value) if "@" in value else await Users.get_user_by_id(value)
        return str(getattr(user, "id")) if user and getattr(user, "id", None) else None

    @staticmethod
    async def _update_user_settings(user_id: str, patch: dict) -> None:
        from open_webui.models.users import Users

        if await Users.update_user_settings_by_id(user_id, patch) is None:
            raise RuntimeError("User settings update failed")

    @staticmethod
    def _settings_dict(user) -> dict:
        if user is None:
            return {}
        settings = getattr(user, "settings", None)
        if isinstance(settings, dict):
            return dict(settings)
        if hasattr(settings, "model_dump"):
            try:
                return settings.model_dump()
            except Exception:
                return {}
        return {}

    @staticmethod
    def _value(value: Any, key: str, default=None):
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @staticmethod
    def _plain(value: Any, limit: int) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[: int(limit)]

    @staticmethod
    def _safe_color(value: str, fallback: str) -> str:
        candidate = (value or "").strip().upper()
        return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else fallback

    @staticmethod
    def _safe_http_url(value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            return ""
        try:
            parsed = urlparse(candidate)
            return candidate if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password else ""
        except Exception:
            return ""

    @staticmethod
    def _usable_request(app, request):
        if request is not None and getattr(request, "app", None) is not None:
            return request
        if app is None:
            raise RuntimeError("Open WebUI application context is required to build the access catalog")
        from starlette.requests import Request

        return Request({
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
            "path": "/", "raw_path": b"/", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 443), "app": app,
        })

    @staticmethod
    def _connect_redis():
        try:
            from open_webui.env import REDIS_URL
            from open_webui.utils.redis import get_redis_connection

            if REDIS_URL:
                try:
                    # Ping idle connections before use so a dropped socket is
                    # reopened instead of raising on the next command.
                    return get_redis_connection(REDIS_URL, decode_responses=True, health_check_interval=30)
                except TypeError:
                    return get_redis_connection(REDIS_URL, decode_responses=True)
        except Exception:
            log.info("[onboarding] redis unavailable, using process-local locking")
        return None

    @staticmethod
    def _redis_prefix() -> str:
        try:
            from open_webui.env import REDIS_KEY_PREFIX

            return REDIS_KEY_PREFIX or "open-webui"
        except Exception:
            return "open-webui"
