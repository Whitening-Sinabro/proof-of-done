"""Second review regressions; synthetic projects only, never live hooks."""
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest

import claim_gate as gate
import inspect_gate
import install
import doctor
import verify_on_stop as hook
from test_review_regressions import trace


@pytest.mark.parametrize("text", [
    "Updated documentation below:", "Changed modules:",
    "Released under the BSD licence.", "Implemented as an optional check.",
    "Deployed workers need credentials.", "Created by a generator, it is read-only.",
    "In the last turn I updated the module.", "Last time we fixed the typo.",
    "수정이 완료되면 다시 확인하겠습니다.", "배포했는지부터 확인해야 합니다.",
    "네, 이 예시에서는 안전합니다.",
])
def test_descriptions_history_and_korean_uncertainty(tmp_path, text):
    path = trace(tmp_path)
    assert gate.check(text, path) is None
    assert inspect_gate.check(text, path) is None


@pytest.mark.parametrize("text", [
    "I just updated the worker.", "We have already fixed the typo.",
    "Summary: Removed the old cache.", "✅ Added the setting.",
    "I went ahead and created the file.", "The test suite passes.",
    "All 18 tests passed.", "I made the change and pushed it.",
])
def test_common_claim_forms_still_need_evidence(tmp_path, text):
    assert gate.check(text, trace(tmp_path, "Read")) is not None


@pytest.mark.parametrize("name,command", [
    ("Bash", 'git commit -m "$(cat <<\'EOF\'\nfix quoted punctuation; & |\nEOF\n)"'),
    ("PowerShell", "Remove-Item a.tmp; Remove-Item b.tmp"),
    ("Bash", "gcloud run deploy demo --source ."),
    ("Bash", "npm run deploy"),
    ("Bash", "python generate.py > report.txt"),
])
def test_change_evidence_command_syntax(tmp_path, name, command):
    assert gate.check("I updated the project.", trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("name,command", [
    ("Bash", "pytest -q 2>&1"), ("Bash", "CI=1 npm run test:unit"),
    ("Bash", "./gradlew test"), ("Bash", "python manage.py test"),
    ("PowerShell", r".\node_modules\.bin\vitest run"),
    ("Bash", "php vendor/bin/phpunit"),
    ("Bash", 'pytest -k "a; b | c"'),
])
def test_test_evidence_command_syntax(tmp_path, name, command):
    assert gate.check("All tests pass.", trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("command", ["pytest -q 2>&1 | tail -5", "pytest; echo ok", "pytest || true"])
def test_masking_is_not_test_success(tmp_path, command):
    assert gate.check("All tests pass.", trace(tmp_path, "Bash", command)) is not None


@pytest.mark.parametrize("command", ["git branch -a", "git branch -rv", "git tag --list", "git stash list"])
def test_read_only_git_is_not_edit_evidence(tmp_path, command):
    assert gate.check("I fixed the handler.", trace(tmp_path, "Bash", command)) is not None


@pytest.mark.parametrize("name", ["Agent", "Task"])
def test_successful_subagent_is_unknown_scope(tmp_path, name):
    path = trace(tmp_path, name)
    assert gate.check("I fixed the handler and all tests pass.", path) is None
    assert inspect_gate.check("I reviewed the whole file.", path) is None


def test_long_read_only_turn_is_not_automatically_unknown(tmp_path):
    events = []
    for i in range(260):
        events.extend([
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "id": str(i), "input": {}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": str(i), "content": "data"}]}},
        ])
    assert gate.check("I fixed the handler.", trace(tmp_path, extras=events)) is not None


def test_bom_stdin_reaches_claim_check(tmp_path):
    path = trace(tmp_path)
    payload = {"last_assistant_message": "I fixed the handler.", "transcript_path": path}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    env.pop(hook.SKIP_ENV, None)
    result = subprocess.run([sys.executable, "-B", hook.__file__], input=json.dumps(payload).encode("utf-8-sig"), capture_output=True, env=env, timeout=15)
    assert json.loads(result.stdout)["decision"] == "block"


@pytest.mark.parametrize("planted", ["shlex.py", "__pycache__"])
def test_preexisting_executable_extras_refused_before_any_write(tmp_path, planted):
    folder = tmp_path / ".claude/proof-of-done/hooks"
    folder.mkdir(parents=True)
    if planted.endswith(".py"):
        (folder / planted).write_text("raise RuntimeError('untrusted fixture')", encoding="utf-8")
    else:
        (folder / planted).mkdir()
    with pytest.raises(ValueError):
        install.install(tmp_path)
    assert not (tmp_path / ".claude/settings.local.json").exists()
    assert not (folder / "claim_gate.py").exists()


def test_doctor_refuses_unchecked_bytecode(tmp_path):
    install.install(tmp_path)
    folder = tmp_path / ".claude/proof-of-done/hooks"
    planted = tmp_path / "fixture.py"
    marker = tmp_path / "marker"
    planted.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    py_compile.compile(str(planted), cfile=str(folder / "__pycache__" / f"claim_gate.{sys.implementation.cache_tag}.pyc"), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    with pytest.raises(ValueError):
        doctor.diagnose(tmp_path)
    assert not marker.exists()


def test_manifest_cannot_extend_uninstall_ownership(tmp_path):
    install.install(tmp_path)
    settings = tmp_path / ".claude/settings.local.json"
    manifest = tmp_path / ".claude/proof-of-done/install-manifest.json"
    data, record = json.loads(settings.read_bytes()), json.loads(manifest.read_bytes())
    foreign = {"type": "command", "command": "echo foreign-hook"}
    data["hooks"]["Stop"].append({"hooks": [foreign]})
    record["blocks"]["Stop"]["hooks"].append(foreign)
    settings.write_text(json.dumps(data), encoding="utf-8")
    manifest.write_text(json.dumps(record), encoding="utf-8")
    install.uninstall(tmp_path)
    after = json.loads(settings.read_bytes())
    assert any(foreign in b["hooks"] for b in after["hooks"]["Stop"])


def test_doctor_safe_path_cli_and_no_bytecode(tmp_path):
    install.install(tmp_path)
    result = subprocess.run([sys.executable, "-I", "-B", doctor.__file__, "--project", str(tmp_path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".claude/proof-of-done/hooks/__pycache__").exists()


def test_installed_wrapper_uses_isolated_no_bytecode_python():
    command = install.hook_blocks(Path("demo"), sys.executable)["Stop"]["hooks"][0]["command"]
    assert " -I -B " in command


def test_post_install_extra_module_and_cache_never_execute(tmp_path):
    install.install(tmp_path)
    folder = tmp_path / ".claude/proof-of-done/hooks"
    marker = tmp_path / "planted-marker"
    (folder / "shlex.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-B", str(folder / "verify_on_stop.py")], input=b"{}", capture_output=True, timeout=15)
    assert result.returncode == 0
    assert "skipped" in json.loads(result.stdout)["systemMessage"]
    assert b"unexpected installed hook files" in result.stderr
    assert not marker.exists()


def test_byte_cap_without_user_boundary_remains_unknown(tmp_path, monkeypatch):
    path = trace(tmp_path, extras=[{"type": "progress", "content": "x" * 1000}] * 10)
    original = gate._read_tail
    monkeypatch.setattr(gate, "_read_tail", lambda p: original(p, max_bytes=2048))
    assert gate.check("I fixed the parser.", path) is None


def test_total_runner_budget_stops_later_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_RUN_DEADLINE", hook.time.monotonic() - 1)
    marker = tmp_path / "budget-must-not-run"
    result, output = hook._run([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"], tmp_path)
    assert result == 124 and "budget" in output
    assert not marker.exists()


def test_pytest_uses_project_python_and_disables_caches(tmp_path, monkeypatch):
    local_python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    local_python.parent.mkdir(parents=True)
    local_python.touch()
    (tmp_path / "tests").mkdir()
    commands = []
    monkeypatch.setattr(hook, "_run", lambda cmd, root: (commands.append(cmd) or 0, ""))
    assert not hook._run_pytest(tmp_path, [], {}, strict=True)[0]
    assert commands[0][:4] == [str(local_python), "-B", "-m", "pytest"]
    assert "no:cacheprovider" in commands[0]


@pytest.mark.parametrize("text", [
    "Added all together, these values could exceed the limit.",
    "Two months ago I created the dashboard.",
    "The archive includes examples, e.g. fixed.json and added.csv.",
    "Tests passed last night in CI, but I haven't run them now.",
    "Updated all settings allow the app to start.",
    "Here is what we changed in v2 according to the changelog.",
])
def test_general_grammar_categories_not_current_actions(tmp_path, text):
    assert gate.check(text, trace(tmp_path)) is None


@pytest.mark.parametrize("command", [
    "npx --yes wrangler deploy", "pnpm exec vercel deploy", "npm install",
    "alembic upgrade head", "python manage.py migrate", "npx prisma migrate deploy",
])
def test_package_runner_and_migration_evidence(tmp_path, command):
    assert gate.check("I updated the project.", trace(tmp_path, "Bash", command)) is None


@pytest.mark.parametrize("text", ["I just renamed the directory.", "I ran the tests; they all pass."])
def test_additional_current_claims(tmp_path, text):
    assert gate.check(text, trace(tmp_path, "Read")) is not None


def test_summary_request_is_historical_but_new_action_is_not(tmp_path):
    path = Path(trace(tmp_path))
    path.write_text(json.dumps({"type": "user", "message": {"content": "Thanks, summarize what you did."}}), encoding="utf-8")
    assert gate.check("I fixed the parser.", str(path)) is None
    path.write_text(json.dumps({"type": "user", "message": {"content": "Summarize what you did, then fix the parser."}}), encoding="utf-8")
    assert gate.check("I fixed the parser.", str(path)) is not None
