"""Independent-review regressions. All files/settings are disposable fixtures."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import shutil

import pytest
import claim_gate as gate
import inspect_gate
import verify_on_stop as hook
import session_snapshot as snap
import install


def trace(tmp_path, name=None, command="", extras=()):
    events = [{"type": "user", "message": {"content": "Do the task."}}]
    if name:
        events += [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "id": "t1", "input": {"command": command}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "ok"}]}},
        ]
    events.extend(extras)
    p = tmp_path / "한글 기록.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("text", [
    "You said it was fixed, but it is not.", "Nothing was added.",
    "If tests pass, merge it.", "I verified nothing.",
    "The retry logic is implemented in client.py.", "The width is fixed at 80 columns.",
    "This option was added in Python 3.8.", "The package is released under the MIT licence.",
    "No files were removed.", "When the PR is merged, the workflow runs.",
    "Make sure the tests pass before you merge.", "Let me know once it is deployed.",
    "Added benefit: it is faster.", "Completed tasks are listed in the sidebar.",
    "No tests passed.", "Nothing has been deployed.", "Each request is added to the queue.",
    "Middleware is applied in order.", "Until it is fixed, avoid calling that endpoint.",
    "I did not run main.py, so nothing is fixed yet.", "Use a monospace font, i.e. fixed pitch.",
    "Whether the patch was applied is unclear.", "I'm unsure whether the bug is fixed.",
    "Tests passed locally before my change, but I have not rerun them.",
    "The tests pass on main; I did not run them on this branch.",
    "Removed items are shown in red.", "The helper agent reported that the bug was fixed.",
    "5 tests passed and 2 failed.", "Yes, that is safe.", "Monitoring the logs is a good idea.",
    "This is working as designed.",
])
def test_ordinary_advice_history_and_partial_results_do_not_block(tmp_path, text):
    path = trace(tmp_path)
    assert gate.check(text, path) is None
    assert inspect_gate.check(text, path) is None
    assert not gate.detect_test_claim(text)


@pytest.mark.parametrize("text", [
    "Done — implemented and verified.", "- Fixed the login bug\n- Added a regression test",
    "I updated the config file.", "I created the migration.", "I changed the timeout to 30 seconds.",
    "Everything works now.", "Done. The fix is in place.", "All tests are passing.",
    "The test passes.", "I ran the tests and they pass.", "I've resolved the issue.",
    "**Fixed** the crash.", "I fixed the bug that would crash on empty input.",
    "I've fixed the issue where users could not log in.", "I fixed it, so it should work now.",
    "I refactored the module.", "I wrote the tests.", "Tests: 12 passed.", "I fixed the May report.",
])
def test_review_completion_examples_without_evidence_block(tmp_path, text):
    assert gate.check(text, trace(tmp_path)) is not None


@pytest.mark.parametrize("text,name,command", [
    ("I committed the patch.", "Bash", "git commit -m fix"),
    ("I deleted the temporary file.", "Bash", "rm temporary.txt"),
    ("I merged the branch.", "Bash", "git merge feature"),
    ("I removed the import.", "Bash", "sed -i '/import unused/d' app.py"),
    ("I deployed the app.", "Bash", "vercel deploy"),
    ("All tests pass.", "Bash", "cargo test"), ("All tests pass.", "Bash", "go test ./..."),
    ("All tests pass.", "Bash", "python -m unittest"), ("All tests pass.", "Bash", "make test"),
    ("All tests pass.", "Bash", "dotnet test"), ("All tests pass.", "Bash", "mvn test"),
    ("All tests pass.", "Bash", "bundle exec rspec"), ("All tests pass.", "Bash", "deno test"),
    ("All tests pass.", "PowerShell", "python -m pytest"),
    ("I verified the file exists.", "Read", ""), ("I verified the output.", "Bash", "ls"),
    ("I added the file.", "mcp__filesystem__write_file", ""),
])
def test_observed_non_error_tools_are_supported(tmp_path, text, name, command):
    assert gate.check(text, trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("command", ["pytest -q; echo done", "echo pytest", "pytest || true", "echo 'cargo test'", "pytest | cat"])
def test_masked_commands_are_not_test_success_evidence(tmp_path, command):
    assert gate.check("All tests pass.", trace(tmp_path, "Bash", command)) is not None


def test_utf8_stdin_is_independent_of_windows_code_page(tmp_path):
    path = trace(tmp_path)
    env = dict(os.environ, PYTHONIOENCODING="cp949", PYTHONUTF8="0", CLAUDE_PROJECT_DIR=str(tmp_path))
    env.pop(hook.SKIP_ENV, None)
    payload = {"transcript_path": path, "last_assistant_message": "I’ve fixed it."}
    result = subprocess.run([sys.executable, hook.__file__], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "block"


def test_no_automatic_execution_on_default_install(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv(hook.SKIP_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"last_assistant_message":"Here is the explanation."}'))
    monkeypatch.setattr(hook, "_changed_files", lambda _: ["app.py"])
    calls = []
    monkeypatch.setattr(hook, "_run_test_suite", lambda *a, **kw: calls.append(True))
    hook.main()
    assert calls == []
    assert not capsys.readouterr().out


def test_empty_settings_are_installable(tmp_path):
    p = tmp_path / ".claude/settings.local.json"
    p.parent.mkdir()
    p.touch()
    assert install.install(tmp_path)["status"] == "installed"


def test_reinstall_after_timeout_edit_does_not_duplicate(tmp_path):
    install.install(tmp_path)
    p = tmp_path / ".claude/settings.local.json"
    data = json.loads(p.read_bytes())
    data["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 17
    p.write_text(json.dumps(data), encoding="utf-8")
    before = p.read_bytes()
    with pytest.raises(ValueError, match="edited"):
        install.install(tmp_path)
    assert p.read_bytes() == before
    install.uninstall(tmp_path)
    assert not json.loads(p.read_bytes())["hooks"].get("Stop")


def test_uninstall_missing_settings_does_not_create_file(tmp_path):
    install.install(tmp_path)
    p = tmp_path / ".claude/settings.local.json"
    p.unlink()
    install.uninstall(tmp_path)
    assert not p.exists()


def test_snapshot_retention_does_not_delete_unrelated_json(tmp_path):
    p = tmp_path / "unrelated.json"
    p.write_text("{}", encoding="utf-8")
    old = time.time() - snap.RETENTION_SECONDS - 100
    os.utime(p, (old, old))
    snap.prune(tmp_path)
    assert p.exists()


def test_session_id_newline_is_rejected(tmp_path):
    assert not snap.save_if_absent("abc\n", tmp_path, [], tmp_path / "state")


def test_long_turn_retains_earlier_edit_evidence(tmp_path):
    extras = [{"type": "progress"}] * 410
    path = trace(tmp_path, "Edit", extras=extras)
    assert gate.check("I fixed it.", path) is None


@pytest.mark.parametrize("extra", [
    {"type": "user", "isMeta": True, "message": {"content": "system reminder"}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}, {"type": "text", "text": "additional tool context"}]}},
])
def test_metadata_and_tool_context_do_not_reset_turn(tmp_path, extra):
    assert gate.check("I fixed it.", trace(tmp_path, "Edit", extras=[extra])) is None


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_git_paths_round_trip_spaces_non_ascii_untracked_directories_and_rename(tmp_path):
    git(tmp_path, "init", "-q")
    old = "old name.py"
    git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / old).write_bytes(b"x = 1\n")
    git(tmp_path, "add", "--", old)
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    git(tmp_path, "mv", "--", old, "한글 new.py")
    (tmp_path / "새 폴더").mkdir()
    (tmp_path / "새 폴더/test file.py").write_text("x", encoding="utf-8")
    assert set(hook._changed_files(tmp_path)) == {"한글 new.py", "새 폴더/test file.py"}
    assert snap.take(tmp_path, hook._changed_files(tmp_path))["한글 new.py"][0] == 6


def test_nested_project_never_enumerates_parent_repository(monkeypatch, tmp_path):
    git(tmp_path, "init", "-q")
    child = tmp_path / "child"
    child.mkdir()
    observed, original = [], hook._run
    def run(cmd, root, **kwargs):
        observed.append(cmd)
        return original(cmd, root, **kwargs)
    monkeypatch.setattr(hook, "_run", run)
    assert hook._changed_files(child) is None
    assert all("status" not in command for command in observed)


def test_private_artifacts_are_ignored_by_git(tmp_path):
    git(tmp_path, "init", "-q")
    settings = tmp_path / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text('{"env":{"DUMMY":"synthetic only"}}', encoding="utf-8")
    install.install(tmp_path)
    base = settings.parent / "proof-of-done"
    snap.save_if_absent("s", tmp_path, [], base / "hooks/.state/stop-verify")
    paths = [str(p.relative_to(tmp_path)).replace("\\", "/") for p in base.rglob("*") if p.is_file()]
    result = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "--stdin"], input="\n".join(paths).encode(), capture_output=True, check=True)
    assert set(result.stdout.decode().splitlines()) == set(paths)
    assert not list(settings.parent.glob("*.backup-*"))


def git_bash():
    candidates = [Path("C:/Program Files/Git/bin/bash.exe"), Path(shutil.which("bash") or "not-installed")]
    for path in candidates:
        if path.is_file() and subprocess.run([str(path), "-c", "exit 0"], capture_output=True).returncode == 0:
            return str(path)
    pytest.skip("No working Bash installed")


def test_stored_command_survives_project_move_quoted_interpreter_and_missing_script(tmp_path):
    project = tmp_path / "시작 folder $quoted'"
    project.mkdir()
    install.install(project)
    # Store Python execution aliases cannot be copied as ordinary executables.
    # A standard-library venv provides a real executable at a quoted path.
    environment = tmp_path / "python folder's $space"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, capture_output=True, timeout=30)
    interpreter = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    block = install.hook_blocks(project / ".claude/proof-of-done", str(interpreter))["Stop"]["hooks"][0]
    moved = tmp_path / "이동 café folder $quoted'"
    assert project.resolve().is_relative_to(tmp_path.resolve()) and moved.absolute().is_relative_to(tmp_path.resolve())
    project.rename(moved)
    path = trace(tmp_path)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(moved))
    env.pop("PYTHONHOME", None)
    env.pop(hook.SKIP_ENV, None)
    payload = json.dumps({"transcript_path": path, "last_assistant_message": "I fixed it."}, ensure_ascii=False).encode("utf-8")
    def run():
        return subprocess.run([git_bash(), "-c", block["command"]], input=payload, capture_output=True, env=env, timeout=15)
    result = run()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block", result.stderr
    installed = moved / ".claude/proof-of-done"
    # Preserve rather than recursively delete. The stored command now sees the
    # same missing-path condition; the acceptance runner covers actual removal.
    archived = moved / ".claude/retained-install"
    assert installed.resolve().is_relative_to(tmp_path.resolve()) and archived.absolute().is_relative_to(tmp_path.resolve())
    installed.rename(archived)
    result = run()
    assert result.returncode == 0 and not result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_junction_state_does_not_read_write_or_prune_outside_target(tmp_path):
    state, outside = tmp_path / "state", tmp_path / "outside"
    outside.mkdir()
    snap.save_if_absent("old", tmp_path, [], outside)
    p = outside / "old.json"
    old = time.time() - snap.RETENTION_SECONDS - 100
    os.utime(p, (old, old))
    command = "New-Item -ItemType Junction -Path '{}' -Target '{}' | Out-Null".format(str(state).replace("'", "''"), str(outside).replace("'", "''"))
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True)
    try:
        assert snap.load("old", tmp_path, state) is None
        assert not snap.save_if_absent("new", tmp_path, [], state)
        snap.prune(state)
        assert p.exists() and not (outside / "new.json").exists()
    finally:
        state.rmdir()  # Remove only this owned junction, never its target.


def test_runner_timeout_is_bounded_when_descendant_inherits_output(monkeypatch, tmp_path):
    monkeypatch.setattr(hook, "PER_CMD_TIMEOUT", 0.2)
    program = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(2)']); time.sleep(8)"
    started = time.monotonic()
    rc, output = hook._run([sys.executable, "-c", program], tmp_path)
    elapsed = time.monotonic() - started
    # Let the known synthetic descendant exit naturally; never kill its tree.
    time.sleep(max(0, 2.3 - elapsed))
    assert rc == 124 and "unknown" in output
    assert elapsed < 1.5


def test_custom_config_cannot_execute_without_opt_in(monkeypatch, tmp_path, capsys):
    config = tmp_path / ".claude/verify.json"
    config.parent.mkdir()
    marker = tmp_path / "executed.txt"
    config.write_text(json.dumps({"test_cmd": [sys.executable, "-c", "from pathlib import Path;Path('executed.txt').write_text('yes')"]}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"last_assistant_message":"All tests pass."}'))
    hook.main()
    assert not marker.exists()
    assert not capsys.readouterr().out


@pytest.mark.parametrize("value", ["pytest; echo done", "npx vitest run", ["cmd.exe", "/c", "echo hi"], ["bash", "-c", "exit 0"]])
def test_custom_shell_expressions_and_implicit_fetch_are_rejected(value):
    assert hook._custom_argv(value) is None


def test_opt_in_runs_direct_command_and_failure_blocks(monkeypatch, tmp_path, capsys):
    install.install(tmp_path, run_tests=True)
    monkeypatch.setattr(sys, "argv", [hook.__file__, "--run-tests"])
    (tmp_path / ".claude/verify.json").write_text(json.dumps({"test_cmd": [sys.executable, "-c", "raise SystemExit(1)"]}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"last_assistant_message":"All tests pass."}'))
    hook.main()
    assert "pytest(strict)" in json.loads(capsys.readouterr().out)["reason"]


def test_missing_runner_and_timeout_are_unknown_not_failures(monkeypatch, tmp_path):
    for code in (124, 127):
        monkeypatch.setattr(hook, "_run", lambda *a, **kw: (code, "unknown"))
        assert hook._run_pytest(tmp_path, [], {"test_cmd": ["unavailable"]})[0] is False
        assert hook._run_tsc(tmp_path, {"typecheck_cmd": ["unavailable"]})[0] is False


def test_resolved_batch_shim_is_not_launched(monkeypatch, tmp_path):
    monkeypatch.setattr(hook.shutil, "which", lambda _: "C:/synthetic/npm.cmd")
    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **kw: pytest.fail("batch shim was launched"))
    assert hook._run(["npm", "test"], tmp_path)[0] == 127


def test_unknown_optional_runner_is_reported_on_stderr(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(hook, "_run_pytest", lambda *a, **kw: (False, "[unknown] runner unavailable"))
    fails = []
    hook._run_test_suite(tmp_path, ["sample.py"], fails, [])
    assert fails == []
    assert "[unknown] runner unavailable" in capsys.readouterr().err


def test_mid_run_human_interruption_has_unknown_evidence_scope(tmp_path):
    path = trace(tmp_path, "Edit", extras=[{"type": "user", "message": {"content": "Also explain why."}}])
    assert gate.check("I fixed it.", path) is None
    assert inspect_gate.check("I am monitoring the build.", path) is None


@pytest.mark.parametrize("text", [
    "Updated displays are refreshed hourly.", "Fixed bearings are greased regularly.",
    "Integrated sensors can be calibrated remotely.", "Completed forms must be signed.",
    "Changed objects remain cached.", "Deleted records contain tombstones.",
])
def test_leading_participle_with_finite_predicate_is_a_description(tmp_path, text):
    # Independently authored grammar probes. No held-out sentences are copied.
    assert gate.check(text, trace(tmp_path)) is None


@pytest.mark.parametrize("text", [
    "Updated the display.", "Fixed two bearings.",
    "Integrated a sensor that can detect pressure.", "Added middleware that is compatible with the API.",
])
def test_bare_completion_with_object_or_relative_clause_still_needs_evidence(tmp_path, text):
    assert gate.check(text, trace(tmp_path)) is not None


def test_repository_metadata_changes_can_support_a_claim(tmp_path):
    assert gate.check("I created the release tag.", trace(tmp_path, "Bash", "git tag v1.1")) is None
    assert gate.check("I created the branch.", trace(tmp_path, "Bash", "git branch --list")) is not None
