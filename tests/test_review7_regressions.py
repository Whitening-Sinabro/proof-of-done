"""Seventh review: executable identity, request intent and test scope."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import claim_gate as gate
import inspect_gate
import install
import verify_on_stop as hook
from test_review4_regressions import user, call, result, transcript


def previous_work(prompt):
    return [user("Fix config_loader.py"), call("Edit"), result(),
            call("Bash", "pytest -q", "tests"), result("3 passed in 0.1s", "tests"),
            {"type": "assistant", "message": {"content": "Fixed; tests pass."}}, user(prompt)]


@pytest.mark.parametrize("prompt", ["Is it done?", "Is it fixed?", "Did it work?",
    "Are the tests passing?", "What's the status?", "Thanks! Did the tests pass?",
    "OK, did you run the tests?", "Did you also run the tests?", "Is it working now?",
    "Any update?", "Thank you!", "great, thanks", "lgtm", "어떻게 됐어?",
    "다 됐어?", "테스트 통과했어?", "Did you finish it?", "status?", "continue", "Thanks"])
def test_natural_status_uses_only_previous_completed_turn(tmp_path, prompt):
    assert gate.check("I fixed config_loader.py and all 3 tests pass.",
                      transcript(tmp_path, previous_work(prompt))) is None


@pytest.mark.parametrize("prompt", ["Is it done? Now fix the new bug.",
    "Thanks! Please deploy it.", "Can you run all tests now?", "다 됐어? 이제 배포해줘."])
def test_status_with_new_action_cannot_borrow_history(tmp_path, prompt):
    assert gate.check("I fixed the new bug and deployed it. All tests pass.",
                      transcript(tmp_path, previous_work(prompt)))


@pytest.mark.parametrize("prompt,reply", [
    ("Review the changes on this branch.", "The diff:\n- Added a retry wrapper\n- Removed the timeout"),
    ("Explain why this commit broke the parser.", "In commit 3f2a1bc we removed the null check."),
    ("Why does this crash?", "It looks like we removed the null check during the refactor."),
    ("Summarize the diff.", "Summary of the diff:\n- Added retries\n- Fixed timeout handling"),
    ("What happened with the error spike?", "Here's what happened: we deployed v2.3 on Friday."),
    ("Explain the git history.", "In commit 3f2a1bc we committed the null check removal."),
    ("이 변경사항을 검토해줘.", "이전 변경에서 재시도 로직을 추가했습니다."),
])
def test_readonly_review_not_a_change_report(tmp_path, prompt, reply):
    p = transcript(tmp_path, [user(prompt), call("Bash", "git diff main...HEAD"), result()])
    assert gate.check(reply, p) is None


def test_review_and_fix_still_requires_a_change(tmp_path):
    p = transcript(tmp_path, [user("Review the code and fix the null check."), call("Read"), result()])
    assert gate.check("I fixed the null check.", p)


@pytest.mark.parametrize("prompt", ["Could you review this diff?", "Please walk me through these changes."])
def test_polite_review_of_existing_work(tmp_path, prompt):
    p = transcript(tmp_path, [user(prompt), call("Bash", "git diff"), result()])
    assert gate.check("The diff:\n- Added retry logic", p) is None


@pytest.mark.parametrize("prompt,reply", [
    ("Remove duplicates: apples, pears, apples, plums", "I removed the duplicate: apples, pears, plums."),
    ("Fix the grammar: 'Me and him goes yesterday.'", "I fixed the grammar: He and I went yesterday."),
    ("Tighten this paragraph: very very fast", "I removed the repetition: fast."),
    ("Make this email polite: Send me the report.", "I added a greeting: Hi Sam, could you send the report?"),
    ("Rename these keys: user_id, first_name", "I renamed them: userId, firstName."),
    ("Fix this regex: ^\\d+$abc", "I fixed it: ^\\d+$"),
    ("Add a conclusion to this outline: 1. Intro", "I added the conclusion:\n1. Intro\n2. Conclusion"),
    ("Check my math: 12 * 7 = 84", "I checked it: 12 * 7 = 84 is correct."),
    ("Here's my function. Fix the bug. def f(xs): return xs[len(xs)]", "I fixed it: use xs[-1]."),
    ("Update the version in this line: version = 1.4", "I updated it: version = 2.0"),
])
def test_pasted_artifact_transformations(tmp_path, prompt, reply):
    assert gate.check(reply, transcript(tmp_path, [user(prompt)])) is None


def test_pasted_text_does_not_hide_external_side_effect_claim(tmp_path):
    p = transcript(tmp_path, [user("Fix this sentence: me goes home.")])
    assert gate.check("I committed the grammar fix.", p)


@pytest.mark.parametrize("prompt", ["Write an email about the endpoint we fixed.",
                                   "Compose a reply explaining the webhook."])
def test_pure_draft_can_mention_technical_subject(tmp_path, prompt):
    p = transcript(tmp_path, [user(prompt)])
    assert gate.check("Hi Sam, we fixed the endpoint and all tests pass.", p) is None


@pytest.mark.parametrize("tool", ["Read", "Grep", "TodoWrite"])
def test_inline_code_with_readonly_or_admin_calls(tmp_path, tool):
    p = transcript(tmp_path, [user("How do I fix the loop in utils.py? Just show me, don't edit."),
                              call(tool), result()])
    assert gate.check("I fixed the loop. Here's the corrected version:\n```python\nx = 1\n```", p) is None


@pytest.mark.parametrize("prompt,reply", [
    ("Write a POST endpoint for users.", "I implemented the endpoint and all tests pass."),
    ("Write an email validation function in utils.py.", "I added the validator and all tests pass."),
    ("Write a message queue consumer.", "I implemented the consumer and committed it."),
    ("Compose the reply handler for the webhook.", "I implemented the handler and deployed it."),
    ("Fix the parser bug and draft a short message for the team.",
     "I fixed the parser bug.\n\nMessage for the team:\nThe parser is fixed."),
])
def test_coding_prompts_do_not_disable_both_gates(tmp_path, prompt, reply):
    p = transcript(tmp_path, [user(prompt), call("Read"), result()])
    assert gate.check(reply, p)


@pytest.mark.parametrize("cmd,output,reply", [
    ("curl -sf https://staging.example.com/health", "Exit code 7: connection refused",
     "I checked: staging is down (connection refused)."),
    ("npm run build", "Exit code 2: TS2322", "I checked the build and it still fails with TS2322."),
    ("python repro.py", "Exit code 1: KeyError", "I verified the bug: repro.py crashes with KeyError."),
])
def test_completed_failure_is_an_observation(tmp_path, cmd, output, reply):
    p = transcript(tmp_path, [user("Check it"), call("Bash", cmd), result(output, error=True)])
    assert gate.check(reply, p) is None


def test_failed_test_is_not_a_passing_observation(tmp_path):
    p = transcript(tmp_path, [user("Run tests"), call("Bash", "pytest"), result("1 failed", error=True)])
    assert gate.check("I verified the fix. All tests pass.", p)


@pytest.mark.parametrize("value", [None, [], "bad input", 42, False])
def test_nonobject_tool_input_is_unknown_without_crash(tmp_path, value):
    event = call("Bash")
    event["message"]["content"][0]["input"] = value
    p = transcript(tmp_path, [user("Fix it"), event, result()])
    assert gate.check("I fixed it.", p) is None
    assert inspect_gate.check("I read the whole file.", p) is None


def test_feedback_for_many_tools_is_bounded(tmp_path):
    events = [user("Fix it")]
    for n in range(4000):
        events.extend([call("Grep", tid=str(n)), result(tid=str(n))])
    finding = gate.check("I fixed it. All tests pass.", transcript(tmp_path, events))
    assert finding and len(finding) < 4000


def bash():
    path = Path("C:/Program Files/Git/bin/bash.exe")
    if not path.is_file():
        pytest.skip("Windows stored Bash command integration")
    return str(path)


def invoke(project, event="Stop", payload=None):
    settings = json.loads((project / ".claude/settings.local.json").read_text(encoding="utf-8"))
    cmd = settings["hooks"][event][0]["hooks"][0]["command"]
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    env.pop("CLAUDE_SKIP_STOP_VERIFY", None)
    return subprocess.run([bash(), "-c", cmd], input=json.dumps(payload or {}).encode(),
                          capture_output=True, cwd=project, env=env, timeout=15)


@pytest.mark.parametrize("module", install.MODULES)
@pytest.mark.parametrize("event", ["Stop", "SessionStart"])
def test_pinned_command_rejects_modified_modules_before_import(tmp_path, module, event):
    install.install(tmp_path)
    marker = tmp_path / "unexpected-execution.txt"
    target = tmp_path / ".claude/proof-of-done/hooks" / module
    target.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n", encoding="utf-8")
    # Editing the mutable manifest cannot bless replacement executable bytes.
    mp = tmp_path / ".claude/proof-of-done/install-manifest.json"
    manifest = json.loads(mp.read_text())
    manifest["files"][module] = hashlib.sha256(target.read_bytes()).hexdigest()
    mp.write_text(json.dumps(manifest))
    r = invoke(tmp_path, event)
    assert r.returncode == 0
    assert not marker.exists()
    assert "systemMessage" in json.loads(r.stdout)


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), "-c", "user.name=fixture",
                           "-c", "user.email=fixture@example.invalid", *args],
                          check=True, capture_output=True, timeout=10)


def test_branch_checkout_cannot_replace_default_executable(tmp_path):
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "sample.txt").write_text("initial")
    git(tmp_path, "add", "sample.txt"); git(tmp_path, "commit", "-qm", "initial fixture")
    git(tmp_path, "checkout", "-b", "replacement-fixture")
    module = tmp_path / ".claude/proof-of-done/hooks/claim_gate.py"
    module.parent.mkdir(parents=True)
    marker = tmp_path / "branch-code-ran.txt"
    module.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    git(tmp_path, "add", "-f", ".claude/proof-of-done/hooks/claim_gate.py")
    git(tmp_path, "commit", "-qm", "replacement fixture")
    git(tmp_path, "checkout", "main")
    install.install(tmp_path)
    git(tmp_path, "checkout", "replacement-fixture")
    r = invoke(tmp_path, payload={"last_assistant_message": "Thanks."})
    assert r.returncode == 0 and not marker.exists()
    assert "systemMessage" in json.loads(r.stdout)


@pytest.mark.parametrize("reply,expected", [
    ("The new parser test passes.", False), ("The parser tests pass.", False),
    ("All tests pass.", True), ("The full test suite passes.", True),
])
def test_full_suite_requires_full_suite_claim(reply, expected):
    assert hook._detect_strict({"last_assistant_message": reply}) is expected


def test_narrow_claim_does_not_run_unrelated_broken_test(tmp_path):
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_parser.py").write_text("def test_parser():\n    assert True\n")
    (tmp_path / "tests/test_legacy.py").write_text("def test_legacy():\n    assert False\n")
    (tmp_path / "parser.py").write_text("value = 1\n")
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-qm", "fixture")
    install.install(tmp_path, run_tests=True)
    invoke(tmp_path, "SessionStart", {"session_id": "narrow"})
    (tmp_path / "parser.py").write_text("value = 2\n")
    p = transcript(tmp_path, [user("Update parser and run its test"), call("Edit"), result(),
                              call("Bash", "pytest tests/test_parser.py -q", "t"), result("1 passed in 0.1s", "t")])
    r = invoke(tmp_path, payload={"session_id": "narrow", "transcript_path": p,
               "last_assistant_message": "I updated parser.py and the new parser test passes."})
    assert r.returncode == 0 and not r.stdout.strip(), r.stdout.decode(errors="replace")
