# Proof of Done

A Stop hook for Claude Code. When the agent's reply says it changed files or that tests pass, and the session shows no edit or no test run, the hook sends the reply back to the agent with the reason.

The agent then sees this and has to correct its report:

```text
Proof of Done found unsupported claims or failed checks: claim-gate. Reconcile the report
with the evidence below. ...

[claim-gate]
Active claim ['I fixed'], but the current turn has no recognised edit or mutating command
without a reported error. tool_use: ['Read']
Test/Verify claim ['all tests pass'], but the current turn has no recognised
test/type-check/lint command without a reported error. ...
```

That was the reply "I fixed the null check in auth.py and all tests pass." after a turn that only read `auth.py`. The same reply after an edit and a passing `pytest` run goes through without a message.

한국어 안내는 [아래](#한국어)에 있습니다.

## What it checks

| The reply says | The turn must contain |
|---|---|
| It changed something: "I fixed", "I've added", "we removed" | A file edit (Edit, Write, MultiEdit, NotebookEdit) or a recognised command that changes files, with no error reported |
| Tests pass | A recognised test, type-check or lint command, with no error reported |
| "I verified", "I checked" | At least one completed tool call |
| It read the whole file | A file read, not only a search or a few lines |
| It is monitoring something | A polling call, such as reading a background task's output |

Korean replies are checked too (수정 완료, 테스트 통과, 검증 완료 and similar).

## When it does not check

- Questions, negations and hedges ("not", "should", "will", "might"), text inside code marks or quotes, and mentions of earlier sessions.
- Drafts you asked for. In "Draft a reply to the client saying the bug is fixed", the claim belongs to the draft.
- Summaries you asked for: "Summarize the session", "What did we do today?".
- Short follow-ups such as "Done?" or "All good?". These can be answered from the most recent turn that did work.
- A review in a turn that only read. Describing what a commit added is not a claim.
- When the evidence is unclear, it lets the reply through. That includes a turn that used a tool it cannot classify: a helper agent, an MCP tool or an unfamiliar command.

It is a heuristic. It checks that evidence exists, not that the work is right.

## Results so far

All numbers come from our own test runs on this version. None come from other people's setups.

- Real sessions: 20 turns in four throwaway projects, Claude Code 2.1.322 with claude-opus-5-5. The prompts were written before this version and kept from the agent that wrote the code. It refused 0 replies. The previous version wrongly refused 5 of the same 20 (three summaries and two drafts).
- In those sessions no reply claimed work that had not happened. When we asked the model to report work it had not done, it refused. So we have no real-session measure of how many false claims it catches. The catch rates below come from written test cases.
- Written test cases, held out from the code author: 53 of 55 false claims refused, 0 of 137 honest or claim-free replies refused.
- All five case sets, including the ones used during development: 147 of 160 false claims refused, 1 of 418 honest or claim-free replies refused.
- A separate reviewer's set, run through the installed hook in four session shapes: 20 of 24 false claims refused and 0 of 20 honest replies refused, in every shape.

## Known misses

From our own test cases:

- "I fixed the null check:" followed by a code block. It reads this as a change shown in the reply, not a claim about a file.
- A "fixed" reply after a command it cannot classify, such as `find`. The evidence counts as unknown, and unknown passes.
- Verbs outside its list, such as "I've bumped requests to 2.32".
- One edit anywhere satisfies "fixed", even in the wrong file.
- Partial work passes. Ten of 210 items done and reported as done is not caught.

If it misfires on your sessions, open an issue with the reply text and the tools the turn used.

## Requirements

- Claude Code with hooks, and `bash` (Git Bash on Windows). Codex CLI is not supported yet: it has Stop hooks, but its session log has a different format, which this checker does not read.
- Python 3.13, installed outside the project. Other versions have not been tested. The installer refuses the project's own virtual environment.
- No third-party packages. No network calls.

Tested only on Windows 11 with Python 3.13. Older Python versions, macOS and Linux have not been run.

## Install

```text
git clone https://github.com/Whitening-Sinabro/proof-of-done
python proof-of-done/install/install.py --project /path/to/your/project
python proof-of-done/install/doctor.py --project /path/to/your/project
```

Add `--dry-run` to the install command to see what it would add first. Install works on one project at a time and does not touch your user-level settings. It:

- adds one SessionStart entry and one Stop entry to `.claude/settings.local.json` and keeps your other hooks. If the file existed, a copy goes to `.claude/proof-of-done/backups/`.
- copies four files to `.claude/proof-of-done/hooks/` and writes a `.gitignore` there, so none of it is committed.

Start a new Claude Code session in the project after installing.

## Check that it works

`doctor.py` checks the wiring and sends two made-up transcripts through the installed hook. Expected output:

```json
{
  "status": "pass",
  "wiring": "project-local",
  "synthetic_false_claim": "blocked",
  "synthetic_claim_with_edit": "allowed",
  "real_claude_session": "not tested",
  "global_or_managed_settings": "not checked"
}
```

This shows that the hook runs. It does not show how it behaves in your real sessions.

## Optional: run tests when the agent stops

```text
python proof-of-done/install/install.py --project /path/to/your/project --run-tests
```

With this option, when a reply claims a change or passing tests, the hook also runs pytest, tsc, vitest or jest on the files changed in the session. A reply that says all tests pass runs the whole suite. You can list your own commands in `.claude/verify.json`. It is off by default. It is not a sandbox: it runs your project's code.

## Turn it off

- For one session: set `CLAUDE_SKIP_STOP_VERIFY=1` before you start Claude Code.
- For one project, for a while: create the file `.claude/skip-stop-verify`.
- Disconnect: run the install command with `--uninstall`. It removes only its own two entries. The files stay in `.claude/proof-of-done/`, and you can delete that folder yourself.

## If the installed files change

The install command records a fingerprint of each of the four files. Every run compares them first. If a file differs, an extra file appears, or the folder is a link, the hook does not run them. It shows "Proof of Done skipped: ..." and lets the reply through. Run `doctor.py`, then reinstall.

## Why I built it

I wrote one rule into my always-loaded instructions: have a fresh reviewer try to refute a substantial or security-related change. I re-taught it at least four times. Over four months my own log shows 404 sessions in which the agent edited code or configuration files, or ran a command with a deploy, push, migration or database keyword. In 214 of them the log found no request for an independent review. The table, how it was counted and what it does not show are in [DATA.md](DATA.md).

Writing the rule down was not enough. This hook checks one narrow thing by machine instead.

## Design rules that held up

1. Look at the current turn. For a short follow-up such as "Done?", also look at the most recent turn that did work.
2. Ask whether evidence exists. Do not judge its quality.
3. Skip questions, negations, quoted text, drafts and summaries the user asked for.
4. A test run that failed is not evidence.
5. When unsure, let the reply through. Wrongly refused replies teach you to switch the checker off.
6. If its own files are changed, do not run them. Say so.
7. Give it an off switch you can see.

## How it was built

An AI coding agent wrote the code. Separate AI sessions reviewed it and tested each version against cases its author had not seen. It took nine versions. The test suite is in `tests/` (931 tests).

## The paid guide

I also sell a 19-page guide, "Claude Code Field Guide: Memory, Rules & Proof of Done", for 12 USD: https://agentfieldnotes.gumroad.com/l/bnccy

It opens with ten dated records from my own work: what an agent reported, what was actually there, and who caught it. It comes with template files for project state, rules and completion reports, and twelve prompts. You do not need it to use this checker.

## 한국어

Claude Code가 "수정 완료", "테스트 통과"라고 답했는데 그 턴에 파일 수정이나 테스트 실행 기록이 없으면, 답을 Claude에게 돌려보내고 이유를 보여 주는 Stop 훅입니다. 한국어 답도 검사합니다. 요약이나 메시지 초안처럼 사용자가 요청한 글은 막지 않습니다. 증거가 있는지만 보고, 작업이 맞는지는 판단하지 않습니다.

설치는 위 Install 절의 세 줄과 같습니다. 윈도우 11, 파이썬 3.13에서만 시험했습니다. 코덱스(Codex CLI)는 아직 지원하지 않습니다. 잘못 막거나 놓친 경우가 있으면 답 원문과 그 턴에 쓴 도구를 이슈로 남겨 주세요.

### 이 검사기와 한국어 PDF 「AI에게 맡긴 일, 어디까지 됐나요?」의 차이

둘 다 "AI가 다 했다는데 실제로는 아닌" 문제를 다루지만, 보는 범위가 다릅니다.

| | 이 검사기 (무료) | 한국어 PDF (9,900원) |
|---|---|---|
| 확인하는 것 | AI가 답할 때마다, 답 속의 "고쳤다·테스트 통과"가 같은 턴의 파일 수정·테스트 실행 기록과 맞는지 | 며칠에 걸친 일이 지금 어디까지 됐는지. 작업 폴더의 기록 파일 세 개(status.md, rules.md, log.md)가 실제 파일과 맞는지 |
| 범위 | 그 턴 하나. "됐어?" 같은 짧은 후속 질문은 직전에 작업한 턴까지 | 여러 날, 여러 대화 |
| 방식 | 자동. 맞지 않으면 답을 AI에게 돌려보냄 | 자동 아님. 직접 따라 하거나, 파일을 읽고 쓸 수 있는 AI 도구에 PDF를 주고 대조·바로잡기를 맡김 |
| 쓸 수 있는 곳 | Claude Code 전용. 설치 필요(파이썬 3.13, bash) | 설치 없음. 특정 도구에 묶이지 않음 |
| 들어 있는 것 | 훅 코드, 설치·진단 도구, 시험 931개 | 20쪽. 가상 예제 하나(견적 메일 초안 3개)를 처음부터 끝까지, 예제 파일 일곱 개, 복사해 쓰는 지시문, 안 될 때 대처 |
| 못 하는 것 | 며칠짜리 진행 상황은 모름. 작업이 맞는지는 판단하지 않음 | 답할 때마다 자동으로 막지는 않음 |

검사기는 그 자리의 거짓 완료를 막고, PDF는 며칠 동안 쌓인 기록이 실제와 어긋나지 않게 합니다. PDF: https://agentfieldnotes.gumroad.com/l/uufkxc

## Licence

MIT. See [LICENSE](LICENSE).
