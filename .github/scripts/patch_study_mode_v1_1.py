from pathlib import Path
import re

p=Path('functions/filters/study-mode/study_mode.py')
s=p.read_text(encoding='utf-8')

def rep(old,new,name):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f'{name}: {n} matches')
    s=s.replace(old,new,1)

def reg(pattern,new,name):
    global s
    s,n=re.subn(pattern,new,s,count=1,flags=re.S)
    if n!=1: raise SystemExit(f'{name}: {n} matches')

rep('author: CallSohail\nversion: 1.0.0','author: Muhammad Sohail\nversion: 1.1.0','metadata')
rep('from typing import Literal, Optional, Any\nimport json\n','from typing import Literal, Optional, Any\nimport ast\nimport json\n','imports')

anchor='''        max_prompt_chars: int = Field(\n            default=12000,\n            ge=3000,\n            le=24000,\n            description="Safety cap for the injected Study Mode instruction.",\n        )\n'''
rep(anchor,anchor+'''        system_prompt_integration: Literal["merge", "separate"] = Field(\n            default="merge",\n            description="Merge is safest across providers; Separate keeps Study Mode as another scoped system message.",\n            json_schema_extra={"input":{"type":"select","options":[\n                {"value":"merge","label":"Merge with existing system prompt"},\n                {"value":"separate","label":"Separate scoped system message"},\n            ]}},\n        )\n''','system valve')

anchor='''        quiz_ready_message: str = Field(\n            default="Quiz ready",\n            min_length=1,\n            max_length=120,\n            description="Completion status used when the interactive quiz is ready.",\n        )\n'''
rep(anchor,anchor+'''        quiz_schema_tolerance: Literal["compatible", "strict"] = Field(\n            default="compatible",\n            description="Compatible repairs common local-model quiz JSON variations after validation; Strict requires the documented schema.",\n            json_schema_extra={"input":{"type":"select","options":[\n                {"value":"compatible","label":"Compatible"},{"value":"strict","label":"Strict schema"},\n            ]}},\n        )\n        multilingual_quiz_detection: bool = Field(default=True, description="Recognize common quiz requests in several languages.")\n        quiz_mathjax: bool = Field(default=False, description="Opt in to pinned MathJax 3.2.2 rendering for LaTeX. Gracefully falls back to raw LaTeX if blocked by CSP/network.")\n        quiz_keyboard_shortcuts: bool = Field(default=True, description="A-E/1-5 answer, Left/Right navigate, Enter continue, H hint, F fullscreen.")\n        quiz_fullscreen_button: bool = Field(default=True, description="Show a fullscreen quiz control when browser/iframe permissions allow it.")\n        quiz_export_html: bool = Field(default=True, description="Show a standalone HTML download control inside the Rich UI iframe.")\n''','quiz valves')

rep('''You are in Study Mode. Your goal is to help the learner build durable understanding, not merely produce an answer.\n\nCORE TEACHING BEHAVIOR''','''You are in Study Mode. Your goal is to help the learner build durable understanding, not merely produce an answer.\n\nSCOPE AND COMPATIBILITY\nTreat Study Mode as a task-specific overlay. Preserve unrelated existing system/model instructions. Apply quiz JSON transport rules only on turns that actually generate an interactive multiple-choice quiz; never force quiz formatting on ordinary tutoring or other tasks. Higher-priority platform safety and access rules remain in force.\n\nCORE TEACHING BEHAVIOR''','scope')

rep('''Rules for the quiz specification:\n- Valid strict JSON only inside the hidden block. No Markdown fences inside it.\n''','''Rules for the quiz specification:\n- Valid strict JSON only inside the hidden block. No Markdown fences inside it.\n- For smaller/local models, prioritize a valid complete schema over extra prose; keep explanations and hints concise if needed.\n- If LaTeX is used, JSON-escape backslashes (for example `\\\\(` in JSON so decoded text contains `\\(`).\n''','quiz prompt hardening')

new_detect=r'''    @staticmethod
    def _looks_like_quiz_request(messages: list[dict], user_valves: "Filter.UserValves", multilingual: bool = True) -> bool:
        if user_valves.style == "quiz": return True
        phrases=(
            "quiz","test me","mcq","multiple choice","practice questions","exam questions","mock test","mock exam",
            "qcm","fais moi un quiz","interroge moi","testez moi","choix multiple","questions d examen","examen blanc",
            "cuestionario","hazme un quiz","ponme a prueba","opcion multiple","preguntas de examen",
            "teste mich","frag mich ab","prufungsfragen","probeprufung",
            "mettimi alla prova","interrogami","scelta multipla","domande d esame",
            "teste me","multipla escolha","perguntas de exame","simulado",
            "overhoor me","meerkeuze","oefenvragen","examenvragen",
            "کوئز","ٹیسٹ","مجھے ٹیسٹ کرو","اختبار","اختيار من متعدد",
        ) if multilingual else ("quiz","test me","mcq","multiple choice","practice questions","exam questions","mock test","mock exam")
        table=str.maketrans({"é":"e","è":"e","ê":"e","ë":"e","à":"a","á":"a","â":"a","ä":"a","ã":"a","ç":"c","í":"i","ï":"i","ó":"o","ö":"o","õ":"o","ú":"u","ü":"u","ñ":"n","ß":"ss","’":" ","'":" ","-":" "})
        checked=0
        for m in reversed(messages):
            if not isinstance(m,dict) or m.get("role")!="user": continue
            c=m.get("content","")
            if isinstance(c,list): c=" ".join(str(x.get("text") or x.get("content") or "") for x in c if isinstance(x,dict))
            t=re.sub(r"\s+"," ",str(c).casefold().translate(table)).strip()
            if any(x in t for x in phrases): return True
            checked+=1
            if checked>=4: break
        return False
'''
reg(r'    @staticmethod\n    def _looks_like_quiz_request\(.*?\n        return False\n',new_detect,'quiz detection')
s=s.replace('self._looks_like_quiz_request(messages, user_valves)','self._looks_like_quiz_request(messages, user_valves, self.valves.multilingual_quiz_detection)')

new_parser=r'''    @staticmethod
    def _balanced_json_object(text: str) -> Optional[str]:
        start=text.find("{") if isinstance(text,str) else -1
        while start>=0:
            depth=0; quote=None; esc=False
            for i in range(start,len(text)):
                ch=text[i]
                if quote:
                    if esc: esc=False
                    elif ch=="\\": esc=True
                    elif ch==quote: quote=None
                    continue
                if ch in ('"',"'"): quote=ch
                elif ch=="{": depth+=1
                elif ch=="}":
                    depth-=1
                    if depth==0: return text[start:i+1]
            start=text.find("{",start+1)
        return None

    def _load_quiz_object(self, raw: str) -> Optional[dict]:
        raw=re.sub(r"^```(?:json)?\\s*|\\s*```$","",raw.strip(),flags=re.I)
        tries=[raw]
        if self.valves.quiz_schema_tolerance=="compatible":
            fixed=raw.replace("“",'"').replace("”",'"')
            fixed=re.sub(r'\\\\(?!["\\\\/bfnrtu])',r'\\\\\\\\',fixed)
            fixed=re.sub(r",\\s*([}\\]])",r"\\1",fixed)
            if fixed!=raw: tries.append(fixed)
        for x in tries:
            try:
                v=json.loads(x)
                if isinstance(v,dict): return v
            except Exception: pass
        if self.valves.quiz_schema_tolerance=="compatible":
            for x in tries:
                try:
                    v=ast.literal_eval(x)
                    if isinstance(v,dict): return v
                except Exception: pass
        return None

    @staticmethod
    def _answer_id(value: Any, options: list[dict]) -> Optional[str]:
        if isinstance(value,bool): return None
        if isinstance(value,int):
            if value==0 and options: return options[0]["id"]
            return options[value-1]["id"] if 1<=value<=len(options) else None
        if not isinstance(value,str): return None
        v=value.strip()
        for o in options:
            if o["id"].casefold()==v.casefold() or o["text"].casefold()==v.casefold(): return o["id"]
        m=re.match(r"^([A-Ea-e1-5])(?:[\\).:\\s]|$)",v)
        if m:
            t=m.group(1)
            if t.isdigit():
                i=int(t)-1
                return options[i]["id"] if 0<=i<len(options) else None
            for o in options:
                if o["id"].casefold()==t.casefold(): return o["id"]
        return None

    def _extract_quiz(self, content: str) -> Optional[dict]:
        if not isinstance(content,str): return None
        m=self._QUIZ_RE.search(content)
        raw=m.group(1).strip() if m else (self._balanced_json_object(content) if self.valves.quiz_schema_tolerance=="compatible" else None)
        if not raw: return None
        quiz=self._load_quiz_object(raw)
        if not isinstance(quiz,dict): return None
        qs=quiz.get("questions")
        if self.valves.quiz_schema_tolerance=="compatible" and not isinstance(qs,list): qs=quiz.get("items")
        if not isinstance(qs,list) or not qs or len(qs)>self.valves.max_quiz_questions: return None
        title=quiz.get("title"); topic=quiz.get("topic"); diff=quiz.get("difficulty")
        if self.valves.quiz_schema_tolerance=="strict":
            if not isinstance(title,str) or not title.strip() or not isinstance(topic,str) or not topic.strip() or not isinstance(diff,str): return None
        else:
            title=title if isinstance(title,str) and title.strip() else "Study quiz"
            topic=topic if isinstance(topic,str) and topic.strip() else title
            diff=diff if isinstance(diff,str) and diff.strip() else "adaptive"
        out=[]
        for i,q in enumerate(qs,1):
            if not isinstance(q,dict): return None
            qt=q.get("question")
            if self.valves.quiz_schema_tolerance=="compatible" and not isinstance(qt,str): qt=q.get("text") or q.get("prompt")
            opts=q.get("options")
            if not isinstance(qt,str) or not qt.strip() or not isinstance(opts,list) or not 2<=len(opts)<=5: return None
            no=[]; ids=set()
            for j,o in enumerate(opts):
                if isinstance(o,str) and self.valves.quiz_schema_tolerance=="compatible": oid=chr(65+j); ot=o
                elif isinstance(o,dict):
                    oid=o.get("id"); ot=o.get("text")
                    if self.valves.quiz_schema_tolerance=="compatible": oid=oid if isinstance(oid,str) and oid.strip() else (o.get("label") or chr(65+j)); ot=ot if isinstance(ot,str) else (o.get("content") or o.get("value"))
                else: return None
                if not isinstance(oid,str) or not oid.strip() or not isinstance(ot,str) or not ot.strip(): return None
                oid=oid.strip()[:8]
                if oid in ids: oid=chr(65+j)
                if oid in ids: return None
                ids.add(oid); no.append({"id":oid,"text":ot.strip()[:1000]})
            cv=q.get("correct")
            if self.valves.quiz_schema_tolerance=="compatible" and cv is None:
                for k in ("answer","correct_answer","correctAnswer","solution"):
                    if k in q: cv=q[k]; break
            correct=self._answer_id(cv,no)
            if correct is None: return None
            ex=q.get("explanation"); hint=q.get("hint")
            if self.valves.quiz_schema_tolerance=="strict" and (not isinstance(ex,str) or not ex.strip()): return None
            if not isinstance(ex,str) or not ex.strip(): ex=f"The correct answer is {correct}: "+next(o["text"] for o in no if o["id"]==correct)+"."
            if not isinstance(hint,str): hint=""
            out.append({"id":str(q.get("id") or f"q{i}")[:64],"question":qt.strip()[:3000],"options":no,"correct":correct,"explanation":ex.strip()[:3000],"hint":hint.strip()[:1500] or "Focus on the key concept and eliminate options that do not fit its definition or behavior."})
        return {"title":str(title).strip()[:200],"topic":str(topic).strip()[:200],"difficulty":str(diff).strip()[:50],"questions":out}
'''
reg(r'    def _extract_quiz\(self, content: str\) -> Optional\[dict\]:\n.*?\n    @staticmethod\n    def _safe_json_for_script',new_parser+'\n    @staticmethod\n    def _safe_json_for_script','parser')

rep('''            .replace(">", "\\\\u003e")\n''','''            .replace(">", "\\\\u003e")\n            .replace("\\u2028", "\\\\u2028")\n            .replace("\\u2029", "\\\\u2029")\n''','unicode escaping')
s=s.replace("box.innerHTML='';","box.replaceChildren();").replace("el('feedback').innerHTML='';","el('feedback').replaceChildren();").replace("layer.innerHTML='';","layer.replaceChildren();").replace("el('mistakes').innerHTML='';","el('mistakes').replaceChildren();")

rep('''</script>\n</body>\n</html>"""\n\n    @staticmethod\n    def _stream_key''','''</script>\n<script>\n{self._quiz_feature_addon_js()}\n</script>\n</body>\n</html>"""\n\n    def _quiz_feature_addon_js(self) -> str:\n        cfg=self._safe_json_for_script({"keyboard":bool(self.valves.quiz_keyboard_shortcuts),"fullscreen":bool(self.valves.quiz_fullscreen_button),"exportHtml":bool(self.valves.quiz_export_html),"mathjax":bool(self.valves.quiz_mathjax)})\n        js=r"""\n(function(){\nconst cfg=__CFG__,controls=document.querySelector('.controls');if(!controls)return;const NS='http://www.w3.org/2000/svg';\nfunction ib(id,label,tip,paths){const b=document.createElement('button');b.type='button';b.className='iconbtn';b.id=id;b.setAttribute('aria-label',label);b.dataset.tip=tip;const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox','0 0 24 24');paths.forEach(d=>{const p=document.createElementNS(NS,'path');p.setAttribute('d',d);svg.appendChild(p)});b.appendChild(svg);controls.appendChild(b);return b}\nlet fs=null,ex=null,snap='';\nif(cfg.fullscreen){fs=ib('fullscreenBtn','Toggle fullscreen','Fullscreen (F)',['M8 3H3v5','M16 3h5v5','M8 21H3v-5','M16 21h5v-5']);fs.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen()}catch(_){}};document.addEventListener('fullscreenchange',()=>{positionTooltip(fs);reportHeight()})}\nif(cfg.exportHtml){ex=ib('exportBtn','Export quiz as HTML','Export HTML',['M12 3v12','m7 10 5 5 5-5','M5 21h14']);setTimeout(()=>{snap='<!doctype html>\\n'+document.documentElement.outerHTML},0);ex.onclick=()=>{try{const blob=new Blob([snap||('<!doctype html>\\n'+document.documentElement.outerHTML)],{type:'text/html;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a'),name=(quiz.title||quiz.topic||'study-quiz').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,60)||'study-quiz';a.href=url;a.download=name+'.html';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(_){}}}\nfunction math(){if(!cfg.mathjax||!window.MathJax||typeof window.MathJax.typesetPromise!=='function')return;try{if(typeof window.MathJax.typesetClear==='function')window.MathJax.typesetClear([el('card')]);window.MathJax.typesetPromise([el('card')]).then(()=>reportHeight()).catch(()=>{})}catch(_){}}\nif(cfg.mathjax){try{window.MathJax=window.MathJax||{tex:{inlineMath:[['\\\\(','\\\\)'],['$','$']],displayMath:[['\\\\[','\\\\]'],['$$','$$']]}};const x=document.createElement('script');x.src='https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js';x.defer=true;x.referrerPolicy='no-referrer';x.onload=math;x.onerror=()=>{};document.head.appendChild(x);const r=render;render=function(){r();setTimeout(math,0)};const a=applyAnswer;applyAnswer=function(){a();setTimeout(math,0)};const f=finish;finish=function(){f();setTimeout(math,0)}}catch(_){}}\nif(cfg.keyboard)document.addEventListener('keydown',e=>{if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey)return;const t=e.target;if(t&&['INPUT','TEXTAREA','SELECT'].includes(t.tagName))return;const q=quiz.questions[index];if(!q)return;let n=-1;if(/^[1-5]$/.test(e.key))n=Number(e.key)-1;else if(/^[a-eA-E]$/.test(e.key))n=e.key.toUpperCase().charCodeAt(0)-65;if(n>=0&&n<q.options.length&&!answers[index]){e.preventDefault();choose(q.options[n].id);return}if(e.key==='ArrowLeft'){e.preventDefault();go(-1)}else if(e.key==='ArrowRight'){e.preventDefault();go(1)}else if((e.key==='h'||e.key==='H')&&SHOW_HINT){e.preventDefault();el('hint').classList.toggle('show');reportHeight()}else if((e.key==='f'||e.key==='F')&&fs){e.preventDefault();fs.click()}else if(e.key==='Enter'&&answers[index]){e.preventDefault();index===quiz.questions.length-1?finish():go(1)}},true);\n[fs,ex].filter(Boolean).forEach(b=>{b.addEventListener('mouseenter',()=>positionTooltip(b));b.addEventListener('focus',()=>positionTooltip(b))});positionTooltips();reportHeight();\n})();\n"""\n        return js.replace("__CFG__",cfg,1)\n\n    @staticmethod\n    def _stream_key''','addon')

reg(r'    @staticmethod\n    def _stream_key\(metadata: Optional\[dict\]\) -> Optional\[str\]:\n.*?\n        return key if isinstance\(key, str\) and key else None','''    @staticmethod\n    def _stream_key(metadata: Optional[dict]) -> Optional[str]:\n        if not isinstance(metadata,dict): return None\n        key=metadata.get("study_mode_quiz_stream_key")\n        if isinstance(key,str) and key: return key\n        parts=[f"{k}={metadata[k]}" for k in ("chat_id","message_id","session_id") if metadata.get(k) is not None]\n        return "study-mode:"+"|".join(parts) if parts else None''','stream key')
rep('''            key = self._stream_key(__metadata__)\n            if not key:\n                key = uuid.uuid4().hex\n                __metadata__["study_mode_quiz_stream_key"] = key\n            self._quiz_streams[key] = {''','''            key = self._stream_key(__metadata__)\n            if not key:\n                key = uuid.uuid4().hex\n            __metadata__["study_mode_quiz_stream_key"] = key\n            self._quiz_streams[key] = {''','metadata')

new_inject=r'''    def _inject_prompt(self, body: dict, prompt: str) -> None:
        messages=body.setdefault("messages",[])
        if not isinstance(messages,list): body["messages"]=[];messages=body["messages"]
        if any(isinstance(m,dict) and m.get("role")=="system" and isinstance(m.get("content"),str) and self._MARKER in m["content"] for m in messages): return
        if self.valves.system_prompt_integration=="separate":
            i=0
            while i<len(messages) and isinstance(messages[i],dict) and messages[i].get("role")=="system": i+=1
            messages.insert(i,{"role":"system","content":prompt});return
        for m in messages:
            if isinstance(m,dict) and m.get("role")=="system" and isinstance(m.get("content"),str): m["content"]=f"{m['content'].rstrip()}\n\n{prompt}";return
        messages.insert(0,{"role":"system","content":prompt})
'''
reg(r'    def _inject_prompt\(self, body: dict, prompt: str\) -> None:\n.*?\n    async def inlet',new_inject+'\n    async def inlet','inject')
rep('''        finally:\n            if key:\n                self._quiz_streams.pop(key, None)\n''','''        finally:\n            await self._finish_quiz_progress_status(__event_emitter__, __metadata__, description="Study response ready")\n            if key:\n                self._quiz_streams.pop(key, None)\n''','status cleanup')

p.write_text(s,encoding='utf-8')
print('patched Study Mode 1.1.0')
