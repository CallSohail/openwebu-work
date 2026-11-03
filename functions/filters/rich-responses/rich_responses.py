"""
title: Rich Responses
author: Muhammad Sohail
author_url: https://github.com/CallSohail/openwebu-work
version: 1.0.0
license: MIT
required_open_webui_version: 0.12.0
description: ChatGPT-style streaming answers for any model. Detects what the user asks (explain, summarize a document, compare, how-to, code, plan, quick fact, chat) and steers the model to stream well-structured Markdown with icons, callouts, tables and diagrams; adds icons to bare headings live while the answer streams. Pairs with the Generative UI tool for interactive cards.
"""

import re
import time
import unicodedata
from typing import Literal, Optional

from pydantic import BaseModel, Field

MARKER = "[RICH_RESPONSES_V1]"

# ----------------------------------------------------------------------------------------------------------------------
# Intent detection. Cheap keyword heuristics in English, French, Spanish and German; the longest/most specific signal
# wins. It only chooses a response *shape*; the model still decides the content.
# ----------------------------------------------------------------------------------------------------------------------
INTENT_PATTERNS: list[tuple[str, str]] = [
    ("creative", r"\b(write|compose|draft|r[ée]dige|[ée]cris|escribe|schreib)\b.{0,40}\b(poem|po[èe]me|poema|gedicht|story|histoire|cuento|song|chanson|letter|lettre|carta|brief|email|e-mail|mail|tweet|post|speech|discours|essay|essai|cover letter)\b"),
    ("summarize", r"\b(summari[sz]e|summary|sum up|tl;?dr|key points|r[ée]sum[ée]r?|synth[èe]se|resume|resumen|resumir|zusammenfass\w*)\b"),
    ("compare", r"\b(compare|comparison|versus|vs\.?|difference between|differences|which is better|pros and cons|comparer|comparaison|diff[ée]rence entre|comparar|diferencia|vergleich\w*|unterschied)\b"),
    ("troubleshoot", r"\b(error|exception|traceback|not working|doesn'?t work|fails?|failing|broken|bug|crash\w*|erreur|ne marche pas|fonctionne pas|fehler|funktioniert nicht|no funciona)\b"),
    ("howto", r"\b(how (do|can|to|should) i|how to|step[- ]by[- ]step|guide|tutorial|install|set ?up|configure|deploy|comment (faire|installer|configurer)|c[oó]mo (hacer|instalar|configurar)|wie (kann|installiere))\b"),
    ("plan", r"\b(plan|itinerary|schedule|roadmap|timeline|agenda|programme|planning|itin[ée]raire|calendrier|plan de|horario|zeitplan)\b"),
    ("code", r"(```|\b(code|function|script|regex|sql|python|javascript|typescript|bash|class|api endpoint|refactor|snippet|fonction|funci[oó]n)\b)"),
    ("recommend", r"\b(recommend|suggest|best \w+ for|top \d+|ideas? for|recommande|sugg[èe]re|meilleur\w* \w+ pour|recomienda|empfiehl\w*)\b"),
    ("explain", r"\b(explain|what is|what are|what's|why|how does|how do .* work|tell me about|describe|overview|explique|expliquer|qu'?est-ce que|pourquoi|comment fonctionne|explica|qu[eé] es|por qu[eé]|erkl[äa]r\w*|was ist|warum)\b"),
]
CHAT_RE = re.compile(r"^\s*(hi|hello|hey|yo|thanks?|thank you|merci|bonjour|salut|hola|gracias|hallo|danke|ok|okay|cool|great|nice|lol|good (morning|night|evening))\b[\s!.?]*$", re.I)
BRIEF_RE = re.compile(r"\b(brief(ly)?|short(ly)?|quick(ly)?|concise|in (one|a) (line|sentence)|one[- ]liner|tl;?dr|no details?|without details?|en bref|court|bri[èe]vement|sans d[ée]tails?|breve|corto|kurz)\b", re.I)
DEEP_RE = re.compile(r"\b(in detail|detailed|deep dive|in depth|thorough(ly)?|comprehensive|everything about|en d[ée]tail|d[ée]taill[ée]|a fondo|detallad[oa]|ausf[üu]hrlich)\b", re.I)

SHAPES: dict[str, str] = {
    "chat": "This is casual conversation. Reply naturally in one to three sentences. No headings, no lists, at most one emoji.",
    "quick": "This is a quick factual question. Give the answer in the first sentence with the key value in **bold**. "
             "One or two sentences, optionally one short supporting line. No headings, no lists.",
    "explain": "Shape: an explanation.\n"
               "1. Start with a one or two sentence direct answer (no heading above it).\n"
               "2. `## 🧭 Overview` or `## 🧩 How it works` with short paragraphs or emoji bullets that **bold** each key idea.\n"
               "3. When there is a process, flow or architecture, add a small ```mermaid diagram (flowchart TD/LR, at most 8 nodes).\n"
               "4. A concrete example or analogy (`## 🧪 Example`).\n"
               "5. One `> [!TIP]` or `> [!NOTE]` callout if it adds real value.\n"
               "6. Finish with `## 🎯 In short` and two or three bullets.",
    "summarize": "Shape: a summary of the provided material (document, text or conversation).\n"
                 "1. `> [!NOTE]` callout with a two or three sentence **TL;DR**.\n"
                 "2. `## 💡 Key points`: four to eight emoji bullets, each starting with a **bold label**.\n"
                 "3. `## 📑 Section by section` with `###` subheadings when the source has clear parts (skip for short sources).\n"
                 "4. A Markdown table for any numbers, dates, people or options found in the source.\n"
                 "5. `## ✅ Takeaways / action items` and, if relevant, `## ⚠️ Caveats or open questions`.\n"
                 "Stay faithful to the source: never add facts it does not contain; keep any [n] citations exactly; say when "
                 "the source is silent on something.",
    "compare": "Shape: a comparison.\n"
               "1. One sentence with the bottom-line verdict first.\n"
               "2. A Markdown comparison table (options as columns or rows, 4-8 criteria, short cells, use ✅ / ❌ / ⚠️ where useful).\n"
               "3. `## ⚖️ Strengths and trade-offs` with a short `###` block per option.\n"
               "4. `## 🎯 Which to choose` as 'Choose X if ...' bullets.",
    "howto": "Shape: a how-to guide.\n"
             "1. One line saying what the reader will achieve.\n"
             "2. `## 📋 Prerequisites` (bullets) when there are any.\n"
             "3. `## 🛠️ Steps` as a numbered list; each step starts with a **bold action**, with commands in fenced code blocks.\n"
             "4. `> [!WARNING]` before any destructive or risky step.\n"
             "5. `## ✅ Verify` with how to check it worked, and `## 🩺 Troubleshooting` for common failures.",
    "troubleshoot": "Shape: troubleshooting.\n"
                    "1. The most likely cause in one or two sentences, in **bold**.\n"
                    "2. `## 🛠️ Fix` as numbered steps with exact commands or code.\n"
                    "3. `## 🔍 If that didn't work` with other causes ranked by likelihood.\n"
                    "4. `> [!TIP]` on how to prevent it.",
    "plan": "Shape: a plan or schedule.\n"
            "1. One line overview (duration, goal).\n"
            "2. A `##` heading per day/phase with an icon (📅, 🗓️, 🚀...), then time-ordered bullets with **bold times**.\n"
            "3. A summary table (when / what / where or cost) when it helps.\n"
            "4. `## 💡 Tips` with a few practical bullets.",
    "code": "Shape: a coding answer.\n"
            "1. One or two sentences on the approach.\n"
            "2. The code in a fenced block with the correct language tag; complete and runnable.\n"
            "3. `## 🔍 How it works` as short bullets referencing names in the code.\n"
            "4. `> [!NOTE]` for edge cases, complexity or security notes when relevant.\n"
            "Keep emoji out of code and out of code comments.",
    "recommend": "Shape: recommendations.\n"
                 "1. One line with the top pick.\n"
                 "2. A numbered list; each item starts with an emoji and a **bold name**, then one line on why and who it suits.\n"
                 "3. A short comparison table if there are more than three options.\n"
                 "4. `## 🎯 My pick` with a one-paragraph recommendation.",
    "creative": "This is a creative or writing request. Output the piece itself, well formatted for its genre (title in bold if "
                "useful). No section headings, no emoji decoration inside the piece, no callouts. At most one short line "
                "before or after it.",
    "general": "Shape: a structured answer.\n"
               "1. Lead with the direct answer in one or two sentences.\n"
               "2. Then organize the details under two to four `##` headings that each start with one relevant emoji.\n"
               "3. Use bullets with **bold** key terms, a table when comparing things, and at most one callout.",
}

STYLE_GUIDE = """{marker}
## Response formatting (ChatGPT-style rich Markdown)
Your reply is streamed and rendered live as Markdown in Open WebUI. Make it easy to scan:
- Start with the answer. Never open with filler ("Sure!", "Great question", "Certainly").
- Short paragraphs (one to three sentences). Bold the **key terms** and **numbers**.
- `##` / `###` headings start with exactly one relevant emoji used as an icon, e.g. `## 🧭 Overview`.
- Bullets may start with an emoji icon when it carries meaning (✅ pros, ❌ cons, ⚠️ risks, 💡 ideas, 📌 key facts, ⏱️ time, 💰 cost). Don't put an emoji on every line.
- Use Markdown tables for comparisons or structured data, fenced code blocks with a language tag for code and commands, and $...$ for math.
- Callouts render with icons; use them sparingly (max two): `> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]` on their own line, followed by `> ` text lines.
- For flows, processes or architectures you may add a small ```mermaid diagram.
- Optional deep dives can go in `<details><summary>More detail</summary> ... </details>`.
- Match length to the request; never pad. Reply in the user's language.
{visual}{length}
{shape}"""

VISUAL_HINT = ("- If a `render_ui` tool is available and the answer needs interactivity (dashboard, calculator, sliders, sortable "
               "table, chart, explorer, map, live weather), call it, then add one to three sentences. Otherwise use Markdown only.\n")
BRIEF_HINT = "- The user asked for a brief answer: at most about 80 words, keep the icons and bold but skip extra sections.\n"
DEEP_HINT = "- The user wants depth: be thorough, use more sections and examples, but keep every paragraph short.\n"

# ----------------------------------------------------------------------------------------------------------------------
# Live icons for bare headings and bold labels. Applied while streaming; never inside code fences.
# ----------------------------------------------------------------------------------------------------------------------
ICON_RULES: list[tuple[str, str]] = [
    (r"tl;?dr|summary|summar|r[ée]sum[ée]|synth[èe]se|resumen|zusammenfassung|in short|en bref|bottom line", "📝"),
    (r"key (points|ideas|takeaways?|facts)|takeaway|highlights|points? cl[ée]s|à retenir|puntos clave|kernpunkte", "💡"),
    (r"overview|introduction|intro\b|aper[çc]u|vue d'ensemble|contexte|context|background|panorama|[üu]berblick", "🧭"),
    (r"how it works|how does|fonctionnement|comment [çc]a marche|c[oó]mo funciona|funktionsweise|mechanism|architecture", "🧩"),
    (r"next steps?|what'?s next|prochaines? [ée]tapes?|siguientes pasos|n[äa]chste schritte|action items?|to ?do", "➡️"),
    (r"step|[ée]tape|paso|schritt|procedure|instructions|guide", "🛠️"),
    (r"prerequisite|requirements?|pr[ée]requis|requisitos|voraussetzung|before you start|what you need", "📋"),
    (r"install|setup|set up|configur|installation|einrichtung|deployment|deploy", "⚙️"),
    (r"example|exemple|ejemplo|beispiel|use case|cas d'usage|demo", "🧪"),
    (r"\bpros\b|advantages?|benefits?|strengths?|avantages?|ventajas?|vorteile", "✅"),
    (r"\bcons\b|disadvantages?|drawbacks?|limitations?|weakness|inconv[ée]nients?|desventajas?|nachteile", "❌"),
    (r"warning|caution|risks?|danger|pitfalls?|attention|avertissement|risques?|advertencia|warnung|risiken", "⚠️"),
    (r"caveat|open questions?|limits|unknowns", "⚠️"),
    (r"tips?|best practices?|conseils?|astuces?|consejos?|tipps?|pro tip", "✨"),
    (r"comparison|compare|versus|\bvs\b|trade-?offs?|comparaison|comparativa|vergleich", "⚖️"),
    (r"which to choose|recommendation|verdict|my pick|choose|conclusion|recommandation|recomendaci[oó]n|empfehlung|fazit", "🎯"),
    (r"cost|price|pricing|budget|co[ûu]t|prix|tarif|precio|kosten|preis", "💰"),
    (r"time|timeline|schedule|duration|calendrier|planning|horario|zeitplan|agenda|day \d|jour \d|d[ií]a \d|tag \d", "🗓️"),
    (r"performance|speed|fast|latency|optimi[sz]", "⚡"),
    (r"security|privacy|s[ée]curit[ée]|seguridad|sicherheit|safety", "🔒"),
    (r"data|statistics|numbers|metrics|donn[ée]es|datos|daten|results?|r[ée]sultats?", "📊"),
    (r"code|implementation|snippet|script|function|impl[ée]mentation", "💻"),
    (r"troubleshoot|debug|fix|solution|d[ée]pannage|soluci[oó]n|fehlerbehebung|diagnos", "🩺"),
    (r"verify|check|test|validation|v[ée]rifi|comprobar|pr[üu]fen", "✅"),
    (r"sources?|references?|further reading|resources?|r[ée]f[ée]rences|ressources|recursos|quellen|bibliograph", "📚"),
    (r"definition|what is|qu'?est-ce|d[ée]finition|definici[oó]n|was ist", "📖"),
    (r"why|pourquoi|por qu[ée]|warum|motivation", "❓"),
    (r"questions?|faq|quiz", "❔"),
    (r"history|origins?|histoire|historia|geschichte", "🏛️"),
    (r"section|part \d|partie|chapter|chapitre", "📑"),
    (r"tools?|outils?|herramientas?|werkzeuge", "🧰"),
    (r"learn|study|apprendre|aprender|lernen|course|cours", "🎓"),
    (r"health|sant[ée]|salud|gesundheit", "🩺"),
    (r"travel|trip|voyage|viaje|reise|itinerary|itin[ée]raire", "✈️"),
    (r"food|eat|restaurant|cuisine|comida|essen|recipe|recette|receta|rezept", "🍽️"),
    (r"ingredients?|ingr[ée]dients?|ingredientes|zutaten", "🧂"),
]
_ICON_RES = [(re.compile(p, re.I), e) for p, e in ICON_RULES]
HEADING_RE = re.compile(r"^(#{1,6})([ \t]+)(.*?)(\r?\n?)$", re.S)
LABEL_RE = re.compile(r"^(\*\*|__)([^*_\n]{1,40}?)(\1)")


def starts_with_icon(text: str) -> bool:
    t = text.lstrip()
    if not t:
        return False
    ch = t[0]
    o = ord(ch)
    if 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF or 0x2190 <= o <= 0x23FF:
        return True
    return unicodedata.category(ch) in ("So", "Sk")


def pick_icon(text: str) -> Optional[str]:
    plain = re.sub(r"[*_`#>\[\]()]", " ", text)
    for rx, emoji in _ICON_RES:
        if rx.search(plain):
            return emoji
    return None


class LineIconizer:
    """Streaming transformer. Text flows through immediately except the start of lines that may be headings, code fences
    or bold labels, which are held for a few characters (headings until end of line) so an icon can be inserted."""

    HOLD_LIMIT = 160

    def __init__(self, headings: bool = True, labels: bool = True) -> None:
        self.headings = headings
        self.labels = labels
        self.buf = ""
        self.holding = False
        self.at_line_start = True
        self.in_fence = False
        self.created = time.monotonic()

    def feed(self, text: str) -> str:
        out: list[str] = []
        for ch in text:
            if self.holding:
                self.buf += ch
                done = self._try_resolve(final=False)
                if done is not None:
                    out.append(done)
                continue
            if self.at_line_start and ch in "#`*_":
                self.holding = True
                self.buf = ch
                self.at_line_start = False
                continue
            if self.at_line_start and ch in " \t" and not self.in_fence:
                out.append(ch)  # indentation keeps us at line start for lists inside lists
                continue
            out.append(ch)
            self.at_line_start = ch == "\n"
        return "".join(out)

    def flush(self) -> str:
        if not self.holding:
            return ""
        return self._try_resolve(final=True) or ""

    def _release(self, text: str, line_done: bool) -> str:
        self.holding = False
        self.buf = ""
        self.at_line_start = line_done
        return text

    def _try_resolve(self, final: bool) -> Optional[str]:
        b = self.buf
        line_done = b.endswith("\n")
        first = b[0]
        # code fences toggle state and are never modified
        if first == "`":
            if b.startswith("```"):
                if line_done or final:
                    self.in_fence = not self.in_fence
                    return self._release(b, line_done)
                return None if len(b) < self.HOLD_LIMIT else self._release(b, False)
            if len(b) >= 3 or line_done or final or b[-1] != "`":
                return self._release(b, line_done)
            return None
        if self.in_fence:
            return self._release(b, line_done) if (line_done or final or len(b) > 1) else None
        if first == "#":
            if not self.headings:
                return self._release(b, line_done)
            m = re.match(r"^#{1,6}", b)
            after = b[m.end():] if m else ""
            if after and after[0] not in " \t":  # "#hashtag" or "####### " -> not a heading
                return self._release(b, line_done)
            if not (line_done or final):
                return None if len(b) < self.HOLD_LIMIT else self._release(b, False)
            return self._release(self._icon_heading(b), line_done)
        if first in "*_":
            if not self.labels:
                return self._release(b, line_done)
            if len(b) >= 2 and b[1] != first:  # "* item" or "*italic*" -> not a bold label
                return self._release(b, line_done)
            m = LABEL_RE.match(b)
            if m:
                if len(b) == m.end() and not (line_done or final):
                    return None  # need one more character to see whether a colon follows
                inner = m.group(2)
                is_label = inner.rstrip().endswith(":") or b[m.end():m.end() + 1] == ":"
                icon = (pick_icon(inner) if is_label and len(inner.split()) <= 5 and not starts_with_icon(inner)
                        else None)
                text = (icon + " " + b) if icon else b
                return self._release(text, line_done)
            if line_done or final or len(b) > 46:
                return self._release(b, line_done)
            return None
        return self._release(b, line_done)

    @staticmethod
    def _icon_heading(line: str) -> str:
        m = HEADING_RE.match(line)
        if not m:
            return line
        hashes, space, title, nl = m.groups()
        if not title.strip() or starts_with_icon(title) or len(title) > 120:
            return line
        icon = pick_icon(title)
        return f"{hashes}{space}{icon} {title}{nl}" if icon else line


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=10, description="Filter order; lower runs earlier. Keep after Study Mode.")
        style: Literal["chatgpt", "compact", "off"] = Field(
            default="chatgpt", description="chatgpt: rich structured answers with icons; compact: same but shorter; off: no guidance.")
        intent_detection: bool = Field(default=True, description="Tailor the answer shape to the request (explain, summarize, compare...).")
        live_heading_icons: bool = Field(default=True, description="While streaming, add an icon to headings the model left without one.")
        live_label_icons: bool = Field(default=True, description="While streaming, add an icon before bold labels such as **Pros:** or **Tip:**.")
        visual_tool_hint: bool = Field(default=True, description="Tell the model to use the Generative UI render_ui tool for interactive needs.")
        system_prompt_integration: Literal["append", "separate"] = Field(
            default="append", description="Append to the existing system prompt or add a separate system message.")
        skip_when_study_mode: bool = Field(default=True, description="Do nothing when the Study Mode filter already shaped the request.")

    class UserValves(BaseModel):
        style: Literal["default", "chatgpt", "compact", "off"] = Field(default="default", description="Your answer style.")
        live_icons: bool = Field(default=True, description="Add icons to headings and labels while answers stream.")

    def __init__(self) -> None:
        self.valves = self.Valves()
        self._streams: dict[str, LineIconizer] = {}
        self._ttl = 900

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _user_valves(user: Optional[dict]) -> "Filter.UserValves":
        cand = (user or {}).get("valves") if isinstance(user, dict) else None
        if isinstance(cand, Filter.UserValves):
            return cand
        try:
            if isinstance(cand, dict):
                return Filter.UserValves.model_validate(cand)
            if hasattr(cand, "model_dump"):
                return Filter.UserValves.model_validate(cand.model_dump())
        except Exception:  # invalid stored values fall back to defaults
            pass
        return Filter.UserValves()

    @staticmethod
    def _last_user_text(messages: list) -> str:
        for m in reversed(messages):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            c = m.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
        return ""

    @staticmethod
    def _has_files(body: dict, metadata: Optional[dict]) -> bool:
        if body.get("files") or (isinstance(metadata, dict) and metadata.get("files")):
            return True
        bm = body.get("metadata")
        return bool(isinstance(bm, dict) and bm.get("files"))

    @staticmethod
    def detect_intent(text: str, has_files: bool = False) -> str:
        t = (text or "").strip()
        if not t:
            return "summarize" if has_files else "general"
        if CHAT_RE.match(t):
            return "chat"
        low = t.lower()
        for name, pattern in INTENT_PATTERNS:
            if re.search(pattern, low, re.I):
                if name == "plan" and re.search(r"\b(explain|what is|qu'?est)\b", low):
                    continue
                return name
        if has_files:
            return "summarize"
        words = len(re.findall(r"\w+", t))
        if words <= 9 and t.endswith("?"):
            return "quick"
        if words <= 4:
            return "quick"
        return "general"

    def build_prompt(self, text: str, has_files: bool, style: str) -> str:
        intent = self.detect_intent(text, has_files) if self.valves.intent_detection else "general"
        length = ""
        if style == "compact" or BRIEF_RE.search(text or ""):
            length = BRIEF_HINT
        elif DEEP_RE.search(text or ""):
            length = DEEP_HINT
        shape = SHAPES.get(intent, SHAPES["general"])
        if intent in ("chat", "quick", "creative"):
            return f"{MARKER}\n## Response style\n{shape} Reply in the user's language."
        return STYLE_GUIDE.format(marker=MARKER, visual=VISUAL_HINT if self.valves.visual_tool_hint else "",
                                  length=length, shape=shape).strip()

    def _inject(self, body: dict, prompt: str) -> None:
        messages = body.setdefault("messages", [])
        if any(isinstance(m, dict) and m.get("role") == "system" and isinstance(m.get("content"), str)
               and MARKER in m["content"] for m in messages):
            return
        if self.valves.system_prompt_integration == "separate":
            i = 0
            while i < len(messages) and isinstance(messages[i], dict) and messages[i].get("role") == "system":
                i += 1
            messages.insert(i, {"role": "system", "content": prompt})
            return
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system" and isinstance(m.get("content"), str):
                m["content"] = m["content"].rstrip() + "\n\n" + prompt
                return
        messages.insert(0, {"role": "system", "content": prompt})

    @staticmethod
    def _key(metadata: Optional[dict]) -> Optional[str]:
        if not isinstance(metadata, dict):
            return None
        parts = [str(metadata[k]) for k in ("chat_id", "message_id") if metadata.get(k)]
        return "|".join(parts) if parts else None

    def _evict(self) -> None:
        now = time.monotonic()
        for k in [k for k, s in self._streams.items() if now - s.created > self._ttl]:
            self._streams.pop(k, None)
        while len(self._streams) > 500:
            self._streams.pop(next(iter(self._streams)))

    def _style(self, user: Optional[dict]) -> tuple[str, bool]:
        uv = self._user_valves(user)
        style = self.valves.style if uv.style == "default" else uv.style
        return style, uv.live_icons

    # ------------------------------------------------------------------ hooks
    async def inlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None) -> dict:
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return body
        if self.valves.skip_when_study_mode and isinstance(__metadata__, dict) and __metadata__.get("study_mode"):
            return body
        style, live = self._style(__user__)
        if style == "off":
            return body
        text = self._last_user_text(messages)
        self._inject(body, self.build_prompt(text, self._has_files(body, __metadata__), style))
        key = self._key(__metadata__)
        if key and live and (self.valves.live_heading_icons or self.valves.live_label_icons):
            self._evict()
            self._streams[key] = LineIconizer(self.valves.live_heading_icons, self.valves.live_label_icons)
        return body

    async def stream(self, event: dict, __metadata__: Optional[dict] = None) -> dict:
        key = self._key(__metadata__)
        it = self._streams.get(key) if key else None
        if it is None or not isinstance(event, dict):
            return event
        finished = False
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                delta["content"] = it.feed(delta["content"])
            if choice.get("finish_reason"):
                finished = True
                tail = it.flush()
                if tail:
                    if not isinstance(delta, dict):
                        delta = choice["delta"] = {}
                    delta["content"] = (delta.get("content") or "") + tail
        etype = event.get("type")
        if etype == "response.output_text.delta" and isinstance(event.get("delta"), str):
            event["delta"] = it.feed(event["delta"])
        elif etype in ("response.output_text.done", "response.completed"):
            finished = True
        if finished:
            self._streams.pop(key, None)
        return event

    async def outlet(self, body: dict, __metadata__: Optional[dict] = None) -> dict:
        key = self._key(__metadata__)
        if key:
            self._streams.pop(key, None)
        return body
