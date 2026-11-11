"""Fourth review and real-session regressions; all transcripts are synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import claim_gate as gate
import inspect_gate
import install
import doctor
import verify_on_stop as hook
from test_review_regressions import trace


def user(text):
    return {"type": "user", "message": {"content": text}}


def call(name, command="", tid="t1"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": tid, "input": {"command": command}}]}}


def result(text="ok", tid="t1", error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": text}]}}


def transcript(tmp_path, events):
    p = tmp_path / "trace.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("prompt", ["Did you commit it?", "Did the tests pass?", "What did you do?",
    "What changed?", "Can you recap?", "tl;dr?", "Status?", "방금 뭐 했어?", "Thanks!", "continue"])
def test_earlier_matching_evidence_makes_scope_unknown(tmp_path, prompt):
    p = transcript(tmp_path, [user("Fix it"), call("Edit"), result(), call("Bash", "pytest -q", "t2"),
        result("2 passed in 0.03s", "t2"), {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}, user(prompt)])
    assert gate.check("I fixed the parser. All tests passed.", p) is None


@pytest.mark.parametrize("earlier", [[], [call("Read"), result()], [call("Edit"), result(error=True)],
    [{**call("Edit"), "isSidechain": True}, result()]])
def test_no_earlier_matching_evidence_still_blocks(tmp_path, earlier):
    p = transcript(tmp_path, [user("Earlier"), *earlier,
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "I stopped here."}]}},
        user("Did you fix it?")])
    assert gate.check("I fixed the parser.", p)


def test_sidechain_prompt_does_not_drop_main_edit(tmp_path):
    p = transcript(tmp_path, [user("Fix it"), call("Edit"), result(), {**user("Review only"), "isSidechain": True}])
    assert gate.check("I fixed the parser.", p) is None


@pytest.mark.parametrize("text", [
    "I can't send that sentence as written, because it would be false: I haven't opened, checked, or changed note.txt. If you want me to fix a bug in it, tell me what the bug is, or let me read the file, and I'll fix it and tell you exactly what I changed.",
    "If you want, I'll fix the parser and explain exactly what I changed.",
    "Here's a PR description:\n\n## Summary\n- Added a cache",
    "Commit message:\n\nFixed the parser crash.", "### Release notes\n- Added a cache",
    "This PR:\n- Added a cache", "Commit a1b2c3d (Sam): Fixed the parser.",
    "The last three commits:\n- Fixed the parser", "We added the cache in #123 last month.",
    "이 PR에서 캐시를 추가했습니다.", "Fix the import so the tests pass.",
    "Next step: make the failing test pass.", "You need to make the tests pass.",
    "The goal is to have all tests pass.", "버그를 수정했으면 좋겠습니다.",
])
def test_not_a_current_completion(tmp_path, text):
    assert gate.check(text, trace(tmp_path)) is None


@pytest.mark.parametrize("text", ["I'll test it tomorrow. I changed the parser today.",
    "I did not run tests, but I changed the parser.", "Here is what I changed: I updated the parser.",
    "### Release notes\n- Added a cache\n\nI fixed the parser."])
def test_new_positive_claim_is_not_hidden(tmp_path, text):
    assert gate.check(text, trace(tmp_path))


@pytest.mark.parametrize("name,command", [
    ("Bash", "npx eslint --fix src"), ("Bash", "ruff check --fix src"), ("Bash", "npx prettier --write src"),
    ("Bash", "terraform apply"), ("Bash", "docker push image"), ("Bash", "fly deploy"),
    ("Bash", "poetry add foo"), ("Bash", "cargo add foo"), ("Bash", "python manage.py makemigrations"),
    ("Bash", "find src -name '*.tmp' | xargs rm"), ("Bash", "echo ok | tee -a notes.txt"),
    ("Bash", "sudo rm old.txt"), ("Bash", "gh issue create --title fix --body text"),
    ("Bash", 'psql -c "ALTER TABLE t ADD v INT"'), ("PowerShell", "if (Test-Path old.txt) { Remove-Item old.txt }"),
    ("PowerShell", "'hello' | Out-File notes.txt"), ("mcp__notion__notion-update-page", ""),
    ("mcp__github__add_issue_comment", ""), ("Bash", 'cd $(git rev-parse --show-toplevel) && git commit -m fix'),
    ("Bash", "git commit -m fix &> output.txt"),
    ("PowerShell", "git commit -m @'\nfix user's input\n'@"),
    ("PowerShell", 'git commit -m "Handle `"quoted`" input"'),
])
def test_mutating_presence(tmp_path, name, command):
    assert gate.check("I updated the project.", trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("command", ["playwright test", "npx mocha", "swift test", "flutter test",
    "cargo nextest run", "uvx pytest", "npm t", "(cd web && npm test)", "source .venv/bin/activate; pytest",
    "export CI=1; npm test", "set -e; pytest", "mypy src"])
def test_test_runner_presence(tmp_path, command):
    assert gate.check("All tests passed.", trace(tmp_path, "Bash", command)) is None


@pytest.mark.parametrize("command,text", [("pytest -q", "Done. All tests pass."),
    ("netstat -ano", "Done — nothing is listening on 3000.")])
def test_done_with_nonmutating_tool(tmp_path, command, text):
    assert gate.check(text, trace(tmp_path, "Bash", command)) is None


@pytest.mark.parametrize("command", ["pytest -q | tail -5", 'cd "a b" && python -m pytest -q 2>&1 | tail -5',
    "python3 -m pytest tests | head -n 20"])
def test_filtered_pytest_requires_its_own_summary(tmp_path, command):
    p = transcript(tmp_path, [user("Test"), call("Bash", command), result(".. [100%]\n2 passed in 0.03s")])
    assert gate.check("All tests passed.", p) is None


@pytest.mark.parametrize("command,output,tid,error", [
    ("pytest | tail -5", "ok", "t1", False),
    ("pytest | tail -5", "2 passed in 0.03s", "other", False),
    ("pytest | tail -5", "2 passed in 0.03s", "t1", True),
    ("pytest | tail -5", "2 passed, 1 failed in 0.03s", "t1", False),
    ("pytest | tail -5", "2 passed, 1 error in 0.03s", "t1", False),
    ("pytest | tail -5", "[output truncated]\n2 passed in 0.03s", "t1", False),
    ("pytest | tail -5", "0 passed in 0.03s", "t1", False),
    ("pytest | tail -5", "2 passed in", "t1", False),
    ("pytest | tail -5", "1 failed in 1s\n2 passed in 0.03s", "t1", False),
    ("pytest | tail -5; echo ok", "2 passed in 0.03s", "t1", False),
    ("pytest | tail -5 || true", "2 passed in 0.03s", "t1", False),
    ("pytest | tail -5 output.txt", "2 passed in 0.03s", "t1", False),
    ("pytest | tail -5 | head -1", "2 passed in 0.03s", "t1", False),
    ("pytest | grep passed", "2 passed in 0.03s", "t1", False),
    ("echo x | tail -5", "2 passed in 0.03s", "t1", False),
    ("pytest --version | tail -5", "2 passed in 0.03s", "t1", False),
])
def test_filtered_pytest_rejects_unreliable_evidence(tmp_path, command, output, tid, error):
    p = transcript(tmp_path, [user("Test"), call("Bash", command), result(output, tid, error)])
    if tid != "t1":
        # V6: a missing matching result is unknown, never positive test evidence.
        assert not gate._filtered_pytest({"name": "Bash", "id": "t1", "input": {"command": command}},
                                         {tid: {"content": output, "is_error": error}})
        assert gate.check("All tests passed.", p) is None
        return
    assert gate.check("All tests passed. 2 passed in 0.03s", p)


@pytest.mark.parametrize("text", ["\n" * 120000 + "ordinary explanation", " " * 120000 + "ordinary explanation",
    ("\n\n    change\n" * 8000)[:131062],
    ("Draft:\nx\n" * 15000)[:131062],
    "Changelog for 4.0:\n" + "".join(f"- item {i}\n" for i in range(8000)) + "\nThat's the full list."],
    ids=["newlines", "spaces", "list", "drafts-131k", "changelog-8000"])
def test_scanning_cost_is_bounded(tmp_path, text):
    # Separate process timeout prevents a regression from hanging the suite.
    p = tmp_path / "reply.txt"
    p.write_text(text, encoding="utf-8")
    script = "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import claim_gate as c, inspect_gate as i; t=Path(sys.argv[2]).read_text(); c._scan(t,c.ACTIVE_CLAIMS); i._scan(t,i.READ_CLAIMS)"
    subprocess.run([sys.executable, "-I", "-B", "-c", script, str(Path(gate.__file__).parent), str(p)], check=True, timeout=3)


def test_inspect_search_coverage_and_future_are_not_read_or_watch_claims(tmp_path):
    p = trace(tmp_path, "Grep")
    assert inspect_gate.check("호출부를 전부 확인했습니다.", p) is None
    assert inspect_gate.check("배포 후 지켜보겠습니다.", p) is None


@pytest.mark.parametrize("rc,failed", [(0, False), (1, True), (2, True), (101, True), (124, False), (127, False)])
def test_custom_runner_exit_codes(tmp_path, monkeypatch, rc, failed):
    monkeypatch.setattr(hook, "_run_custom", lambda *a: (rc, "output"))
    assert hook._run_pytest(tmp_path, [], {"test_cmd": ["runner"]})[0] is failed


def test_project_disable_all_hooks_is_not_a_pass(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text('{"disableAllHooks":true}')
    with pytest.raises(ValueError, match="disable|off switch"):
        install.install(tmp_path)
    (tmp_path / ".claude/settings.json").write_text('{}')
    install.install(tmp_path)
    (tmp_path / ".claude/settings.json").write_text('{"disableAllHooks":true}')
    with pytest.raises(ValueError, match="disable|off switch"):
        doctor.diagnose(tmp_path)


def test_new_install_does_not_adopt_prepopulated_state(tmp_path):
    state = tmp_path / ".claude/proof-of-done/.state/stop-verify"
    state.mkdir(parents=True)
    (state / "existing.json").write_text('{}')
    with pytest.raises(ValueError, match="state"):
        install.install(tmp_path)
    assert (state / "existing.json").read_text() == '{}'


def test_missing_module_reports_skipped_in_session(tmp_path):
    install.install(tmp_path)
    (tmp_path / '.claude/proof-of-done/hooks/session_snapshot.py').unlink()
    script = tmp_path / '.claude/proof-of-done/hooks/verify_on_stop.py'
    proc = subprocess.run([sys.executable, '-I', '-B', '-S', str(script)], input=b'{}', capture_output=True, timeout=10)
    assert proc.returncode == 0
    assert 'missing' in json.loads(proc.stdout)['systemMessage']


@pytest.mark.parametrize('command', ['pytest | tail -5', 'pytest | head -5'])
@pytest.mark.parametrize('output', ['2 passed, 2 passed in 0.03s', '2 passed in 0.03s\n[output truncated]'])
def test_ambiguous_pytest_summary_is_not_evidence(tmp_path, command, output):
    p = transcript(tmp_path, [user('Test'), call('Bash', command), result(output)])
    assert gate.check('All tests passed.', p)


def test_quoted_mutation_or_command_expansion_is_not_a_mutating_program(tmp_path):
    assert gate.check('I fixed the parser.', trace(tmp_path, 'Bash', 'echo "terraform apply --fix"'))
    # A substitution can execute code: unknown, unlike a quoted literal.
    assert gate.check('I fixed the parser.', trace(tmp_path, 'Bash', 'echo $(git commit -am fix)')) is None
    assert not gate._has_active_evidence([{'name': 'Bash', 'input': {'command': 'echo $(git commit -am fix)'}}])


@pytest.mark.parametrize('missing', ['interpreter', 'hook'])
def test_stored_bash_wrapper_reports_missing_runtime(tmp_path, missing):
    bash = Path('C:/Program Files/Git/bin/bash.exe')
    if not bash.is_file():
        pytest.skip('Windows Git Bash wrapper check')
    install.install(tmp_path)
    base = tmp_path / '.claude/proof-of-done'
    interpreter = Path(install.trusted_python(tmp_path))
    if missing == 'interpreter':
        interpreter = interpreter.with_name('proof-of-done-missing-python.exe')
        assert not interpreter.exists()
    else:
        (base / 'hooks/verify_on_stop.py').unlink()
    command = install.hook_blocks(base, str(interpreter))['Stop']['hooks'][0]['command']
    env = os.environ.copy()
    env['CLAUDE_PROJECT_DIR'] = str(tmp_path)
    proc = subprocess.run([str(bash), '-c', command], input=b'{}', env=env, cwd=tmp_path,
        capture_output=True, timeout=10)
    assert proc.returncode == 0
    assert 'unavailable' in json.loads(proc.stdout)['systemMessage']


@pytest.mark.parametrize('footer,allowed', [
    ('\nShell cwd was reset to C:/test-work/p5', True),
    ('\nThere were additional failures', False),
    ('\nShell cwd was reset to C:/test-work/p5\nThere were additional failures', False),
])
def test_pytest_summary_before_observed_tool_annotation(tmp_path, footer, allowed):
    p = transcript(tmp_path, [user('Test'), call('Bash', 'cd work && python -m pytest -q 2>&1 | tail -5'),
        result('.. [100%]\n2 passed in 0.03s'+footer)])
    assert (gate.check('Both tests passed.', p) is None) is allowed
