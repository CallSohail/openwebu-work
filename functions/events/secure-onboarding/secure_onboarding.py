"""
title: Secure Dynamic Onboarding Rich UI
author: Open WebUI administrator
version: 4.1.0
required_open_webui_version: 0.11.3
description: Privacy-first, bilingual, role-aware onboarding built from each user's accessible Open WebUI resources.
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

ONBOARDING_VERSION = 4
SETTINGS_KEY = "evegpt_onboarding"
TEST_SETTINGS_KEY = "evegpt_onboarding_test"

FALLBACK_TEXT_FR = """## Bienvenue sur AI Assistant

Votre guide interactif et personnalisé s'affiche au-dessus de ce message. Il est
construit uniquement à partir des modèles, prompts, outils, compétences, bases de
connaissances et fonctions auxquels votre compte peut accéder.

Si le guide ne s'affiche pas, actualisez la page ou contactez le support AI Assistant.
"""

FALLBACK_TEXT_EN = """## Welcome to AI Assistant

Your personalized interactive guide appears above this message. It is built only
from models, prompts, tools, skills, knowledge bases, and functions your account
is allowed to access.

If the guide does not appear, refresh the page or contact AI Assistant support.
"""


# The iframe is intentionally self-contained. It loads no scripts, fonts, images,
# or data from third-party origins. Dynamic values are serialized as inert JSON.
ONBOARDING_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none';">
<title>Interactive onboarding</title>
<style>
:root{color-scheme:light;--bg:#f5f5f6;--surface:#fff;--raised:#fff;--soft:#f7f7f8;--soft2:#efeff1;--text:#18181b;--muted:#62626b;--line:#dedee3;--accent:#5b4df7;--accent2:#786df9;--accent-soft:#efedff;--good:#14804a;--warn:#a15c00;--shadow:0 18px 55px rgba(24,24,27,.11);--shadow2:0 22px 70px rgba(24,24,27,.18);--font:Arial,Helvetica,sans-serif}
[data-theme="dark"]{color-scheme:dark;--bg:#0e0e10;--surface:#171719;--raised:#202023;--soft:#1d1d20;--soft2:#29292e;--text:#f5f5f6;--muted:#b1b1ba;--line:#36363d;--accent:#a598ff;--accent2:#c0b8ff;--accent-soft:#2d2949;--good:#67d99b;--warn:#f3c85b;--shadow:0 20px 60px rgba(0,0,0,.38);--shadow2:0 26px 85px rgba(0,0,0,.55)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0e0e10;--surface:#171719;--raised:#202023;--soft:#1d1d20;--soft2:#29292e;--text:#f5f5f6;--muted:#b1b1ba;--line:#36363d;--accent:#a598ff;--accent2:#c0b8ff;--accent-soft:#2d2949;--good:#67d99b;--warn:#f3c85b;--shadow:0 20px 60px rgba(0,0,0,.38);--shadow2:0 26px 85px rgba(0,0,0,.55)}}
*{box-sizing:border-box}html,body{margin:0;min-width:0}body{padding:6px;background:transparent;color:var(--text);font:16px/1.55 var(--font);text-rendering:optimizeLegibility}
button,select,a{font:inherit}button,summary,select,a{outline:none}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid color-mix(in srgb,var(--accent) 52%,transparent);outline-offset:3px}
.shell{width:100%;max-width:1160px;margin:auto;overflow:hidden;border:1px solid var(--line);border-radius:24px;background:var(--surface);box-shadow:var(--shadow)}
.header{padding:22px 28px 18px;border-bottom:1px solid var(--line);background:var(--surface)}
.row,.brand,.controls,.meta,.progress-row,.footer,.card-top,.resource-meta,.modal-actions{display:flex;align-items:center}.row{justify-content:space-between;gap:18px}.brand{gap:12px;min-width:0}.mark{display:grid;place-items:center;width:40px;height:40px;flex:0 0 auto;border-radius:12px;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));font-size:14px;font-weight:900;letter-spacing:-.03em}.brand strong{display:block;font-size:16px;line-height:1.2}.brand span{display:block;margin-top:2px;color:var(--muted);font-size:13px}.controls{gap:8px}.select{height:40px;padding:0 12px;border:1px solid var(--line);border-radius:10px;color:var(--text);background:var(--soft);cursor:pointer}
.hero{max-width:800px;margin-top:25px}.eyebrow{margin:0 0 6px;color:var(--accent);font-size:13px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.hero h1{margin:0;font-size:clamp(30px,4.5vw,46px);line-height:1.08;letter-spacing:-.045em}.hero p{margin:11px 0 0;color:var(--muted);font-size:16px}.meta{flex-wrap:wrap;gap:7px;margin-top:14px}.pill{display:inline-flex;align-items:center;min-height:28px;padding:4px 10px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--soft);font-size:13px;font-weight:700}.pill.good{color:var(--good)}
.progress-row{gap:13px;margin-top:20px}.track{height:6px;flex:1;overflow:hidden;border-radius:99px;background:var(--soft2)}.bar{width:0;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .28s ease}.progress-text{min-width:84px;color:var(--muted);font-size:13px;text-align:right}
.layout{display:grid;grid-template-columns:238px minmax(0,1fr)}.rail{padding:15px 11px;border-right:1px solid var(--line);background:var(--soft)}.rail button{display:flex;align-items:center;width:100%;min-height:46px;gap:10px;padding:8px 10px;border:0;border-radius:12px;color:var(--muted);background:transparent;font-size:14px;font-weight:700;text-align:left;cursor:pointer}.rail button+button{margin-top:4px}.rail button:hover{color:var(--text);background:var(--surface)}.rail button[aria-current="step"]{color:var(--text);background:var(--surface);box-shadow:0 4px 15px rgba(24,24,27,.08)}.step-number{display:grid;place-items:center;width:27px;height:27px;flex:0 0 auto;border:1px solid var(--line);border-radius:50%;background:var(--surface);font-size:12px}.rail button[aria-current="step"] .step-number{border-color:var(--accent);color:#fff;background:var(--accent)}.rail button.done .step-number{border-color:color-mix(in srgb,var(--good) 55%,var(--line));color:var(--good)}
.main{position:relative;min-width:0;padding:30px 64px 23px}.stage{min-height:500px}.panel[hidden]{display:none}.panel{animation:enterRight .28s cubic-bezier(.2,.72,.2,1) both}.panel.reverse{animation-name:enterLeft}.panel h2{margin:0;max-width:760px;font-size:clamp(27px,3.5vw,36px);line-height:1.15;letter-spacing:-.035em}.lead{max-width:780px;margin:9px 0 22px;color:var(--muted);font-size:16px}
.edge{position:absolute;top:46%;z-index:3;display:grid;place-items:center;width:46px;height:46px;border:1px solid var(--line);border-radius:50%;color:var(--text);background:var(--raised);box-shadow:0 8px 26px rgba(24,24,27,.12);font-size:24px;line-height:1;cursor:pointer;transition:transform .16s ease,background .16s ease}.edge:hover{transform:scale(1.06);background:var(--accent-soft)}.edge:disabled{opacity:.3;cursor:not-allowed;transform:none}.edge.prev{left:10px}.edge.next{right:10px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.card{position:relative;min-height:154px;padding:17px;border:1px solid var(--line);border-radius:16px;color:var(--text);background:var(--raised);text-align:left;cursor:pointer;transition:transform .17s ease,border-color .17s ease,box-shadow .17s ease}.card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--accent) 45%,var(--line));box-shadow:0 12px 30px rgba(24,24,27,.09)}.card-top{align-items:flex-start;gap:12px}.icon{display:grid;place-items:center;width:34px;height:34px;flex:0 0 auto;border-radius:10px;color:var(--accent);background:var(--accent-soft);font-size:13px;font-weight:900}.card h3{margin:1px 0 0;font-size:17px;line-height:1.25}.card p{margin:9px 0 0;color:var(--muted);font-size:14px}.path-preview{display:flex;align-items:center;flex-wrap:wrap;gap:5px;margin-top:13px;color:var(--muted);font-size:12px}.path-preview span{padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--soft)}.learn{display:inline-flex;align-items:center;gap:5px;margin-top:13px;color:var(--accent);font-size:13px;font-weight:800}.learn:after{content:"→"}
.notice{padding:14px 15px;border:1px solid var(--line);border-radius:14px;color:var(--muted);background:var(--soft);font-size:14px}.notice strong{color:var(--text)}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:16px 0}.stat{padding:14px;border:1px solid var(--line);border-radius:13px;background:var(--raised)}.stat b{display:block;font-size:25px;line-height:1.1}.stat span{display:block;margin-top:5px;color:var(--muted);font-size:13px}
.section-label{margin:22px 0 9px;color:var(--muted);font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.resource-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.resource-card{min-height:120px}.resource-meta{justify-content:space-between;align-items:flex-start;gap:10px}.resource-id{display:block;margin-top:3px;color:var(--muted);font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.status{display:inline-flex;align-items:center;gap:6px;color:var(--good);font-size:12px;font-weight:800;white-space:nowrap}.status:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chip{padding:4px 7px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--soft);font-size:12px}
.footer{justify-content:space-between;gap:12px;margin-top:24px;padding-top:17px;border-top:1px solid var(--line)}.fresh{color:var(--muted);font-size:13px}.footer-buttons{display:flex;gap:8px}.primary,.secondary{min-height:42px;padding:8px 15px;border-radius:10px;font-weight:800;cursor:pointer}.primary{border:1px solid var(--accent);color:#fff;background:var(--accent)}.primary:hover{background:var(--accent2)}.secondary{border:1px solid var(--line);color:var(--text);background:var(--surface)}.secondary:disabled{opacity:.4;cursor:not-allowed}
.backdrop{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:22px;background:rgba(9,9,11,.58);backdrop-filter:blur(3px);animation:fadeIn .16s ease}.backdrop[hidden]{display:none}.modal{width:min(760px,100%);max-height:min(760px,calc(100vh - 44px));overflow:auto;border:1px solid var(--line);border-radius:20px;background:var(--surface);box-shadow:var(--shadow2);animation:modalIn .22s cubic-bezier(.2,.72,.2,1)}.modal-header{position:sticky;top:0;z-index:2;display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:21px 22px 17px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--surface) 92%,transparent);backdrop-filter:blur(10px)}.modal-header h2{margin:0;font-size:25px;line-height:1.2;letter-spacing:-.025em}.modal-header p{margin:6px 0 0;color:var(--muted);font-size:14px}.close{display:grid;place-items:center;width:38px;height:38px;flex:0 0 auto;border:1px solid var(--line);border-radius:10px;color:var(--text);background:var(--soft);font-size:20px;cursor:pointer}.modal-body{padding:21px 22px 23px}.label{margin:0 0 8px;color:var(--muted);font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.where{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:20px}.where span{padding:5px 8px;border:1px solid var(--line);border-radius:8px;background:var(--soft);font-size:13px;font-weight:700}.where i{color:var(--muted);font-style:normal}.steps{margin:0 0 21px;padding:0;list-style:none;counter-reset:guide}.steps li{position:relative;min-height:29px;margin:0 0 10px;padding-left:38px;color:var(--muted);font-size:15px;counter-increment:guide}.steps li:before{content:counter(guide);position:absolute;left:0;top:-1px;display:grid;place-items:center;width:27px;height:27px;border:1px solid var(--line);border-radius:50%;color:var(--text);background:var(--soft);font-size:12px;font-weight:900}.examples{display:grid;gap:9px}.example{padding:13px;border:1px solid var(--line);border-radius:11px;background:var(--soft)}.example code{display:block;color:var(--text);font:14px/1.5 var(--font);white-space:pre-wrap;overflow-wrap:anywhere}.modal-actions{justify-content:flex-end;gap:8px;margin-top:10px}.try{min-height:36px;padding:6px 10px;border:1px solid var(--accent);border-radius:9px;color:var(--accent);background:transparent;font-size:13px;font-weight:800;cursor:pointer}.try:hover{color:#fff;background:var(--accent)}.dialog-note{margin-top:18px;padding:13px 14px;border-left:3px solid var(--accent);border-radius:0 10px 10px 0;color:var(--muted);background:var(--accent-soft);font-size:14px}
.toast{position:fixed;left:50%;bottom:18px;z-index:30;max-width:calc(100% - 30px);padding:11px 15px;border-radius:10px;color:#fff;background:#27272a;box-shadow:0 12px 35px rgba(0,0,0,.28);font-size:14px;font-weight:800;opacity:0;transform:translate(-50%,10px);pointer-events:none;transition:.18s}.toast.show{opacity:1;transform:translate(-50%,0)}
@keyframes enterRight{from{opacity:0;transform:translateX(18px)}to{opacity:1;transform:none}}@keyframes enterLeft{from{opacity:0;transform:translateX(-18px)}to{opacity:1;transform:none}}@keyframes fadeIn{from{opacity:0}to{opacity:1}}@keyframes modalIn{from{opacity:0;transform:translateY(10px) scale(.985)}to{opacity:1;transform:none}}
@media(max-width:860px){body{padding:2px}.shell{border-radius:18px}.header{padding:18px}.layout{display:block}.rail{display:flex;gap:6px;overflow-x:auto;padding:9px;border-right:0;border-bottom:1px solid var(--line)}.rail button{width:auto;flex:0 0 auto;padding:7px}.rail button+button{margin-top:0}.nav-label{display:none}.main{padding:25px 58px 20px}.edge.prev{left:7px}.edge.next{right:7px}.stage{min-height:0}}
@media(max-width:620px){.header{padding:16px}.brand span{display:none}.controls{gap:5px}.select{max-width:92px;padding:0 7px}.hero{margin-top:20px}.hero h1{font-size:31px}.main{padding:22px 47px 18px}.grid,.resource-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.edge{width:38px;height:38px;font-size:20px}.edge.prev{left:4px}.edge.next{right:4px}.modal-header,.modal-body{padding-left:17px;padding-right:17px}.footer{display:block}.footer-buttons{margin-top:12px}.footer-buttons button{flex:1}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style>
</head>
<body>
<section class="shell" aria-label="Interactive onboarding">
<header class="header">
  <div class="row">
    <div class="brand"><div class="mark" aria-hidden="true">AI</div><div><strong id="product"></strong><span id="organization"></span></div></div>
    <div class="controls">
      <select class="select" id="language" aria-label="Language"><option value="en">English</option><option value="fr">Français</option></select>
      <select class="select" id="theme" aria-label="Theme"><option value="auto">Auto</option><option value="light">Light</option><option value="dark">Dark</option></select>
    </div>
  </div>
  <div class="hero"><p class="eyebrow" id="eyebrow"></p><h1 id="welcome"></h1><p id="heroText"></p><div class="meta" id="identity"></div></div>
  <div class="progress-row"><div class="track" aria-hidden="true"><div class="bar" id="bar"></div></div><span class="progress-text" id="progress"></span></div>
</header>
<div class="layout">
  <nav class="rail" id="rail" aria-label="Onboarding sections"></nav>
  <main class="main">
    <button class="edge prev" id="edgePrev" type="button" aria-label="Previous section">‹</button>
    <div class="stage" id="stage"></div>
    <button class="edge next" id="edgeNext" type="button" aria-label="Next section">›</button>
    <footer class="footer"><span class="fresh" id="fresh"></span><div class="footer-buttons"><button class="secondary" id="back" type="button"></button><button class="primary" id="next" type="button"></button></div></footer>
  </main>
</div>
</section>
<div class="backdrop" id="backdrop" hidden>
  <section class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle" aria-describedby="modalDescription">
    <header class="modal-header"><div><h2 id="modalTitle"></h2><p id="modalDescription"></p></div><button class="close" id="closeModal" type="button" aria-label="Close">×</button></header>
    <div class="modal-body" id="modalBody"></div>
  </section>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script type="application/json" id="snapshot">__SNAPSHOT_JSON__</script>
<script>
(function(){
'use strict';
var data=JSON.parse(document.getElementById('snapshot').textContent);
var root=document.documentElement,lang=data.ui.default_language==='fr'?'fr':'en',current=0,pages=[],topics={},lastFocus=null,toastTimer,direction=1;
var E=function(tag,cls,text){var n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
var add=function(parent){for(var i=1;i<arguments.length;i++)if(arguments[i])parent.appendChild(arguments[i]);return parent;};
var L=function(fr,en){return lang==='fr'?fr:en;};
var feature=function(key){var a=(data.resources&&data.resources.features)||[];for(var i=0;i<a.length;i++)if(a[i].key===key)return !!a[i].enabled;return false;};
var access=function(group,key){return !!(data.access&&data.access[group]&&data.access[group][key]);};
var visible=function(key){return !!(data.ui&&data.ui[key]);};
var reportHeight=function(){parent.postMessage({type:'iframe:height',height:Math.ceil(document.documentElement.scrollHeight)},'*');};
function sendPrompt(text){parent.postMessage({type:'input:prompt',text:String(text||'').slice(0,4000)},'*');showToast();}
function showToast(){clearTimeout(toastTimer);var n=document.getElementById('toast');n.textContent=L('Exemple placé dans la zone de saisie. Relisez-le avant envoi.','Example placed in the chat input. Review it before sending.');n.classList.add('show');toastTimer=setTimeout(function(){n.classList.remove('show');},2400);}
function topic(id,icon,title,description,where,steps,examples,note,extra){var t={id:id,icon:icon,title:title,description:description,where:where||[],steps:steps||[],examples:examples||[],note:note||'',extra:extra||[]};topics[id]=t;return t;}
function pathNode(items,cls){var n=E('div',cls||'where');items.forEach(function(x,i){if(i)add(n,E('i','', '›'));add(n,E('span','',x));});return n;}
function card(t){var b=E('button','card');b.type='button';var top=E('div','card-top'),copy=E('div');add(copy,E('h3','',t.title),E('p','',t.description));add(top,E('span','icon',t.icon),copy);add(b,top);if(t.where.length)add(b,pathNode(t.where,'path-preview'));add(b,E('span','learn',L('Voir le guide','Open guide')));b.addEventListener('click',function(){openModal(t.id,b);});return b;}
function page(id,label,titleText,leadText,items,resourceItems){var p=E('section','panel');p.dataset.id=id;add(p,E('h2','',titleText),E('p','lead',leadText));if(items&&items.length){var g=E('div','grid');items.forEach(function(t){g.appendChild(card(t));});add(p,g);}if(resourceItems&&resourceItems.length){add(p,E('div','section-label',L('Disponibles pour votre compte','Available to your account')));var rg=E('div','resource-grid');resourceItems.forEach(function(t){rg.appendChild(card(t));});add(p,rg);}pages.push({id:id,label:label,node:p});}
function modalLabel(text){return E('div','label',text);}
function openModal(id,source){var t=topics[id];if(!t)return;lastFocus=source||document.activeElement;document.getElementById('modalTitle').textContent=t.title;document.getElementById('modalDescription').textContent=t.description;var body=document.getElementById('modalBody');body.replaceChildren();if(t.where.length)add(body,modalLabel(L('Où le trouver','Where to find it')),pathNode(t.where));if(t.steps.length){add(body,modalLabel(L('Étapes recommandées','Recommended steps')));var ol=E('ol','steps');t.steps.forEach(function(x){add(ol,E('li','',x));});add(body,ol);}if(t.extra.length){add(body,modalLabel(L('Informations disponibles','Available information')));var chips=E('div','chips');t.extra.forEach(function(x){add(chips,E('span','chip',x));});add(body,chips);}if(t.examples.length){add(body,modalLabel(L('Exemples prêts à utiliser','Ready-to-use examples')));var ex=E('div','examples');t.examples.forEach(function(x){var box=E('div','example');add(box,E('code','',x));var actions=E('div','modal-actions'),btn=E('button','try',L('Placer dans le chat','Place in chat'));btn.type='button';btn.addEventListener('click',function(){sendPrompt(x);});add(actions,btn);add(box,actions);add(ex,box);});add(body,ex);}if(t.note)add(body,E('div','dialog-note',t.note));var bg=document.getElementById('backdrop');bg.hidden=false;document.body.style.overflow='hidden';document.getElementById('closeModal').focus();requestAnimationFrame(reportHeight);}
function closeModal(){var bg=document.getElementById('backdrop');if(bg.hidden)return;bg.hidden=true;document.body.style.overflow='';if(lastFocus&&lastFocus.focus)lastFocus.focus();requestAnimationFrame(reportHeight);}
function resourceTopics(items,kind){return (items||[]).map(function(item,index){var id='resource-'+kind+'-'+String(index),where=[],steps=[],examples=[],extra=[].concat(item.tags||[],item.capabilities||[]),desc=item.description||L('Ressource autorisée pour votre compte.','Resource permitted for your account.');
if(kind==='model'){where=[L('Nouveau chat','New chat'),L('Sélecteur en haut','Top model selector'),item.name||item.id];steps=[L('Ouvrez le sélecteur de modèle.','Open the model selector.'),L('Recherchez ce nom ou utilisez son identifiant exact.','Search this name or use its exact ID.'),L('Vérifiez les capacités et choisissez-le avant d’envoyer.','Review capabilities and select it before sending.')];examples=(item.examples||[]).slice(0,3);if(!examples.length)examples=[L('Avec ce modèle, explique le sujet étape par étape et signale ce qui doit être vérifié.','With this model, explain the topic step by step and flag what needs verification.')];(item.actions||[]).forEach(function(x){extra.push(L('Action : ','Action: ')+(x.name||x.id));});(item.filters||[]).forEach(function(x){extra.push(L('Filtre : ','Filter: ')+(x.name||x.id));});}
if(kind==='prompt'){where=[L('Zone de saisie','Message input'),'/',item.command?'/'+String(item.command).replace(/^[/]/,''):item.name];steps=[L('Tapez / pour ouvrir vos prompts.','Type / to open your prompts.'),L('Sélectionnez cette commande.','Select this command.'),L('Complétez les variables affichées et relisez le résultat.','Complete shown variables and review the result.')];examples=item.command?['/'+String(item.command).replace(/^[/]/,'')]:[];}
if(kind==='skill'){where=[L('Zone de saisie','Message input'),'$',item.name||''];steps=[L('Tapez $ pour ouvrir les compétences.','Type $ to open skills.'),L('Sélectionnez cette compétence.','Select this skill.'),L('Ajoutez votre objectif, vos sources et vos contraintes.','Add your goal, sources, and constraints.')];examples=[L('$ puis : applique cette compétence au document joint et explique les contrôles effectués.','$ then: apply this skill to the attached document and explain the checks performed.')];}
if(kind==='knowledge'){where=[L('Zone de saisie','Message input'),'#',item.name||''];steps=[L('Tapez # pour ouvrir les connaissances.','Type # to open knowledge.'),L('Sélectionnez cette base.','Select this knowledge base.'),L('Posez une question précise et demandez les passages sources.','Ask a precise question and request source passages.')];examples=[L('# Réponds uniquement à partir de cette base. Cite le document et la section; indique si la réponse est absente.','# Answer only from this knowledge base. Cite the document and section; say when the answer is absent.')];}
if(kind==='tool'){where=[L('Zone de saisie','Message input'),'+',L('Tools / Integrations','Tools / Integrations'),item.name||''];steps=[L('Ouvrez + puis Tools ou Integrations.','Open +, then Tools or Integrations.'),L('Activez cet outil et connectez-vous uniquement via le flux officiel si nécessaire.','Enable this tool and connect only through the official flow if needed.'),L('Décrivez précisément la lecture ou l’action attendue.','Precisely describe the requested read or action.'),L('Confirmez séparément toute opération qui écrit, partage ou supprime.','Separately confirm anything that writes, shares, or deletes.')];examples=[L('Avec cet outil, effectue uniquement une lecture et présente les résultats sans rien modifier.','With this tool, perform a read-only lookup and present the results without changing anything.')];if(item.authenticated===false)extra.push(L('Connexion requise','Connection required'));}
if(kind==='channel'){where=[L('Barre latérale','Sidebar'),L('Channels','Channels'),item.name||''];steps=[L('Ouvrez le canal et vérifiez son niveau de visibilité.','Open the channel and verify its visibility.'),L('Utilisez @utilisateur pour notifier une personne ou @modèle pour inviter une IA.','Use @username to notify a person or @model to invite AI.'),L('Répondez dans un fil pour garder la chronologie lisible.','Reply in a thread to keep the timeline readable.')];examples=[L('@modèle Résume ce fil, sépare décisions et questions ouvertes, puis propose trois actions.','@model Summarize this thread, separate decisions from open questions, then propose three actions.')];}
return topic(id,(kind==='model'?'M':kind==='prompt'?'/':kind==='skill'?'$':kind==='knowledge'?'#':kind==='channel'?'@':'T'),item.name||item.id,desc,where,steps,examples,'',extra.slice(0,12));});}
function build(){
pages=[];topics={};
var overview=[];
overview.push(topic('privacy','01',L('Un guide privé par utilisateur','A private guide for each user'),L('Le serveur applique les droits avant de construire cette interface.','The server applies access controls before building this interface.'),[L('Connexion','Sign in'),L('Contrôles d’accès','Access checks'),L('Guide personnel','Personal guide')],[L('Aucun e-mail, jeton, session, corps de prompt, instruction de compétence ou document n’est inclus.','No email, token, session, prompt body, skill instruction, or document is included.'),L('Les noms personnels et inventaires sensibles sont masqués par défaut et contrôlés par Valves.','Personal names and sensitive inventories are hidden by default and controlled by Valves.'),L('Les boutons d’exemple remplissent le chat sans envoyer automatiquement.','Example buttons fill chat without submitting automatically.')],[],L('Cette interface ne lit pas le DOM parent, les cookies ou le stockage local Open WebUI.','This interface does not read the parent DOM, cookies, or Open WebUI local storage.')));
overview.push(topic('navigation','02',L('Naviguer dans ce guide','Navigate this guide'),L('Utilisez le menu, les flèches latérales ou les raccourcis clavier.','Use the menu, side arrows, or keyboard shortcuts.'),[L('Guide','Guide'),L('Rubrique','Section'),L('Carte','Card'),L('Fenêtre détaillée','Detail dialog')],[L('Utilisez les grandes flèches à gauche et à droite pour changer de rubrique.','Use the large left and right arrows to change sections.'),L('Ouvrez une carte pour voir les étapes et exemples complets.','Open a card for complete steps and examples.'),L('Appuyez sur Échap pour fermer une fenêtre.','Press Escape to close a dialog.'),L('Alt + flèche gauche/droite change de rubrique.','Alt + Left/Right changes sections.')],[],''));
page('welcome',L('Bienvenue','Welcome'),L('Commencez avec un espace clair et sécurisé','Start with a clear, secure workspace'),L('Ce parcours montre uniquement les fonctions autorisées par votre configuration et vos Valves. Ouvrez une carte pour consulter le guide complet.','This journey shows only capabilities allowed by your configuration and Valves. Open a card for the complete guide.'),overview,[]);
if(visible('show_models')){var mg=topic('model-guide','M',L('Choisir un modèle','Choose a model'),L('Sélectionnez le modèle selon la tâche, les capacités et les règles de votre organisation.','Select a model based on the task, capabilities, and organization policy.'),[L('Nouveau chat','New chat'),L('En haut du chat','Top of chat'),L('Sélecteur de modèle','Model selector')],[L('Cliquez sur le modèle affiché en haut du nouveau chat.','Click the model shown at the top of a new chat.'),L('Recherchez par nom et lisez la description disponible.','Search by name and read the available description.'),L('Pour un identifiant connu, tapez /model suivi de l’identifiant exact.','For a known ID, type /model followed by the exact ID.'),L('Changez de modèle si la tâche demande vision, outils, code ou un contexte plus long.','Switch models when the task requires vision, tools, code, or longer context.')],[L('Aide-moi à choisir le meilleur modèle disponible pour analyser un PDF et produire un tableau vérifiable.','Help me choose the best available model for analyzing a PDF and producing a verifiable table.')],L('Un nom dans ce guide signifie seulement que ce compte peut voir le modèle au moment de la génération.','A name in this guide only means this account could see the model when the guide was generated.'));page('models',L('Modèles','Models'),L('Choisissez le bon moteur avant de commencer','Choose the right engine before you begin'),L('Comprenez le sélecteur puis ouvrez, si autorisé, les fiches des modèles réellement accessibles.','Understand the selector, then open the cards for actually accessible models when allowed.'),[mg],resourceTopics(data.resources.models,'model'));}
if(visible('show_chat_basics')){var ask=topic('ask','A',L('Formuler une demande utile','Write a useful request'),L('Une bonne consigne contient contexte, objectif, format, contraintes et critères de contrôle.','A good request includes context, objective, format, constraints, and validation criteria.'),[L('Chat','Chat'),L('Zone de saisie','Message input')],[L('Donnez seulement le contexte nécessaire.','Give only necessary context.'),L('Décrivez le livrable attendu avec un verbe clair.','Describe the desired deliverable with a clear verb.'),L('Précisez format, langue, longueur et public.','Specify format, language, length, and audience.'),L('Demandez les sources, hypothèses et incertitudes.','Request sources, assumptions, and uncertainties.')],[L('Contexte : réunion de projet. Objectif : résumer le document joint. Format : tableau avec décision, responsable et échéance. Contraintes : 250 mots, langage clair, cite les pages et signale toute incertitude.','Context: project meeting. Objective: summarize the attached document. Format: table with decision, owner, and due date. Constraints: 250 words, plain language, cite pages, and flag uncertainty.')],'');
var selectors=topic('selectors','/ $ # +',L('Utiliser les sélecteurs rapides','Use quick selectors'),L('/ pour les prompts, $ pour les compétences, # pour les connaissances et + pour fichiers/outils.','/ for prompts, $ for skills, # for knowledge, and + for files/tools.'),[L('Chat','Chat'),L('Zone de saisie','Message input')],[L('Tapez le symbole correspondant.','Type the matching symbol.'),L('Choisissez uniquement une ressource visible dans la liste.','Choose only a resource visible in the picker.'),L('Vérifiez les éléments attachés avant l’envoi.','Review attached items before sending.')],[],'');page('chat',L('Bien demander','Ask well'),L('Maîtrisez la zone de saisie','Master the message input'),L('Des demandes structurées et les bons sélecteurs rendent les réponses plus fiables et reproductibles.','Structured requests and the right selectors make answers more reliable and repeatable.'),[ask,selectors],[]);}
var reusable=[],rr=[];
if(visible('show_prompts')&&(data.available.prompts||false)){reusable.push(topic('prompt-guide','/',L('Lancer un prompt réutilisable','Run a reusable prompt'),L('Tapez /, sélectionnez une commande et complétez les variables affichées.','Type /, select a command, and complete the shown variables.'),[L('Zone de saisie','Message input'),'/'],[L('Tapez /.','Type /.'),L('Recherchez la commande.','Search for the command.'),L('Sélectionnez-la et remplissez ses variables.','Select it and fill its variables.'),L('Relisez le texte généré avant envoi.','Review the generated text before sending.')],[L('/ puis choisissez un prompt adapté à la tâche.','Type / then choose a prompt suited to the task.')],L('Le corps du prompt n’est jamais inclus dans ce guide.','The prompt body is never included in this guide.')));rr=rr.concat(resourceTopics(data.resources.prompts,'prompt'));}
if(visible('show_skills')&&(data.available.skills||false)){reusable.push(topic('skill-guide','$',L('Appliquer une compétence','Apply a skill'),L('Une compétence fournit une méthode spécialisée au modèle.','A skill gives the model a specialized method.'),[L('Zone de saisie','Message input'),'$'],[L('Tapez $.','Type $.'),L('Sélectionnez une compétence autorisée.','Select an allowed skill.'),L('Ajoutez votre objectif, vos sources et vos contraintes.','Add your goal, sources, and constraints.'),L('Vérifiez le résultat comme toute autre réponse.','Validate the result like any other answer.')],[L('$ puis : analyse ce document, explique ta méthode et cite les éléments utilisés.','$ then: analyze this document, explain your method, and cite the evidence used.')],L('Une compétence guide le modèle; elle ne constitue pas une autorisation d’effectuer une action.','A skill guides the model; it is not authorization to perform an action.')));rr=rr.concat(resourceTopics(data.resources.skills,'skill'));}
if(reusable.length)page('reusable',L('Prompts et compétences','Prompts and skills'),L('Réutilisez les bonnes méthodes','Reuse the right methods'),L('Sélectionnez des instructions et compétences autorisées sans exposer leur contenu interne.','Select permitted prompts and skills without exposing their internal content.'),reusable,rr);
if(visible('show_knowledge')&&((data.available.knowledge||false)||access('workspace','knowledge'))){var kt=[];
if(visible('show_knowledge_creation')&&access('workspace','knowledge'))kt.push(topic('knowledge-create','K+',L('Créer une base de connaissances','Create a knowledge base'),L('Structurez des documents autorisés et définissez précisément les accès.','Organize permitted documents and define access precisely.'),[L('Barre latérale','Sidebar'),L('Workspace','Workspace'),L('Knowledge','Knowledge'),'+'],[L('Créez la base avec un nom et une description non sensibles.','Create the base with a non-sensitive name and description.'),L('Ajoutez uniquement des fichiers autorisés et attendez la fin du traitement.','Add only permitted files and wait for processing.'),L('Vérifiez la liste des documents et retirez les erreurs.','Review the document list and remove mistakes.'),L('Configurez utilisateurs et groupes; évitez Public pour les données internes.','Configure users and groups; avoid Public for internal data.'),L('Dans Workspace > Models, attachez la base au modèle seulement si elle doit être disponible par défaut.','In Workspace > Models, attach the base only when it should be available by default.')],[L('Crée une synthèse à partir de #, cite chaque document utilisé et indique ce qui manque.','Create a synthesis from #, cite every document used, and state what is missing.')],L('Knowledge utilise la recherche documentaire. Demandez toujours les passages sources.','Knowledge uses document retrieval. Always request source passages.')));
kt.push(topic('knowledge-use','#',L('Utiliser une connaissance dans un chat','Use knowledge in chat'),L('Ajoutez ponctuellement une base avec # ou utilisez un modèle auquel elle est attachée.','Attach a knowledge base with # or use a model that already includes it.'),[L('Zone de saisie','Message input'),'#'],[L('Tapez # et sélectionnez une base ou un fichier autorisé.','Type # and select an allowed base or file.'),L('Posez une question ciblée avec les termes du document.','Ask a focused question using document terminology.'),L('Demandez document, section et citation.','Request document, section, and citation.'),L('Si la réponse est absente, demandez au modèle de le dire explicitement.','If the answer is absent, ask the model to say so explicitly.')],[L('# Réponds uniquement avec les documents sélectionnés. Donne la source et la section pour chaque affirmation.','# Answer only with the selected documents. Give the source and section for every claim.')],''));
page('knowledge',L('Connaissances','Knowledge'),L('Transformez des documents en contexte contrôlé','Turn documents into controlled context'),L('Créez, attachez et interrogez les bases accessibles sans confondre recherche documentaire et vérité garantie.','Create, attach, and query accessible knowledge without treating retrieval as guaranteed truth.'),kt,resourceTopics(data.resources.knowledge,'knowledge'));}
if(visible('show_notes')&&feature('notes')){var nt=[
topic('notes-create','N',L('Créer et rédiger une note','Create and draft a note'),L('Notes conserve un document persistant hors de l’historique normal du chat.','Notes keeps a persistent document outside normal chat history.'),[L('Barre latérale','Sidebar'),L('Notes','Notes'),'+'],[L('Ouvrez Notes et créez une note.','Open Notes and create a note.'),L('Ajoutez un titre puis utilisez Markdown ou Rich Text.','Add a title, then use Markdown or Rich Text.'),L('Ouvrez le chat IA de la note avec le bouton bulle.','Open the note AI chat with the speech-bubble button.'),L('Choisissez un modèle compatible avec les outils natifs pour lire ou modifier la note.','Choose a model compatible with native tools to read or modify the note.'),L('Utilisez Insert pour placer une réponse à la position du curseur.','Use Insert to place a response at the cursor.')],[L('Crée un plan de note de compte rendu avec décisions, actions, responsables, échéances et questions ouvertes.','Create a meeting-note outline with decisions, actions, owners, due dates, and open questions.')],''),
topic('notes-update','AI',L('Mettre à jour une note avec l’IA','Update a note with AI'),L('Recherchez, lisez, créez ou remplacez une note avec les outils natifs autorisés.','Search, read, create, or replace a note with permitted native tools.'),[L('Notes','Notes'),L('Ouvrir une note','Open a note'),L('Chat IA','AI chat')],[L('Surlignez un passage pour demander une réécriture ciblée.','Highlight a passage for a targeted rewrite.'),L('Utilisez search_notes et view_note pour trouver et lire.','Use search_notes and view_note to find and read.'),L('Utilisez write_note pour créer ou replace_note_content pour mettre à jour.','Use write_note to create or replace_note_content to update.'),L('Relisez le changement et utilisez Undo/Redo si nécessaire.','Review the change and use Undo/Redo if needed.')],[L('Recherche mes notes Projet X et trouve le schéma de base de données.','Search my Project X notes and find the database schema.'),L('Ajoute une tâche à ma note hebdomadaire : relire la demande de fusion vendredi.','Add a task to my weekly note: review the pull request on Friday.')],L('Une note attachée manuellement à un chat est en lecture seule pour l’IA. La modification nécessite le mode natif et les outils Notes.','A note manually attached to chat is read-only for AI. Editing requires Native Mode and Notes tools.')),
topic('notes-pin','↗',L('Épingler et joindre une note','Pin and attach a note'),L('Placez les notes utiles dans la barre latérale ou joignez-les à un chat.','Place useful notes in the sidebar or attach them to chat.'),[L('Notes','Notes'),'⋯',L('Pin to Sidebar','Pin to Sidebar')],[L('Dans la liste ou l’éditeur, ouvrez ⋯ puis Pin to Sidebar.','In the list or editor, open ⋯ then Pin to Sidebar.'),L('Le dossier Notes apparaît après la première note épinglée.','The Notes folder appears after the first pinned note.'),L('Pour joindre une note : Chat > + > Attach Notes.','To attach a note: Chat > + > Attach Notes.'),L('Vous pouvez aussi faire glisser une note épinglée dans le chat.','You can also drag a pinned note into chat.')],[L('Résume la note jointe en cinq actions classées par priorité et cite les sections.','Summarize the attached note into five prioritized actions and cite sections.')],L('La note complète utilise la fenêtre de contexte. Évitez de joindre des notes inutilement longues.','The full note uses context-window space. Avoid attaching unnecessarily long notes.'))];
page('notes',L('Notes','Notes'),L('Écrivez, améliorez et réutilisez vos notes','Write, improve, and reuse your notes'),L('Apprenez la rédaction assistée, les outils natifs, l’épinglage et l’ajout de notes au contexte.','Learn assisted writing, native tools, pinning, and adding notes to context.'),nt,[]);}
var st=[],sr=[];
if(visible('show_file_upload')&&feature('file_upload'))st.push(topic('files','+',L('Joindre et analyser un fichier','Attach and analyze a file'),L('Ajoutez un PDF, document, image ou fichier de données selon la configuration.','Add a PDF, document, image, or data file according to configuration.'),[L('Zone de saisie','Message input'),'+',L('Upload Files','Upload Files')],[L('Choisissez un fichier autorisé et attendez la fin du chargement.','Choose a permitted file and wait for upload.'),L('Décrivez précisément la sortie attendue.','Describe the desired output precisely.'),L('Demandez des références aux pages, sections ou lignes.','Request page, section, or row references.'),L('Vérifiez que le modèle a réellement lu le fichier.','Verify that the model actually read the file.')],[L('Analyse le fichier joint : résume les risques, cite les pages et liste les données manquantes.','Analyze the attached file: summarize risks, cite pages, and list missing data.')],''));
if(visible('show_web_search')&&feature('web_search'))st.push(topic('web','W',L('Activer la recherche web','Enable web search'),L('Utilisez-la pour les faits récents, règles, nouvelles, prix ou horaires.','Use it for recent facts, rules, news, prices, or schedules.'),[L('Zone de saisie','Message input'),'+',L('Web Search','Web Search')],[L('Activez Web Search avant l’envoi.','Enable Web Search before sending.'),L('Précisez date, zone géographique et sources préférées.','Specify date, geography, and preferred sources.'),L('Demandez des liens directs et la date de chaque source.','Request direct links and each source date.'),L('Comparez plusieurs sources lorsque l’enjeu est important.','Compare multiple sources when stakes are high.')],[L('Recherche les informations officielles les plus récentes sur ce sujet. Donne la date de chaque source et sépare faits, citations et hypothèses.','Find the latest official information on this topic. Give each source date and separate facts, quotes, and assumptions.')],L('Sans recherche web, le modèle peut utiliser des connaissances plus anciennes.','Without web search, the model may use older knowledge.')));
if(visible('show_tools')&&(data.available.tools||false)){st.push(topic('tool-guide','T',L('Activer un outil ou une intégration','Enable a tool or integration'),L('Un outil peut lire une source externe ou réaliser une action.','A tool can read an external source or perform an action.'),[L('Zone de saisie','Message input'),'+',L('Tools / Integrations','Tools / Integrations')],[L('Activez uniquement l’outil nécessaire.','Enable only the necessary tool.'),L('Utilisez le flux OAuth officiel; ne collez jamais de jeton dans le chat.','Use the official OAuth flow; never paste a token into chat.'),L('Commencez par une lecture lorsque possible.','Start with a read-only request when possible.'),L('Confirmez cible et conséquence avant toute écriture, publication ou suppression.','Confirm target and consequence before writing, publishing, or deleting.')],[L('Avec l’outil sélectionné, recherche les éléments ouverts et présente-les sans les modifier.','With the selected tool, find open items and present them without modifying anything.')],''));sr=resourceTopics(data.resources.tools,'tool');}
if(st.length)page('sources',L('Sources et outils','Sources and tools'),L('Ajoutez le bon contexte au bon moment','Add the right context at the right time'),L('Utilisez fichiers, recherche web et outils avec des consignes précises et des validations adaptées au risque.','Use files, web search, and tools with precise requests and risk-appropriate confirmation.'),st,sr);
if(visible('show_channels')&&feature('channels')){var ct=[topic('channel-guide','@',L('Communiquer dans Channels','Communicate in Channels'),L('Une chronologie partagée pour les humains et les modèles.','A shared timeline for people and models.'),[L('Barre latérale','Sidebar'),L('Channels','Channels'),L('Choisir un canal','Choose a channel')],[L('Vérifiez si le canal est public, privé, de groupe ou un message direct.','Check whether the channel is public, private, group-based, or a direct message.'),L('Utilisez @utilisateur pour notifier une personne.','Use @username to notify a person.'),L('Utilisez @modèle pour inviter une IA; une réponse de premier niveau apparaît généralement dans un fil.','Use @model to invite AI; a top-level answer normally appears in a thread.'),L('Utilisez #nom-du-canal pour créer un lien vers un autre canal.','Use #channel-name to link another channel.'),L('Réagissez, épinglez, répondez dans les fils et joignez des fichiers si autorisé.','React, pin, reply in threads, and attach files when allowed.')],[L('@modèle Résume ce fil en décisions, risques, responsables et prochaines étapes.','@model Summarize this thread into decisions, risks, owners, and next steps.')],L('Tout membre d’un canal peut lire ce qui y est publié. Vérifiez la visibilité avant de partager.','Every channel member can read what is posted. Verify visibility before sharing.'))];page('channels',L('Channels','Channels'),L('Collaborez avec les personnes et l’IA','Collaborate with people and AI'),L('Utilisez mentions, fils, réactions et fichiers tout en respectant la visibilité du canal.','Use mentions, threads, reactions, and files while respecting channel visibility.'),ct,resourceTopics(data.resources.channels,'channel'));}
var ot=[];
if(visible('show_folders')&&feature('folders'))ot.push(topic('folders','F',L('Organiser avec des dossiers','Organize with folders'),L('Regroupez les conversations par projet ou activité.','Group conversations by project or activity.'),[L('Barre latérale','Sidebar'),L('Folders','Folders')],[L('Créez un dossier au nom non sensible.','Create a folder with a non-sensitive name.'),L('Glissez les conversations concernées dans le dossier.','Drag relevant conversations into it.'),L('Vérifiez avant toute suppression; retirer du dossier et supprimer sont différents.','Check before deleting; removing from a folder and deleting are different.')],[],''));
if(visible('show_memory')&&feature('memories'))ot.push(topic('memory','M',L('Contrôler la mémoire','Control memory'),L('Consultez, corrigez ou supprimez les préférences persistantes.','Review, correct, or delete persistent preferences.'),[L('Profil','Profile'),L('Settings','Settings'),L('Personalization / Memory','Personalization / Memory')],[L('Consultez régulièrement les éléments mémorisés.','Regularly review remembered items.'),L('Corrigez les informations inexactes.','Correct inaccurate information.'),L('Supprimez les éléments sensibles ou inutiles.','Delete sensitive or unnecessary items.'),L('Répétez le contexte important dans une demande critique.','Repeat important context in a critical request.')],[L('Mémorise que je préfère des réponses brèves avec les sources séparées.','Remember that I prefer concise answers with sources listed separately.')],''));
if(visible('show_calendar')&&feature('calendar'))ot.push(topic('calendar','C',L('Utiliser Calendar','Use Calendar'),L('Consultez ou préparez des événements selon les accès accordés.','Review or prepare events according to granted access.'),[L('Barre latérale','Sidebar'),L('Calendar','Calendar')],[L('Choisissez le calendrier autorisé.','Choose the permitted calendar.'),L('Vérifiez fuseau horaire, participants et confidentialité.','Check timezone, participants, and privacy.'),L('Demandez un brouillon avant toute création lorsque le risque est élevé.','Request a draft before creation when risk is high.')],[L('Prépare un ordre du jour pour ma prochaine réunion sans modifier le calendrier.','Prepare an agenda for my next meeting without modifying the calendar.')],''));
if(visible('show_automations')&&feature('automations'))ot.push(topic('automation','A',L('Créer une automatisation','Create an automation'),L('Planifiez une tâche ou déclenchez-la selon une condition.','Schedule a task or trigger it on a condition.'),[L('Barre latérale','Sidebar'),L('Automations','Automations')],[L('Définissez résultat, cadence et condition d’arrêt.','Define outcome, cadence, and stopping condition.'),L('Limitez les sources et outils au strict nécessaire.','Limit sources and tools to what is necessary.'),L('Testez sur des données non sensibles.','Test with non-sensitive data.'),L('Contrôlez les premières exécutions et désactivez en cas d’anomalie.','Review early runs and disable on anomalies.')],[L('Chaque lundi, prépare un brouillon des éléments ouverts; ne publie et ne modifie rien automatiquement.','Every Monday, draft a summary of open items; do not publish or modify anything automatically.')],''));
if(ot.length)page('organize',L('Organisation','Organization'),L('Gardez un espace de travail maîtrisé','Keep your workspace under control'),L('Dossiers, mémoire, calendrier et automatisations apparaissent séparément selon les Valves et permissions.','Folders, memory, calendar, and automations appear separately according to Valves and permissions.'),ot,[]);
var mt=[];
if(visible('show_image_generation')&&feature('image_generation'))mt.push(topic('images','I',L('Créer ou modifier une image','Create or edit an image'),L('Décrivez composition, style, format et texte exact.','Describe composition, style, format, and exact text.'),[L('Chat','Chat'),'+',L('Image generation','Image generation')],[L('Activez la génération ou choisissez un modèle compatible.','Enable generation or choose a compatible model.'),L('Précisez sujet, cadrage, palette, format et éléments interdits.','Specify subject, framing, palette, format, and forbidden elements.'),L('Pour une modification, joignez l’image et listez seulement les changements.','For an edit, attach the image and list only the changes.')],[L('Crée une illustration 16:9 minimaliste d’une équipe travaillant avec une IA, sans logo, sans marque et sans texte.','Create a minimalist 16:9 illustration of a team working with AI, with no logo, brand, or text.')],''));
if(visible('show_code_interpreter')&&feature('code_interpreter'))mt.push(topic('code','</>',L('Analyser avec Code Interpreter','Analyze with Code Interpreter'),L('Effectuez des calculs, contrôles, tableaux et graphiques dans un environnement isolé.','Perform calculations, checks, tables, and charts in an isolated environment.'),[L('Chat','Chat'),'+',L('Code Interpreter','Code Interpreter')],[L('Activez Code Interpreter et joignez les données.','Enable Code Interpreter and attach data.'),L('Définissez colonnes, règles de calcul et sortie attendue.','Define columns, calculation rules, and desired output.'),L('Demandez les hypothèses et contrôles qualité.','Request assumptions and quality checks.'),L('Téléchargez et vérifiez les fichiers produits.','Download and verify output files.')],[L('Analyse ce CSV, signale les valeurs manquantes, vérifie les types, calcule les tendances et crée un graphique accompagné d’une table de contrôle.','Analyze this CSV, flag missing values, validate types, calculate trends, and create a chart plus a validation table.')],''));
if(visible('show_voice')&&(feature('stt')||feature('tts')||feature('call')))mt.push(topic('voice','V',L('Utiliser la voix','Use voice'),L('Dictez, écoutez ou lancez un appel selon les fonctions activées.','Dictate, listen, or call according to enabled capabilities.'),[L('Chat','Chat'),L('Microphone / casque','Microphone / headset')],[L('Utilisez le microphone pour transcrire si STT est actif.','Use the microphone to transcribe if STT is enabled.'),L('Relisez toujours la transcription avant envoi.','Always review the transcription before sending.'),L('Utilisez Read Aloud si TTS est actif.','Use Read Aloud if TTS is enabled.'),L('Utilisez Call pour une conversation vocale lorsque disponible.','Use Call for voice conversation when available.')],[],''));
if(visible('show_multiple_models')&&feature('multiple_models'))mt.push(topic('multi','2×',L('Comparer plusieurs modèles','Compare multiple models'),L('Posez la même question à plusieurs modèles pour comparer les résultats.','Ask multiple models the same question to compare results.'),[L('Sélecteur de modèle','Model selector'),L('Ajouter un modèle','Add model')],[L('Sélectionnez plusieurs modèles avant l’envoi.','Select multiple models before sending.'),L('Utilisez exactement le même contexte.','Use exactly the same context.'),L('Comparez exactitude, sources, vitesse et coût.','Compare accuracy, sources, speed, and cost.'),L('Ne retenez pas une réponse uniquement parce que plusieurs modèles la répètent.','Do not accept an answer only because several models repeat it.')],[],''));
if(mt.length)page('create',L('Créer et analyser','Create and analyze'),L('Produisez avec des capacités spécialisées','Produce with specialized capabilities'),L('Chaque capacité est indépendante dans les Valves et reste conditionnée par les permissions effectives.','Each capability is independently controlled by Valves and still gated by effective permissions.'),mt,[]);
if(visible('show_safety')){var safe=[topic('verify','✓',L('Vérifier avant d’utiliser','Verify before use'),L('Une réponse convaincante peut être incorrecte.','A convincing answer can be incorrect.'),[L('Réponse','Response'),L('Sources','Sources'),L('Validation humaine','Human review')],[L('Contrôlez chiffres, dates, liens et citations.','Check numbers, dates, links, and citations.'),L('Comparez la réponse aux documents d’origine.','Compare the answer with original documents.'),L('Documentez les hypothèses pour les décisions importantes.','Document assumptions for important decisions.')],[],''),
topic('protect','🔒',L('Protéger les données et les actions','Protect data and actions'),L('Appliquez minimisation, confidentialité et moindre privilège.','Apply minimization, confidentiality, and least privilege.'),[L('Avant saisie','Before input'),L('Avant action','Before action'),L('Avant partage','Before sharing')],[L('Ne saisissez que les données autorisées et nécessaires.','Enter only permitted and necessary data.'),L('Ne collez jamais de mot de passe, jeton ou clé API.','Never paste passwords, tokens, or API keys.'),L('Vérifiez cible, visibilité et conséquence avant toute action.','Check target, visibility, and consequence before every action.'),L('L’humain reste responsable de l’usage final.','A person remains responsible for final use.')],[],'')];page('safety',L('Bonnes pratiques','Best practices'),L('Vérifiez, protégez, puis décidez','Verify, protect, then decide'),L('Terminez avec les règles essentielles pour utiliser la plateforme de manière responsable.','Finish with the essential rules for responsible platform use.'),safe,[]);}
}
function render(){
root.lang=lang;document.getElementById('product').textContent=data.brand.product||L('Assistant IA','AI Assistant');var org=document.getElementById('organization');org.textContent=data.brand.organization||'';org.hidden=!org.textContent;
document.getElementById('eyebrow').textContent=L('Guide interactif','Interactive guide');var greeting=L('Bienvenue','Welcome');if(data.user&&data.user.name)greeting+=', '+data.user.name;document.getElementById('welcome').textContent=greeting;document.getElementById('heroText').textContent=L('Découvrez où trouver chaque fonction, comment l’utiliser et des exemples lisibles prêts à essayer.','Learn where every feature lives, how to use it, and readable examples ready to try.');
var identity=document.getElementById('identity');identity.replaceChildren();if(data.user&&data.user.role_label&&data.user.role_label[lang])add(identity,E('span','pill',L('Rôle : ','Role: ')+data.user.role_label[lang]);add(identity,E('span','pill good',L('Accès vérifiés côté serveur','Server-side access verified')));
build();current=Math.min(current,Math.max(0,pages.length-1));var rail=document.getElementById('rail');rail.replaceChildren();pages.forEach(function(pg,i){var b=E('button','');b.type='button';b.dataset.step=String(i);add(b,E('span','step-number',String(i+1)),E('span','nav-label',pg.label));b.addEventListener('click',function(){go(i,i>=current?1:-1,true);});rail.appendChild(b);});document.getElementById('back').textContent=L('Précédent','Previous');document.getElementById('fresh').textContent=L('Guide généré : ','Guide generated: ')+data.generated_at_label[lang];go(current,1,false);
}
function go(index,dir,focus){if(!pages.length)return;direction=dir||1;current=Math.max(0,Math.min(pages.length-1,index));var stage=document.getElementById('stage'),node=pages[current].node;node.classList.toggle('reverse',direction<0);stage.replaceChildren(node);document.querySelectorAll('[data-step]').forEach(function(b,i){b.classList.toggle('done',i<current);if(i===current)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});var atStart=current===0,atEnd=current===pages.length-1;document.getElementById('edgePrev').disabled=atStart;document.getElementById('edgeNext').disabled=atEnd;document.getElementById('back').disabled=atStart;document.getElementById('next').textContent=atEnd?L('Commencer','Start'):L('Suivant','Next');document.getElementById('bar').style.width=String(((current+1)/pages.length)*100)+'%';document.getElementById('progress').textContent=L(String(current+1)+' sur '+String(pages.length),String(current+1)+' of '+String(pages.length));if(focus){var h=node.querySelector('h2');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}}requestAnimationFrame(reportHeight);}
document.getElementById('language').value=lang;document.getElementById('language').addEventListener('change',function(e){lang=e.target.value==='fr'?'fr':'en';closeModal();render();});
document.getElementById('theme').addEventListener('change',function(e){if(e.target.value==='auto')delete root.dataset.theme;else root.dataset.theme=e.target.value;requestAnimationFrame(reportHeight);});
document.getElementById('edgePrev').addEventListener('click',function(){go(current-1,-1,true);});document.getElementById('edgeNext').addEventListener('click',function(){go(current+1,1,true);});document.getElementById('back').addEventListener('click',function(){go(current-1,-1,true);});document.getElementById('next').addEventListener('click',function(){if(current<pages.length-1)go(current+1,1,true);else sendPrompt(L('Aide-moi à choisir le bon modèle et à formuler une première demande sûre et précise.','Help me choose the right model and write a safe, precise first request.'));});
document.getElementById('closeModal').addEventListener('click',closeModal);document.getElementById('backdrop').addEventListener('click',function(e){if(e.target===this)closeModal();});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeModal();if(e.altKey&&e.key==='ArrowRight'){e.preventDefault();go(current+1,1,true);}if(e.altKey&&e.key==='ArrowLeft'){e.preventDefault();go(current-1,-1,true);}if(!document.getElementById('backdrop').hidden&&e.key==='Tab'){var modal=document.querySelector('.modal'),focusable=modal.querySelectorAll('button:not([disabled]),a[href]');if(focusable.length){var first=focusable[0],last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}}});
window.addEventListener('load',reportHeight);new ResizeObserver(reportHeight).observe(document.body);render();
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


class Event:
    class Valves(BaseModel):
        enabled: bool = Field(True, description="Active le moteur d’onboarding AI Assistant.")
        production_enabled: bool = Field(
            False,
            description="Bascule de sécurité. Activer uniquement après validation avec le compte de test.",
        )
        create_on_first_login: bool = Field(
            False,
            description="Créer le guide v4 à la première connexion des comptes existants qui ne l’ont pas encore.",
        )
        refresh_on_login: bool = Field(
            True,
            description="Actualiser le guide existant à la connexion quand les accès ont changé.",
        )
        refresh_interval_minutes: int = Field(
            360, ge=15, le=10080, description="Délai minimal entre deux vérifications d’accès à la connexion."
        )
        welcome_title_fr: str = Field("Bienvenue sur AI Assistant", description="Titre français du chat d’accueil.")
        welcome_title_en: str = Field("Welcome to AI Assistant", description="Titre anglais du chat d’accueil.")
        preferred_welcome_model_id: str = Field(
            "", description="Modèle préféré. Il est utilisé seulement si l’utilisateur y a réellement accès."
        )
        default_group_id: str = Field(
            "", description="Groupe attribué aux nouveaux comptes avant le calcul de leurs accès. Vide pour désactiver."
        )
        default_language: str = Field("fr", pattern="^(fr|en)$", description="Langue affichée lors de l’ouverture: fr ou en.")
        product_name: str = Field("AI Assistant", max_length=60, description="Nom du service.")
        organization_name: str = Field(
            "", max_length=120, description="Optional organization label shown under the product name."
        )
        primary_color: str = Field("#6D4AFF", description="Legacy v3 color valve; retained for upgrade compatibility.")
        secondary_color: str = Field("#6D4AFF", description="Legacy v3 color valve; retained for upgrade compatibility.")
        accent_color: str = Field("#6D4AFF", description="Legacy v3 color valve; retained for upgrade compatibility.")
        warm_color: str = Field("#6D4AFF", description="Legacy v3 color valve; retained for upgrade compatibility.")
        font_family: str = Field(
            "Arial", pattern="^(Arial|System)$", description="Police locale: Arial ou System. Aucune police distante n’est chargée."
        )
        support_url: str = Field("", description="URL HTTPS du support AI Assistant.")
        privacy_url: str = Field("", description="URL HTTPS de la politique de confidentialité.")
        acceptable_use_url: str = Field("", description="URL HTTPS des règles d’utilisation.")
        feedback_url: str = Field("", description="URL HTTPS du formulaire de retour.")
        show_user_name: bool = Field(False, description="Opt-in: afficher le prénom/nom du compte dans le guide.")
        show_role_badge: bool = Field(False, description="Opt-in: afficher le libellé du rôle.")
        show_access_counts: bool = Field(False, description="Opt-in: afficher les nombres de ressources accessibles.")
        show_welcome: bool = Field(True, description="Afficher la rubrique de bienvenue et confidentialité.")
        show_models: bool = Field(True, description="Afficher le tutoriel de sélection des modèles.")
        show_chat_basics: bool = Field(True, description="Afficher les bases de rédaction et les sélecteurs / $ # +.")
        show_prompts: bool = Field(True, description="Afficher le tutoriel Prompts si des prompts sont accessibles.")
        show_skills: bool = Field(True, description="Afficher le tutoriel Skills si des compétences sont accessibles.")
        show_knowledge: bool = Field(True, description="Afficher le tutoriel Knowledge si la fonction est accessible.")
        show_knowledge_creation: bool = Field(True, description="Afficher la création Knowledge seulement avec workspace.knowledge.")
        show_notes: bool = Field(True, description="Afficher Notes seulement si la permission effective est active.")
        show_web_search: bool = Field(True, description="Afficher Web Search seulement si la permission effective est active.")
        show_file_upload: bool = Field(True, description="Afficher File Upload seulement si la permission effective est active.")
        show_tools: bool = Field(True, description="Afficher Tools si des outils sont accessibles.")
        show_channels: bool = Field(True, description="Afficher Channels seulement si la permission effective est active.")
        show_folders: bool = Field(True, description="Afficher Folders seulement si la permission effective est active.")
        show_memory: bool = Field(True, description="Afficher Memory seulement si la permission effective est active.")
        show_calendar: bool = Field(True, description="Afficher Calendar seulement si la permission effective est active.")
        show_automations: bool = Field(True, description="Afficher Automations seulement si la permission effective est active.")
        show_image_generation: bool = Field(True, description="Afficher Image Generation seulement si la permission effective est active.")
        show_code_interpreter: bool = Field(True, description="Afficher Code Interpreter seulement si la permission effective est active.")
        show_voice: bool = Field(True, description="Afficher STT, TTS et Call seulement si au moins une permission est active.")
        show_multiple_models: bool = Field(True, description="Afficher Multiple Models seulement si la permission effective est active.")
        show_safety: bool = Field(True, description="Afficher la rubrique sécurité et bonnes pratiques.")
        expose_model_names: bool = Field(True, description="Inclure noms/IDs publics des modèles accessibles.")
        expose_prompt_names: bool = Field(False, description="Opt-in: inclure noms/commandes des prompts accessibles; jamais leur contenu.")
        expose_tool_names: bool = Field(True, description="Inclure noms/descriptions courts des outils accessibles; jamais schémas ou secrets.")
        expose_skill_names: bool = Field(True, description="Inclure noms/descriptions courts des skills accessibles; jamais leurs instructions.")
        expose_knowledge_names: bool = Field(False, description="Opt-in: inclure noms/descriptions des bases; jamais les documents.")
        expose_channel_names: bool = Field(False, description="Opt-in: inclure noms/descriptions des canaux visibles; jamais messages ou membres.")
        show_disabled_features: bool = Field(
            False, description="Conservé pour compatibilité; les tutoriels restent masqués si la permission effective est absente."
        )
        max_items_per_section: int = Field(
            8, ge=1, le=30, description="Nombre maximal de ressources affichées par catégorie."
        )
        max_functions_per_model: int = Field(
            6, ge=0, le=20, description="Nombre maximal d’actions et filtres affichés par modèle."
        )
        max_description_chars: int = Field(
            220, ge=60, le=1000, description="Longueur maximale des descriptions affichées."
        )
        catalog_timeout_seconds: int = Field(
            20, ge=3, le=60, description="Délai maximal par source de catalogue."
        )
        test_user: str = Field("", description="E-mail ou ID du compte utilisé pour la préproduction.")
        test_revision: int = Field(
            0, ge=0, description="Augmenter ce nombre pour créer un nouveau chat de test."
        )
        test_assign_group: bool = Field(
            False, description="Ajouter le compte de test au groupe par défaut pendant le test."
        )

    def __init__(self):
        self.valves = self.Valves()
        self._redis = self._connect_redis()
        self._prefix = self._redis_prefix()
        self._local_locks: set[str] = set()

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
                if not self.valves.production_enabled:
                    return
                user_id = self._user_id_from_event(__event_name__, event)
                if user_id:
                    await self._maybe_onboard(user_id, __app__, __request__, __event_name__)
                return

            if __event_name__ == "auth.login" and self.valves.production_enabled:
                user_id = self._user_id_from_event(__event_name__, event)
                if user_id:
                    await self._handle_login(user_id, __app__, __request__)
                return

            if __event_name__ == "function.valves_updated":
                subject_id = str((event.get("subject") or {}).get("id") or "")
                if __id__ and subject_id == str(__id__):
                    await self._maybe_run_test(__app__, __request__)
        except Exception:
            log.exception("[onboarding] unhandled event=%s id=%s", __event_name__, __event_id__)

    async def _maybe_onboard(self, user_id: str, app, request, source: str) -> None:
        marker = await self._get_marker(user_id)
        if marker and int(marker.get("version", 0)) >= ONBOARDING_VERSION:
            return
        token = self._acquire_lock(user_id)
        if token is None:
            return
        try:
            marker = await self._get_marker(user_id)
            if marker and int(marker.get("version", 0)) >= ONBOARDING_VERSION:
                return
            created = await self._onboard(user_id, app, request, source, assign_group=True)
            if created:
                await self._save_marker(user_id, created)
        finally:
            self._release_lock(user_id, token)

    async def _handle_login(self, user_id: str, app, request) -> None:
        marker = await self._get_marker(user_id)
        if not marker or int(marker.get("version", 0)) < ONBOARDING_VERSION:
            if self.valves.create_on_first_login:
                await self._maybe_onboard(user_id, app, request, "auth.login")
            return
        if not self.valves.refresh_on_login:
            return
        age = int(time.time()) - int(marker.get("refreshed_at", marker.get("created_at", 0)) or 0)
        if age < int(self.valves.refresh_interval_minutes) * 60:
            return
        token = self._acquire_lock(user_id)
        if token is None:
            return
        try:
            await self._refresh_existing(user_id, marker, app, request)
        finally:
            self._release_lock(user_id, token)

    async def _onboard(self, user_id: str, app, request, source: str, assign_group: bool) -> Optional[dict]:
        from open_webui.models.users import Users

        user = await Users.get_user_by_id(user_id)
        if user is None:
            log.warning("[onboarding] user not found id=%s", user_id)
            return None
        if assign_group and (self.valves.default_group_id or "").strip():
            await self._assign_group(user_id)
            user = await Users.get_user_by_id(user_id) or user

        snapshot = await self._build_snapshot(user, app, request)
        result = await self._create_welcome_chat(user, snapshot)
        if result:
            result.update({"version": ONBOARDING_VERSION, "source": source})
            log.info("[onboarding] created user=%s source=%s resources=%s", user_id, source, snapshot["counts"])
        return result

    async def _refresh_existing(self, user_id: str, marker: dict, app, request) -> None:
        from open_webui.models.chats import Chats
        from open_webui.models.users import Users

        chat_id = str(marker.get("chat_id") or "")
        message_id = str(marker.get("message_id") or "")
        if not chat_id or not message_id:
            return
        user = await Users.get_user_by_id(user_id)
        if user is None:
            return
        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
        if chat is None:
            log.warning("[onboarding] refresh skipped, chat ownership check failed user=%s", user_id)
            return

        snapshot = await self._build_snapshot(user, app, request)
        new_hash = self._snapshot_hash(snapshot)
        now = int(time.time())
        new_marker = {**marker, "refreshed_at": now, "catalog_hash": new_hash}
        if new_hash != marker.get("catalog_hash"):
            stored = (getattr(chat, "chat", None) or {}).get("history", {}).get("messages", {}).get(message_id, {})
            message = {**stored, "content": self._fallback_text(), "embeds": [self._render_html(snapshot)]}
            result = await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, message, touch=False)
            if result is None:
                log.warning("[onboarding] refresh write failed user=%s", user_id)
                return
            log.info("[onboarding] refreshed user=%s resources=%s", user_id, snapshot["counts"])
        await self._save_marker(user_id, new_marker)

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
            "content": self._fallback_text(),
            "embeds": [self._render_html(snapshot)],
            "files": [],
            "sources": [],
            "model": model_id,
            "modelName": model_id,
            "modelIdx": 0,
            "timestamp": now,
            "done": True,
        }
        title = self.valves.welcome_title_en if self.valves.default_language == "en" else self.valves.welcome_title_fr
        chat = {
            "id": "",
            "title": self._plain(title, 120) or "AI Assistant onboarding",
            "models": [model_id] if model_id else [],
            "params": {},
            "history": {"messages": {message_id: message}, "currentId": message_id},
            "messages": [message],
            "tags": [],
            "timestamp": now * 1000,
            "meta": {"evegpt_onboarding": {"version": ONBOARDING_VERSION, "created_at": now}},
        }
        result = await Chats.insert_new_chat(chat_id, str(user.id), ChatForm(chat=chat))
        if result is None:
            return None
        return {
            "chat_id": chat_id,
            "message_id": message_id,
            "created_at": now,
            "refreshed_at": now,
            "catalog_hash": self._snapshot_hash(snapshot),
        }

    async def _build_snapshot(self, user, app, request) -> dict:
        req = self._usable_request(app, request)
        timeout = int(self.valves.catalog_timeout_seconds)

        async def safe(name: str, coroutine, fallback):
            try:
                return await asyncio.wait_for(coroutine, timeout=timeout)
            except Exception as exc:
                log.warning("[onboarding] catalog source=%s user=%s failed: %s", name, user.id, type(exc).__name__)
                return fallback

        permissions_task = safe("permissions", self._load_permissions(user), {})
        models_task = safe("models", self._load_models(req, user), [])
        prompts_task = safe("prompts", self._load_prompts(user), [])
        tools_task = safe("tools", self._load_tools(req, user), [])
        skills_task = safe("skills", self._load_skills(req, user), [])
        knowledge_task = safe("knowledge", self._load_knowledge(user), [])
        channels_task = safe("channels", self._load_channels(user), [])
        permissions, models, prompts, tools, skills, knowledge, channels = await asyncio.gather(
            permissions_task, models_task, prompts_task, tools_task, skills_task, knowledge_task, channels_task
        )

        features = self._features_from_permissions(permissions)
        max_items = int(self.valves.max_items_per_section)
        resources = {
            "models": [self._model_public(x) for x in models[:max_items]] if self.valves.expose_model_names else [],
            "prompts": [self._prompt_public(x) for x in prompts[:max_items]] if self.valves.expose_prompt_names else [],
            "tools": [self._tool_public(x) for x in tools[:max_items]] if self.valves.expose_tool_names else [],
            "skills": [self._skill_public(x) for x in skills[:max_items]] if self.valves.expose_skill_names else [],
            "knowledge": [self._knowledge_public(x) for x in knowledge[:max_items]] if self.valves.expose_knowledge_names else [],
            "channels": [self._channel_public(x) for x in channels[:max_items]] if self.valves.expose_channel_names else [],
            "features": features,
        }
        preferred = (self.valves.preferred_welcome_model_id or "").strip()
        model_ids = {x["id"] for x in resources["models"]}
        selected_model_id = preferred if preferred in model_ids else (resources["models"][0]["id"] if resources["models"] else "")
        now = int(time.time())
        date_fr = time.strftime("%d/%m/%Y %H:%M", time.localtime(now))
        date_en = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        role = str(getattr(user, "role", "user") or "user")
        role_labels = {
            "admin": {"fr": "Administrateur", "en": "Administrator"},
            "pending": {"fr": "En attente", "en": "Pending"},
            "user": {"fr": "Utilisateur", "en": "User"},
        }.get(role, {"fr": "Utilisateur", "en": "User"})
        return {
            "schema": 1,
            "generated_at": now,
            "generated_at_label": {"fr": date_fr, "en": date_en},
            "user": {
                "name": self._plain(getattr(user, "name", ""), 100) if self.valves.show_user_name else "",
                "role": role if self.valves.show_role_badge else "",
                "role_label": role_labels if self.valves.show_role_badge else {"fr": "", "en": ""},
            },
            "brand": {
                "product": self._plain(self.valves.product_name, 60) or "AI Assistant",
                "organization": self._plain(self.valves.organization_name, 120),
                
            },
            "ui": {
                "default_language": self.valves.default_language,
                "font": "Arial, Helvetica, sans-serif" if self.valves.font_family == "Arial" else "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                "show_models": bool(self.valves.show_models),
                "show_chat_basics": bool(self.valves.show_chat_basics),
                "show_prompts": bool(self.valves.show_prompts),
                "show_skills": bool(self.valves.show_skills),
                "show_knowledge": bool(self.valves.show_knowledge),
                "show_knowledge_creation": bool(self.valves.show_knowledge_creation),
                "show_notes": bool(self.valves.show_notes),
                "show_web_search": bool(self.valves.show_web_search),
                "show_file_upload": bool(self.valves.show_file_upload),
                "show_tools": bool(self.valves.show_tools),
                "show_channels": bool(self.valves.show_channels),
                "show_folders": bool(self.valves.show_folders),
                "show_memory": bool(self.valves.show_memory),
                "show_calendar": bool(self.valves.show_calendar),
                "show_automations": bool(self.valves.show_automations),
                "show_image_generation": bool(self.valves.show_image_generation),
                "show_code_interpreter": bool(self.valves.show_code_interpreter),
                "show_voice": bool(self.valves.show_voice),
                "show_multiple_models": bool(self.valves.show_multiple_models),
                "show_safety": bool(self.valves.show_safety),
            },
            "links": {
                "support_url": self._safe_http_url(self.valves.support_url),
                "privacy_url": self._safe_http_url(self.valves.privacy_url),
                "acceptable_use_url": self._safe_http_url(self.valves.acceptable_use_url),
                "feedback_url": self._safe_http_url(self.valves.feedback_url),
            },
            "selected_model_id": selected_model_id,
            "counts": ({
                "models": len(models), "prompts": len(prompts), "tools": len(tools),
                "skills": len(skills), "knowledge": len(knowledge),
                "features": sum(1 for x in features if x["enabled"]), "channels": len(channels),
            } if self.valves.show_access_counts else {}),
            "available": {
                "models": bool(models), "prompts": bool(prompts), "tools": bool(tools),
                "skills": bool(skills), "knowledge": bool(knowledge), "channels": bool(channels),
            },
            "access": {
                "workspace": {k: bool((permissions.get("workspace") or {}).get(k, False)) for k in ("models", "prompts", "tools", "functions", "knowledge")},
                "chat": {k: bool((permissions.get("chat") or {}).get(k, False)) for k in ("file_upload", "multiple_models", "stt", "tts", "call")},
                "features": {k: bool((permissions.get("features") or {}).get(k, False)) for k in FEATURE_DEFINITIONS},
            },
            "resources": resources,
        }

    async def _load_permissions(self, user) -> dict:
        from open_webui.models.config import Config
        from open_webui.utils.access_control import get_permissions

        defaults = await Config.get("user.permissions")
        return await get_permissions(str(user.id), defaults or {})

    async def _load_models(self, request, user) -> list[dict]:
        from open_webui.models.config import Config
        from open_webui.utils.models import get_all_models, get_filtered_models

        models = await get_all_models(request, refresh=False, user=user)
        models = [m for m in models if not (isinstance(m.get("pipeline"), dict) and m["pipeline"].get("type") == "filter")]
        models = list({str(m.get("id")): m for m in models if m.get("id")}.values())
        models = await get_filtered_models(models, user)
        order = await Config.get("ui.model_order_list") or []
        if order:
            priority = {value: index for index, value in enumerate(order)}
            models.sort(key=lambda m: (priority.get(m.get("id"), 10**9), str(m.get("name") or "").casefold()))
        return models

    async def _load_prompts(self, user) -> list:
        from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
        from open_webui.models.prompts import Prompts

        if getattr(user, "role", None) == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
            return await Prompts.get_prompts()
        return await Prompts.get_prompts_by_user_id(str(user.id), "read")

    async def _load_tools(self, request, user) -> list:
        from open_webui.routers.tools import get_tools

        return await get_tools(request=request, query=None, user=user, db=None)

    async def _load_skills(self, request, user) -> list:
        from open_webui.routers.skills import get_skills

        return await get_skills(request=request, query=None, user=user, db=None)

    async def _load_channels(self, user) -> list:
        from open_webui.models.channels import Channels

        return await Channels.get_channels_by_user_id(str(user.id))

    async def _load_knowledge(self, user) -> list:
        from open_webui.routers.knowledge import get_knowledge_bases

        result = await get_knowledge_bases(page=1, user=user, db=None)
        return list(getattr(result, "items", None) or self._value(result, "items", []) or [])

    def _model_public(self, model: dict) -> dict:
        info = model.get("info") if isinstance(model.get("info"), dict) else {}
        meta = info.get("meta") if isinstance(info.get("meta"), dict) else {}
        description = meta.get("description") or info.get("description") or model.get("description") or ""
        tags = []
        for tag in model.get("tags") or meta.get("tags") or []:
            name = tag.get("name") if isinstance(tag, dict) else tag
            if name:
                tags.append(self._plain(name, 50))
        capabilities = []
        for key, value in (meta.get("capabilities") or {}).items():
            if value is True:
                capabilities.append(self._plain(key.replace("_", " "), 50))
        limit = int(self.valves.max_functions_per_model)
        examples = []
        for suggestion in meta.get("suggestion_prompts") or []:
            if isinstance(suggestion, dict):
                content = suggestion.get("content") or suggestion.get("prompt") or ""
            else:
                content = suggestion
            clean = self._plain(content, 400)
            if clean:
                examples.append(clean)
        return {
            "id": self._plain(model.get("id"), 160),
            "name": self._plain(model.get("name") or model.get("id"), 120),
            "description": self._plain(description, self.valves.max_description_chars),
            "tags": tags[:8],
            "capabilities": capabilities[:8],
            "examples": examples[:3],
            "actions": [self._function_public(x) for x in (model.get("actions") or [])[:limit]],
            "filters": [self._function_public(x) for x in (model.get("filters") or [])[:limit]],
        }

    def _prompt_public(self, prompt) -> dict:
        meta = self._value(prompt, "meta", {}) or {}
        description = meta.get("description", "") if isinstance(meta, dict) else ""
        return {
            "id": self._plain(self._value(prompt, "id", ""), 160),
            "name": self._plain(self._value(prompt, "name", ""), 120),
            "command": self._plain(self._value(prompt, "command", ""), 100).lstrip("/"),
            "description": self._plain(description, self.valves.max_description_chars),
            "tags": [self._plain(x, 50) for x in (self._value(prompt, "tags", []) or [])[:8]],
        }

    def _tool_public(self, tool) -> dict:
        meta = self._value(tool, "meta", {}) or {}
        description = self._value(meta, "description", "")
        authenticated = self._value(tool, "authenticated", None)
        return {
            "id": self._plain(self._value(tool, "id", ""), 160),
            "name": self._plain(self._value(tool, "name", ""), 120),
            "description": self._plain(description, self.valves.max_description_chars),
            **({"authenticated": bool(authenticated)} if authenticated is not None else {}),
        }

    def _skill_public(self, skill) -> dict:
        meta = self._value(skill, "meta", {}) or {}
        tags = self._value(meta, "tags", []) or []
        return {
            "id": self._plain(self._value(skill, "id", ""), 160),
            "name": self._plain(self._value(skill, "name", ""), 120),
            "description": self._plain(self._value(skill, "description", ""), self.valves.max_description_chars),
            "tags": [self._plain(x, 50) for x in tags[:8]],
        }

    def _channel_public(self, channel) -> dict:
        return {
            "id": self._plain(self._value(channel, "id", ""), 160),
            "name": self._plain(self._value(channel, "name", ""), 120),
            "description": self._plain(self._value(channel, "description", ""), self.valves.max_description_chars),
        }

    def _knowledge_public(self, knowledge) -> dict:
        return {
            "id": self._plain(self._value(knowledge, "id", ""), 160),
            "name": self._plain(self._value(knowledge, "name", ""), 120),
            "description": self._plain(self._value(knowledge, "description", ""), self.valves.max_description_chars),
        }

    def _function_public(self, function: dict) -> dict:
        return {
            "id": self._plain(function.get("id", ""), 160),
            "name": self._plain(function.get("name", ""), 120),
            "description": self._plain(function.get("description", ""), self.valves.max_description_chars),
        }

    def _features_from_permissions(self, permissions: dict) -> list[dict]:
        features = permissions.get("features", {}) if isinstance(permissions, dict) else {}
        chat = permissions.get("chat", {}) if isinstance(permissions, dict) else {}
        results = []
        for key, labels in FEATURE_DEFINITIONS.items():
            source = chat if key in {"file_upload", "multiple_models", "stt", "tts", "call"} else features
            enabled = bool(source.get(key, False)) if isinstance(source, dict) else False
            if enabled or self.valves.show_disabled_features:
                results.append({"key": key, "enabled": enabled, "label": {"fr": labels[0], "en": labels[1]}, "description": {"fr": "", "en": ""}})
        return results

    def _render_html(self, snapshot: dict) -> str:
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        # Prevent script termination and HTML parser ambiguity inside the JSON script block.
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return ONBOARDING_HTML.replace("__SNAPSHOT_JSON__", payload)

    def _snapshot_hash(self, snapshot: dict) -> str:
        stable = {k: v for k, v in snapshot.items() if k not in {"generated_at", "generated_at_label"}}
        raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def _assign_group(self, user_id: str) -> None:
        group_id = (self.valves.default_group_id or "").strip()
        if not group_id:
            return
        try:
            from open_webui.models.groups import Groups

            result = await Groups.add_users_to_group(group_id, [user_id])
            if result is None:
                log.warning("[onboarding] default group not found id=%s", group_id)
        except Exception:
            log.exception("[onboarding] default group assignment failed user=%s", user_id)

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
        created = await self._onboard(user_id, app, request, f"test:{revision}", bool(self.valves.test_assign_group))
        if created:
            await self._update_user_settings(user_id, {TEST_SETTINGS_KEY: {"revision": revision, "run_at": int(time.time()), **created}})

    async def _get_marker(self, user_id: str) -> Optional[dict]:
        try:
            from open_webui.models.users import Users

            user = await Users.get_user_by_id(user_id)
            marker = self._settings_dict(user).get(SETTINGS_KEY)
            if marker is True:
                return {"version": 1}
            return dict(marker) if isinstance(marker, dict) else None
        except Exception:
            log.warning("[onboarding] marker read failed user=%s", user_id, exc_info=True)
            return None

    async def _save_marker(self, user_id: str, marker: dict) -> None:
        await self._update_user_settings(user_id, {SETTINGS_KEY: marker})
        if self._redis is not None:
            try:
                self._redis.set(self._marker_key(user_id), str(marker.get("version", ONBOARDING_VERSION)))
            except Exception:
                log.warning("[onboarding] redis marker write failed", exc_info=True)

    def _acquire_lock(self, user_id: str, ttl: int = 300) -> Optional[str]:
        key = self._lock_key(user_id)
        token = str(uuid.uuid4())
        if self._redis is not None:
            try:
                return token if self._redis.set(key, token, nx=True, ex=ttl) else None
            except Exception:
                log.warning("[onboarding] redis lock unavailable", exc_info=True)
        if key in self._local_locks:
            return None
        self._local_locks.add(key)
        return token

    def _release_lock(self, user_id: str, token: str) -> None:
        key = self._lock_key(user_id)
        if self._redis is not None:
            try:
                self._redis.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1, key, token,
                )
            except Exception:
                log.warning("[onboarding] redis lock release failed", exc_info=True)
        self._local_locks.discard(key)

    def _marker_key(self, user_id: str) -> str:
        return f"{self._prefix}:evegpt:onboarding:v{ONBOARDING_VERSION}:user:{user_id}"

    def _lock_key(self, user_id: str) -> str:
        return f"{self._prefix}:evegpt:onboarding:lock:user:{user_id}"

    def _fallback_text(self) -> str:
        return FALLBACK_TEXT_EN if self.valves.default_language == "en" else FALLBACK_TEXT_FR

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

        result = await Users.update_user_settings_by_id(user_id, patch)
        if result is None:
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