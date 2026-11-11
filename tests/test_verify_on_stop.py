"""Regression tests for the shipped verify on stop."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify_on_stop as hook


def _tmp_root(monkeypatch, files: dict[str, str]) -> Path:
    """Regression:  tmp root."""
    root = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if content == "DIR":
            p.mkdir(exist_ok=True)
        else:
            p.write_text(content, encoding="utf-8")
    return root


class _RunRecorder:
    def __init__(self, rc: int = 0, out: str = ""):
        self.rc = rc
        self.out = out
        self.calls: list[tuple] = []

    def __call__(self, cmd, cwd, use_shell=False):
        self.calls.append((cmd, str(cwd), use_shell))
        return self.rc, self.out


# === _pytest_strict_targets ===

def test_strict_targets_finds_python_tests(monkeypatch):
    root = _tmp_root(monkeypatch, {"python/tests": "DIR"})
    targets = hook._pytest_strict_targets(root)
    assert len(targets) == 1
    assert targets[0].endswith("python/tests") or targets[0].endswith("python\\tests")


def test_strict_targets_empty_when_no_dirs(monkeypatch):
    root = _tmp_root(monkeypatch, {})
    assert hook._pytest_strict_targets(root) == []


def test_strict_targets_finds_multiple(monkeypatch):
    root = _tmp_root(monkeypatch, {"tests": "DIR", "python/tests": "DIR"})
    targets = hook._pytest_strict_targets(root)
    assert len(targets) == 2




def test_run_pytest_strict_ignores_changed_py(monkeypatch):
    root = _tmp_root(monkeypatch, {"python/tests": "DIR"})
    rec = _RunRecorder(rc=0, out="10 passed")
    monkeypatch.setattr(hook, "_run", rec)

    failed, _ = hook._run_pytest(root, changed_py=["some_source.py"], config={}, strict=True)

    assert failed is False
    assert len(rec.calls) == 1
    cmd = rec.calls[0][0]

    assert any(str(root / "python/tests") in str(c) or str(root / "python\\tests") in str(c) for c in cmd)
    assert not any("some_source" in str(c) for c in cmd)


def test_run_pytest_fast_uses_changed_files_targeting(monkeypatch):
    """Regression: run pytest fast uses changed files targeting."""
    root = _tmp_root(monkeypatch, {"python/tests": "DIR"})
    rec = _RunRecorder(rc=0, out="")
    monkeypatch.setattr(hook, "_run", rec)


    hook._run_pytest(root, changed_py=["foo.py"], config={}, strict=False)

    assert len(rec.calls) == 1

    cmd = rec.calls[0][0]
    assert "python" in cmd[0].lower() or "pytest" in " ".join(str(c) for c in cmd)


# === test_cmd / test_cmd_strict override ===

def test_run_pytest_strict_prefers_test_cmd_strict(monkeypatch):
    root = _tmp_root(monkeypatch, {})
    rec = _RunRecorder(rc=0, out="")
    monkeypatch.setattr(hook, "_run", rec)

    config = {"test_cmd": "pytest -q python/tests", "test_cmd_strict": "pytest -q --cov"}
    hook._run_pytest(root, changed_py=[], config=config, strict=True)

    assert rec.calls[0][0] == ["pytest", "-q", "--cov"]
    assert rec.calls[0][2] is False  # No shell evaluation.


def test_run_pytest_strict_falls_back_to_test_cmd(monkeypatch):
    """Regression: run pytest strict falls back to test cmd."""
    root = _tmp_root(monkeypatch, {})
    rec = _RunRecorder(rc=0, out="")
    monkeypatch.setattr(hook, "_run", rec)

    config = {"test_cmd": "pytest -q python/tests"}
    hook._run_pytest(root, changed_py=[], config=config, strict=True)

    assert rec.calls[0][0] == ["pytest", "-q", "python/tests"]


def test_run_pytest_fast_uses_test_cmd(monkeypatch):
    """Regression: run pytest fast uses test cmd."""
    root = _tmp_root(monkeypatch, {})
    rec = _RunRecorder(rc=0, out="")
    monkeypatch.setattr(hook, "_run", rec)

    config = {"test_cmd": "pytest -q python/tests", "test_cmd_strict": "pytest -q --cov"}
    hook._run_pytest(root, changed_py=[], config=config, strict=False)

    assert rec.calls[0][0] == ["pytest", "-q", "python/tests"]


# === _run_test_suite strict=True with no changed files ===

def test_run_test_suite_strict_runs_pytest_without_changed_py(monkeypatch):
    root = _tmp_root(monkeypatch, {"python/tests": "DIR"})
    rec = _RunRecorder(rc=0, out="")
    monkeypatch.setattr(hook, "_run", rec)

    fails: list[str] = []
    sections: list[str] = []
    hook._run_test_suite(root, changed=[], fails=fails, sections=sections, strict=True)


    assert fails == []
    assert len(rec.calls) >= 1


def test_run_test_suite_fast_skips_pytest_without_changed_py(monkeypatch):
    """Regression: run test suite fast skips pytest without changed py."""
    root = _tmp_root(monkeypatch, {"python/tests": "DIR"})
    rec = _RunRecorder(rc=0, out="")
    monkeypatch.setattr(hook, "_run", rec)

    hook._run_test_suite(root, changed=[], fails=[], sections=[], strict=False)

    assert rec.calls == []


def test_run_test_suite_strict_pytest_failure_labels_strict(monkeypatch):
    """Regression: run test suite strict pytest failure labels strict."""
    root = _tmp_root(monkeypatch, {"python/tests": "DIR"})
    rec = _RunRecorder(rc=1, out="1 failed")
    monkeypatch.setattr(hook, "_run", rec)

    fails: list[str] = []
    sections: list[str] = []
    hook._run_test_suite(root, changed=[], fails=fails, sections=sections, strict=True)

    assert "pytest(strict)" in fails
    assert any("pytest(strict)" in s for s in sections)
