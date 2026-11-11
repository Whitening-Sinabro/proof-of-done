"""Fifth independent review: synthetic traces and disposable project runners."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
import claim_gate as gate
import inspect_gate
import install
import verify_on_stop as hook
from test_review_regressions import trace
from test_review4_regressions import user, call, result, transcript


@pytest.mark.parametrize("name,command", [
    ("Bash", "npx cdk deploy"), ("Bash", "helm upgrade --install app ./chart"),
    ("Bash", "uv pip install requests"), ("Bash", "python -m alembic upgrade head"),
    ("Bash", "npm run db:migrate"), ("Bash", "rsync dist/ host:/www"),
    ("Bash", "python - <<'EOF'\nopen('x','w').write('ok')\nEOF"),
    ("PowerShell", '[System.IO.File]::WriteAllText("x", "ok")'),
    ("PowerShell", 'git commit `\n -m fix'), ("mcp__supabase__execute_sql", ""),
    ("mcp__claude-in-chrome__computer", ""), ("Bash", "bash -c 'rm old'"),
    ("Bash", "for f in old; do rm $f; done"), ("Bash", "if test -f old; then rm old; fi"),
    ("Bash", "unfamiliar_command --anything"),
])
def test_unclassified_actions_are_unknown_not_negative(tmp_path, name, command):
    assert gate.check("I updated the project.", trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("command", ["mix test", "bazel test //...", "./scripts/test.sh",
    "vendor/bin/pest", "dart test", "sbt test", "npx nx test", "npm run test-unit", "hatch run test"])
def test_unclassified_runners_are_unknown(tmp_path, command):
    assert gate.check("All tests pass.", trace(tmp_path, "Bash", command)) is None


@pytest.mark.parametrize("name,command", [("Read", ""), ("Grep", ""), ("Glob", ""),
    ("Bash", "git status"), ("Bash", "git diff"), ("Bash", "ls"), ("Bash", "cat src.py")])
def test_known_reads_cannot_establish_changes_or_test_runs(tmp_path, name, command):
    p = trace(tmp_path, name, command)
    assert gate.check("I fixed the parser. All tests pass.", p)


@pytest.mark.parametrize("text", [
    "Here's a message you can send:\n\nHi Sam, I fixed the login bug.",
    "Suggested reply:\n\nI fixed the issue and pushed the update.",
    "의뢰인께 보낼 답장 초안입니다:\n\n로그인 문제를 수정했습니다.",
    "This branch:\n- Added a retry wrapper\n- Removed the timeout",
    "Recent commits:\n- Fixed the login redirect", "Django 5.0 changes:\n- Added the field option",
    "Looking at the log, the tests passed; the failure is in the deploy step.",
    "I checked the config you pasted; the port should be 8080.",
    "테스트 통과 여부를 먼저 확인해야 합니다.", "수정 완료 여부를 알려주시면 이어서 진행하겠습니다.",
    "검증 완료 시점은 내일 오전입니다.", "팀원분이 배포했다고 하셨는데, 새 버전이 보이지 않습니다.",
    "I added a short example below:\n```python\nx = 0\n```",
    "I wrote the steps as a checklist below so you can follow along.",
    "I renamed `x` to `count`:\n```python\ncount = 0\n```",
    "I've updated the plan to include a migration step before the backfill.",
])
def test_excluded_reports_and_reply_edits(tmp_path, text):
    assert gate.check(text, trace(tmp_path)) is None


@pytest.mark.parametrize("text", ["I ran the tests and they all pass.", "I've made the requested changes.",
    "Successfully deployed the app.", "Everything is working now.", "커밋하고 푸시했습니다.",
    "I've gone ahead and updated the config.", "## Summary of changes\n\n- Fixed the parser\n- Added tests",
    "Summary of what I did:\n- Fixed the parser", "Highlights:\n- Fixed the parser",
    "Commit and push:\n- Fixed the parser", "I updated the plan file on disk."])
def test_neutral_completion_claims_still_block(tmp_path, text):
    assert gate.check(text, trace(tmp_path, "Read"))


def test_korean_verify_and_program_description(tmp_path):
    p = trace(tmp_path, "Read")
    assert gate.check("설정 파일 검증 완료했습니다.", p) is None
    assert not gate.detect_test_claim("설정 파일 검증 완료했습니다.")
    assert gate.check("설정 파일 검증 완료했습니다.", trace(tmp_path))
    assert inspect_gate.check("이 데몬은 디렉터리를 실시간으로 감시합니다.", p) is None
    assert inspect_gate.check("제가 실시간으로 감시하고 있습니다.", p)


@pytest.mark.parametrize("runner", ["pytest", "python -m pytest", "py -3.13 -m pytest", "python -B -m pytest"])
def test_progress_dots_are_not_truncation(tmp_path, runner):
    p = transcript(tmp_path, [user("test"), call("Bash", runner + " -q 2>&1 | tail -5"),
        result("........................................................................ [87%]\n........... [100%]\n83 passed in 4.21s")])
    assert gate.check("All tests pass.", p) is None


@pytest.mark.parametrize("command", ["pytest --version", "ruff --version", "python -m pytest --help", "pytest --collect-only"])
def test_runner_information_is_not_a_run(tmp_path, command):
    assert gate.check("All tests pass.", trace(tmp_path, "Bash", command))


def test_incomplete_tool_is_unknown_not_positive_evidence(tmp_path):
    p = transcript(tmp_path, [user("Fix it"), call("Edit")])
    assert gate.check("I fixed it.", p) is None
    assert gate._evidence_state([{"name": "Edit", "id": "t1", "input": {}}], "active", {}) == "unknown"


def test_large_shell_payload_is_unknown_without_scanning_its_body(tmp_path):
    command = "python -c " + "a" * (33 * 1024)
    with pytest.raises(ValueError, match="unknown"):
        gate._shell_tokens(command, False)
    assert gate.check("I fixed the parser.", trace(tmp_path, "Bash", command)) is None


def test_plain_done_and_todo_are_not_disk_claims(tmp_path):
    assert gate.check("Done.", trace(tmp_path)) is None
    assert gate.check("I updated the todo list.", trace(tmp_path, "TodoWrite")) is None


@pytest.mark.parametrize("piece", [" \n", "\u00a0\n", "- \n", "a \n"])
def test_whitespace_lines_do_not_stall(tmp_path, piece):
    p = tmp_path / "reply.txt"
    p.write_text(piece * 8000 + "\nI fixed the parser.", encoding="utf-8")
    script = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import claim_gate as c, inspect_gate as i; t=Path(sys.argv[2]).read_text(encoding='utf-8'); assert c._scan(t,c.ACTIVE_CLAIMS); i._scan(t,i.READ_CLAIMS)"
    subprocess.run([sys.executable, "-I", "-B", "-c", script, str(Path(gate.__file__).parent), str(p)], check=True, timeout=3)


def test_opted_in_pytest_runs_from_isolated_hook_without_venv(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_failure.py").write_text("def test_failure():\n assert False\n")
    script = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import verify_on_stop as h; failed,out=h._run_pytest(Path(sys.argv[2]),[],{},strict=True); print(out); assert failed"
    proc = subprocess.run([sys.executable, "-I", "-B", "-S", "-c", script,
        str(Path(gate.__file__).parent), str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 failed" in proc.stdout


def test_collection_errors_are_test_failures(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(hook, "_run", lambda *args: (2, "ERROR collecting test_bad.py"))
    assert hook._run_pytest(tmp_path, [], {}, strict=True)[0]


def test_test_discovery_prunes_dependencies(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text("")
    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded rglob must not be used")
    monkeypatch.setattr(Path, "rglob", forbidden)
    assert hook._pytest_targets_for_changed(tmp_path, ["app.py"]) == [str(tmp_path / "tests/test_app.py")]


def test_uninstall_after_user_moved_folder_preserves_foreign_hook(tmp_path):
    install.install(tmp_path)
    settings = tmp_path / ".claude/settings.local.json"
    data = json.loads(settings.read_text())
    foreign = {"hooks": [{"type": "command", "command": "echo keep"}]}
    data["hooks"]["Stop"].append(foreign)
    settings.write_text(json.dumps(data))
    (tmp_path / ".claude/proof-of-done").rename(tmp_path / "preserved-installation")
    assert install.uninstall(tmp_path)["hooks_removed"] == ["SessionStart", "Stop"]
    assert json.loads(settings.read_text())["hooks"]["Stop"] == [foreign]
