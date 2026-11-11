"""Regression tests for the shipped inspect gate."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inspect_gate import check  # noqa: E402



def _write(events: list[dict]) -> str:
    # Complete legacy synthetic calls; missing-result behaviour is tested in v6.
    existing = {b.get("tool_use_id") for e in events for b in e.get("message", {}).get("content", [])
                if isinstance(b, dict) and b.get("type") == "tool_result"}
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
            for b in e.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b["id"] not in existing:
                    f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                        "tool_use_id": b["id"], "is_error": False, "content": "ok"}]}}) + "\n")
    return path


def _user(text: str = "요청") -> dict:
    return {"type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _say(text: str) -> dict:
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _tool(name: str, input_: dict | None = None, id_: str = "t1") -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": name, "input": input_ or {}, "id": id_}]}}


def _run(events: list[dict], *, check_verdicts=False) -> str | None:
    p = _write(events)
    try:
        return check(transcript_path=p, check_verdicts=check_verdicts)
    finally:
        Path(p).unlink(missing_ok=True)



def test_r1_blocks_full_read_claim_with_only_grep():
    """Regression: r1 blocks full read claim with only grep."""
    out = _run([
        _user(),
        _tool("Bash", {"command": "grep -nA20 'def _gate_check' src/hooks/pre_tool.py"}),
        _say("전체 확인했습니다. destructive 차단 로직이 아예 없습니다."),
    ])
    assert out is not None
    assert "read-scope" in out


def test_r1_blocks_when_grep_tool_used():
    out = _run([
        _user(),
        _tool("Grep", {"pattern": "mcp__", "path": "commands/"}),
        _say("커맨드 9개 전부 확인했습니다."),
    ])
    assert out is not None and "read-scope" in out


def test_r1_passes_with_read_call():
    out = _run([
        _user(),
        _tool("Read", {"file_path": "/x/pre_tool.py"}),
        _say("파일을 전부 읽었습니다."),
    ])
    assert out is None


def test_r1_passes_when_read_and_grep_both_used():
    """Regression: r1 passes when read and grep both used."""
    out = _run([
        _user(),
        _tool("Grep", {"pattern": "def "}, "t1"),
        _tool("Read", {"file_path": "/x/a.py"}, "t2"),
        _say("전체를 확인했습니다."),
    ])
    assert out is None


def test_r1_ignores_claim_without_scope_word():
    """Regression: r1 ignores claim without scope word."""
    out = _run([
        _user(),
        _tool("Grep", {"pattern": "x"}),
        _say("해당 줄을 확인했습니다."),
    ])
    assert out is None


def test_r1_ignores_negated_sentence():
    out = _run([
        _user(),
        _tool("Grep", {"pattern": "x"}),
        _say("전체를 다 읽지는 못했습니다."),
    ])
    assert out is None


def test_r1_ignores_code_block():
    out = _run([
        _user(),
        _tool("Grep", {"pattern": "x"}),
        _say("```\n전체 파일을 전부 읽었습니다\n```\n라고 출력됩니다."),
    ])
    assert out is None



def test_future_watch_plan_is_not_current_monitoring():
    """V5 mirrors English future-plan exclusion; present-tense still checked."""
    out = _run([
        _user(),
        _say("생성이 도는 동안 실시간으로 지켜보겠습니다."),
    ])
    assert out is None


def test_r2_blocks_monitoring_phrase():
    out = _run([
        _user(),
        _tool("Bash", {"command": "echo hi"}),
        _say("백그라운드 작업을 모니터링 중입니다."),
    ])
    assert out is not None and "monitoring" in out


def test_r2_passes_with_screenshot_polling():
    out = _run([
        _user(),
        _tool("mcp__claude-in-chrome__computer", {"action": "screenshot"}),
        _say("실시간으로 확인하고 있습니다."),
    ])
    assert out is None


def test_r2_passes_with_bashoutput_polling():
    out = _run([
        _user(),
        _tool("BashOutput", {"bash_id": "b1"}),
        _say("계속 지켜보고 있습니다."),
    ])
    assert out is None


def test_r2_ignores_future_plan_question():
    out = _run([_user(), _say("실시간으로 감시할까요?")])
    assert out is None



def test_r3_blocks_verdict_with_no_tools():
    """Legacy callers explicitly opting in can still check zero-tool verdicts."""
    out = _run([
        _user(),
        _say("노션 연동은 경쟁이 거의 없습니다."),
    ], check_verdicts=True)
    assert out is not None and "unsupported-verdict" in out


def test_r3_blocks_regression_verdict_with_no_tools():
    out = _run([_user(), _say("이건 실제 회귀입니다.")], check_verdicts=True)
    assert out is not None and "unsupported-verdict" in out


def test_r3_passes_when_tool_was_called():
    out = _run([
        _user(),
        _tool("Bash", {"command": "git log --oneline -5"}),
        _say("이건 실제 회귀입니다."),
    ])
    assert out is None


def test_r3_ignores_negation():
    out = _run([_user(), _say("경쟁이 없다고 단정할 수 없습니다.")])
    assert out is None


def test_r3_ignores_question():
    out = _run([_user(), _say("이게 회귀입니까?")])
    assert out is None



def test_no_transcript_returns_none():
    assert check(transcript_path=None) is None


def test_empty_text_returns_none():
    out = _run([_user(), _tool("Read", {"file_path": "/x"})])
    assert out is None


def test_multiple_violations_are_all_reported():
    out = _run([
        _user(),
        _tool("Grep", {"pattern": "x"}),
        _say("전체를 확인했습니다. 그리고 실시간으로 감시하고 있습니다."),
    ])
    assert out is not None
    assert "read-scope" in out and "monitoring" in out


def test_clean_turn_passes():
    out = _run([
        _user(),
        _tool("Read", {"file_path": "/x/a.py"}, "t1"),
        _tool("BashOutput", {"bash_id": "b1"}, "t2"),
        _say("파일을 전부 읽었고 실시간으로 지켜보고 있습니다. 확인됐습니다."),
    ])
    assert out is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v) and not v.__code__.co_argcount]
    passed, failed = 0, []
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception:
            failed.append((fn.__name__, traceback.format_exc(limit=2)))
    print(f"{passed} passed, {len(failed)} failed")
    for name, tb in failed:
        print(f"\n--- {name} ---\n{tb}")
    sys.exit(1 if failed else 0)
