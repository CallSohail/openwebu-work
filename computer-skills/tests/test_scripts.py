"""Tests for the computer-skills helper scripts. Run: python -m pytest computer-skills/tests -q"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parent / "skills"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SKILLS / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scratch = load("scratch", "workspace-hygiene/scripts/scratch.py")
finder = load("find_files", "file-finder/scripts/find_files.py")


def test_scratch_lifecycle_keeps_deliverable_and_removes_helpers(tmp_path, capsys):
    root = tmp_path / "work"
    root.mkdir()
    user_file = root / "existing.xlsx"
    user_file.write_text("user data")

    scratch.main(["--root", str(root), "start", "merge exports"])
    task_dir = Path(capsys.readouterr().out.strip())
    assert task_dir.is_dir() and task_dir.parent.name == ".scratch"

    helper = task_dir / "merge.py"
    helper.write_text("print('helper')")
    result = task_dir / "merged.xlsx"
    result.write_text("result")
    stray = root / "debug.log"
    time.sleep(0.05)
    stray.write_text("temp log created by the task")

    scratch.main(["--root", str(root), "track", str(stray)])
    scratch.main(["--root", str(root), "keep", str(result)])
    scratch.main(["--root", str(root), "finish"])

    assert (root / "outputs" / "merged.xlsx").read_text() == "result"
    assert not helper.exists() and not stray.exists()
    assert not (root / ".scratch").exists()
    assert user_file.read_text() == "user data"


def test_track_refuses_files_older_than_task(tmp_path, capsys):
    root = tmp_path / "work"
    root.mkdir()
    old = root / "thesis.docx"
    old.write_text("precious")
    past = time.time() - 3600
    os.utime(old, (past, past))
    if hasattr(os, "utime"):
        pass
    scratch.main(["--root", str(root), "start", "t"])
    capsys.readouterr()
    scratch.main(["--root", str(root), "track", str(old)])
    out = capsys.readouterr().out
    # On Linux ctime cannot be backdated; created_at uses min(mtime, ctime) so the old mtime wins.
    assert "refused" in out
    scratch.main(["--root", str(root), "finish"])
    assert old.read_text() == "precious"


def test_finish_dry_run_changes_nothing(tmp_path, capsys):
    root = tmp_path / "w"
    root.mkdir()
    scratch.main(["--root", str(root), "start", "x"])
    task_dir = Path(capsys.readouterr().out.strip())
    (task_dir / "a.py").write_text("x")
    scratch.main(["--root", str(root), "finish", "--dry-run"])
    assert (task_dir / "a.py").exists()
    assert "a.py" in capsys.readouterr().out


def test_start_refuses_second_active_task(tmp_path, capsys):
    root = tmp_path / "w"
    root.mkdir()
    scratch.main(["--root", str(root), "start", "one"])
    try:
        scratch.main(["--root", str(root), "start", "two"])
    except SystemExit as e:
        assert "already active" in str(e)
    else:
        raise AssertionError("second start should fail")


def test_finder_hidden_dupes_temp(tmp_path, capsys):
    (tmp_path / ".config").mkdir()
    (tmp_path / "a.txt").write_text("same")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("same")
    (tmp_path / "~$report.docx").write_text("")
    (tmp_path / "notes - Copy.txt").write_text("x")

    finder.main(["hidden", str(tmp_path), "--json"])
    hidden = json.loads(capsys.readouterr().out)
    assert any(r["path"].endswith(".config") for r in hidden)

    finder.main(["dupes", str(tmp_path), "--min-kb", "0", "--json"])
    dupes = json.loads(capsys.readouterr().out)
    assert {Path(r["path"]).name for r in dupes} == {"a.txt", "b.txt"}

    finder.main(["temp", str(tmp_path), "--json"])
    temp = {Path(r["path"]).name: r["reason"] for r in json.loads(capsys.readouterr().out)}
    assert temp["~$report.docx"] == "temp/lock"
    assert "notes - Copy.txt" in temp


def test_skills_have_valid_frontmatter():
    sys.path.insert(0, str(HERE.parent))
    from validate_skills import validate  # noqa: E402
    problems = validate(SKILLS)
    assert problems == [], problems
