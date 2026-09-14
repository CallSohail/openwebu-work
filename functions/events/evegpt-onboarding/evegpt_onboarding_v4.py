"""
title: EveGPT Dynamic Onboarding Rich UI
author: Muhammad Sohail, DSIN, Université d'Évry Paris-Saclay
version: 4.0.0
required_open_webui_version: 0.11.3
description: Secure, bilingual, role-aware onboarding built from each user's accessible Open WebUI resources.
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


log = logging.getLogger("evegpt.onboarding")
log.setLevel(logging.INFO)

ONBOARDING_VERSION = 4
SETTINGS_KEY = "evegpt_onboarding"
TEST_SETTINGS_KEY = "evegpt_onboarding_test"

FALLBACK_TEXT_FR = """## Bienvenue sur EveGPT

Votre guide interactif et personnalisé s'affiche au-dessus de ce message. Il est
construit uniquement à partir des modèles, prompts, outils, compétences, bases de
connaissances et fonctions auxquels votre compte peut accéder.

Si le guide ne s'affiche pas, actualisez la page ou contactez le support EveGPT.
"""

FALLBACK_TEXT_EN = """## Welcome to EveGPT

Your personalized interactive guide appears above this message. It is built only
from models, prompts, tools, skills, knowledge bases, and functions your account
is allowed to access.

If the guide does not appear, refresh the page or contact EveGPT support.
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
    :root {
      color-scheme: light;
      --bg:#f6f6f7; --surface:#ffffff; --soft:#f1f1f3; --soft2:#fafafa;
      --text:#18181b; --muted:#71717a; --line:#e4e4e7; --accent:#6d4aff;
      --accent-soft:#efecff; --ok:#16834b; --warn:#a16207;
      --shadow:0 18px 55px rgba(24,24,27,.10);
      --font:Arial,Helvetica,sans-serif;
    }
    [data-theme="dark"] {
      color-scheme:dark;
      --bg:#0d0d0f; --surface:#171719; --soft:#222225; --soft2:#1c1c1f;
      --text:#f4f4f5; --muted:#a1a1aa; --line:#343439; --accent:#a78bfa;
      --accent-soft:#2c2444; --ok:#63d89a; --warn:#facc15;
      --shadow:0 20px 62px rgba(0,0,0,.34);
    }
    @media (prefers-color-scheme:dark) {
      :root:not([data-theme="light"]) {
        color-scheme:dark;
        --bg:#0d0d0f; --surface:#171719; --soft:#222225; --soft2:#1c1c1f;
        --text:#f4f4f5; --muted:#a1a1aa; --line:#343439; --accent:#a78bfa;
        --accent-soft:#2c2444; --ok:#63d89a; --warn:#facc15;
        --shadow:0 20px 62px rgba(0,0,0,.34);
      }
    }
    *{box-sizing:border-box}
    html,body{margin:0;min-width:0}
    body{padding:5px;background:transparent;color:var(--text);font:14px/1.5 var(--font)}
    button,select{font:inherit}
    button,summary,select{outline:none}
    button:focus-visible,summary:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid color-mix(in srgb,var(--accent) 50%,transparent);outline-offset:2px}
    .app{width:100%;max-width:1080px;margin:auto;overflow:hidden;border:1px solid var(--line);border-radius:20px;background:var(--surface);box-shadow:var(--shadow)}
    .top{padding:18px 22px 15px;border-bottom:1px solid var(--line);background:var(--surface)}
    .toprow,.brand,.controls,.progressrow,.footer,.resource-head,.tutorial-head,.availability{display:flex;align-items:center}
    .toprow{justify-content:space-between;gap:14px}
    .brand{gap:10px;min-width:0}
    .brandmark{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;color:#fff;background:var(--text);font-weight:800}
    [data-theme="dark"] .brandmark{color:#171719;background:#f4f4f5}
    .brand strong{display:block;font-size:15px}
    .brand small{display:block;color:var(--muted);font-size:11px}
    .controls{gap:7px}
    .select{height:36px;padding:0 9px;border:1px solid var(--line);border-radius:9px;color:var(--text);background:var(--soft2)}
    .intro{max-width:720px;margin-top:23px}
    .kicker{margin:0 0 6px;color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}
    h1{margin:0;font-size:clamp(25px,4vw,38px);line-height:1.1;letter-spacing:-.035em}
    .intro p{margin:9px 0 0;color:var(--muted);font-size:14px}
    .badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
    .badge{display:inline-flex;align-items:center;min-height:24px;padding:3px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--soft2);font-size:10px;font-weight:700}
    .badge.ok{color:var(--ok)}
    .progressrow{gap:12px;margin-top:18px}
    .track{height:5px;flex:1;overflow:hidden;border-radius:99px;background:var(--soft)}
    .bar{width:0;height:100%;border-radius:inherit;background:var(--accent);transition:width .2s ease}
    .progresslabel{min-width:70px;color:var(--muted);font-size:11px;text-align:right}
    .layout{display:grid;grid-template-columns:232px minmax(0,1fr)}
    .rail{padding:14px 10px;border-right:1px solid var(--line);background:var(--soft2)}
    .rail button{display:flex;align-items:center;width:100%;min-height:42px;gap:9px;padding:7px 9px;border:0;border-radius:10px;color:var(--muted);background:transparent;font-size:12px;font-weight:700;text-align:left;cursor:pointer}
    .rail button+button{margin-top:3px}
    .rail button:hover{color:var(--text);background:var(--soft)}
    .rail button[aria-current="step"]{color:var(--text);background:var(--surface);box-shadow:0 2px 10px rgba(24,24,27,.08)}
    .num{display:grid;place-items:center;width:24px;height:24px;flex:0 0 auto;border:1px solid var(--line);border-radius:50%;background:var(--surface);font-size:10px}
    .rail button[aria-current="step"] .num{border-color:var(--accent);color:#fff;background:var(--accent)}
    .done .num{border-color:color-mix(in srgb,var(--ok) 45%,var(--line));color:var(--ok)}
    main{min-width:0;padding:25px 29px 20px}
    .panel[hidden]{display:none}
    .panel{animation:fade .16s ease both}
    .panel h2{margin:0;font-size:clamp(21px,3vw,28px);line-height:1.2;letter-spacing:-.025em}
    .lead{margin:7px 0 18px;color:var(--muted)}
    .notice{padding:12px 13px;border:1px solid var(--line);border-radius:12px;color:var(--muted);background:var(--soft2);font-size:12px}
    .notice strong{color:var(--text)}
    .stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:15px 0}
    .stat{padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
    .stat b{display:block;font-size:21px}
    .stat span{color:var(--muted);font-size:10px}
    .syntax{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:15px 0}
    .syntax div{padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--soft2)}
    .syntax code{display:block;color:var(--accent);font:bold 19px/1 ui-monospace,SFMono-Regular,Consolas,monospace}
    .syntax b{display:block;margin-top:7px;font-size:11px}
    .syntax small{display:block;margin-top:2px;color:var(--muted);font-size:10px}
    .tutorials,.resources{display:grid;gap:10px}
    .tutorial{border:1px solid var(--line);border-radius:13px;background:var(--surface);overflow:hidden}
    .tutorial summary{display:flex;align-items:flex-start;gap:11px;padding:14px;cursor:pointer;list-style:none}
    .tutorial summary::-webkit-details-marker{display:none}
    .tutorial summary:after{content:"+";margin-left:auto;color:var(--muted);font-size:18px;line-height:1}
    .tutorial[open] summary:after{content:"−"}
    .tutorial-icon{display:grid;place-items:center;width:28px;height:28px;flex:0 0 auto;border-radius:8px;color:var(--accent);background:var(--accent-soft);font-size:11px;font-weight:800}
    .tutorial-head{align-items:flex-start;gap:8px;min-width:0}
    .tutorial-title b{display:block;font-size:13px}
    .tutorial-title span{display:block;margin-top:2px;color:var(--muted);font-size:11px;font-weight:400}
    .tutorial-body{padding:0 14px 14px 53px}
    .label{margin:2px 0 6px;color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
    .where{display:flex;align-items:center;flex-wrap:wrap;gap:5px;margin-bottom:12px}
    .where span{padding:4px 7px;border:1px solid var(--line);border-radius:7px;background:var(--soft2);font-size:10px;font-weight:700}
    .where i{color:var(--muted);font-style:normal}
    ol.guide{margin:0 0 12px;padding:0;list-style:none;counter-reset:step}
    ol.guide li{position:relative;margin:0 0 8px;padding-left:29px;color:var(--muted);font-size:12px;counter-increment:step}
    ol.guide li:before{content:counter(step);position:absolute;left:0;top:0;display:grid;place-items:center;width:20px;height:20px;border:1px solid var(--line);border-radius:50%;color:var(--text);background:var(--soft2);font-size:9px;font-weight:800}
    .examples{display:grid;gap:7px}
    .example{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding:9px 10px;border:1px solid var(--line);border-radius:9px;background:var(--soft2)}
    .example code{color:var(--text);font:11px/1.45 var(--font);white-space:pre-wrap;overflow-wrap:anywhere}
    .try{flex:0 0 auto;padding:5px 8px;border:1px solid var(--accent);border-radius:7px;color:var(--accent);background:transparent;font-size:10px;font-weight:800;cursor:pointer}
    .try:hover{color:#fff;background:var(--accent)}
    .resource{padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
    .resource-head{justify-content:space-between;align-items:flex-start;gap:10px}
    .resource h3{margin:0;font-size:13px;overflow-wrap:anywhere}
    .rid{display:block;margin-top:2px;color:var(--muted);font:9px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
    .resource p{margin:7px 0 0;color:var(--muted);font-size:11px}
    .chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
    .chip{padding:3px 6px;border:1px solid var(--line);border-radius:999px;color:var(--muted);background:var(--soft2);font-size:9px}
    .resource-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
    .availability{gap:6px;color:var(--ok);font-size:9px;font-weight:800;white-space:nowrap}
    .dot{width:6px;height:6px;border-radius:50%;background:currentColor}
    .formula{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:12px}
    .formula div{padding:11px;border:1px solid var(--line);border-radius:11px;background:var(--soft2)}
    .formula b{display:block;font-size:11px}
    .formula small{display:block;margin-top:3px;color:var(--muted);font-size:10px}
    .feature-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
    .feature{padding:12px;border:1px solid var(--line);border-radius:11px;background:var(--soft2)}
    .feature b{display:block;font-size:12px}
    .feature p{margin:4px 0 0;color:var(--muted);font-size:10px}
    .footer{justify-content:space-between;gap:12px;margin-top:20px;padding-top:15px;border-top:1px solid var(--line)}
    .fresh{color:var(--muted);font-size:10px}
    .actions{display:flex;gap:7px}
    .primary,.secondary{min-height:38px;padding:7px 12px;border-radius:9px;font-weight:700;cursor:pointer}
    .primary{border:1px solid var(--accent);color:#fff;background:var(--accent)}
    .secondary{border:1px solid var(--line);color:var(--text);background:var(--surface)}
    .secondary:disabled{opacity:.45;cursor:not-allowed}
    .toast{position:fixed;left:50%;bottom:14px;z-index:5;max-width:calc(100% - 28px);padding:9px 12px;border-radius:9px;color:#fff;background:#27272a;box-shadow:0 8px 28px rgba(0,0,0,.25);font-size:11px;font-weight:700;opacity:0;transform:translate(-50%,8px);pointer-events:none;transition:.15s}
    .toast.show{opacity:1;transform:translate(-50%,0)}
    @keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
    @media(max-width:780px){
      body{padding:2px}.app{border-radius:15px}.top{padding:15px}.layout{display:block}.rail{display:flex;gap:5px;overflow-x:auto;padding:8px;border-right:0;border-bottom:1px solid var(--line)}.rail button{width:auto;flex:0 0 auto;padding:6px}.rail button+button{margin-top:0}.navtext{display:none}main{padding:19px 15px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.tutorial-body{padding-left:14px}
    }
    @media(max-width:510px){
      .brand small{display:none}.controls{gap:4px}.select{max-width:82px;padding:0 5px}.syntax,.formula,.feature-grid{grid-template-columns:1fr 1fr}.footer{display:block}.actions{margin-top:10px}.actions button{flex:1}
    }
    @media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
  </style>
</head>
<body>
<section class="app" aria-label="Interactive onboarding">
  <header class="top">
    <div class="toprow">
      <div class="brand"><div class="brandmark" aria-hidden="true">AI</div><div><strong id="product"></strong><small id="organization"></small></div></div>
      <div class="controls">
        <label for="language" class="sr" hidden>Language</label>
        <select class="select" id="language" aria-label="Language"><option value="en">English</option><option value="fr">Français</option></select>
        <select class="select" id="theme" aria-label="Theme"><option value="auto">Auto</option><option value="light">Light</option><option value="dark">Dark</option></select>
      </div>
    </div>
    <div class="intro">
      <p class="kicker" id="kicker"></p>
      <h1 id="welcome"></h1>
      <p id="introText"></p>
      <div class="badges"><span class="badge" id="role"></span><span class="badge ok" id="verified"></span></div>
    </div>
    <div class="progressrow"><div class="track" aria-hidden="true"><div class="bar" id="bar"></div></div><span class="progresslabel" id="progress"></span></div>
  </header>
  <div class="layout">
    <nav class="rail" id="rail" aria-label="Onboarding steps"></nav>
    <main>
      <div id="panels"></div>
      <footer class="footer"><span class="fresh" id="fresh"></span><div class="actions"><button class="secondary" id="back" type="button"></button><button class="primary" id="next" type="button"></button></div></footer>
    </main>
  </div>
</section>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script type="application/json" id="snapshot">__SNAPSHOT_JSON__</script>
<script>
(function(){
  'use strict';
  var data=JSON.parse(document.getElementById('snapshot').textContent);
  var root=document.documentElement;
  var lang=data.ui.default_language==='fr'?'fr':'en';
  var current=0, pages=[], toastTimer;
  var E=function(tag,cls,text){var n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  var add=function(parent){for(var i=1;i<arguments.length;i++){if(arguments[i])parent.appendChild(arguments[i]);}return parent;};
  var L=function(fr,en){return lang==='fr'?fr:en;};
  var enabled=function(key){var list=(data.resources&&data.resources.features)||[];for(var i=0;i<list.length;i++){if(list[i].key===key)return !!list[i].enabled;}return false;};
  var allowed=function(group,key){return !!(data.access&&data.access[group]&&data.access[group][key]);};
  var sendPrompt=function(text){parent.postMessage({type:'input:prompt',text:String(text||'').slice(0,4000)},'*');showToast();};
  var reportHeight=function(){parent.postMessage({type:'iframe:height',height:Math.ceil(document.documentElement.scrollHeight)},'*');};
  function showToast(){clearTimeout(toastTimer);var t=document.getElementById('toast');t.textContent=L('Exemple copié dans la zone de saisie','Example placed in the chat input');t.classList.add('show');toastTimer=setTimeout(function(){t.classList.remove('show');},2100);}
  function action(label,prompt){var b=E('button','try',label);b.type='button';b.addEventListener('click',function(){sendPrompt(prompt);});return b;}
  function path(items){var wrap=E('div','where');items.forEach(function(x,i){if(i)add(wrap,E('i','', '›'));add(wrap,E('span','',x));});return wrap;}
  function guide(opt){
    var d=E('details','tutorial');if(opt.open)d.open=true;
    var summary=E('summary');var icon=E('span','tutorial-icon',opt.icon||'01');var head=E('span','tutorial-title');add(head,E('b','',opt.title),E('span','',opt.description));add(summary,icon,head);add(d,summary);
    var body=E('div','tutorial-body');add(body,E('div','label',L('Où le trouver','Where to find it')),path(opt.where||[]));
    if(opt.steps&&opt.steps.length){add(body,E('div','label',L('Étapes','Steps')));var ol=E('ol','guide');opt.steps.forEach(function(x){add(ol,E('li','',x));});add(body,ol);}
    if(opt.examples&&opt.examples.length){add(body,E('div','label',L('Exemples intégrés','Built-in examples')));var ex=E('div','examples');opt.examples.forEach(function(x){var row=E('div','example');add(row,E('code','',x),action(L('Utiliser','Use'),x));add(ex,row);});add(body,ex);}
    if(opt.note)add(body,E('div','notice',opt.note));
    add(d,body);return d;
  }
  function base(title,lead){var p=E('section','panel');add(p,E('h2','',title),E('p','lead',lead));return p;}
  function resources(items,kind){
    var list=E('div','resources');
    if(!items||!items.length){add(list,E('div','notice',L('Aucun élément accessible dans cette catégorie.','No accessible item in this category.')));return list;}
    items.forEach(function(item){
      var card=E('article','resource'),hd=E('div','resource-head'),names=E('div'),avail=E('span','availability');
      add(names,E('h3','',item.name||item.id));if(item.id&&(kind==='model'||kind==='channel'))add(names,E('span','rid',item.id));
      add(avail,E('span','dot'),document.createTextNode(kind==='tool'&&item.authenticated===false?L('Connexion requise','Connection required'):L('Accessible','Accessible')));
      add(hd,names,avail);add(card,hd);if(item.description)add(card,E('p','',item.description));
      var vals=[].concat(item.tags||[],item.capabilities||[]).slice(0,8);if(vals.length){var chips=E('div','chips');vals.forEach(function(v){add(chips,E('span','chip',v));});add(card,chips);}
      var acts=E('div','resource-actions');
      if(kind==='model')add(acts,action(L('Préparer la sélection','Prepare selection'),'/model '+item.id));
      if(kind==='prompt'&&item.command)add(acts,action('/'+String(item.command).replace(/^[/]/,''),'/'+String(item.command).replace(/^[/]/,'')));
      if(kind==='skill')add(acts,action(L('Ouvrir le sélecteur $','Open $ picker'),'$'));
      if(kind==='knowledge')add(acts,action(L('Ouvrir le sélecteur #','Open # picker'),'#'));
      if(kind==='tool')add(acts,action(L('Préparer un essai','Prepare a try'),L('Utilise l’outil « '+(item.name||'')+' » pour : ','Use the “'+(item.name||'')+'” tool to: ')));
      if(acts.childElementCount)add(card,acts);
      if(kind==='model'&&(item.examples||[]).length){var ex=E('div','examples');ex.style.marginTop='9px';item.examples.forEach(function(x){var row=E('div','example');add(row,E('code','',x),action(L('Utiliser','Use'),x));add(ex,row);});add(card,ex);}
      list.appendChild(card);
    });return list;
  }
  function push(id,label,panel){pages.push({id:id,label:label,panel:panel});}
  function overview(){
    var p=base(L('Votre espace, expliqué avec vos vrais accès','Your workspace, explained with your real access'),L('Ce guide est généré après les contrôles d’accès Open WebUI. Une rubrique apparaît uniquement si votre compte peut réellement l’utiliser.','This guide is generated after Open WebUI access checks. A section appears only when your account can actually use it.'));
    var stats=E('div','stats');
    [['models',L('modèles','models')],['prompts',L('prompts','prompts')],['skills',L('compétences','skills')],['knowledge',L('bases','knowledge bases')],['tools',L('outils','tools')],['channels',L('canaux','channels')],['features',L('fonctions','features')]].forEach(function(x){var s=E('div','stat');add(s,E('b','',data.counts[x[0]]||0),E('span','',x[1]));add(stats,s);});
    add(p,stats,E('div','notice','🔒 '+L('Sécurité : seuls les noms, descriptions courtes et identifiants nécessaires sont intégrés. Aucun corps de prompt, instruction de compétence, document, secret, jeton ou configuration privée n’est envoyé dans cette interface.','Security: only necessary names, short descriptions, and identifiers are embedded. No prompt body, skill instruction, document, secret, token, or private configuration is sent to this interface.')));
    return p;
  }
  function modelsPage(){
    var p=base(L('Choisir le bon modèle','Choose the right model'),L('Le sélecteur affiche les modèles autorisés pour votre rôle et vos groupes. Les modèles ci-dessous proviennent de votre catalogue réel.','The selector shows models allowed for your role and groups. The models below come from your real catalog.'));
    var t=E('div','tutorials');add(t,guide({open:true,icon:'M',title:L('Sélectionner un modèle','Select a model'),description:L('Avant d’envoyer votre premier message','Before sending your first message'),where:[L('Nouveau chat','New chat'),L('En haut du chat','Top of chat'),L('Nom du modèle','Model name')],steps:[L('Cliquez sur le nom du modèle en haut.','Click the model name at the top.'),L('Lisez sa description et ses capacités.','Read its description and capabilities.'),L('Choisissez celui adapté à votre tâche. Vous pouvez aussi taper /model suivi de son identifiant exact.','Choose the one suited to the task. You can also type /model followed by its exact ID.')],examples:[L('Aide-moi à choisir parmi mes modèles pour analyser un PDF.','Help me choose among my models to analyze a PDF.')] }));add(p,t,E('div','label',L('Vos modèles','Your models')),resources(data.resources.models,'model'));return p;
  }
  function chatPage(){
    var p=base(L('Maîtriser la zone de saisie','Master the chat input'),L('Quatre raccourcis permettent d’ajouter une ressource sans connaître son identifiant. Les résultats du sélecteur respectent vos droits.','Four shortcuts add a resource without knowing its ID. Picker results respect your permissions.'));
    var syntax=E('div','syntax');[['/','Prompt',L('Instruction réutilisable','Reusable instruction')],['$','Skill',L('Méthode spécialisée','Specialized method')],['#','Knowledge',L('Source documentaire','Document source')],['+','Tools',L('Fichiers et outils','Files and tools')]].forEach(function(x){var d=E('div');add(d,E('code','',x[0]),E('b','',x[1]),E('small','',x[2]));add(syntax,d);});add(p,syntax);
    var t=E('div','tutorials');add(t,guide({open:true,icon:'01',title:L('Écrire une demande solide','Write a strong request'),description:L('Contexte + objectif + format + contraintes','Context + objective + format + constraints'),where:[L('Nouveau chat','New chat'),L('Zone de saisie','Message input')],steps:[L('Expliquez le contexte utile.','Give useful context.'),L('Dites exactement le résultat attendu.','State the exact outcome.'),L('Imposez le format, la langue et les limites.','Set format, language, and limits.'),L('Demandez de signaler les incertitudes.','Ask it to flag uncertainty.')],examples:[L('Contexte : je prépare une réunion. Objectif : résumer le document joint. Format : tableau décision, responsable, échéance. Contraintes : français clair, 250 mots maximum, signale les informations incertaines.','Context: I am preparing a meeting. Objective: summarize the attached document. Format: table with decision, owner, due date. Constraints: clear English, maximum 250 words, flag uncertain information.')] }));add(p,t);return p;
  }
  function promptsPage(){
    var p=base(L('Utiliser vos prompts','Use your prompts'),L('Un prompt réutilisable démarre avec /. Son contenu privé reste masqué dans ce guide.','A reusable prompt starts with /. Its private body stays hidden from this guide.'));
    add(p,guide({open:true,icon:'/',title:L('Lancer un prompt','Run a prompt'),description:L('Utilisez le menu qui apparaît après /','Use the picker that appears after /'),where:[L('Chat','Chat'),L('Zone de saisie','Message input'),'/'],steps:[L('Tapez /.','Type /.'),L('Recherchez le nom ou la commande.','Search by name or command.'),L('Sélectionnez le prompt.','Select the prompt.'),L('Remplissez les variables demandées puis relisez avant envoi.','Fill requested variables, then review before sending.')],examples:[L('/ puis choisissez un prompt accessible','Type / then choose an accessible prompt')] }),E('div','label',L('Prompts accessibles','Accessible prompts')),resources(data.resources.prompts,'prompt'));return p;
  }
  function skillsPage(){
    var p=base(L('Ajouter une compétence','Add a skill'),L('Une compétence donne au modèle une méthode spécialisée. Elle n’exécute pas elle-même une action.','A skill gives the model a specialized method. It does not execute an action itself.'));
    add(p,guide({open:true,icon:'$',title:L('Sélectionner une compétence','Select a skill'),description:L('Par $ ou le menu Intégrations','With $ or the Integrations menu'),where:[L('Chat','Chat'),L('Zone de saisie','Message input'),'$'],steps:[L('Tapez $ pour ouvrir le sélecteur.','Type $ to open the picker.'),L('Choisissez une compétence visible pour votre compte.','Choose a skill visible to your account.'),L('Ajoutez votre objectif et vos contraintes.','Add your objective and constraints.'),L('Vérifiez qu’elle reste attachée au message avant envoi.','Check that it remains attached before sending.')],examples:[L('$ puis : prépare une analyse structurée de ce document.','$ then: prepare a structured analysis of this document.')] }),E('div','label',L('Compétences accessibles','Accessible skills')),resources(data.resources.skills,'skill'));return p;
  }
  function knowledgePage(){
    var canCreate=allowed('workspace','knowledge');
    var p=base(L('Créer et utiliser une base de connaissances','Create and use a knowledge base'),L('La connaissance ajoute vos documents comme contexte. Le sélecteur # ne montre que les bases accessibles.','Knowledge adds your documents as context. The # picker only shows accessible bases.'));
    var t=E('div','tutorials');
    if(canCreate)add(t,guide({open:true,icon:'K',title:L('Créer une base','Create a knowledge base'),description:L('Votre permission Workspace autorise la création','Your Workspace permission allows creation'),where:[L('Barre latérale','Sidebar'),L('Workspace','Workspace'),L('Knowledge','Knowledge')],steps:[L('Ouvrez Workspace puis Knowledge.','Open Workspace, then Knowledge.'),L('Cliquez sur + et donnez un nom et une description précis.','Click + and enter a precise name and description.'),L('Ajoutez les PDF, DOCX, Markdown ou autres fichiers autorisés.','Add permitted PDF, DOCX, Markdown, or other files.'),L('Attendez la fin du traitement puis vérifiez les documents.','Wait for processing, then verify the documents.'),L('Définissez les accès utilisateur/groupe; évitez Public pour les données internes.','Set user/group access; avoid Public for internal data.'),L('Dans Workspace > Models, attachez la base à un modèle si elle doit être toujours disponible.','In Workspace > Models, attach the base to a model if it should always be available.')],examples:[L('Crée une synthèse des politiques de voyage à partir de # puis cite les passages utilisés.','Summarize the travel policies from # and cite the passages used.')] }));
    add(t,guide({open:!canCreate,icon:'#',title:L('Utiliser une base dans un chat','Use a base in chat'),description:L('Ajout ponctuel avec #','Attach it for one conversation with #'),where:[L('Chat','Chat'),L('Zone de saisie','Message input'),'#'],steps:[L('Tapez #.','Type #.'),L('Choisissez la base ou le fichier autorisé.','Choose the permitted base or file.'),L('Posez une question ciblée et demandez les sources.','Ask a focused question and request sources.'),L('Si rien n’est trouvé, reformulez avec les termes du document.','If nothing is found, retry with terms used in the document.')],examples:[L('# Quelle est la procédure de validation ? Cite le document et la section.','# What is the approval procedure? Cite the document and section.')]}));add(p,t,E('div','label',L('Bases accessibles','Accessible knowledge bases')),resources(data.resources.knowledge,'knowledge'));return p;
  }
  function notesPage(){
    var p=base(L('Travailler avec Notes','Work with Notes'),L('Notes conserve un document persistant hors du chat. Le contenu d’une note attachée est injecté en entier, pas recherché par RAG.','Notes keeps a persistent document outside chat. An attached note is injected in full, not retrieved through RAG.'));
    var t=E('div','tutorials');
    add(t,guide({open:true,icon:'N',title:L('Créer et faire rédiger une note','Create and draft a note'),description:L('Éditeur riche avec assistant latéral','Rich editor with an AI sidebar'),where:[L('Barre latérale','Sidebar'),L('Notes','Notes'),'+'],steps:[L('Ouvrez Notes puis créez une note.','Open Notes and create a note.'),L('Saisissez un titre, puis utilisez Markdown ou le mode texte enrichi.','Enter a title, then use Markdown or Rich Text.'),L('Ouvrez le chat IA de la note; choisissez un modèle, un outil, une compétence, un fichier ou la recherche web si disponible.','Open the note AI chat; choose a model, tool, skill, file, or web search if available.'),L('Utilisez Insert sur une réponse pour l’ajouter à l’emplacement du curseur.','Use Insert on a response to place it at the cursor.')],examples:[L('Crée un plan de note intitulé « Compte rendu hebdomadaire » avec décisions, actions et échéances.','Create a note outline titled “Weekly review” with decisions, actions, and due dates.')]}));
    add(t,guide({icon:'AI',title:L('Mettre à jour une note avec l’IA','Update a note with AI'),description:L('Recherche, ajout et remplacement contrôlés','Controlled search, append, and replace'),where:[L('Notes','Notes'),L('Ouvrir une note','Open a note'),L('Chat IA','AI chat')],steps:[L('Surlignez un passage et demandez une réécriture sur place.','Highlight a passage and ask for an in-place rewrite.'),L('Pour modifier une autre note, utilisez un modèle avec appel de fonctions natif et les outils Notes autorisés.','To modify another note, use a model with native function calling and permitted Notes tools.'),L('Relisez toujours la modification et utilisez l’historique si nécessaire.','Always review the edit and use history if needed.')],examples:[L('Recherche mes notes Projet X et trouve le schéma de base de données.','Search my Project X notes and find the database schema.'),L('Ajoute à ma note « Tâches hebdomadaires » : relire la demande de fusion vendredi.','Add to my “Weekly tasks” note: review the pull request on Friday.')],note:L('Une note attachée manuellement au chat est en lecture seule pour l’IA. La modification nécessite le mode natif et les outils Notes, notamment write_note ou replace_note_content.','A note manually attached to chat is read-only for AI. Editing requires Native Mode and Notes tools such as write_note or replace_note_content.')}));
    add(t,guide({icon:'↗',title:L('Épingler ou joindre une note','Pin or attach a note'),description:L('Accès rapide depuis la barre latérale ou un chat','Quick access from the sidebar or a chat'),where:[L('Notes','Notes'),'⋯',L('Épingler à la barre latérale','Pin to Sidebar')],steps:[L('Dans la liste ou l’éditeur, ouvrez ⋯ puis choisissez Épingler à la barre latérale.','In the list or editor, open ⋯ and choose Pin to Sidebar.'),L('Le dossier Notes apparaît dans la barre latérale dès qu’une note est épinglée.','The Notes folder appears in the sidebar once a note is pinned.'),L('Pour l’utiliser dans un chat : + > Attach Notes, ou faites glisser la note épinglée dans le chat.','To use it in chat: + > Attach Notes, or drag the pinned note into the chat.')],examples:[L('Résume la note jointe en cinq actions classées par priorité.','Summarize the attached note into five prioritized actions.')]}));add(p,t);return p;
  }
  function webFilesPage(){
    var p=base(L('Ajouter des sources et des données récentes','Add sources and current data'),L('Les documents donnent le contexte du fichier; la recherche web sert aux informations qui peuvent avoir changé.','Documents provide file context; web search is for information that may have changed.'));
    var t=E('div','tutorials');
    if(enabled('file_upload'))add(t,guide({open:true,icon:'+',title:L('Joindre un fichier','Attach a file'),description:L('PDF, document, image ou données selon la configuration','PDF, document, image, or data as configured'),where:[L('Chat','Chat'),L('Zone de saisie','Message input'),'+',L('Upload Files','Upload Files')],steps:[L('Cliquez sur + puis choisissez le fichier autorisé.','Click + and choose a permitted file.'),L('Attendez que le fichier soit chargé.','Wait for upload to finish.'),L('Demandez une tâche précise et exigez des références aux pages/sections.','Ask for a precise task and require page/section references.')],examples:[L('Analyse le fichier joint : résume les risques, cite les pages et liste les questions ouvertes.','Analyze the attached file: summarize risks, cite pages, and list open questions.')]}));
    if(enabled('web_search'))add(t,guide({open:!enabled('file_upload'),icon:'W',title:L('Activer la recherche web','Enable web search'),description:L('Pour les nouvelles, règles, prix ou faits récents','For news, rules, prices, or recent facts'),where:[L('Chat','Chat'),L('Zone de saisie','Message input'),'+',L('Web Search','Web Search')],steps:[L('Activez Web Search avant l’envoi.','Enable Web Search before sending.'),L('Précisez la date, la zone géographique et les sources préférées.','Specify date, geography, and preferred sources.'),L('Demandez des liens et vérifiez leur date de publication.','Request links and check publication dates.')],examples:[L('Recherche les informations officielles les plus récentes sur ce sujet, indique la date de chaque source et distingue faits et hypothèses.','Find the latest official information on this topic, give each source date, and separate facts from assumptions.')],note:L('Sans recherche web activée, un modèle peut répondre avec des connaissances plus anciennes.','Without web search enabled, a model may answer from older knowledge.')}));
    add(p,t);return p;
  }
  function toolsPage(){
    var p=base(L('Utiliser vos outils','Use your tools'),L('Un outil exécute une action ou consulte une source externe. Une authentification peut être demandée au premier usage.','A tool performs an action or consults an external source. Authentication may be requested on first use.'));
    add(p,guide({open:true,icon:'T',title:L('Activer un outil pour le chat','Enable a tool for the chat'),description:L('L’outil doit être sélectionné avant la demande','Select the tool before the request'),where:[L('Chat','Chat'),L('Zone de saisie','Message input'),'+',L('Integrations / Tools','Integrations / Tools')],steps:[L('Ouvrez + puis la liste des outils ou intégrations.','Open +, then the tools or integrations list.'),L('Activez uniquement l’outil nécessaire.','Enable only the tool you need.'),L('Connectez votre compte si Open WebUI affiche une autorisation OAuth.','Connect your account if Open WebUI shows OAuth authorization.'),L('Décrivez l’action, sa cible et les limites; confirmez toute écriture sensible.','Describe the action, target, and limits; confirm sensitive writes.')],examples:[L('Avec l’outil sélectionné, recherche uniquement les éléments dont le statut est ouvert et présente-les sans les modifier.','With the selected tool, find only open items and present them without modifying anything.')],note:L('Ne collez jamais de clé API ou de jeton dans le chat. Utilisez uniquement le flux de connexion fourni par l’interface.','Never paste an API key or token into chat. Use only the connection flow provided by the interface.')}),E('div','label',L('Outils accessibles','Accessible tools')),resources(data.resources.tools,'tool'));return p;
  }
  function channelsPage(){
    var p=base(L('Collaborer dans Channels','Collaborate in Channels'),L('Channels est une chronologie partagée pour les échanges humains et IA. Vérifiez toujours si le canal est public, privé ou limité à un groupe.','Channels is a shared timeline for human and AI conversation. Always check whether the channel is public, private, or group-restricted.'));
    add(p,guide({open:true,icon:'@',title:L('Écrire, mentionner et répondre','Post, mention, and reply'),description:L('Messages, modèles, utilisateurs et fils','Messages, models, users, and threads'),where:[L('Barre latérale','Sidebar'),L('Channels','Channels'),L('Choisir un canal','Choose a channel')],steps:[L('Ouvrez un canal existant ou utilisez + si votre permission autorise la création.','Open an existing channel or use + if your permission allows creation.'),L('Tapez @nom_utilisateur pour notifier une personne.','Type @username to notify a person.'),L('Tapez @nom_modèle pour inviter un modèle à répondre; sa réponse apparaît normalement dans un fil.','Type @model-name to invite a model; its answer normally appears in a thread.'),L('Utilisez #nom-du-canal pour lier un autre canal.','Use #channel-name to link another channel.'),L('Répondez dans les fils, ajoutez des réactions, épinglez les messages utiles et joignez des fichiers si autorisé.','Reply in threads, react, pin useful messages, and attach files if allowed.')],examples:[L('@modèle Résume ce fil et propose trois prochaines actions.','@model Summarize this thread and propose three next actions.'),L('@utilisateur peux-tu vérifier la décision dans le fil ?','@username can you verify the decision in the thread?')],note:L('Tout membre d’un canal public peut lire son contenu. Ne partagez pas de données sensibles sans vérifier la visibilité et la politique interne.','Any member of a public channel can read its content. Do not share sensitive data without checking visibility and policy.')}));if(data.resources.channels&&data.resources.channels.length)add(p,E('div','label',L('Canaux visibles','Visible channels')),resources(data.resources.channels,'channel'));return p;
  }
  function organizePage(){
    var p=base(L('Organiser votre travail','Organize your work'),L('Voici uniquement les fonctions d’organisation activées pour votre compte.','These are only the organization features enabled for your account.'));
    var t=E('div','tutorials');
    if(enabled('folders'))add(t,guide({open:true,icon:'F',title:L('Classer les conversations','Organize conversations'),description:L('Dossiers dans la barre latérale','Folders in the sidebar'),where:[L('Barre latérale','Sidebar'),L('Folders','Folders')],steps:[L('Créez un dossier avec un nom lié au projet.','Create a folder named for the project.'),L('Glissez les conversations concernées dans ce dossier.','Drag relevant conversations into it.'),L('Renommez ou retirez un chat sans supprimer son contenu par erreur.','Rename or remove a chat without accidentally deleting its content.')]}));
    if(enabled('memories'))add(t,guide({open:!enabled('folders'),icon:'M',title:L('Contrôler la mémoire','Control memory'),description:L('Préférences persistantes, modifiables et supprimables','Persistent preferences you can edit or delete'),where:[L('Profil','Profile'),L('Settings','Settings'),L('Personalization / Memory','Personalization / Memory')],steps:[L('Consultez ce qui a été mémorisé.','Review what is remembered.'),L('Corrigez ou supprimez tout élément inexact ou sensible.','Correct or delete anything inaccurate or sensitive.'),L('Ne supposez pas qu’une mémoire remplace le contexte important de votre demande.','Do not assume memory replaces important context in your request.')],examples:[L('Mémorise que je préfère des réponses courtes en français.','Remember that I prefer concise answers in English.')]}));
    if(enabled('calendar'))add(t,guide({icon:'C',title:L('Utiliser le calendrier','Use the calendar'),description:L('Consulter et préparer les événements autorisés','Review and prepare permitted events'),where:[L('Barre latérale','Sidebar'),L('Calendar','Calendar')],steps:[L('Ouvrez Calendar et choisissez le calendrier accessible.','Open Calendar and choose an accessible calendar.'),L('Vérifiez fuseau horaire, participants et confidentialité avant toute création.','Check timezone, participants, and privacy before creating anything.')],examples:[L('Prépare un ordre du jour pour ma prochaine réunion, sans modifier le calendrier.','Prepare an agenda for my next meeting without modifying the calendar.')]}));
    if(enabled('automations'))add(t,guide({icon:'A',title:L('Créer une automatisation','Create an automation'),description:L('Tâche planifiée ou déclenchée','Scheduled or triggered task'),where:[L('Barre latérale','Sidebar'),L('Automations','Automations')],steps:[L('Définissez clairement le résultat et la fréquence.','Define the result and cadence clearly.'),L('Choisissez seulement les sources et outils nécessaires.','Choose only necessary sources and tools.'),L('Testez sur un périmètre non sensible puis contrôlez les premières exécutions.','Test on a non-sensitive scope, then review early runs.')],examples:[L('Chaque lundi, prépare un brouillon de synthèse des éléments ouverts; ne publie rien automatiquement.','Every Monday, draft a summary of open items; do not publish automatically.')]}));
    add(p,t);return p;
  }
  function mediaPage(){
    var p=base(L('Création, code et voix','Creation, code, and voice'),L('Ces fonctions apparaissent car elles sont activées pour votre compte.','These capabilities appear because they are enabled for your account.'));
    var t=E('div','tutorials');
    if(enabled('image_generation'))add(t,guide({open:true,icon:'I',title:L('Créer ou modifier une image','Create or edit an image'),description:L('Décrivez le sujet, le style et le format','Describe subject, style, and format'),where:[L('Chat','Chat'),'+',L('Image generation','Image generation')],steps:[L('Activez la génération d’images ou sélectionnez un modèle compatible.','Enable image generation or select a compatible model.'),L('Décrivez composition, ambiance, couleurs, format et texte exact.','Describe composition, mood, colors, format, and exact text.'),L('Pour une modification, joignez l’image et listez uniquement les changements.','For an edit, attach the image and list only the changes.')],examples:[L('Crée une illustration 16:9 minimaliste d’une équipe travaillant avec une IA, sans logo ni texte.','Create a minimalist 16:9 illustration of a team working with AI, with no logo or text.')]}));
    if(enabled('code_interpreter'))add(t,guide({open:!enabled('image_generation'),icon:'</>',title:L('Analyser des données avec le code','Analyze data with code'),description:L('Calculs, tableaux et graphiques contrôlés','Controlled calculations, tables, and charts'),where:[L('Chat','Chat'),'+',L('Code Interpreter','Code Interpreter')],steps:[L('Activez Code Interpreter et joignez vos données.','Enable Code Interpreter and attach your data.'),L('Définissez les colonnes, règles de calcul et résultat attendu.','Define columns, calculation rules, and expected result.'),L('Demandez les hypothèses, contrôles qualité et fichiers de sortie.','Request assumptions, quality checks, and output files.')],examples:[L('Analyse ce CSV, signale les valeurs manquantes, calcule les tendances et crée un graphique avec une table de contrôle.','Analyze this CSV, flag missing values, calculate trends, and create a chart plus a validation table.')]}));
    if(enabled('stt')||enabled('tts')||enabled('call'))add(t,guide({icon:'V',title:L('Utiliser la voix','Use voice'),description:L('Dicter, écouter ou lancer un appel selon vos droits','Dictate, listen, or call according to access'),where:[L('Chat','Chat'),L('Icône microphone / casque','Microphone / headset icon')],steps:[L('Utilisez le microphone pour transcrire votre message si STT est activé.','Use the microphone to transcribe your message if STT is enabled.'),L('Relisez toujours la transcription avant envoi.','Always review the transcription before sending.'),L('Utilisez Lire à voix haute sur une réponse si TTS est activé, ou Call pour une conversation vocale.','Use Read Aloud on a response if TTS is enabled, or Call for voice conversation.')]}));
    if(enabled('multiple_models'))add(t,guide({icon:'2×',title:L('Comparer plusieurs modèles','Compare multiple models'),description:L('Même question, réponses côte à côte','Same request, side-by-side answers'),where:[L('Sélecteur de modèle','Model selector'),L('Ajouter un modèle','Add model')],steps:[L('Sélectionnez plusieurs modèles avant l’envoi.','Select multiple models before sending.'),L('Utilisez exactement le même contexte.','Use exactly the same context.'),L('Comparez exactitude, sources, coût et vitesse.','Compare accuracy, sources, cost, and speed.')]}));add(p,t);return p;
  }
  function safetyPage(){
    var p=base(L('Vérifier, protéger, décider','Verify, protect, decide'),L('L’IA peut produire une réponse convaincante mais incorrecte. La responsabilité de l’usage final reste humaine.','AI can produce a convincing but incorrect answer. Final-use responsibility remains human.'));
    var grid=E('div','feature-grid');
    [[L('Vérifiez les faits','Verify facts'),L('Contrôlez dates, chiffres, liens et citations avant une décision.','Check dates, numbers, links, and citations before a decision.')],[L('Protégez les données','Protect data'),L('Respectez les règles internes, la confidentialité et la minimisation.','Follow internal rules, confidentiality, and data minimization.')],[L('Confirmez les actions','Confirm actions'),L('Relisez la cible et l’effet avant tout envoi, modification ou suppression.','Review target and effect before any send, change, or delete.')],[L('Gardez une trace','Keep a record'),L('Documentez les hypothèses et la validation humaine pour les usages importants.','Document assumptions and human review for important uses.')]].forEach(function(x){var c=E('div','feature');add(c,E('b','',x[0]),E('p','',x[1]));add(grid,c);});add(p,grid);
    var links=E('div','resource-actions');[['support_url',L('Support','Support')],['privacy_url',L('Confidentialité','Privacy')],['acceptable_use_url',L('Règles d’utilisation','Acceptable use')],['feedback_url',L('Donner un avis','Send feedback')]].forEach(function(x){if(data.links[x[0]]){var a=E('a','try',x[1]);a.href=data.links[x[0]];a.target='_blank';a.rel='noopener noreferrer';add(links,a);}});if(links.childElementCount)add(p,links);return p;
  }
  function build(){
    pages=[];push('welcome',L('Bienvenue','Welcome'),overview());
    if(data.ui.show_models)push('models',L('Modèles','Models'),modelsPage());
    push('chat',L('Bien demander','Ask well'),chatPage());
    if(data.ui.show_prompts&&(data.resources.prompts||[]).length)push('prompts',L('Prompts','Prompts'),promptsPage());
    if(data.ui.show_skills&&(data.resources.skills||[]).length)push('skills',L('Compétences','Skills'),skillsPage());
    if(data.ui.show_knowledge&&((data.resources.knowledge||[]).length||allowed('workspace','knowledge')))push('knowledge',L('Connaissances','Knowledge'),knowledgePage());
    if(enabled('notes'))push('notes',L('Notes','Notes'),notesPage());
    if(enabled('web_search')||enabled('file_upload'))push('sources',L('Sources récentes','Current sources'),webFilesPage());
    if(data.ui.show_tools&&(data.resources.tools||[]).length)push('tools',L('Outils','Tools'),toolsPage());
    if(enabled('channels'))push('channels',L('Canaux','Channels'),channelsPage());
    if(enabled('folders')||enabled('memories')||enabled('calendar')||enabled('automations'))push('organize',L('Organiser','Organize'),organizePage());
    if(enabled('image_generation')||enabled('code_interpreter')||enabled('stt')||enabled('tts')||enabled('call')||enabled('multiple_models'))push('media',L('Créer et analyser','Create and analyze'),mediaPage());
    push('safety',L('Bonnes pratiques','Best practices'),safetyPage());
  }
  function render(){
    root.lang=lang;document.getElementById('product').textContent=data.brand.product||'Open WebUI';document.getElementById('organization').textContent=data.brand.organization||L('Assistant IA privé','Private AI assistant');
    document.getElementById('kicker').textContent=L('Guide interactif personnalisé','Personalized interactive guide');document.getElementById('welcome').textContent=L('Bienvenue, ','Welcome, ')+(data.user.name||'');
    document.getElementById('introText').textContent=L('Découvrez où trouver chaque fonction, comment l’utiliser et des exemples prêts à essayer.','See where every feature lives, how to use it, and ready-to-try examples.');
    document.getElementById('role').textContent=L('Rôle : ','Role: ')+(data.user.role_label[lang]||data.user.role);document.getElementById('verified').textContent=L('Accès vérifiés','Access verified');
    build();current=Math.min(current,pages.length-1);var rail=document.getElementById('rail'),panels=document.getElementById('panels');rail.replaceChildren();panels.replaceChildren();
    pages.forEach(function(pg,i){var b=E('button','');b.type='button';b.dataset.step=String(i);add(b,E('span','num',String(i+1)),E('span','navtext',pg.label));b.addEventListener('click',function(){show(i,true);});rail.appendChild(b);pg.panel.dataset.panel=String(i);panels.appendChild(pg.panel);});
    document.getElementById('back').textContent=L('Précédent','Previous');document.getElementById('fresh').textContent=L('Accès vérifiés : ','Access checked: ')+data.generated_at_label[lang];show(current,false);requestAnimationFrame(reportHeight);
  }
  function show(index,focus){
    current=Math.max(0,Math.min(pages.length-1,index));document.querySelectorAll('[data-panel]').forEach(function(p,i){p.hidden=i!==current;});document.querySelectorAll('[data-step]').forEach(function(b,i){b.classList.toggle('done',i<current);if(i===current)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});
    var back=document.getElementById('back'),next=document.getElementById('next');back.disabled=current===0;next.textContent=current===pages.length-1?L('Commencer','Start'):L('Suivant','Next');document.getElementById('bar').style.width=String(((current+1)/pages.length)*100)+'%';document.getElementById('progress').textContent=L(String(current+1)+' sur '+String(pages.length),String(current+1)+' of '+String(pages.length));
    if(focus){var h=document.querySelector('[data-panel="'+String(current)+'"] h2');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}}requestAnimationFrame(reportHeight);
  }
  document.getElementById('language').value=lang;document.getElementById('language').addEventListener('change',function(e){lang=e.target.value==='fr'?'fr':'en';render();});
  document.getElementById('theme').addEventListener('change',function(e){var v=e.target.value;if(v==='auto')delete root.dataset.theme;else root.dataset.theme=v;requestAnimationFrame(reportHeight);});
  document.getElementById('back').addEventListener('click',function(){show(current-1,true);});document.getElementById('next').addEventListener('click',function(){if(current<pages.length-1)show(current+1,true);else sendPrompt(L('Aide-moi à choisir le bon modèle et à formuler ma première demande.','Help me choose the right model and write my first request.'));});
  document.addEventListener('keydown',function(e){if(e.altKey&&e.key==='ArrowRight'){e.preventDefault();show(current+1,true);}if(e.altKey&&e.key==='ArrowLeft'){e.preventDefault();show(current-1,true);}});
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
        enabled: bool = Field(True, description="Active le moteur d’onboarding EveGPT.")
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
        welcome_title_fr: str = Field("Bienvenue sur EveGPT", description="Titre français du chat d’accueil.")
        welcome_title_en: str = Field("Welcome to EveGPT", description="Titre anglais du chat d’accueil.")
        preferred_welcome_model_id: str = Field(
            "", description="Modèle préféré. Il est utilisé seulement si l’utilisateur y a réellement accès."
        )
        default_group_id: str = Field(
            "", description="Groupe attribué aux nouveaux comptes avant le calcul de leurs accès. Vide pour désactiver."
        )
        default_language: str = Field("fr", pattern="^(fr|en)$", description="Langue affichée lors de l’ouverture: fr ou en.")
        product_name: str = Field("EveGPT", max_length=60, description="Nom du service.")
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
        support_url: str = Field("", description="URL HTTPS du support EveGPT.")
        privacy_url: str = Field("", description="URL HTTPS de la politique de confidentialité.")
        acceptable_use_url: str = Field("", description="URL HTTPS des règles d’utilisation.")
        feedback_url: str = Field("", description="URL HTTPS du formulaire de retour.")
        show_models: bool = Field(True, description="Afficher les modèles accessibles.")
        show_prompts: bool = Field(True, description="Afficher les prompts accessibles, sans leur contenu.")
        show_tools: bool = Field(True, description="Afficher les outils et serveurs d’outils accessibles.")
        show_skills: bool = Field(True, description="Afficher les compétences accessibles, sans leurs instructions.")
        show_knowledge: bool = Field(True, description="Afficher les bases accessibles, sans leurs documents.")
        show_disabled_features: bool = Field(
            False, description="Afficher également les fonctions explicitement indisponibles."
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
            "title": self._plain(title, 120) or "EveGPT onboarding",
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
            "models": [self._model_public(x) for x in models[:max_items]],
            "prompts": [self._prompt_public(x) for x in prompts[:max_items]],
            "tools": [self._tool_public(x) for x in tools[:max_items]],
            "skills": [self._skill_public(x) for x in skills[:max_items]],
            "knowledge": [self._knowledge_public(x) for x in knowledge[:max_items]],
            "channels": [self._channel_public(x) for x in channels[:max_items]],
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
            "user": {"name": self._plain(getattr(user, "name", "") or "EveGPT", 100), "role": role, "role_label": role_labels},
            "brand": {
                "product": self._plain(self.valves.product_name, 60) or "EveGPT",
                "organization": self._plain(self.valves.organization_name, 120),
                "primary": self._safe_color(self.valves.primary_color, "#0A3D67"),
                "secondary": self._safe_color(self.valves.secondary_color, "#00B3C3"),
                "accent": self._safe_color(self.valves.accent_color, "#FFBC00"),
                "warm": self._safe_color(self.valves.warm_color, "#FF8500"),
            },
            "ui": {
                "default_language": self.valves.default_language,
                "font": "Arial, Helvetica, sans-serif" if self.valves.font_family == "Arial" else "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                "show_models": bool(self.valves.show_models),
                "show_prompts": bool(self.valves.show_prompts),
                "show_tools": bool(self.valves.show_tools),
                "show_skills": bool(self.valves.show_skills),
                "show_knowledge": bool(self.valves.show_knowledge),
            },
            "links": {
                "support_url": self._safe_http_url(self.valves.support_url),
                "privacy_url": self._safe_http_url(self.valves.privacy_url),
                "acceptable_use_url": self._safe_http_url(self.valves.acceptable_use_url),
                "feedback_url": self._safe_http_url(self.valves.feedback_url),
            },
            "selected_model_id": selected_model_id,
            "counts": {
                "models": len(models), "prompts": len(prompts), "tools": len(tools),
                "skills": len(skills), "knowledge": len(knowledge),
                "features": sum(1 for x in features if x["enabled"]), "channels": len(channels),
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