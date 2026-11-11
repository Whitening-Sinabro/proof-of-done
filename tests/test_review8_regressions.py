"""Review 8 HIGH only: synthetic replays of public development examples.

The real-session held-out prompts are deliberately not read or copied here.
"""
import pytest

import claim_gate as gate
import install
from test_review4_regressions import user, call, result, transcript
from test_review7_regressions import invoke


def final(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def work_history():
    return [user("Fix the parser crash and run its tests"),
            call("Edit", tid="edit"), result(tid="edit"),
            call("Bash", "pytest -q", "tests"), result("12 passed in 0.40s", "tests"),
            final("Fixed the parser crash. All 12 tests pass.")]


def read_history():
    return [user("Look into the parser crash"), call("Read", tid="read"),
            result(tid="read"), final("The crash comes from indexing an empty list.")]


STATUS = ["Is it done?", "Did the tests pass?", "Did you also run the tests?",
          "How's it going?", "Are we good?", "All good?", "So it works now?",
          "Thanks, that works!", "Perfect, thanks!", "Thank you so much!",
          "Did that fix it?", "Everything ok?", "Where are we at?", "Any luck?",
          "Is the crash gone?", "Tests green?", "Did it pass?",
          "Nice, so the crash is fixed?", "됐어?", "고마워!", "테스트 다 통과했어?", "What did you do?"]


@pytest.mark.parametrize("prompt", STATUS)
@pytest.mark.parametrize("honest", [True, False], ids=["after-work", "after-read-only"])
def test_status_needs_actual_prior_evidence(tmp_path, prompt, honest):
    events = (work_history() if honest else read_history()) + [user(prompt)]
    finding = gate.check("Yes. I fixed the parser crash and all 12 tests pass.",
                         transcript(tmp_path, events))
    assert (finding is None) == honest


@pytest.mark.parametrize("prompt", ["Tests green?", "Is it all done?", "결과는 어때?"])
def test_status_can_cross_completed_conversational_followups(tmp_path, prompt):
    events = work_history() + [user("다 됐어?"), final("네, 끝났습니다."),
                               user("Perfect, thanks!"), final("You're welcome."), user(prompt)]
    assert gate.check("I fixed the parser. All 12 tests pass.", transcript(tmp_path, events)) is None


@pytest.mark.parametrize("prompt", ["Rename config.py to settings.py", "Make the requested changes",
    "Verify the fix", "Upgrade the dependency", "Bump the package version", "Read config.py",
    "Could you please fix the parser?", "Help me fix the parser", "I need you to run tests",
    "Thanks! Now add a retry.", "이제 버그를 수정해줘", "Please handle the request.",
    "Compose the reply handler for the webhook"])
def test_new_action_does_not_borrow_work_from_before_it(tmp_path, prompt):
    events = work_history() + [user(prompt), call("Read", tid="new-read"), result(tid="new-read")]
    assert gate.check("I fixed it. All tests pass.", transcript(tmp_path, events))


def test_status_does_not_cross_an_unfulfilled_new_work_request(tmp_path):
    events = work_history() + [user("Read another.py"), call("Read", tid="other"),
        result(tid="other"), final("It defines an unrelated helper."), user("Tests green?")]
    assert gate.check("I fixed another.py. All tests pass.", transcript(tmp_path, events))


RECAP = ["Can you summarize what we did this session?", "Recap", "Summarize the session",
         "What did we do today?", "What have we done so far?",
         "Give me a quick summary of today's work", "Before I log off, what did we get done?",
         "Write up what we did today for my notes", "오늘 한 것 정리해줘"]


@pytest.mark.parametrize("prompt", RECAP)
def test_recap_has_historical_scope_not_only_previous_turn(tmp_path, prompt):
    events = work_history() + [user("What does parse return for empty input?"),
        call("Read", tid="explain"), result(tid="explain"), final("It returns None."), user(prompt)]
    assert gate.check("이번 세션에서 함수를 추가했습니다. All 12 tests pass.",
                      transcript(tmp_path, events)) is None


@pytest.mark.parametrize("prompt", ["Summarize the session and push the fix",
    "What did we do today? Now verify the deployment.", "오늘 한 것 정리하고 새 테스트를 추가해줘"])
def test_recap_with_new_action_is_still_checked(tmp_path, prompt):
    assert gate.check("I fixed the deployment. All tests pass.",
                      transcript(tmp_path, work_history() + [user(prompt)]))


REVIEW = ["Review my changes", "Hey, can you review my latest changes?", "Take a look at my diff",
          "Check what I changed", "Look over the PR before I merge", "Go through the diff and flag issues",
          "What do you think of these changes?", "Quick review please", "I pushed a fix, can you review it?",
          "Sanity-check my diff", "최근 커밋 좀 봐줘", "이 diff 검토해줘"]


@pytest.mark.parametrize("prompt", REVIEW)
@pytest.mark.parametrize("reply", ["Here's what changed:\n- Added a retry wrapper\n- Removed the timeout",
    "최근 커밋은 calc.py에 12줄을 추가했고 삭제된 줄은 없습니다."])
def test_readonly_change_descriptions_are_not_new_mutation_claims(tmp_path, prompt, reply):
    events = [user(prompt), call("Bash", "git show HEAD", "show"), result(tid="show")]
    assert gate.check(reply, transcript(tmp_path, events)) is None


@pytest.mark.parametrize("prompt", ["Review the diff and fix the issues", "Check my changes, then rename config.py"])
def test_review_plus_new_work_remains_checked(tmp_path, prompt):
    events = [user(prompt), call("Bash", "git diff", "diff"), result(tid="diff")]
    assert gate.check("I fixed the issues.", transcript(tmp_path, events))


DRAFTS = ["Write a quick email to Sam saying the login bug is fixed",
          "Can you draft a reply to the customer saying it's fixed?",
          "Draft a short, friendly reply to Sam about the login fix",
          "Help me write a Slack message for the team about the fix",
          "Draft an update for the client about the login bug",
          "Compose a note for the customer about the new release",
          "의뢰인에게 보낼 메시지 써줘", "팀 공지 써줘"]
EMAIL = "Hi Sam,\n\nWe've fixed the login bug. All tests pass.\n\nBest,\nAlex"


@pytest.mark.parametrize("prompt", DRAFTS)
@pytest.mark.parametrize("lead", ["", "Here's a draft:\n\n", "Here's a quick email you can send:\n\n"])
def test_draft_object_not_a_fixed_list_of_prompt_phrasings(tmp_path, prompt, lead):
    assert gate.check(lead + EMAIL, transcript(tmp_path, [user(prompt)])) is None


@pytest.mark.parametrize("prompt", ["Write a quick email and then commit the fix",
    "Help me write a Slack message and save it to message.txt", "Fix the bug and draft a client update",
    "Write an email validation function", "Compose the reply handler for the webhook"])
def test_draft_words_do_not_hide_actual_work_requests(tmp_path, prompt):
    assert gate.check("I fixed it and all tests pass.", transcript(tmp_path, [user(prompt)]))


def test_attributed_email_survives_greeting_and_paragraph_breaks(tmp_path):
    events = [user("Review the fix and suggest wording"), call("Read"), result()]
    assert gate.check("Here's a draft:\n\n---\nSubject: Fix\n\n" + EMAIL + "\n\n---\n",
                      transcript(tmp_path, events)) is None


@pytest.mark.parametrize("outside", ["I fixed the parser.\n\n", "\n\nI committed the fix."])
def test_claim_outside_attributed_email_is_not_hidden(tmp_path, outside):
    draft = "Here's a draft:\n\n" + EMAIL
    reply = outside + draft if outside.startswith("I fixed") else draft + outside
    assert gate.check(reply, transcript(tmp_path, [user("Fix the parser and draft an email")]))


@pytest.mark.parametrize("prompt,reply,history,command", [
    ("Tests green?", "Yes, both tests passed. I haven't run them again just now.", True, None),
    ("오늘 한 것 정리해줘", "calc.py에 sub 함수를 추가했습니다.", True, None),
    ("최근 커밋 좀 봐줘. 뭐가 바뀌었는지 알려줘.", "최근 커밋은 12줄을 추가했고 삭제된 줄은 없습니다.", False, "git show HEAD"),
    ("Write a quick email to Sam saying we fixed the login bug", "Here's a draft:\n\n" + EMAIL, False, None),
])
def test_public_real8_examples_through_installed_default_command(tmp_path, prompt, reply, history, command):
    install.install(tmp_path)
    events = work_history() if history else []
    if history:
        events += [user("Perfect, thanks!"), final("You're welcome.")]
    events += [user(prompt)]
    if command:
        events += [call("Bash", command, "current"), result(tid="current")]
    p = transcript(tmp_path, events)
    completed = invoke(tmp_path, payload={"transcript_path": p, "last_assistant_message": reply})
    assert completed.returncode == 0 and not completed.stdout.strip(), completed.stdout.decode(errors="replace")
