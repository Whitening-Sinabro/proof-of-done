"""Public English behavior: claims, qualifiers, quotes, and clause boundaries."""
import json

import pytest
import claim_gate
import inspect_gate


def transcript(tmp_path, tools=()):
    events = [{"type": "user", "message": {"content": "Do the requested task."}}]
    for number, name in enumerate(tools):
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "id": f"t{number}", "input": {"command": "pytest"}}]}})
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{number}", "is_error": False, "content": "ok"}]}})
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("text", [
    "I have not fixed it.", "I haven't fixed it.", "We have never deployed it.",
    "It should be fixed.", "It can be removed.", "It might be fixed.",
    "I will have completed it tomorrow.", "I expect all tests pass.",
    "All tests should pass.", "Tests have not passed.", "I have not verified it.",
    "Previously I fixed the bug.", "Earlier tests passed.", "I fixed it yesterday.",
    'The example says "I fixed the bug".', "The example says 'I fixed the bug'.",
    "The example says “All tests passed”.", "The example says ‘All tests passed’.",
    "> I fixed the bug.\nThis is quoted output.", "`I fixed it` is an example.",
    "```text\nI fixed it.\n```", "Is it fixed?", "Have all tests passed?",
    "The word fixed is a trigger.", "A fixed income product is an example.",
    "The removed field is optional.", "Look at the completed tasks list.",
])
def test_qualified_or_quoted_text_is_not_a_claim(tmp_path, text):
    assert claim_gate.check(text, transcript(tmp_path)) is None
    assert not claim_gate.detect_test_claim(text)


@pytest.mark.parametrize("text", [
    "I fixed the bug.", "We have implemented validation.", "I've fixed it.",
    "I’ve fixed it.", "Fixed the bug.", "Implemented the feature.",
    "Released the new version.",
    "All tests passed.", "Tests pass.", "I verified the result.",
    "Verified the output.",
])
def test_unsubstantiated_english_claims_block(tmp_path, text):
    assert claim_gate.check(text, transcript(tmp_path)) is not None


@pytest.mark.parametrize("text", [
    "I have not fixed it, but all tests passed.",
    "I have not fixed it; all tests passed.",
    "It may be wrong. I fixed the other bug.",
    "It may be wrong. Fixed the other bug.",
    'The example says "I fixed it". I fixed this bug.',
    "I haven't fixed that and all tests passed.",
])
def test_qualifier_does_not_hide_another_claim(tmp_path, text):
    assert claim_gate.check(text, transcript(tmp_path)) is not None


@pytest.mark.parametrize("text,tools", [
    ("I fixed the bug.", ("Edit",)),
    ("All tests passed.", ("Bash",)),
    ("I fixed it. All tests passed.", ("Write", "Bash")),
])
def test_claim_with_current_turn_evidence_passes(tmp_path, text, tools):
    assert claim_gate.check(text, transcript(tmp_path, tools)) is None


def test_failed_edit_is_not_support(tmp_path):
    path = transcript(tmp_path, ("Edit",))
    text = open(path, encoding="utf-8").read().replace('"is_error": false', '"is_error": true')
    from pathlib import Path
    Path(path).write_text(text, encoding="utf-8")
    assert claim_gate.check("I fixed the bug.", path) is not None


@pytest.mark.parametrize("text,tools,label", [
    ("I reviewed the entire file.", ("Grep",), "read-scope"),
    ("I am monitoring the build.", (), "monitoring"),
])
def test_inspect_claims_block(tmp_path, text, tools, label):
    assert label in inspect_gate.check(text, transcript(tmp_path, tools))


@pytest.mark.parametrize("text", [
    "I have not reviewed the entire file.", "I should read the entire file.",
    "I am not monitoring the build.", "I might be monitoring the build later.",
    "This is not safe.", "There may be no problems.",
    'The report says "This is safe".', 'An example: "monitoring the build".',
])
def test_inspect_qualified_claims_do_not_block(tmp_path, text):
    assert inspect_gate.check(text, transcript(tmp_path, ("Grep",))) is None


@pytest.mark.parametrize("text", ["The bug is fixed.", "It has been removed.", "It is verified.", "This is safe.", "There are no problems."])
def test_descriptive_passive_or_verdict_without_personal_action_is_out_of_scope(tmp_path, text):
    # v2 intentionally limits English action claims to personal/bare completion.
    path = transcript(tmp_path)
    assert claim_gate.check(text, path) is None
    assert inspect_gate.check(text, path) is None
