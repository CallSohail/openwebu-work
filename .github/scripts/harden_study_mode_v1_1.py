from pathlib import Path

p=Path('functions/filters/study-mode/study_mode.py')
s=p.read_text(encoding='utf-8')

def one(old,new,label):
    global s
    count=s.count(old)
    if count!=1:
        raise SystemExit(f'{label}: expected one match, got {count}')
    s=s.replace(old,new,1)

one(
    '    def _extract_quiz(self, content: str) -> Optional[dict]:\n',
    '    def _extract_quiz(self, content: str, *, allow_unmarked: bool = True) -> Optional[dict]:\n',
    'parser signature',
)
one(
    '        raw=m.group(1).strip() if m else (self._balanced_json_object(content) if self.valves.quiz_schema_tolerance=="compatible" else None)\n',
    '        raw=m.group(1).strip() if m else (self._balanced_json_object(content) if allow_unmarked and self.valves.quiz_schema_tolerance=="compatible" else None)\n',
    'unmarked guard',
)
one(
    '                "answer_policy": user_valves.answer_policy,\n',
    '                "answer_policy": user_valves.answer_policy,\n                "quiz_request": is_quiz_request,\n',
    'quiz metadata',
)
one(
    '        quiz = self._extract_quiz(source)\n',
    '        study_meta = __metadata__.get("study_mode", {}) if isinstance(__metadata__, dict) else {}\n        allow_unmarked = bool(isinstance(study_meta, dict) and study_meta.get("quiz_request"))\n        quiz = self._extract_quiz(source, allow_unmarked=allow_unmarked)\n',
    'outlet guard',
)

p.write_text(s,encoding='utf-8')
