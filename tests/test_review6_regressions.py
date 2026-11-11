"""Sixth review: request scope, realistic histories, output formats and failures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import claim_gate as gate
import inspect_gate
import install
from test_review4_regressions import user, call, result, transcript
from test_review_regressions import trace


@pytest.mark.parametrize("lead", ["Here's a reply you can send to the client:", "Here's the email:",
    "Here's a Slack update you could post:", "Here is your standup:", "Message for the customer:",
    "의뢰인께 보낼 메시지입니다."])
def test_drafted_artifact_not_external_action(tmp_path, lead):
    p = transcript(tmp_path, [user("Write a reply to the client saying we fixed the login bug")])
    assert gate.check(lead + "\n\nWe fixed the login bug and deployed it. All tests pass.", p) is None


@pytest.mark.parametrize("text", ["I added comments to the code below:", "I added type hints and a docstring:",
    "I fixed the off-by-one error. Here's the corrected version:", "I updated the query to use a JOIN:",
    "I renamed the variables for clarity:", "아래 코드에 주석을 추가했습니다:"])
def test_inline_code_revision_not_disk_change(tmp_path, text):
    p = transcript(tmp_path, [user("Fix this snippet: ```python\nx = 0\n```")])
    assert gate.check(text + "\n```python\nx = 1\n```", p) is None


def test_inline_table_revision(tmp_path):
    assert gate.check("| a | b |\n|---|---|\n| 1 | 2 |\n\nI updated the table above with the corrected numbers.",
                      trace(tmp_path)) is None


@pytest.mark.parametrize("text", ["I won't say I fixed it, because I haven't touched the file.",
    "I couldn't confirm that I fixed it; the reproduction script needs credentials.",
    "I wouldn't say we fixed the root cause yet.", "After I've updated the schema, we can backfill the column.",
    "As soon as I've fixed the parser, I'll run the full suite.",
    "The test passes because it mocks compute entirely.", "The test passes if the parser returns None.",
    "These tests pass a fake clock into the scheduler.", "Both tests pass the config as a dict.",
    "The CI run shows all tests passed, so the red badge comes from the lint job.",
    "According to the CI log, all tests passed; the failure is in the deploy step."])
def test_plans_refusals_and_code_descriptions(tmp_path, text):
    assert gate.check(text, trace(tmp_path, "Read")) is None


@pytest.mark.parametrize("text", ["I checked the traceback you sent: the KeyError comes from settings.",
    "I checked your code and the bug is on line 3.", "I've checked the numbers: 12 times 3 is 36."])
def test_user_material_and_reasoning_need_no_tool(tmp_path, text):
    assert gate.check(text, trace(tmp_path)) is None


@pytest.mark.parametrize("prior", ["edit-test", "null-read", "read", "todo", "task-update"])
@pytest.mark.parametrize("prompt,reply", [("Fix the new bug in parser.py", "I fixed the bug in parser.py."),
    ("Run the tests again now", "All tests pass."), ("Deploy it", "Deployed to production."),
    ("Verify the fix now", "I verified the fix works.")])
def test_new_task_does_not_borrow_old_or_administrative_evidence(tmp_path, prior, prompt, reply):
    if prior == "edit-test":
        old = [call("Edit"), result(), call("Bash", "pytest", "t2"), result("4 passed in 0.2s", "t2")]
    else:
        old = [call("Bash", "ls src 2>/dev/null"), result()] if prior == "null-read" else [call("Read"), result()]
    events = [user("Earlier task"), *old, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}, user(prompt)]
    if prior in {"todo", "task-update"}:
        events += [call("TodoWrite" if prior == "todo" else "TaskUpdate", tid="admin"), result(tid="admin")]
    assert gate.check(reply, transcript(tmp_path, events))


@pytest.mark.parametrize("prompt", ["Did you fix it?", "What did you change?", "Status?", "Thanks!", "방금 뭐 했어?"])
def test_recent_status_report_retains_previous_turn_scope(tmp_path, prompt):
    p = transcript(tmp_path, [user("Fix it"), call("Edit"), result(),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed."}]}}, user(prompt)])
    assert gate.check("I fixed the parser.", p) is None


def test_old_evidence_is_not_carried_through_unrelated_turns(tmp_path):
    p = transcript(tmp_path, [user("Fix it"), call("Edit"), result(),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed."}]}},
        user("Explain X"), call("Read", tid="r2"), result(tid="r2"),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "X means Y."}]}}, user("Did you fix the new bug?")])
    assert gate.check("I fixed the new bug.", p)


@pytest.mark.parametrize("command", ["ls src 2>/dev/null", "git status >/dev/null", "git diff 2>NUL"])
def test_readonly_null_redirection_stays_negative(tmp_path, command):
    assert gate.check("I fixed it. All tests pass.", trace(tmp_path, "Bash", command))


def test_todo_reports_are_administrative(tmp_path):
    p = trace(tmp_path, "TodoWrite")
    assert gate.check("I updated the todo list.", p) is None
    assert gate.check("I updated parser.py.", p)


@pytest.mark.parametrize("command,output", [
    ("python -m pytest tests/ 2>&1 | tail -6", "tests/test_api.py ...... [ 50%]\ntests/test_parser.py ...... [100%]\n12 passed in 0.42s"),
    ("uv run pytest -q 2>&1 | tail -5", ".... [100%]\n4 passed in 0.21s"),
    ("poetry run pytest -q | tail -5", "4 passed in 0.21s"),
    ("PYTHONPATH=src python -m pytest -q 2>&1 | tail -5", "4 passed in 0.21s"),
    ("env CI=1 pytest -q | tail -n5", "4 passed in 0.21s"),
    ("source .venv/bin/activate && pytest -q | head -n5", "4 passed in 0.21s"),
    ("pytest -v | tail -4", "tests/test_titles.py::test_truncate_long_titles PASSED [100%]\n1 passed in 0.02s"),
])
def test_pytest_output_and_safe_runner_forms(tmp_path, command, output):
    p = transcript(tmp_path, [user("Run tests"), call("Bash", command), result(output)])
    assert gate.check("All tests pass.", p) is None


@pytest.mark.parametrize("name", ["claim_gate", "inspect_gate"])
@pytest.mark.parametrize("broken", ["def broken(:", ""])
def test_broken_gate_emits_visible_notice(tmp_path, name, broken):
    install.install(tmp_path)
    hooks = tmp_path / ".claude/proof-of-done/hooks"
    (hooks / (name + ".py")).write_text(broken)
    payload = {"transcript_path": trace(tmp_path), "last_assistant_message": "Thanks."}
    r = subprocess.run([sys.executable, "-I", "-B", "-S", str(hooks / "verify_on_stop.py")],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=5,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path)))
    assert r.returncode == 0
    message = json.loads(r.stdout)
    assert "systemMessage" in message and name in message["systemMessage"]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\x1c", "\x1d", "\x1e"])
def test_jsonl_unicode_string_is_not_a_new_event(tmp_path, separator):
    event = call("Edit")
    event["message"]["content"][0]["input"] = {"new_string": "before" + separator + "after"}
    p = transcript(tmp_path, [user("Fix it"), event, result(), call("Read", tid="r"), result(tid="r")])
    assert gate.check("I fixed the translation string.", p) is None


@pytest.mark.parametrize("command,reply", [('echo "$(./scripts/deploy.sh)"', "I deployed the app."),
    ('echo "Result: $(python manage.py migrate)"', "I applied the migration."),
    ('OUT="$(pytest -q 2>&1)"; echo "$OUT" | tail -3', "All tests pass."),
    ('echo "`./scripts/deploy.sh`"', "I deployed the app.")])
def test_quoted_substitution_is_unknown_not_literal(tmp_path, command, reply):
    assert gate.check(reply, trace(tmp_path, "Bash", command)) is None


def test_conflicting_results_are_unknown(tmp_path):
    p = transcript(tmp_path, [user("Fix it"), call("Edit"), result(), result(error=True)])
    assert gate.check("I fixed it.", p) is None


def test_bare_stash_is_a_mutation(tmp_path):
    assert gate.check("I removed the local changes.", trace(tmp_path, "Bash", "git stash")) is None


@pytest.mark.parametrize("command", ["go test -list .", "cargo test --no-run"])
def test_discovery_or_build_is_not_a_test_run(tmp_path, command):
    assert gate.check("All tests pass.", trace(tmp_path, "Bash", command))


def test_dotted_lines_and_large_evidence_sets_are_bounded(tmp_path):
    p = tmp_path / "reply.txt"
    p.write_text(("a." * 2000 + "\n") * 31)
    script = "import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import claim_gate as g,inspect_gate as i;t=Path(sys.argv[2]).read_text();g._scan(t,g.ACTIVE_CLAIMS);i._scan(t,i.READ_CLAIMS);calls=[{'name':'Bash','id':str(n),'input':{'command':'echo '+ 'x'*32000}} for n in range(240)];results={str(n):{'is_error':False} for n in range(240)};assert g._evidence_state(calls,'active',results)=='unknown'"
    subprocess.run([sys.executable, "-I", "-B", "-c", script, str(Path(gate.__file__).parent), str(p)], check=True, timeout=4)
