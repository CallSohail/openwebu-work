import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("study_mode.py")
spec = importlib.util.spec_from_file_location("study_mode", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Filter = module.Filter


class StudyModeTests(unittest.TestCase):
    def setUp(self):
        self.f = Filter()
        self.u = self.f.UserValves()

    def test_multilingual_quiz_detection(self):
        prompts = [
            "Quiz me on Python OOP",
            "Fais-moi un quiz sur Python",
            "Hazme un quiz sobre SQL",
            "Teste mich zu Netzwerken",
            "Mettimi alla prova su Linux",
            "Teste me sobre bancos de dados",
            "Overhoor me over geschiedenis",
            "مجھے پائتھن پر ٹیسٹ کرو",
            "اختبار اختيار من متعدد عن بايثون",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertTrue(self.f._looks_like_quiz_request([{"role": "user", "content": prompt}], self.u, True))

    def test_quiz_style_does_not_need_keyword(self):
        u = self.f.UserValves(style="quiz")
        self.assertTrue(self.f._looks_like_quiz_request([{"role": "user", "content": "Python OOP"}], u, False))

    def test_compatible_parser_accepts_string_options_and_answer_text(self):
        raw = '<!-- STUDY_MODE_QUIZ_START {"title":"T","topic":"X","questions":[{"question":"2+2?","options":["3","4"],"answer":"4"}]} STUDY_MODE_QUIZ_END -->'
        quiz = self.f._extract_quiz(raw)
        self.assertIsNotNone(quiz)
        self.assertEqual(quiz["questions"][0]["correct"], "B")
        self.assertTrue(quiz["questions"][0]["explanation"])

    def test_compatible_parser_accepts_unmarked_json(self):
        raw = '{"questions":[{"text":"Capital of France?","options":["Paris","Rome"],"correct_answer":1,}],}'
        quiz = self.f._extract_quiz(raw)
        self.assertIsNotNone(quiz)
        self.assertEqual(quiz["title"], "Study quiz")
        self.assertEqual(quiz["questions"][0]["correct"], "A")

    def test_compatible_parser_repairs_latex_backslashes(self):
        raw = r'''<!-- STUDY_MODE_QUIZ_START
{"title":"Math","topic":"Algebra","questions":[{"question":"Solve \\(x^2=4\\)","options":[{"id":"A","text":"\\(x=2\\)"},{"id":"B","text":"\\(x=\\pm2\\)"}],"correct":"B","explanation":"Both roots satisfy the equation.","hint":"Think about both square roots."}]}
STUDY_MODE_QUIZ_END -->'''
        # Simulate a local model that emits invalid JSON with single LaTeX slashes.
        raw = raw.replace(r"\\(", r"\(").replace(r"\\)", r"\)").replace(r"\\pm", r"\pm")
        quiz = self.f._extract_quiz(raw)
        self.assertIsNotNone(quiz)
        self.assertIn(r"\(", quiz["questions"][0]["question"])

    def test_strict_mode_rejects_compatible_variant(self):
        self.f.valves.quiz_schema_tolerance = "strict"
        raw = '<!-- STUDY_MODE_QUIZ_START {"questions":[{"question":"2+2?","options":["3","4"],"answer":"4"}]} STUDY_MODE_QUIZ_END -->'
        self.assertIsNone(self.f._extract_quiz(raw))

    def test_safe_json_escapes_script_sensitive_characters(self):
        safe = self.f._safe_json_for_script({"x": "<>&\u2028\u2029"})
        self.assertNotIn("<", safe)
        self.assertNotIn(">", safe)
        self.assertNotIn("&", safe)
        self.assertIn(r"\u2028", safe)
        self.assertIn(r"\u2029", safe)

    def test_prompt_merge_preserves_existing_system_prompt(self):
        body = {"messages": [{"role": "system", "content": "BASE RULE"}, {"role": "user", "content": "Teach me Python"}]}
        self.f._inject_prompt(body, "[STUDY_MODE_V1_0]\nOVERLAY")
        self.assertTrue(body["messages"][0]["content"].startswith("BASE RULE"))
        self.assertIn("OVERLAY", body["messages"][0]["content"])
        self.assertEqual(len([m for m in body["messages"] if m["role"] == "system"]), 1)

    def test_separate_system_mode_preserves_base(self):
        self.f.valves.system_prompt_integration = "separate"
        body = {"messages": [{"role": "system", "content": "BASE RULE"}, {"role": "user", "content": "Teach me Python"}]}
        self.f._inject_prompt(body, "[STUDY_MODE_V1_0]\nOVERLAY")
        systems = [m["content"] for m in body["messages"] if m["role"] == "system"]
        self.assertEqual(systems, ["BASE RULE", "[STUDY_MODE_V1_0]\nOVERLAY"])

    def test_quiz_feature_addon_contains_requested_controls(self):
        self.f.valves.quiz_mathjax = True
        js = self.f._quiz_feature_addon_js()
        for token in ("mathjax@3.2.2", "fullscreenBtn", "exportBtn", "keydown", "ArrowLeft", "ArrowRight"):
            self.assertIn(token, js)
        self.assertNotIn("eval(", js)
        self.assertNotIn("new Function", js)

    def test_rendered_html_avoids_dynamic_innerhtml_clears(self):
        quiz = {"title":"T","topic":"X","difficulty":"easy","questions":[{"id":"q1","question":"Q?","options":[{"id":"A","text":"A"},{"id":"B","text":"B"}],"correct":"A","explanation":"E","hint":"H"}]}
        html = self.f._render_quiz_embed(quiz)
        self.assertNotIn("innerHTML=''", html)
        self.assertIn("replaceChildren()", html)

    def test_status_cleanup_is_idempotent(self):
        calls = []
        async def emitter(event):
            calls.append(event)
        metadata = {"study_mode_quiz_progress_status": True}
        asyncio.run(self.f._finish_quiz_progress_status(emitter, metadata, description="done"))
        self.assertFalse(metadata["study_mode_quiz_progress_status"])
        self.assertTrue(calls and calls[-1]["data"]["done"])


if __name__ == "__main__":
    unittest.main()
