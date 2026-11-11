# The numbers behind this repo

Everything here comes from one developer's own Claude Code sessions. It is a record of what happened in one setup, not a study. The counts were taken on 11 November 2026.

## 1. A written rule, followed in fewer than half the sessions

The rule: after a substantial or security-related change, ask a fresh reviewer to try to refute it before calling it done. It was written in the always-loaded instructions and re-taught at least four times.

A small logger runs at every stop. It reads the session transcript and records whether the session changed code or configuration, and whether an independent review was requested anywhere in the session. It never blocks anything.

| Period | Sessions with a counted change | Review requested at some point | No review requested at all |
|---|---|---|---|
| Jul 2026 (from the 3rd) | 76 | 53 | 23 |
| Aug 2026 | 65 | 30 | 35 |
| Sep 2026 | 148 | 85 | 63 |
| Oct 2026 | 103 | 16 | 87 |
| Nov 2026 (to the 11th) | 12 | 6 | 6 |
| Total | 404 | 190 | 214 |

How to read it:

- A session is counted once, by its last judged stop. The raw log has more than 5,600 rows because it writes one row per stop.
- "No review requested" is the more reliable column. It means the log found no reviewer being dispatched in that session. 201 of the 214 are high confidence. In 13 an agent was launched and the logger may have missed a review. One session's later stops were too large to judge.
- "Review requested" is an upper bound. The logger matches words in the request (refute, adversarial, review skills and similar). It does not check that the review was any good. In 152 of the 190 the request names a changed file; in 38 it does not.
- A change counts when an edit touches a code or config file, or a shell command contains a deploy, push, migration or database keyword anywhere in it. The logger does not check that the command actually did that. Any single edit counts, not only substantial ones. Test files, temp folders and dependency folders are excluded. In 52 of the 404 sessions (31 of the 214) the only counted change was such a command. The log keeps only the first eight changes of a session, so that 52 is a floor.
- 328 rows were skipped because the transcript was too large to read. They are not in the table.
- The log does not say why October was worse. I have not investigated it.

## 2. Why searching for the rule did not surface it

I had a memory tool that attached related past notes to each message when their similarity to the message passed 0.30. An independent check of that tool on 3 July 2026 measured the same rule against four kinds of request:

| Request | Similarity to the rule | Attached? |
|---|---|---|
| Build something | 0.09 | no |
| Add a feature | 0.12 | no |
| Security and deploy work (the case the rule was written for) | 0.19 | no |
| A conversation already about verification | 0.49 | yes |

The rule appeared when it was redundant and stayed silent when it was needed. A behaviour rule is not written in the words of the situations it governs.

The same check stopped a fix I had designed ("show a rule more strongly the more often it was re-taught"): the counter I meant to use went up when the same text was saved again, not when the rule was re-taught. The design was dropped before any code was written.

## 3. What the checker in this repo has caught and missed

Dated notes from the checker's own history:

- 29 Jul 2026. Test output containing non-English text crashed the script's decoding. The crash reached the outer safety net, which lets the reply through, so findings already collected were lost with it.
- 6 Nov 2026. In a repository with thousands of uncommitted changes, a session that only read files set off the whole test suite and hit a 180-second limit. The script now records the working tree at session start and tests only what the session changed, unless the reply claims tests pass.
- 11 Nov 2026. A reply that listed the trigger words as examples was refused, because no file was edited in that turn. Quoting looked like claiming.

## What this does not show

- That the checker reduces skipped reviews. The logger and the checker measure different things, and there is no before-and-after comparison.
- Anything about other people's setups, other models or other tools.
- A cause for any change between months.
