"""Third review regressions: disposable transcripts and installations only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import claim_gate as gate
import inspect_gate
import install
import verify_on_stop as hook
from test_review_regressions import trace


@pytest.mark.parametrize("prompt", [
    "Summarise what we did in this session.",
    "Please summarize the changes from this session.",
    "Give me a short recap of what we have done so far.",
    "Can you summarize our work today?",
    "Thanks. Briefly recap the work completed earlier.",
    "지금까지 한 작업을 요약해 줘", "이번 세션에서 한 일을 정리해 줘",
])
def test_retrospective_request(tmp_path, prompt):
    path = Path(trace(tmp_path))
    path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}), encoding="utf-8")
    text = "We added a sub function with a test and wrote a notes file. None of it has been tested or committed yet."
    assert gate.check(text, str(path)) is None


@pytest.mark.parametrize("prompt", [
    "Summarize the work, then update the parser.",
    "Summarize our work today and commit it.",
    "Summarize a plan for updating the parser.",
    "Explain how to write a summary function.",
    "지금까지 한 작업을 요약하고 파일도 수정해 줘",
])
def test_summary_word_does_not_exempt_new_action(tmp_path, prompt):
    path = Path(trace(tmp_path))
    path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}), encoding="utf-8")
    assert gate.check("I updated the parser.", str(path)) is not None


@pytest.mark.parametrize("text", [
    "Summary of PR #42 by Dana:\n- Added a retry wrapper\n- Removed the deprecated flag",
    "React 19 highlights:\n- Added the use hook\n- Removed the legacy API",
    "The last commit (abc, by Sam):\n- Updated the README\n- Fixed the typo",
    "Changelog for 4.0:\nAdded the streaming API.\nRemoved the v1 endpoints.",
    "Code review findings:\n1. Added the cache without an eviction policy.\n2. Removed the validation.",
    "Here is a draft PR description:\n\n## Summary\n- Added a retry wrapper\n- Fixed the timeout",
    "Suggested commit message:\n\nFixed the parser crash on empty input",
    "Draft release notes for v1.4:\n- Added the export command",
    "The pre-commit hook ensures tests pass before every push.",
    "The PR template requires that all tests pass before merge.",
    "Tests passing is a prerequisite for the release job.",
    "This test passes only when TZ=UTC is set.",
    "The flaky test passes locally but fails in CI.",
    "Verified users can post without moderation.",
    "수정 완료 후 다시 실행하세요.", "테스트 완료 후 머지해 주세요.",
    "입력값 검증 완료 시 다음 단계로 넘어갑니다.",
    "웹훅 핸들러는 서명이 검증됨을 확인한 뒤 처리합니다.",
    "어제 배포했던 버전에서 문제가 생겼습니다.",
    "아까 수정했던 부분을 설명드리면, 캐시 키 형식이 바뀌었습니다.",
    "이 함수는 입력을 처리했던 이전 로직과 다릅니다.",
])
def test_descriptions_drafts_and_attributed_blocks(tmp_path, text):
    path = trace(tmp_path, "Read")
    assert gate.check(text, path) is None
    assert inspect_gate.check(text, path) is None


@pytest.mark.parametrize("text", [
    "I made the changes you asked for.", "Tests: 12 passed, 0 failed.",
    "Changelog for v2:\n- Added the old feature\n\nI fixed the current parser.",
    "The hook ensures tests pass. I updated the parser.",
    "수정 완료했습니다.", "테스트 완료했습니다.",
])
def test_current_claims_remain_checked(tmp_path, text):
    assert gate.check(text, trace(tmp_path, "Read")) is not None


@pytest.mark.parametrize("name,command", [
    ("Bash", "git push origin main 2>&1 | tail -3"),
    ("Bash", "npm install zod 2>&1 | tail -5"),
    ("Bash", "cat > config.yaml <<'EOF'\ntext: \"quoted\nEOF"),
    ("Bash", "git commit -F - <<'EOF'\nfix one \" quote\nEOF"),
    ("Bash", "git add -A && git commit -m fix && git log --oneline | head -3"),
    ("PowerShell", "Get-ChildItem *.txt | ForEach-Object { (Get-Content $_) | Set-Content $_ -Encoding utf8 }"),
    ("Bash", "gh pr create --title fix --body done"), ("Bash", "gh pr merge 7 --squash"),
    ("Bash", 'git tag -a v1.2 -m "Release"'), ("Bash", 'git -C vendor/lib commit -am "fix"'),
    ("Bash", "perl -pi -e 's/teh/the/g' README.md"), ("Bash", "find . -name '*.pyc' -delete"),
    ("PowerShell", "Rename-Item old.txt new.txt"), ("Bash", "uv add requests"),
    ("Bash", "kubectl apply -f deploy.yaml"), ("Bash", "npx vercel --prod"),
    ("Bash", "firebase deploy --only hosting"), ("Bash", "psql db -f migration.sql"),
    ("Bash", "bin/rails db:migrate"), ("Bash", "php artisan migrate --force"),
    ("mcp__serena__replace_symbol_body", ""), ("mcp__github__merge_pull_request", ""),
])
def test_mutating_call_evidence(tmp_path, name, command):
    assert gate.check("I updated the project.", trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("name,command", [
    ("Bash", "./mvnw test"), ("Bash", "bin/rails test"), ("Bash", "php artisan test"),
    ("Bash", "./vendor/bin/phpunit"), ("Bash", "tox -e py313"),
    ("Bash", "uv run --frozen pytest -q"), ("Bash", "turbo run test"),
    ("Bash", "node --test"), ("Bash", "timeout 300 pytest"),
    ("Bash", "env CI=1 npm test"), ("Bash", "cd web; npm test"),
    ("PowerShell", '$env:CI="1"; npm test'),
])
def test_runner_call_evidence(tmp_path, name, command):
    assert gate.check("All tests pass.", trace(tmp_path, name, command)) is None


@pytest.mark.parametrize("command", [
    "pytest | tail -5", "pytest; echo ok", "pytest || true",
    "echo 'git push' | cat", "cat <<'EOF'\ngit push\nEOF",
])
def test_pipes_do_not_invent_success_or_mutations(tmp_path, command):
    claim = "All tests pass." if command.startswith("pytest") else "I pushed the changes."
    assert gate.check(claim, trace(tmp_path, "Bash", command)) is not None


def test_head_revision_is_not_partial_read_command(tmp_path):
    assert inspect_gate.check("I read the whole file and the diff.", trace(tmp_path, "Bash", "cat -n app.py && git diff HEAD -- app.py")) is None


def test_whole_test_output_is_not_a_whole_file_claim(tmp_path):
    assert inspect_gate.check("테스트 출력을 전부 확인했습니다.", trace(tmp_path, "Bash", "npm test | tail -30")) is None


def test_interruption_marker_means_unknown_not_previous_turn_proof(tmp_path):
    extras = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Now I will"}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        {"type": "user", "message": {"content": "continue"}},
    ]
    assert gate.check("I fixed the parser.", trace(tmp_path, "Edit", extras=extras)) is None


def test_long_claim_dense_reply_has_bounded_cost(tmp_path):
    text = "I fixed the parser. I checked the input. All tests pass.\n" * 2000
    start = time.monotonic()
    gate.check(text, trace(tmp_path, "Read"))
    inspect_gate.check(text, trace(tmp_path, "Read"))
    assert time.monotonic() - start < 5


def test_project_python_refused_before_writing(tmp_path, monkeypatch):
    python = tmp_path / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(sys, "executable", str(python))
    with pytest.raises(ValueError, match="interpreter|Python"):
        install.install(tmp_path)
    assert not (tmp_path / ".claude").exists()


def test_hook_command_disables_site_initialization():
    command = install.hook_blocks(Path("demo"), sys.executable)["Stop"]["hooks"][0]["command"]
    assert " -S " in command


def test_files_alone_cannot_enable_default_runner(tmp_path, monkeypatch):
    install.install(tmp_path)
    (tmp_path / ".claude/proof-of-done/runner-settings.json").write_text('{"enabled":true}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [hook.__file__])
    assert not hook._runner_enabled(tmp_path)


def test_explicit_opt_in_is_in_stored_command(tmp_path):
    install.install(tmp_path, run_tests=True)
    settings = json.loads((tmp_path / ".claude/settings.local.json").read_bytes())
    assert " --run-tests" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]


def test_real_project_venv_installer_is_refused(tmp_path):
    venv = tmp_path / ".venv"
    result = subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(venv)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run([str(python), "-I", "-B", "-S", install.__file__, "--project", str(tmp_path)], capture_output=True, timeout=15)
    assert result.returncode == 2 and b"outside" in result.stderr
    assert not (tmp_path / ".claude").exists()


@pytest.mark.parametrize("opt_in", [False, True])
def test_actual_stored_command_requires_opt_in_for_local_argv(tmp_path, opt_in):
    import shutil
    bash = shutil.which("bash")
    if os.name == "nt":
        bash = r"C:\Program Files\Git\bin\bash.exe"
    if not bash or not Path(bash).is_file():
        pytest.skip("Git Bash unavailable")
    (tmp_path / ".claude").mkdir()
    marker = tmp_path / "command-ran"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran'); raise SystemExit(1)"
    (tmp_path / ".claude/verify.json").write_text(json.dumps({"test_cmd": [sys.executable, "-c", code]}), encoding="utf-8")
    install.install(tmp_path, run_tests=opt_in)
    # This same change previously escalated a default install into execution.
    (tmp_path / ".claude/proof-of-done/runner-settings.json").write_text('{"enabled":true}', encoding="utf-8")
    command = json.loads((tmp_path / ".claude/settings.local.json").read_bytes())["hooks"]["Stop"][0]["hooks"][0]["command"]
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    env.pop(hook.SKIP_ENV, None)
    result = subprocess.run([bash, "-c", command], input=b'{"last_assistant_message":"All tests pass."}', capture_output=True, env=env, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    assert marker.exists() == opt_in
    if opt_in:
        assert json.loads(result.stdout)["decision"] == "block"
    else:
        assert not result.stdout


def test_test_report_can_refer_to_previously_added_tests(tmp_path):
    assert gate.check("All 42 tests pass, including the 3 I added for the parser fix.", trace(tmp_path, "Bash", "pytest")) is None


def test_cat_piped_to_head_is_still_partial(tmp_path):
    assert inspect_gate.check("I read the whole file.", trace(tmp_path, "Bash", "cat app.py | head -10")) is not None


def test_huge_reply_is_unknown_with_diagnostic(tmp_path, capsys):
    text = "I fixed the parser. " * 20000
    assert gate.check(text, trace(tmp_path)) is None
    assert "scan limit" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["Tests are green.", "I ran the test suite and everything is green.", "The tests now pass."])
def test_additional_english_test_claims(tmp_path, text):
    assert gate.check(text, trace(tmp_path, "Read")) is not None
    assert gate.check(text, trace(tmp_path, "Bash", "pytest")) is None


def test_previous_step_check_is_historical(tmp_path):
    assert gate.check("Yes, I checked them in the previous step and they looked fine.", trace(tmp_path)) is None


def test_quote_inside_quoted_heredoc_data(tmp_path):
    command = 'git commit -m "$(cat <<\'EOF\'\nSupport 3" displays\nEOF\n)"'
    assert gate.check("Committed the fix.", trace(tmp_path, "Bash", command)) is None
    command = 'echo "$(cat <<\'EOF\'\ngit push "anything\nEOF\n)"'
    assert gate.check("I pushed the changes.", trace(tmp_path, "Bash", command)) is not None


def test_powershell_spaced_environment_assignment_before_test(tmp_path):
    assert gate.check("All tests pass.", trace(tmp_path, "PowerShell", '$env:CI = "1"; npm test')) is None
