"""
title: Study Mode
author: Muhammad Sohail
version: 1.1.0
description: Model-agnostic Study Mode filter for Open WebUI with guided learning, Socratic tutoring, native ask_user support, interactive quizzes, hidden quiz transport, randomized answer positions, progress status, hints, quiz copy controls, and accessible perfect-score feedback.
required_open_webui_version: 0.11.1
icon_url: https://cdn.jsdelivr.net/npm/@tabler/icons@3.31.0/icons/outline/school.svg
"""

from typing import Literal, Optional, Any
import ast
import json
import re
import time
import uuid

from pydantic import BaseModel, Field


class Filter:
    """Production-oriented Study Mode filter for Open WebUI v0.11.1+.

    Design goals:
    - Works with text-only models by using system-prompt guidance.
    - Uses the native `ask_user` tool opportunistically when the selected model
      supports Native tool calling and the Open WebUI Ask User builtin is enabled.
    - Degrades gracefully to normal conversational questions when tools are not
      available.
    - Uses Open WebUI UserValves for per-user/per-chat learning preferences.
    - Does not depend on external Python packages.
    """

    class Valves(BaseModel):
        priority: int = Field(
            default=5,
            description="Filter execution order. Lower values run earlier.",
        )
        status_updates: bool = Field(
            default=True,
            description="Show a short Study Mode status line when the filter is applied.",
        )
        source_grounding: bool = Field(
            default=True,
            description=(
                "When study materials are attached or retrieved, instruct the model to "
                "treat them as the primary source and distinguish outside knowledge."
            ),
        )
        academic_learning_focus: bool = Field(
            default=True,
            description=(
                "Prefer teaching, reasoning, hints, and feedback over answer dumping for "
                "homework-like or assessment-like requests."
            ),
        )
        max_prompt_chars: int = Field(
            default=12000,
            ge=3000,
            le=24000,
            description="Safety cap for the injected Study Mode instruction.",
        )
        system_prompt_integration: Literal["merge", "separate"] = Field(
            default="merge",
            description="Merge is safest across providers; Separate keeps Study Mode as another scoped system message.",
            json_schema_extra={"input":{"type":"select","options":[
                {"value":"merge","label":"Merge with existing system prompt"},
                {"value":"separate","label":"Separate scoped system message"},
            ]}},
        )
        interactive_quiz_ui: bool = Field(
            default=True,
            description=(
                "Render structured multiple-choice quizzes as persistent interactive "
                "Rich UI cards in the assistant message."
            ),
        )
        max_quiz_questions: int = Field(
            default=20,
            ge=1,
            le=30,
            description="Maximum number of questions accepted by the Rich UI renderer.",
        )
        suppress_quiz_transport: bool = Field(
            default=True,
            description=(
                "Suppress the model-generated quiz JSON transport while streaming so students "
                "see only the interactive quiz card, not STUDY_MODE_QUIZ_START/END payloads."
            ),
        )
        quiz_embed_only: bool = Field(
            default=True,
            description=(
                "When an interactive quiz renders successfully, clear normal assistant text and "
                "show only the Rich UI quiz card."
            ),
        )
        quiz_hint_button: bool = Field(
            default=True,
            description="Show a per-question Hint control in the quiz card.",
        )
        quiz_copy_button: bool = Field(
            default=True,
            description="Show a Copy quiz control in the quiz card.",
        )
        quiz_perfect_score_celebration: bool = Field(
            default=True,
            description=(
                "Show a brief, one-time, motion-safe sparkle celebration when a student "
                "answers every quiz question correctly."
            ),
        )
        quiz_randomize_options: bool = Field(
            default=True,
            description=(
                "Randomize the answer-option order independently for every quiz question "
                "when the Rich UI is rendered, while preserving the correct answer."
            ),
        )
        quiz_progress_status: bool = Field(
            default=True,
            description=(
                "Show a live status message while an interactive quiz is being prepared. "
                "The status is finalized when rendering completes or falls back."
            ),
        )
        quiz_progress_message: str = Field(
            default="Preparing your quiz...",
            min_length=1,
            max_length=120,
            description="Status text shown while the quiz is being generated and validated.",
        )
        quiz_ready_message: str = Field(
            default="Quiz ready",
            min_length=1,
            max_length=120,
            description="Completion status used when the interactive quiz is ready.",
        )
        quiz_schema_tolerance: Literal["compatible", "strict"] = Field(
            default="compatible",
            description="Compatible repairs common local-model quiz JSON variations after validation; Strict requires the documented schema.",
            json_schema_extra={"input":{"type":"select","options":[
                {"value":"compatible","label":"Compatible"},{"value":"strict","label":"Strict schema"},
            ]}},
        )
        multilingual_quiz_detection: bool = Field(default=True, description="Recognize common quiz requests in several languages.")
        quiz_mathjax: bool = Field(default=False, description="Opt in to pinned MathJax 3.2.2 rendering for LaTeX. Gracefully falls back to raw LaTeX if blocked by CSP/network.")
        quiz_keyboard_shortcuts: bool = Field(default=True, description="A-E/1-5 answer, Left/Right navigate, Enter continue, H hint, F fullscreen.")
        quiz_fullscreen_button: bool = Field(default=True, description="Show a fullscreen quiz control when browser/iframe permissions allow it.")
        quiz_export_html: bool = Field(default=True, description="Show a standalone HTML download control inside the Rich UI iframe.")

    class UserValves(BaseModel):
        style: Literal[
            "adaptive",
            "guided",
            "socratic",
            "explain_then_practice",
            "quiz",
        ] = Field(
            default="adaptive",
            description="How Study Mode should teach.",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "adaptive", "label": "Adaptive"},
                        {"value": "guided", "label": "Guided"},
                        {"value": "socratic", "label": "Socratic"},
                        {
                            "value": "explain_then_practice",
                            "label": "Explain, then practice",
                        },
                        {"value": "quiz", "label": "Quiz"},
                    ],
                }
            },
        )
        level: Literal["auto", "beginner", "intermediate", "advanced"] = Field(
            default="auto",
            description="Learner level. Auto lets the model infer or briefly ask.",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "auto", "label": "Auto"},
                        {"value": "beginner", "label": "Beginner"},
                        {"value": "intermediate", "label": "Intermediate"},
                        {"value": "advanced", "label": "Advanced"},
                    ],
                }
            },
        )
        pace: Literal["adaptive", "slow", "normal", "fast"] = Field(
            default="adaptive",
            description="How quickly the tutor should progress.",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "adaptive", "label": "Adaptive"},
                        {"value": "slow", "label": "Slow"},
                        {"value": "normal", "label": "Normal"},
                        {"value": "fast", "label": "Fast"},
                    ],
                }
            },
        )
        answer_policy: Literal[
            "adaptive", "guide_first", "hints_first", "direct_allowed"
        ] = Field(
            default="adaptive",
            description="How readily the tutor should reveal final answers.",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "adaptive", "label": "Adaptive"},
                        {"value": "guide_first", "label": "Guide me first"},
                        {"value": "hints_first", "label": "Hints first"},
                        {"value": "direct_allowed", "label": "Direct answers allowed"},
                    ],
                }
            },
        )
        prefer_ask_user: bool = Field(
            default=True,
            description=(
                "Prefer Open WebUI's native ask_user card for important clarification "
                "when that builtin tool is available."
            ),
        )
        check_understanding: bool = Field(
            default=True,
            description="Use short comprehension checks when they improve learning.",
        )
        use_analogies: bool = Field(
            default=True,
            description="Use simple analogies when they genuinely clarify a concept.",
        )
        use_course_materials: bool = Field(
            default=True,
            description=(
                "Prioritize attached notes, PDFs, slides, worksheets, images, and "
                "knowledge-base material when the user asks to study them."
            ),
        )
        personalize_with_memory: bool = Field(
            default=False,
            description=(
                "When Open WebUI Memory tools are available, let the tutor use relevant "
                "saved learning preferences or goals. The filter never stores memory itself."
            ),
        )
        one_question_at_a_time: bool = Field(
            default=True,
            description=(
                "Prefer one learning question at a time so the student can answer before "
                "the tutor continues."
            ),
        )
        quiz_setup: Literal["ask_if_missing", "use_defaults"] = Field(
            default="ask_if_missing",
            description=(
                "For quiz requests, ask for question count and difficulty when possible, "
                "or use the configured defaults immediately."
            ),
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "ask_if_missing", "label": "Ask if missing"},
                        {"value": "use_defaults", "label": "Use defaults"},
                    ],
                }
            },
        )
        quiz_default_count: int = Field(
            default=5,
            ge=1,
            le=20,
            description="Default quiz length when the user does not specify one.",
        )
        quiz_default_difficulty: Literal[
            "adaptive", "easy", "medium", "hard"
        ] = Field(
            default="adaptive",
            description="Default quiz difficulty when the user does not specify one.",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "adaptive", "label": "Adaptive"},
                        {"value": "easy", "label": "Easy"},
                        {"value": "medium", "label": "Medium"},
                        {"value": "hard", "label": "Hard"},
                    ],
                }
            },
        )

    _MARKER = "[STUDY_MODE_V1_0]"
    _QUIZ_RE = re.compile(
        r"<!--\s*STUDY_MODE_QUIZ_START\s*(.*?)\s*STUDY_MODE_QUIZ_END\s*-->",
        re.DOTALL | re.IGNORECASE,
    )

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True
        self.name = "Study Mode"
        self.icon = (
            "https://cdn.jsdelivr.net/npm/@tabler/icons@3.31.0/icons/outline/school.svg"
        )
        # Request-local stream buffers used only for interactive quiz transport.
        # Keys are per-message UUIDs stored in __metadata__; stale buffers are evicted.
        self._quiz_streams: dict[str, dict[str, Any]] = {}
        self._stream_ttl_seconds = 900

    @staticmethod
    def _user_valves(__user__: Optional[dict]) -> "Filter.UserValves":
        if __user__:
            candidate = __user__.get("valves")
            if candidate is not None:
                if isinstance(candidate, Filter.UserValves):
                    return candidate
                try:
                    if isinstance(candidate, dict):
                        return Filter.UserValves.model_validate(candidate)
                    if hasattr(candidate, "model_dump"):
                        return Filter.UserValves.model_validate(candidate.model_dump())
                except Exception:
                    pass
        return Filter.UserValves()

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") in {
                        "text",
                        "input_text",
                    }:
                        text = item.get("text") or item.get("content") or ""
                        if isinstance(text, str):
                            parts.append(text)
                return "\n".join(parts).strip()
        return ""

    @staticmethod
    def _has_files(body: dict, metadata: Optional[dict]) -> bool:
        if body.get("files"):
            return True
        if metadata and metadata.get("files"):
            return True
        body_metadata = body.get("metadata")
        return bool(isinstance(body_metadata, dict) and body_metadata.get("files"))

    def _build_prompt(
        self,
        user_valves: "Filter.UserValves",
        *,
        has_files: bool,
    ) -> str:
        style_map = {
            "adaptive": (
                "Choose the best teaching method for the request. Mix concise explanation, "
                "guided questions, examples, retrieval practice, and feedback as needed."
            ),
            "guided": (
                "Guide the learner step by step. Give one useful step or hint, let them try, "
                "then respond to their attempt before moving on."
            ),
            "socratic": (
                "Use a Socratic approach. Prefer focused questions that help the learner "
                "derive the idea or next step themselves. Explain directly when questioning "
                "would become unhelpful."
            ),
            "explain_then_practice": (
                "Start with a layered explanation and a small worked example, then give a "
                "closely related practice question and respond to the learner's attempt."
            ),
            "quiz": (
                "Run an interactive quiz. Ask one question, wait for the answer, give concise "
                "feedback explaining why it is correct or incorrect, then adapt the next item."
            ),
        }

        level_map = {
            "auto": (
                "Infer the learner's level from the conversation and their answers. If the "
                "level is genuinely unclear and would materially change the lesson, ask one "
                "brief calibration question."
            ),
            "beginner": (
                "Assume little prior knowledge. Define terms, use concrete examples, avoid "
                "unexplained jargon, and build from fundamentals."
            ),
            "intermediate": (
                "Assume the fundamentals are known. Focus on connections, application, and "
                "common mistakes without over-explaining basics."
            ),
            "advanced": (
                "Assume strong fundamentals. Emphasize deeper reasoning, edge cases, tradeoffs, "
                "formal detail, and challenging application."
            ),
        }

        pace_map = {
            "adaptive": "Adjust depth and pacing from the learner's responses.",
            "slow": "Move slowly, use small chunks, and pause frequently for understanding.",
            "normal": "Use a balanced pace with concise checkpoints.",
            "fast": "Move quickly, minimize repetition, and focus on the essential reasoning.",
        }

        answer_map = {
            "adaptive": (
                "For learning or problem-solving requests, prefer guidance before a final answer. "
                "For simple factual requests, explicit answer requests, or when guidance would "
                "not add learning value, answer directly and explain enough to build understanding."
            ),
            "guide_first": (
                "Do not lead with the final solution to a learning problem. First ask for an "
                "attempt or provide a scaffold. Reveal the full solution after meaningful engagement "
                "or when the learner asks again after trying."
            ),
            "hints_first": (
                "Give one or two targeted hints before showing the complete solution. If the learner "
                "is stuck after the hints, provide the solution with reasoning."
            ),
            "direct_allowed": (
                "Direct answers are allowed, but still explain the reasoning, key concept, or method "
                "so the response teaches rather than only supplies an answer."
            ),
        }

        ask_user_rule = (
            "If the native `ask_user` tool is available and an important clarification, level choice, "
            "or learning-path choice would materially improve the lesson, use `ask_user` instead of "
            "writing the clarification as ordinary text. Use it only when useful, not on every turn. "
            "It must be the only tool call in that turn. Use one call containing 1-3 short questions; "
            "each question must have an id, header, question, and 2-3 labeled options. Allow a custom "
            "answer when appropriate. After the user answers, continue naturally. If `ask_user` is "
            "not available or fails, ask one concise clarification question in normal chat and wait."
            if user_valves.prefer_ask_user
            else (
                "Do not depend on `ask_user`. If clarification is necessary, ask one concise question "
                "in normal chat and wait for the learner's reply."
            )
        )

        understanding_rule = (
            "Use brief understanding checks at natural points. Prefer questions that require the learner "
            "to explain, predict, choose a next step, or apply the idea. Do not turn every answer into a quiz."
            if user_valves.check_understanding
            else "Do not add comprehension checks unless the user explicitly asks for a quiz or practice."
        )

        analogy_rule = (
            "Use analogies only when they simplify the concept, and connect the analogy back to the real concept."
            if user_valves.use_analogies
            else "Avoid analogies unless the learner explicitly asks for one."
        )

        one_at_a_time_rule = (
            "For interactive teaching and quizzes, normally ask only one learner-facing question at a time and wait."
            if user_valves.one_question_at_a_time
            else "You may group a few short learner-facing questions when that is more efficient."
        )

        memory_rule = (
            "Use relevant conversation context to personalize teaching. If Open WebUI Memory tools are available, "
            "you may search existing memories for learning goals, preferred explanation style, or previously studied topics "
            "when that would improve the lesson. Do not create, edit, or delete memories unless the user explicitly asks you to remember something."
            if user_valves.personalize_with_memory
            else "Personalize from the current conversation only. Do not require Memory tools."
        )

        source_rule = ""
        if self.valves.source_grounding and user_valves.use_course_materials:
            source_rule = (
                "When the user asks to study, review, summarize, quiz, or answer from attached course materials, "
                "treat those materials as the primary source. Preserve their terminology and framing. Do not silently "
                "fill unsupported gaps with general knowledge. If outside knowledge is useful, label it clearly as "
                "additional context. Cite the page, slide, section, or retrieved source when available. If native file "
                "or knowledge tools are available, use them when needed to inspect the relevant material."
            )
            if has_files:
                source_rule += " Relevant study material is attached to this conversation."

        academic_rule = ""
        if self.valves.academic_learning_focus:
            academic_rule = (
                "For homework, worksheets, coding exercises, mathematics, science problems, and assessment-style work, "
                "optimize for learning: diagnose where the learner is stuck, scaffold the reasoning, react to their attempt, "
                "and explain mistakes. Do not invent course rules or claim an assignment is graded unless the user says so. "
                "If the learner explicitly needs the final answer, follow the selected answer policy rather than refusing by default."
            )

        prompt = f"""
{self._MARKER}
You are in Study Mode. Your goal is to help the learner build durable understanding, not merely produce an answer.

SCOPE AND COMPATIBILITY
Treat Study Mode as a task-specific overlay. Preserve unrelated existing system/model instructions. Apply quiz JSON transport rules only on turns that actually generate an interactive multiple-choice quiz; never force quiz formatting on ordinary tutoring or other tasks. Higher-priority platform safety and access rules remain in force.

CORE TEACHING BEHAVIOR
1. Start from the learner's goal and current understanding. Do not ask unnecessary setup questions when the request is already clear.
2. Break difficult ideas into manageable steps. Explain in layers: simple first, then more depth when useful.
3. Actively respond to the learner's reasoning. When they attempt an answer, first identify what is correct, then address the specific gap or misconception.
4. Use worked examples, comparisons, retrieval practice, and gradually reduced scaffolding when they improve learning.
5. Adapt difficulty. If the learner is succeeding, make the next step slightly harder. If they are struggling, simplify, give a smaller hint, or use a concrete example.
6. Do not mechanically end every response with a question. Ask one when the learner's answer is genuinely needed for the next teaching step.
7. Match the user's language unless they ask for another language. Be clear, concise, patient, and respectful.
8. For urgent safety-critical situations, prioritize a clear direct answer over the tutoring pattern.

CURRENT STUDY SETTINGS
Teaching style: {user_valves.style}. {style_map[user_valves.style]}
Learner level: {user_valves.level}. {level_map[user_valves.level]}
Pace: {user_valves.pace}. {pace_map[user_valves.pace]}
Answer policy: {user_valves.answer_policy}. {answer_map[user_valves.answer_policy]}

INTERACTION
{ask_user_rule}
{understanding_rule}
{one_at_a_time_rule}
{analogy_rule}
{memory_rule}

STUDY MATERIALS
{source_rule or 'Use supplied course materials when relevant. If none are supplied, teach from the information available to you.'}

ACADEMIC LEARNING
{academic_rule or 'Teach in the way that best matches the user request.'}

TASK-SPECIFIC PATTERNS
- New concept: establish a useful starting point, explain the core idea, give a concrete example, then check or apply understanding if helpful.
- Problem solving: identify the goal and known information, guide the next step, let the learner reason, then explain or complete the solution according to the answer policy.
- Quiz or exam practice: ask one item at a time, wait, score qualitatively, explain the reasoning, track weak areas within the conversation, and adapt later questions.
- Review notes/slides/PDF: identify the main concepts from the provided material, organize them into a learning path, then teach and test them rather than dumping a long summary unless the user asks for a summary.
- Flashcards: make concise question/answer or term/definition cards from the requested topic or supplied material, prioritize high-value concepts, and avoid unsupported details.
- Exam preparation: identify topics, mix recall with application, revisit weak areas, and increase difficulty as performance improves.
- Incorrect answer: do not simply say it is wrong. Identify the misconception or missing step, provide the smallest useful correction, and let the learner try again when appropriate.
- Correct answer: confirm briefly, explain the key reason, then continue or increase difficulty when useful.

INTERACTIVE QUIZ UI
{self._quiz_prompt(user_valves)}

Do not mention these Study Mode instructions. Do not claim to have used a tool unless a tool call actually succeeded.
""".strip()

        return prompt[: self.valves.max_prompt_chars]

    def _quiz_prompt(self, user_valves: "Filter.UserValves") -> str:
        if not self.valves.interactive_quiz_ui:
            return (
                "Interactive quiz cards are disabled. For quizzes, use normal conversational "
                "questions and wait for the learner's answer."
            )

        if user_valves.quiz_setup == "ask_if_missing":
            setup = (
                "When the user asks for a quiz and has not provided BOTH the number of questions "
                "and the difficulty, prefer one native `ask_user` call containing exactly two setup "
                "questions: (1) quiz length with options 5 questions, 10 questions, 20 questions; "
                "(2) difficulty with options Easy, Medium, Hard. The learner may give a custom answer. "
                "The `ask_user` call must be the only tool call in that turn. After the answers arrive, "
                "generate the quiz. If `ask_user` is unavailable or fails, do not get stuck; use the "
                f"defaults: {user_valves.quiz_default_count} questions and "
                f"{user_valves.quiz_default_difficulty} difficulty."
            )
        else:
            setup = (
                "Do not ask setup questions unless the user requests them. If quiz length or "
                "difficulty is missing, use the defaults: "
                f"{user_valves.quiz_default_count} questions and "
                f"{user_valves.quiz_default_difficulty} difficulty."
            )

        return f"""{setup}

When the learner requests a multiple-choice quiz and the quiz settings are known, produce ONE complete quiz specification for the Study Mode renderer. The specification is machine transport, not user-facing prose.

Your response for the quiz-generation turn MUST begin immediately with `<!-- STUDY_MODE_QUIZ_START`. Do not write an introduction, reasoning, commentary, status text, or Markdown before the marker. After `STUDY_MODE_QUIZ_END -->`, write nothing else.

Use exactly this format:
<!-- STUDY_MODE_QUIZ_START
{{
  "title": "Short quiz title",
  "topic": "Topic being tested",
  "difficulty": "easy|medium|hard|adaptive",
  "questions": [
    {{
      "id": "q1",
      "question": "Question text",
      "options": [
        {{"id": "A", "text": "Option A"}},
        {{"id": "B", "text": "Option B"}},
        {{"id": "C", "text": "Option C"}},
        {{"id": "D", "text": "Option D"}}
      ],
      "correct": "A",
      "explanation": "Concise explanation of why the correct answer is correct.",
      "hint": "A short useful hint that helps without revealing the answer."
    }}
  ]
}}
STUDY_MODE_QUIZ_END -->

Rules for the quiz specification:
- Valid strict JSON only inside the hidden block. No Markdown fences inside it.
- For smaller/local models, prioritize a valid complete schema over extra prose; keep explanations and hints concise if needed.
- If LaTeX is used, JSON-escape backslashes (for example `\\(` in JSON so decoded text contains `\(`).
- Use 2 to 5 answer options per question. Four is preferred.
- Exactly one option must be defensibly correct.
- `correct` must exactly match an option id.
- EVERY question must contain a non-empty `hint` that helps without revealing the answer.
- Questions must be self-contained, technically accurate, unambiguous, and aligned with the requested topic and difficulty.
- Before emitting the block, silently verify every question, answer, distractor, explanation, and hint.
- Avoid wording where more than one option can technically be true. For lifecycle or sequence questions, state precisely which stage or operation is being tested.
- Distractors should be plausible and educational, not silly.
- Do not intentionally keep the correct answer in a fixed option position; the renderer also randomizes option order before display.
- Explanations must teach the underlying concept.
- Do not include scripts, HTML, URLs, or executable code in any quiz field.
- Never exceed {self.valves.max_quiz_questions} questions.

For free-response Socratic teaching, normal explanations, flashcards, or one-question-at-a-time tutoring, do NOT use this quiz block."""

    @staticmethod
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

    @staticmethod
    def _assistant_message(body: dict) -> Optional[dict]:
        messages = body.get("messages")
        if not isinstance(messages, list):
            return None
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return message
        return None

    @staticmethod
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

    def _extract_quiz(self, content: str, *, allow_unmarked: bool = True) -> Optional[dict]:
        if not isinstance(content,str): return None
        m=self._QUIZ_RE.search(content)
        raw=m.group(1).strip() if m else (self._balanced_json_object(content) if allow_unmarked and self.valves.quiz_schema_tolerance=="compatible" else None)
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

    @staticmethod
    def _safe_json_for_script(data: dict) -> str:
        return (
            json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )

    def _render_quiz_embed(self, quiz: dict) -> str:
        payload = self._safe_json_for_script(quiz)
        show_hint = "true" if self.valves.quiz_hint_button else "false"
        show_copy = "true" if self.valves.quiz_copy_button else "false"
        celebrate_perfect = (
            "true" if self.valves.quiz_perfect_score_celebration else "false"
        )
        randomize_options = "true" if self.valves.quiz_randomize_options else "false"
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>
:root {{ color-scheme: light dark; --bg:#fff; --fg:#171717; --muted:#6b7280; --border:#e5e7eb; --soft:#f7f7f8; --good:#15803d; --goodbg:#ecfdf3; --bad:#dc2626; --badbg:#fef2f2; --accent:#111827; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#171717; --fg:#f5f5f5; --muted:#a3a3a3; --border:#3f3f46; --soft:#222; --good:#4ade80; --goodbg:#12351f; --bad:#f87171; --badbg:#3a1717; --accent:#fafafa; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:transparent; color:var(--fg); font:15px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.card {{ background:var(--bg); border:1px solid var(--border); border-radius:22px; padding:22px; width:100%; max-width:880px; margin:2px auto; }}
.header {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:14px; }}
.title {{ font-size:14px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }}
.controls {{ display:flex; align-items:center; gap:4px; flex-shrink:0; }}
.nav {{ display:flex; align-items:center; gap:7px; color:var(--muted); font-size:13px; margin-right:5px; }}
.iconbtn {{ position:relative; border:0; background:transparent; color:var(--fg); cursor:pointer; width:36px; height:36px; border-radius:50%; display:grid; place-items:center; padding:0; }}
.iconbtn:hover {{ background:var(--soft); }}
.iconbtn:disabled {{ opacity:.3; cursor:default; }}
.iconbtn svg {{ width:20px; height:20px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }}
.iconbtn[data-tip]::after {{ content:attr(data-tip); position:absolute; bottom:calc(100% + 8px); left:50%; transform:translateX(-50%); background:#111; color:#fff; font-size:12px; font-weight:600; line-height:1; padding:7px 9px; border-radius:7px; opacity:0; visibility:hidden; pointer-events:none; white-space:nowrap; transition:opacity .12s ease, visibility .12s ease; z-index:40; box-shadow:0 2px 8px rgba(0,0,0,.18); }}
.iconbtn[data-tip]:hover::after, .iconbtn[data-tip]:focus-visible::after {{ opacity:1; visibility:visible; }}
.iconbtn.tip-below[data-tip]::after {{ top:calc(100% + 8px); bottom:auto; }}
.iconbtn.tip-align-start[data-tip]::after {{ left:0; transform:none; }}
.iconbtn.tip-align-end[data-tip]::after {{ left:auto; right:0; transform:none; }}
.question {{ font-size:18px; font-weight:600; margin:8px 0 18px; }}
.options {{ display:grid; gap:10px; }}
.option {{ width:100%; text-align:left; display:grid; grid-template-columns:38px 1fr; gap:10px; align-items:start; border:1px solid transparent; background:transparent; color:var(--fg); padding:9px 10px; border-radius:14px; cursor:pointer; }}
.option:hover:not(:disabled) {{ background:var(--soft); }}
.badge {{ width:32px; height:32px; border:1px solid var(--border); border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:600; }}
.option.correct {{ background:var(--goodbg); }}
.option.correct .badge {{ color:white; background:var(--good); border-color:var(--good); }}
.option.wrong {{ background:var(--badbg); }}
.option.wrong .badge {{ color:white; background:var(--bad); border-color:var(--bad); }}
.feedback {{ display:none; margin-top:16px; padding:13px 14px; border-radius:14px; background:var(--soft); }}
.feedback.show {{ display:block; }}
.feedback strong.good {{ color:var(--good); }}
.feedback strong.bad {{ color:var(--bad); }}
.hint {{ display:none; margin-top:14px; color:var(--muted); background:var(--soft); border-radius:12px; padding:11px 13px; }}
.hint.show {{ display:block; }}
.footer {{ display:flex; justify-content:flex-end; gap:12px; margin-top:22px; }}
.btn {{ border:1px solid var(--border); background:var(--bg); color:var(--fg); border-radius:999px; padding:9px 16px; cursor:pointer; font-weight:600; }}
.btn:hover {{ background:var(--soft); }}
.btn.primary {{ background:var(--accent); color:var(--bg); border-color:var(--accent); }}
.btn:disabled {{ opacity:.4; cursor:default; }}
.summary {{ display:none; text-align:center; padding:22px 6px 8px; position:relative; min-height:250px; overflow:hidden; border-radius:18px; }}
.summary.show {{ display:block; }}
.perfect-label {{ display:none; width:max-content; max-width:100%; margin:0 auto 8px; padding:5px 10px; border:1px solid var(--border); border-radius:999px; color:var(--muted); font-size:12px; font-weight:650; letter-spacing:.01em; background:color-mix(in srgb, var(--bg) 88%, var(--soft)); }}
.perfect-label.show {{ display:block; }}
.score {{ font-size:46px; line-height:1.05; font-weight:750; margin:6px 0 10px; letter-spacing:-.03em; }}
.score-message {{ font-size:15px; max-width:520px; margin:0 auto; }}
.celebration {{ position:absolute; inset:0; pointer-events:none; overflow:hidden; border-radius:18px; z-index:0; }}
.summary > :not(.celebration) {{ position:relative; z-index:1; }}
.spark {{ position:absolute; left:50%; top:47%; width:var(--size,6px); height:var(--size,6px); border-radius:50%; background:var(--spark,#d6a51d); opacity:0; transform:translate(-50%,-50%) scale(.25); animation:study_mode-spark 1.55s cubic-bezier(.2,.7,.2,1) var(--delay,0ms) forwards; }}
.spark.diamond {{ border-radius:1px; }}
@keyframes study_mode-spark {{ 0% {{ opacity:0; transform:translate(-50%,-50%) scale(.2) rotate(0deg); }} 16% {{ opacity:.95; }} 72% {{ opacity:.8; }} 100% {{ opacity:0; transform:translate(calc(-50% + var(--x)),calc(-50% + var(--y))) scale(1) rotate(var(--rot,120deg)); }} }}
@media (prefers-reduced-motion: reduce) {{ .spark {{ animation:none !important; display:none; }} }}
.meta {{ color:var(--muted); margin-bottom:18px; }}
.mistakes {{ text-align:left; margin:16px auto; max-width:680px; }}
.mistake {{ padding:10px 0; border-top:1px solid var(--border); }}
.summary-actions {{ display:flex; gap:10px; justify-content:center; flex-wrap:wrap; margin-top:18px; }}
@media (max-width:600px) {{ .card {{ padding:16px; border-radius:18px; }} .question {{ font-size:17px; }} .option {{ grid-template-columns:34px 1fr; }} .title {{ max-width:40%; }} }}
</style>
</head>
<body>
<div class="card" id="card">
  <div id="quizView">
    <div class="header">
      <div class="title" id="title"></div>
      <div class="controls">
        <div class="nav">
          <button class="iconbtn" id="prev" aria-label="Previous question" data-tip="Previous"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg></button>
          <span id="progress"></span>
          <button class="iconbtn" id="nextTop" aria-label="Next question" data-tip="Next"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button>
        </div>
        <button class="iconbtn" id="hintBtn" aria-label="Hint" data-tip="Hint"><svg viewBox="0 0 24 24"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M8.5 14.5A6 6 0 1 1 15.5 14.5C14.5 15.3 14 16 14 17h-4c0-1-.5-1.7-1.5-2.5Z"/></svg></button>
        <button class="iconbtn" id="copyBtn" aria-label="Copy quiz" data-tip="Copy quiz"><svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
      </div>
    </div>
    <div class="question" id="question"></div>
    <div class="options" id="options"></div>
    <div class="feedback" id="feedback"></div>
    <div class="hint" id="hint"></div>
    <div class="footer"><button class="btn primary" id="nextBtn" disabled>Next</button></div>
  </div>
  <div class="summary" id="summary"><div class="celebration" id="celebration" aria-hidden="true"></div><div class="meta" id="summaryTitle"></div><div class="perfect-label" id="perfectLabel">Perfect score</div><div class="score" id="score"></div><div class="score-message" id="scoreText" aria-live="polite"></div><div class="mistakes" id="mistakes"></div><div class="summary-actions"><button class="btn" id="reviewBtn">Review mistakes</button><button class="btn primary" id="studyBtn">Continue studying</button><button class="btn" id="newQuizBtn">New quiz</button></div></div>
</div>
<script>
const quiz = {payload};
const SHOW_HINT = {show_hint};
const SHOW_COPY = {show_copy};
const CELEBRATE_PERFECT = {celebrate_perfect};
const RANDOMIZE_OPTIONS = {randomize_options};

function randomUnit() {{
  try {{
    if(window.crypto && window.crypto.getRandomValues) {{
      const a=new Uint32Array(1);
      window.crypto.getRandomValues(a);
      return a[0]/4294967296;
    }}
  }} catch(_) {{}}
  return Math.random();
}}
function shuffleInPlace(items) {{
  for(let i=items.length-1;i>0;i--) {{
    const j=Math.floor(randomUnit()*(i+1));
    const t=items[i]; items[i]=items[j]; items[j]=t;
  }}
  return items;
}}
function randomizeQuizOptions() {{
  if(!RANDOMIZE_OPTIONS || !Array.isArray(quiz.questions)) return;
  quiz.questions.forEach(q=>{{
    if(!q || !Array.isArray(q.options) || q.options.length<2) return;
    const originalCorrect=q.correct;
    const shuffled=q.options.map(o=>({{...o,__originalId:o.id}}));
    shuffleInPlace(shuffled);
    shuffled.forEach((o,i)=>{{o.id=String.fromCharCode(65+i);}});
    const correctOption=shuffled.find(o=>o.__originalId===originalCorrect);
    q.options=shuffled.map(o=>{{const clean={{id:o.id,text:o.text}};return clean;}});
    if(correctOption) q.correct=correctOption.id;
  }});
}}
randomizeQuizOptions();

let index = 0;
let celebrated = false;
const answers = Array(quiz.questions.length).fill(null);
const el = id => document.getElementById(id);
function reportHeight() {{ parent.postMessage({{type:'iframe:height',height:document.documentElement.scrollHeight}}, '*'); }}
function positionTooltip(btn) {{
  if(!btn || !btn.dataset.tip) return;
  btn.classList.remove('tip-below','tip-align-start','tip-align-end');
  const r=btn.getBoundingClientRect();
  if(r.top < 48) btn.classList.add('tip-below');
  const label=(btn.dataset.tip||'').length;
  const estimated=Math.max(56, Math.min(180, label*7.4+20));
  if(r.left + r.width/2 - estimated/2 < 6) btn.classList.add('tip-align-start');
  if(r.left + r.width/2 + estimated/2 > window.innerWidth-6) btn.classList.add('tip-align-end');
}}
function positionTooltips() {{ document.querySelectorAll('.iconbtn[data-tip]').forEach(positionTooltip); }}
new ResizeObserver(()=>{{reportHeight();positionTooltips();}}).observe(document.body);
window.addEventListener('load',()=>{{reportHeight();positionTooltips();}});
window.addEventListener('resize',positionTooltips);
function render() {{
  const q=quiz.questions[index]; el('title').textContent=quiz.title||quiz.topic||'Study quiz'; el('progress').textContent=`${{index+1}} of ${{quiz.questions.length}}`; el('question').textContent=q.question;
  el('prev').disabled=index===0; el('nextTop').disabled=index===quiz.questions.length-1; el('feedback').className='feedback'; el('feedback').textContent=''; el('hint').className='hint'; el('hint').textContent=q.hint||'';
  el('hintBtn').style.display=SHOW_HINT?'grid':'none'; el('copyBtn').style.display=SHOW_COPY?'grid':'none';
  const box=el('options'); box.replaceChildren(); q.options.forEach(opt=>{{ const btn=document.createElement('button'); btn.className='option'; btn.type='button'; const badge=document.createElement('span'); badge.className='badge'; badge.textContent=opt.id; const txt=document.createElement('span'); txt.textContent=opt.text; btn.append(badge,txt); btn.onclick=()=>choose(opt.id); box.appendChild(btn); }});
  if(answers[index]) applyAnswer(); el('nextBtn').disabled=!answers[index]; el('nextBtn').textContent=index===quiz.questions.length-1?'Finish':'Next'; positionTooltips(); reportHeight();
}}
function choose(id) {{ if(answers[index]) return; answers[index]=id; applyAnswer(); el('nextBtn').disabled=false; reportHeight(); }}
function applyAnswer() {{ const q=quiz.questions[index],chosen=answers[index]; [...el('options').querySelectorAll('.option')].forEach(btn=>{{ const id=btn.querySelector('.badge').textContent; btn.disabled=true; if(id===q.correct)btn.classList.add('correct'); else if(id===chosen)btn.classList.add('wrong'); }}); const good=chosen===q.correct; el('feedback').className='feedback show'; el('feedback').replaceChildren(); const strong=document.createElement('strong'); strong.className=good?'good':'bad'; strong.textContent=good?'Correct.':'Not quite.'; const span=document.createElement('span'); span.textContent=' '+q.explanation; el('feedback').append(strong,span); }}
function go(delta) {{ const n=index+delta; if(n<0||n>=quiz.questions.length)return; index=n; render(); }}
function perfectCelebration() {{
  if(celebrated || !CELEBRATE_PERFECT || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  celebrated=true;
  const layer=el('celebration');
  layer.replaceChildren();
  const count=30;
  for(let i=0;i<count;i++) {{
    const p=document.createElement('span');
    p.className='spark'+(i%3===0?' diamond':'');
    const angle=(Math.PI*2*i/count)+((i%4)-1.5)*0.045;
    const radius=105+(i%6)*14;
    const x=Math.cos(angle)*radius;
    const y=Math.sin(angle)*radius*.58;
    p.style.setProperty('--x',`${{x.toFixed(1)}}px`);
    p.style.setProperty('--y',`${{y.toFixed(1)}}px`);
    p.style.setProperty('--rot',`${{90+(i*37)%240}}deg`);
    p.style.setProperty('--delay',`${{(i%8)*22}}ms`);
    p.style.setProperty('--size',`${{4+(i%4)*1.4}}px`);
    layer.appendChild(p);
  }}
  setTimeout(()=>{{layer.replaceChildren();}},1900);
}}
function finish() {{
  el('quizView').style.display='none';
  el('summary').className='summary show';
  const correct=answers.reduce((n,a,i)=>n+(a===quiz.questions[i].correct?1:0),0);
  const perfect=correct===quiz.questions.length;
  el('summaryTitle').textContent=quiz.title||'Quiz complete';
  el('perfectLabel').className=perfect?'perfect-label show':'perfect-label';
  el('score').textContent=`${{correct}}/${{quiz.questions.length}}`;
  el('scoreText').textContent=perfect
    ? `Congratulations. You answered all ${{quiz.questions.length}} questions correctly.`
    : `You answered ${{correct}} of ${{quiz.questions.length}} correctly.`;
  const mistakes=quiz.questions.map((q,i)=>({{q,i}})).filter(x=>answers[x.i]!==x.q.correct);
  el('mistakes').replaceChildren();
  mistakes.forEach(x=>{{ const d=document.createElement('div'); d.className='mistake'; d.textContent=`${{x.i+1}}. ${{x.q.question}}`; el('mistakes').appendChild(d); }});
  el('reviewBtn').style.display=mistakes.length?'inline-block':'none';
  el('studyBtn').style.display=mistakes.length?'inline-block':'none';
  if(perfect) perfectCelebration();
  reportHeight();
}}
function submitPrompt(text) {{ parent.postMessage({{type:'input:prompt:submit',text}}, '*'); }}
function quizText() {{ let out=(quiz.title||quiz.topic||'Quiz')+'\\n\\n'; quiz.questions.forEach((q,i)=>{{ out+=`${{i+1}}. ${{q.question}}\\n`; q.options.forEach(o=>{{out+=`${{o.id}}. ${{o.text}}\\n`;}}); out+='\\n'; }}); return out.trim(); }}
async function copyQuiz() {{ const text=quizText(); let ok=false; try {{ await navigator.clipboard.writeText(text); ok=true; }} catch(e) {{ try {{ const ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.focus(); ta.select(); ok=document.execCommand('copy'); ta.remove(); }} catch(_) {{}} }} const b=el('copyBtn'); const old=b.dataset.tip||'Copy quiz'; b.dataset.tip=ok?'Copied':'Copy failed'; positionTooltip(b); setTimeout(()=>{{b.dataset.tip=old;positionTooltip(b);}},1200); }}
el('prev').onclick=()=>go(-1); el('nextTop').onclick=()=>go(1); el('hintBtn').onclick=()=>{{el('hint').classList.toggle('show');reportHeight();}}; el('copyBtn').onclick=copyQuiz; el('nextBtn').onclick=()=>{{if(index===quiz.questions.length-1)finish();else go(1);}};
document.querySelectorAll('.iconbtn[data-tip]').forEach(btn=>{{btn.addEventListener('mouseenter',()=>positionTooltip(btn));btn.addEventListener('focus',()=>positionTooltip(btn));}});
el('reviewBtn').onclick=()=>{{const i=answers.findIndex((a,j)=>a!==quiz.questions[j].correct);if(i>=0){{el('summary').className='summary';el('quizView').style.display='block';index=i;render();}}}};
el('studyBtn').onclick=()=>{{const weak=quiz.questions.filter((q,i)=>answers[i]!==q.correct).map(q=>q.question).slice(0,5);submitPrompt(`Study Mode quiz result for ${{quiz.topic}}: I need more help with these questions: ${{weak.join(' | ')}}. Teach the weak concepts step by step, then check my understanding.`);}};
el('newQuizBtn').onclick=()=>submitPrompt(`Quiz me again on ${{quiz.topic}}. Use a new set of questions and adapt the difficulty based on my previous attempt.`);
render();
</script>
<script>
{self._quiz_feature_addon_js()}
</script>
</body>
</html>"""

    def _quiz_feature_addon_js(self) -> str:
        cfg=self._safe_json_for_script({"keyboard":bool(self.valves.quiz_keyboard_shortcuts),"fullscreen":bool(self.valves.quiz_fullscreen_button),"exportHtml":bool(self.valves.quiz_export_html),"mathjax":bool(self.valves.quiz_mathjax)})
        js=r"""
(function(){
const cfg=__CFG__,controls=document.querySelector('.controls');if(!controls)return;const NS='http://www.w3.org/2000/svg';
function ib(id,label,tip,paths){const b=document.createElement('button');b.type='button';b.className='iconbtn';b.id=id;b.setAttribute('aria-label',label);b.dataset.tip=tip;const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox','0 0 24 24');paths.forEach(d=>{const p=document.createElementNS(NS,'path');p.setAttribute('d',d);svg.appendChild(p)});b.appendChild(svg);controls.appendChild(b);return b}
let fs=null,ex=null,snap='';
if(cfg.fullscreen){fs=ib('fullscreenBtn','Toggle fullscreen','Fullscreen (F)',['M8 3H3v5','M16 3h5v5','M8 21H3v-5','M16 21h5v-5']);fs.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen()}catch(_){}};document.addEventListener('fullscreenchange',()=>{positionTooltip(fs);reportHeight()})}
if(cfg.exportHtml){ex=ib('exportBtn','Export quiz as HTML','Export HTML',['M12 3v12','m7 10 5 5 5-5','M5 21h14']);setTimeout(()=>{snap='<!doctype html>\n'+document.documentElement.outerHTML},0);ex.onclick=()=>{try{const blob=new Blob([snap||('<!doctype html>\n'+document.documentElement.outerHTML)],{type:'text/html;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a'),name=(quiz.title||quiz.topic||'study-quiz').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,60)||'study-quiz';a.href=url;a.download=name+'.html';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(_){}}}
function math(){if(!cfg.mathjax||!window.MathJax||typeof window.MathJax.typesetPromise!=='function')return;try{if(typeof window.MathJax.typesetClear==='function')window.MathJax.typesetClear([el('card')]);window.MathJax.typesetPromise([el('card')]).then(()=>reportHeight()).catch(()=>{})}catch(_){}}
if(cfg.mathjax){try{window.MathJax=window.MathJax||{tex:{inlineMath:[['\\(','\\)'],['$','$']],displayMath:[['\\[','\\]'],['$$','$$']]}};const x=document.createElement('script');x.src='https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js';x.defer=true;x.referrerPolicy='no-referrer';x.onload=math;x.onerror=()=>{};document.head.appendChild(x);const r=render;render=function(){r();setTimeout(math,0)};const a=applyAnswer;applyAnswer=function(){a();setTimeout(math,0)};const f=finish;finish=function(){f();setTimeout(math,0)}}catch(_){}}
if(cfg.keyboard)document.addEventListener('keydown',e=>{if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey)return;const t=e.target;if(t&&['INPUT','TEXTAREA','SELECT'].includes(t.tagName))return;const q=quiz.questions[index];if(!q)return;let n=-1;if(/^[1-5]$/.test(e.key))n=Number(e.key)-1;else if(/^[a-eA-E]$/.test(e.key))n=e.key.toUpperCase().charCodeAt(0)-65;if(n>=0&&n<q.options.length&&!answers[index]){e.preventDefault();choose(q.options[n].id);return}if(e.key==='ArrowLeft'){e.preventDefault();go(-1)}else if(e.key==='ArrowRight'){e.preventDefault();go(1)}else if((e.key==='h'||e.key==='H')&&SHOW_HINT){e.preventDefault();el('hint').classList.toggle('show');reportHeight()}else if((e.key==='f'||e.key==='F')&&fs){e.preventDefault();fs.click()}else if(e.key==='Enter'&&answers[index]){e.preventDefault();index===quiz.questions.length-1?finish():go(1)}},true);
[fs,ex].filter(Boolean).forEach(b=>{b.addEventListener('mouseenter',()=>positionTooltip(b));b.addEventListener('focus',()=>positionTooltip(b))});positionTooltips();reportHeight();
})();
"""
        return js.replace("__CFG__",cfg,1)

    @staticmethod
    def _stream_key(metadata: Optional[dict]) -> Optional[str]:
        if not isinstance(metadata,dict): return None
        key=metadata.get("study_mode_quiz_stream_key")
        if isinstance(key,str) and key: return key
        parts=[f"{k}={metadata[k]}" for k in ("chat_id","message_id","session_id") if metadata.get(k) is not None]
        return "study-mode:"+"|".join(parts) if parts else None

    def _evict_streams(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self._quiz_streams.items() if now - float(v.get("created", now)) > self._stream_ttl_seconds]
        for key in stale:
            self._quiz_streams.pop(key, None)

    @staticmethod
    def _clean_quiz_transport(text: str) -> str:
        if not isinstance(text, str):
            return ""
        cleaned = Filter._QUIZ_RE.sub("", text)
        cleaned = re.sub(r"(?im)^\\s*Interactive quiz ready\\.?\\s*$", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _scrub_assistant_transport(assistant: dict, *, clear_all: bool) -> str:
        content = assistant.get("content")
        cleaned = Filter._clean_quiz_transport(content) if isinstance(content, str) else ""
        if clear_all:
            cleaned = ""
        assistant["content"] = cleaned
        # Some providers/Open WebUI parsers expose visible reasoning separately.
        for key in ("reasoning", "reasoning_content", "thinking", "analysis"):
            value = assistant.get(key)
            if isinstance(value, str):
                assistant[key] = "" if clear_all else Filter._clean_quiz_transport(value)
        return cleaned

    def _buffer_stream_text(self, key: str, text: Any) -> None:
        if not isinstance(text, str) or not text:
            return
        state = self._quiz_streams.get(key)
        if state is not None:
            state["raw"] = state.get("raw", "") + text

    async def stream(
        self,
        event: dict,
        __metadata__: Optional[dict] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        """Hide quiz transport from the live UI while preserving it for outlet rendering."""
        if not self.valves.interactive_quiz_ui or not self.valves.suppress_quiz_transport:
            return event
        key = self._stream_key(__metadata__)
        if not key or key not in self._quiz_streams:
            return event

        # OpenAI Chat Completions-style streaming, used by vLLM and many local models.
        for choice in event.get("choices", []) if isinstance(event, dict) else []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                for field in ("content", "reasoning_content", "reasoning"):
                    value = delta.get(field)
                    if isinstance(value, str):
                        self._buffer_stream_text(key, value)
                        delta[field] = ""
            message = choice.get("message")
            if isinstance(message, dict):
                for field in ("content", "reasoning_content", "reasoning"):
                    value = message.get(field)
                    if isinstance(value, str):
                        self._buffer_stream_text(key, value)
                        message[field] = ""

        # OpenAI Responses-style events.
        etype = event.get("type") if isinstance(event, dict) else None
        if etype == "response.output_text.delta" and isinstance(event.get("delta"), str):
            self._buffer_stream_text(key, event["delta"])
            event["delta"] = ""
        elif etype == "response.output_text.done" and isinstance(event.get("text"), str):
            self._buffer_stream_text(key, event["text"])
            event["text"] = ""
        elif etype in {"response.content_part.done", "response.output_item.done", "response.completed"}:
            def scrub(node: Any) -> None:
                if isinstance(node, dict):
                    if node.get("type") in {"output_text", "input_text"} and isinstance(node.get("text"), str):
                        self._buffer_stream_text(key, node["text"])
                        node["text"] = ""
                    for value in node.values():
                        scrub(value)
                elif isinstance(node, list):
                    for value in node:
                        scrub(value)
            scrub(event)

        return event

    def _inject_prompt(self, body: dict, prompt: str) -> None:
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

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __model__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> dict:
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return body

        user_valves = self._user_valves(__user__)
        has_files = self._has_files(body, __metadata__)
        prompt = self._build_prompt(user_valves, has_files=has_files)
        self._inject_prompt(body, prompt)

        # For interactive quiz requests, buffer model text during streaming. This
        # prevents the machine-only STUDY_MODE_QUIZ_START/END JSON from ever appearing
        # in the visible assistant response, including reasoning/thought panes.
        self._evict_streams()
        key: Optional[str] = None
        if (
            self.valves.interactive_quiz_ui
            and self.valves.suppress_quiz_transport
            and self._looks_like_quiz_request(messages, user_valves, self.valves.multilingual_quiz_detection)
            and __metadata__ is not None
        ):
            key = self._stream_key(__metadata__)
            if not key:
                key = uuid.uuid4().hex
            __metadata__["study_mode_quiz_stream_key"] = key
            self._quiz_streams[key] = {
                "raw": "",
                "created": time.monotonic(),
                "progress_status_started": False,
            }

        is_quiz_request = self._looks_like_quiz_request(messages, user_valves, self.valves.multilingual_quiz_detection)

        # Add lightweight request-local metadata for observability and for any
        # later filters in the same request chain. This does not persist learner data.
        if __metadata__ is not None:
            __metadata__["study_mode"] = {
                "active": True,
                "style": user_valves.style,
                "level": user_valves.level,
                "pace": user_valves.pace,
                "answer_policy": user_valves.answer_policy,
                "quiz_request": is_quiz_request,
            }

        if __event_emitter__ is not None:
            try:
                if is_quiz_request and self.valves.quiz_progress_status:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": self.valves.quiz_progress_message.strip(),
                                "done": False,
                                "hidden": False,
                                "action": "study_mode.quiz",
                            },
                        }
                    )
                    if key and key in self._quiz_streams:
                        self._quiz_streams[key]["progress_status_started"] = True
                    if __metadata__ is not None:
                        __metadata__["study_mode_quiz_progress_status"] = True
                elif self.valves.status_updates:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": "Study Mode is active",
                                "done": True,
                                "hidden": False,
                                "action": "study_mode",
                            },
                        }
                    )
            except Exception:
                # UI feedback must never break the chat request.
                pass

        return body

    async def _finish_quiz_progress_status(
        self,
        __event_emitter__,
        __metadata__: Optional[dict],
        *,
        description: Optional[str] = None,
    ) -> None:
        """Finalize an in-progress quiz status so the UI never shimmers forever."""
        if not self.valves.quiz_progress_status or __event_emitter__ is None:
            return
        started = False
        if isinstance(__metadata__, dict):
            started = bool(__metadata__.get("study_mode_quiz_progress_status"))
        if not started:
            return
        try:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": (description or self.valves.quiz_ready_message).strip(),
                        "done": True,
                        "hidden": True,
                        "action": "study_mode.quiz",
                    },
                }
            )
        except Exception:
            pass
        finally:
            if isinstance(__metadata__, dict):
                __metadata__["study_mode_quiz_progress_status"] = False

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> dict:
        """Render a quiz as Rich UI and remove all transport text from the message."""
        if not self.valves.interactive_quiz_ui:
            return body

        assistant = self._assistant_message(body)
        key = self._stream_key(__metadata__)
        state = self._quiz_streams.get(key) if key else None
        buffered = state.get("raw", "") if isinstance(state, dict) else ""

        if not assistant:
            await self._finish_quiz_progress_status(
                __event_emitter__, __metadata__, description="Quiz preparation finished"
            )
            if key:
                self._quiz_streams.pop(key, None)
            return body

        content = assistant.get("content")
        content_text = content if isinstance(content, str) else ""
        extra_parts = []
        for field in ("reasoning", "reasoning_content", "thinking", "analysis"):
            value = assistant.get(field)
            if isinstance(value, str) and value:
                extra_parts.append(value)
        source = "\n".join(part for part in [buffered, *extra_parts, content_text] if part)
        study_meta = __metadata__.get("study_mode", {}) if isinstance(__metadata__, dict) else {}
        allow_unmarked = bool(isinstance(study_meta, dict) and study_meta.get("quiz_request"))
        quiz = self._extract_quiz(source, allow_unmarked=allow_unmarked)

        try:
            if quiz and __event_emitter__ is not None:
                # Persist a clean assistant message and also clear the already-streamed
                # live content. This handles both normal content and provider reasoning UI.
                visible = self._scrub_assistant_transport(
                    assistant, clear_all=self.valves.quiz_embed_only
                )
                await __event_emitter__({"type": "replace", "data": {"content": visible}})

                html = self._render_quiz_embed(quiz)
                await __event_emitter__(
                    {
                        "type": "embeds",
                        "data": {"embeds": [html], "replace": True},
                    }
                )
                await self._finish_quiz_progress_status(
                    __event_emitter__, __metadata__
                )
                if __metadata__ is not None:
                    __metadata__["study_mode_quiz"] = {
                        "rendered": True,
                        "topic": quiz.get("topic"),
                        "question_count": len(quiz.get("questions", [])),
                        "difficulty": quiz.get("difficulty"),
                    }
            elif buffered and __event_emitter__ is not None:
                # The stream was intentionally hidden because the request looked like
                # a quiz, but the model did not produce a valid quiz payload. Restore
                # its response instead of leaving the user with a blank message.
                fallback = self._clean_quiz_transport(buffered) or content_text
                assistant["content"] = fallback
                await __event_emitter__({"type": "replace", "data": {"content": fallback}})
                await self._finish_quiz_progress_status(
                    __event_emitter__, __metadata__, description="Study response ready"
                )
            else:
                # Covers ask_user/setup turns and providers that return no quiz payload.
                await self._finish_quiz_progress_status(
                    __event_emitter__, __metadata__, description="Quiz setup ready"
                )
        finally:
            await self._finish_quiz_progress_status(__event_emitter__, __metadata__, description="Study response ready")
            if key:
                self._quiz_streams.pop(key, None)

        return body

