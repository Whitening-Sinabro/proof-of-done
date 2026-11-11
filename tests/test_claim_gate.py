"""Regression tests for the shipped claim gate."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from claim_gate import check, detect_test_claim


def _write_transcript(events: list[dict]) -> str:
    # These legacy examples describe completed calls. Supply the result records
    # omitted by the original fixtures; incomplete traces have separate tests.
    existing = {b.get("tool_use_id") for e in events for b in e.get("message", {}).get("content", [])
                if isinstance(b, dict) and b.get("type") == "tool_result"}
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
            for b in e.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b["id"] not in existing:
                    f.write(json.dumps(_tool_result(b["id"]), ensure_ascii=False) + "\n")
    return path


def _user(text: str = "Implement the requested feature.") -> dict:
    # These fixtures exercise a new-work request. An unspecified "request"
    # cannot distinguish work from a read-only review under the v9 policy.
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _tool_result(tool_use_id: str = "tid1", is_error: bool = False, output: str = "ok") -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": output,
                "is_error": is_error,
            }],
        },
    }


def _assist_tool(name: str, input_: dict, id_: str = "tid1") -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": input_, "id": id_}],
        },
    }


def _assist_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }




def test_active_claim_with_edit_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Edit", {"file_path": "x"}),
    ])
    assert check("기능을 구현했습니다.", tp) is None


def test_active_claim_only_read_block():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    result = check("기능을 구현했습니다.", tp)
    assert result is not None
    assert "Active claim" in result


def test_test_claim_with_pytest_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "python -m pytest tests/"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", is_error=False, output="5 passed"),
    ])
    assert check("모든 테스트 통과했습니다.", tp) is None


def test_test_claim_only_read_block():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    result = check("테스트 완료.", tp)
    assert result is not None
    assert "Test/Verify claim" in result


def test_negation_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    assert check("아직 구현 못했습니다.", tp) is None


def test_codeblock_claim_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    text = "예시 출력:\n```\n구현 완료\n```\n"
    assert check(text, tp) is None




def test_previous_turn_edit_makes_claim_time_unknown():
    """V5: matching earlier evidence is unknown timing, not current proof."""
    tp = _write_transcript([
        _user("첫 요청"),
        _assist_tool("Edit", {"file_path": "x"}, id_="tid1"),
        _user("다음 질문"),
        _assist_tool("Read", {"file_path": "y"}, id_="tid2"),
    ])
    result = check("기능을 구현했습니다.", tp)
    assert result is None


def test_test_claim_with_failed_pytest_block():
    """Regression: test claim with failed pytest block."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "pytest"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", is_error=True, output="1 failed"),
    ])
    result = check("모든 테스트 통과.", tp)
    assert result is not None
    assert "Test/Verify claim" in result




def test_english_active_claim_with_write_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Write", {"file_path": "x"}),
    ])
    assert check("Implemented the feature.", tp) is None


def test_question_form_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    assert check("구현 완료됐나요?", tp) is None


def test_past_reference_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    assert check("이전 Phase 에서 이미 구현 완료된 상태.", tp) is None


def test_no_transcript_skip():
    """Regression: no transcript skip."""
    assert check("구현했습니다.", None) is None


def test_no_claim_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    assert check("코드를 읽었습니다.", tp) is None


def test_pnpm_test_evidence_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "pnpm run test"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", is_error=False, output="ok"),
    ])
    assert check("테스트 통과.", tp) is None


def test_bun_typecheck_evidence_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "bun run typecheck"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", is_error=False, output="ok"),
    ])
    assert check("검증 완료.", tp) is None


def test_fallback_to_transcript_text():
    """Regression: fallback to transcript text."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
        _assist_text("기능을 구현했습니다."),
    ])
    result = check("", tp)
    assert result is not None
    assert "Active claim" in result


def test_tool_result_between_same_turn_pass():
    """Regression: tool result between same turn pass."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "python -m pytest"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", output="passed"),
        _assist_tool("Edit", {"file_path": "x"}, id_="tid2"),
    ])
    assert check("기능을 구현했습니다.", tp) is None


def test_flexible_korean_variant_pass():
    """Regression: flexible korean variant pass."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Edit", {"file_path": "x"}),
    ])
    assert check("구현을 마쳤습니다.", tp) is None


def test_flexible_korean_variant_without_evidence_block():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    result = check("구현을 마쳤습니다.", tp)
    assert result is not None


def test_npm_check_exact_match_pass():
    """Regression: npm check exact match pass."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "npm run check"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", output="ok"),
    ])
    assert check("검증 완료.", tp) is None


def test_npm_unrelated_command_block():
    """Regression: npm unrelated command block."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "npm install"}, id_="tid1"),
        _tool_result(tool_use_id="tid1", output="ok"),
    ])
    result = check("테스트 통과.", tp)
    assert result is not None




def test_deployment_claim_with_edit_pass():
    """Regression: deployment claim with edit pass."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Edit", {"file_path": "x"}),
    ])
    assert check("새 버전을 배포했습니다.", tp) is None


def test_commit_claim_without_evidence_block():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    result = check("변경사항을 커밋했습니다.", tp)
    assert result is not None
    assert "Active claim" in result


def test_english_released_with_write_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Write", {"file_path": "x"}),
    ])
    assert check("Released the new version.", tp) is None


def test_english_deployed_without_evidence_block():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    result = check("Deployed to production.", tp)
    assert result is not None


def test_integration_claim_with_edit_pass():
    tp = _write_transcript([
        _user(),
        _assist_tool("Edit", {"file_path": "x"}),
    ])
    assert check("두 모듈을 통합했습니다.", tp) is None


def test_merge_claim_without_evidence_block():
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    result = check("브랜치를 병합했습니다.", tp)
    assert result is not None




def test_nonexistent_transcript_path_skip():
    """Regression: nonexistent transcript path skip."""
    assert check("기능을 구현했습니다.", "C:/nonexistent/path_9999.jsonl") is None


def test_empty_text_no_transcript_skip():
    """Regression: empty text no transcript skip."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
    ])
    assert check("", tp) is None


def test_empty_last_message_but_transcript_has_claim_block():
    """Regression: empty last message but transcript has claim block."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Read", {"file_path": "x"}),
        _assist_text("기능을 배포했습니다."),
    ])
    result = check("", tp)
    assert result is not None
    assert "Active claim" in result


# === detect_test_claim: strict-mode trigger ===

def test_detect_test_claim_english_all_tests_pass():
    assert detect_test_claim("All tests passed!", None) is True


def test_detect_test_claim_korean_verify():
    # Generic verification need not execute tests; it uses the verify evidence rule.
    assert detect_test_claim("검증 완료", None) is False


def test_detect_test_claim_active_only_false():
    """Regression: detect test claim active only false."""
    assert detect_test_claim("기능을 구현했습니다", None) is False


def test_detect_test_claim_empty_false():
    assert detect_test_claim("", None) is False


def test_detect_test_claim_negation_skipped():
    """Regression: detect test claim negation skipped."""
    assert detect_test_claim("아직 검증 안 됐", None) is False


def test_detect_test_claim_past_ref_skipped():
    """Regression: detect test claim past ref skipped."""
    assert detect_test_claim("이전 세션에서 이미 검증 완료", None) is False


def test_detect_test_claim_question_skipped():
    """Regression: detect test claim question skipped."""
    assert detect_test_claim("all tests passed?", None) is False


def test_detect_test_claim_falls_back_to_transcript():
    """Regression: detect test claim falls back to transcript."""
    tp = _write_transcript([
        _user(),
        _assist_tool("Bash", {"command": "pytest"}),
        _assist_text("All tests passed."),
    ])
    assert detect_test_claim("", tp) is True


def test_detect_test_claim_code_block_excluded():
    """Regression: detect test claim code block excluded."""
    msg = "```python\n# all tests pass (comment)\nprint('hi')\n```\n\n구현만 했습니다."
    assert detect_test_claim(msg, None) is False
