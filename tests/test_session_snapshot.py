"""Regression tests for the shipped session snapshot."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import session_snapshot as snap
import verify_on_stop as hook


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=fixture@example.invalid", "-c", "user.name=Fixture", *args],
                          check=True, capture_output=True, text=True).stdout


def _repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def _age(p: Path, seconds: float = 7200) -> None:
    old = time.time() - seconds
    os.utime(p, (old, old))


def _session(root: Path) -> tuple[Path, dict]:
    """Regression:  session."""
    sdir = Path(tempfile.mkdtemp())
    assert snap.save_if_absent("sess-1", root, hook._changed_files(root) or [], snapshot_dir=sdir)
    return sdir, snap.load("sess-1", root, snapshot_dir=sdir)


def _session_changes(root: Path, baseline: dict) -> list[str]:
    return snap.changed_since(root, hook._changed_files(root) or [], baseline)




def test_pre_existing_changes_are_excluded():
    root = _repo({"a.py": "A = 1\n"})
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    (root / "draft.py").write_text("x = 1\n", encoding="utf-8")
    _, base = _session(root)
    assert _session_changes(root, base) == []


def test_in_place_edit_during_session_is_included():
    root = _repo({"a.py": "A = 1\n"})
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    _, base = _session(root)
    time.sleep(0.05)
    (root / "a.py").write_text("A = 3\n", encoding="utf-8")
    assert _session_changes(root, base) == ["a.py"]




def test_git_mv_keeps_old_mtime_but_is_included():
    root = _repo({"a.py": "A = 1\n"})
    _age(root / "a.py")
    _, base = _session(root)
    _git(root, "mv", "a.py", "b.py")
    assert (root / "b.py").stat().st_mtime < time.time() - 3600
    assert any("b.py" in e for e in _session_changes(root, base))


def test_restore_from_backup_with_copy2_is_included():
    root = _repo({"a.py": "A = 1\n"})
    backup = root.parent / (root.name + "-a.py.bak")
    backup.write_text("A = broken(\n", encoding="utf-8")
    _age(backup)
    _, base = _session(root)
    shutil.copy2(backup, root / "a.py")
    assert _session_changes(root, base) == ["a.py"]


def test_restore_over_pre_existing_change_is_included():
    root = _repo({"a.py": "A = 1\n"})
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    backup = root.parent / (root.name + "-a.py.bak")
    backup.write_text("A = 9\n", encoding="utf-8")
    _age(backup)
    _, base = _session(root)
    shutil.copy2(backup, root / "a.py")
    assert _session_changes(root, base) == ["a.py"]


def test_tar_extraction_with_preserved_mtime_is_included():
    root = _repo({"a.py": "A = 1\n"})
    member = root.parent / (root.name + "-member.py")
    member.write_text("A = broken(\n", encoding="utf-8")
    _age(member)
    archive = root.parent / (root.name + ".tar")
    with tarfile.open(archive, "w") as t:
        t.add(member, arcname="a.py")
    _, base = _session(root)
    with tarfile.open(archive) as t:
        t.extractall(root, filter="data")
    assert _session_changes(root, base) == ["a.py"]


def test_git_reset_that_reveals_a_change_is_included():
    root = _repo({"a.py": "A = 1\n"})
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    _git(root, "commit", "-qam", "pre-session change")
    _, base = _session(root)
    _git(root, "reset", "-q", "HEAD~1")
    assert _session_changes(root, base) == ["a.py"]


def test_moving_an_untracked_draft_into_place_is_included():
    root = _repo({"a.py": "A = 1\n", "tests/test_a.py": "def test_a():\n    assert True\n"})
    draft = root.parent / (root.name + "-test_draft.py")
    draft.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    _age(draft)
    _, base = _session(root)
    os.replace(draft, root / "tests" / "test_draft.py")
    assert _session_changes(root, base) == ["tests/test_draft.py"]




def test_first_session_start_wins_resume_does_not_move_the_baseline():
    root = _repo({"a.py": "A = 1\n"})
    sdir = Path(tempfile.mkdtemp())
    assert snap.save_if_absent("s", root, [], snapshot_dir=sdir)
    (root / "a.py").write_text("A = 2\n", encoding="utf-8")
    assert not snap.save_if_absent("s", root, hook._changed_files(root) or [], snapshot_dir=sdir)  # resume/compact
    assert snap.changed_since(root, hook._changed_files(root) or [], snap.load("s", root, snapshot_dir=sdir)) == ["a.py"]


def test_missing_corrupt_foreign_or_unsafe_snapshots_load_as_none():
    root = _repo({"a.py": "A = 1\n"})
    other = _repo({"b.py": "B = 1\n"})
    sdir = Path(tempfile.mkdtemp())
    assert snap.load("absent", root, snapshot_dir=sdir) is None
    (sdir / "bad.json").write_text("{not json", encoding="utf-8")
    assert snap.load("bad", root, snapshot_dir=sdir) is None
    snap.save_if_absent("elsewhere", other, [], snapshot_dir=sdir)
    assert snap.load("elsewhere", root, snapshot_dir=sdir) is None
    assert not snap.save_if_absent("../escape", root, [], snapshot_dir=sdir)
    assert snap.load("../escape", root, snapshot_dir=sdir) is None
    assert not snap.save_if_absent(None, root, [], snapshot_dir=sdir)


def test_prune_removes_only_expired_snapshots():
    sdir = Path(tempfile.mkdtemp())
    old, new = sdir / "old.json", sdir / "new.json"
    snap.save_if_absent("old", sdir, [], sdir)
    snap.save_if_absent("new", sdir, [], sdir)
    _age(old, snap.RETENTION_SECONDS + 60)
    snap.prune(snapshot_dir=sdir)
    assert not old.exists() and new.exists()




def _run_main(monkeypatch, root: Path, sdir: Path, hook_input: dict) -> list:
    calls: list = []
    monkeypatch.setattr(hook, "_runner_enabled", lambda root: True)
    hook_input = {**hook_input, "last_assistant_message": "I updated the module."}
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv(hook.SKIP_ENV, raising=False)
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", sdir)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    monkeypatch.setattr(hook, "_run_claim_gate", lambda *a: None)
    monkeypatch.setattr(hook, "_run_inspect_gate", lambda *a: None)
    monkeypatch.setattr(hook, "_detect_strict", lambda h: False)
    monkeypatch.setattr(hook, "_run_test_suite", lambda r, c, f, s, strict=False: calls.append((list(c), strict)))
    hook.main()
    return calls


def _start(monkeypatch, root: Path, sdir: Path, session_id: str) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv(hook.SKIP_ENV, raising=False)
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", sdir)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": session_id, "source": "startup"})))
    monkeypatch.setattr(hook, "_runner_enabled", lambda root: True)
    hook.session_start()


def test_read_only_session_in_dirty_tree_runs_no_tests(monkeypatch):
    """Regression: read only session in dirty tree runs no tests."""
    root = _repo({f"pkg/m{i}.py": f"V = {i}\n" for i in range(30)})
    for i in range(30):
        (root / f"pkg/m{i}.py").write_text(f"V = {i + 100}\n", encoding="utf-8")
    sdir = Path(tempfile.mkdtemp())
    _start(monkeypatch, root, sdir, "ro-session")
    assert _run_main(monkeypatch, root, sdir, {"session_id": "ro-session"}) == []


def test_session_edit_is_verified_and_pre_existing_change_is_not(monkeypatch):
    root = _repo({"old.py": "A = 1\n", "edited.py": "B = 1\n"})
    (root / "old.py").write_text("A = 2\n", encoding="utf-8")
    sdir = Path(tempfile.mkdtemp())
    _start(monkeypatch, root, sdir, "edit-session")
    (root / "edited.py").write_text("B = 2\n", encoding="utf-8")
    assert _run_main(monkeypatch, root, sdir, {"session_id": "edit-session"}) == [(["edited.py"], False)]


def test_without_snapshot_the_whole_working_tree_is_checked(monkeypatch):
    root = _repo({"old.py": "A = 1\n"})
    (root / "old.py").write_text("A = 2\n", encoding="utf-8")
    sdir = Path(tempfile.mkdtemp())
    assert _run_main(monkeypatch, root, sdir, {"session_id": "never-started"}) == [(["old.py"], False)]
    assert _run_main(monkeypatch, root, sdir, {}) == [(["old.py"], False)]
