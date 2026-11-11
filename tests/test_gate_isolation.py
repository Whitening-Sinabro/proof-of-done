"""A broken stage must not discard a finding from a different stage."""
import io
import json
import sys

import pytest
import claim_gate
import inspect_gate
import verify_on_stop as hook


def explode(*args, **kwargs):
    raise ValueError("synthetic stage error")


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(hook, "_runner_enabled", lambda root: True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv(hook.SKIP_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"last_assistant_message": "I fixed it. All tests pass."})))
    monkeypatch.setattr(hook, "_changed_files", lambda root: [])


@pytest.mark.parametrize("broken", ["claim", "inspect", "strict", "suite"])
def test_exception_keeps_other_failure(monkeypatch, tmp_path, capsys, broken):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(claim_gate, "check", explode if broken == "claim" else lambda **kw: "claim finding")
    monkeypatch.setattr(inspect_gate, "check", explode if broken == "inspect" else lambda **kw: "inspect finding")
    monkeypatch.setattr(claim_gate, "detect_test_claim", explode if broken == "strict" else lambda **kw: True)
    monkeypatch.setattr(hook, "_run_test_suite", explode if broken == "suite" else lambda *a, **kw: None)
    hook.main()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["decision"] == "block"
    assert "continue" not in output
    assert ("inspect finding" if broken == "claim" else "claim finding") in output["reason"]
    assert "error" in captured.err


def test_broken_gate_without_findings_fails_open(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(claim_gate, "check", explode)
    monkeypatch.setattr(inspect_gate, "check", lambda **kw: None)
    monkeypatch.setattr(claim_gate, "detect_test_claim", lambda **kw: False)
    hook.main()
    result = capsys.readouterr()
    output = json.loads(result.out)
    assert "decision" not in output
    assert "skipped claim_gate" in output["systemMessage"]
    assert "claim_gate error" in result.err


def test_recursion_guard_prevents_repeated_block(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"stop_hook_active":true}'))
    monkeypatch.setattr(claim_gate, "check", explode)
    hook.main()
    assert not capsys.readouterr().out
