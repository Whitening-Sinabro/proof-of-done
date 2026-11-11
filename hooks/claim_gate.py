"""Heuristic current-turn claim/evidence check, not proof of correctness.

English descriptions and passive statements are deliberately excluded. Tool
presence does not prove that its scope matches a claim. Incomplete transcripts
and unrecognised command languages cannot establish reliable negative evidence.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
import time
from bisect import bisect_left, bisect_right
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DONE = (
    r"(?:implemented|added|fixed|completed|applied|merged|removed|deleted"
    r"|deployed|committed|released|integrated|updated|created|changed|resolved|refactored|wrote|pushed|renamed)"
)
ACTIVE_CLAIMS = [
    r"(구현|추가|수정|반영|해결|포함|처리|적용|삭제|제거|배포|커밋|푸시|완성|릴리스|통합|병합)"
    r"(?:을|를|은|는|이|가|도)?\s*(?:모두|전부|다)?\s*(?:완료|했|마쳤|끝냈|끝마쳤)",
    rf"\b(?:I|we)(?:\s+have|'ve)?\s+(?:(?:successfully|now|just|also|already|then)\s+|(?:went|gone) ahead and\s+)*{_DONE}\b",
    rf"(?:^|(?<=[.!;:])(?=\s)|(?<=\n))\s*(?:successfully\s+)?{_DONE}\b(?=\s|[.!](?:\s|$)|$)",
    r"\b(?:I|we)(?:\s+have|'ve)?\s+made\s+(?:the|a|this)\s+(?:requested\s+)?changes?\b",
    r"(?:^|(?<=[.!;\n]))\s*Done(?=\s*[.!—–]|\s*$)",
    r"\bEverything\s+(?:works|is\s+working)\s+now\b|\bThe\s+fix\s+is\s+in\s+place\b",
]
TEST_CLAIMS = [
    r"테스트(?:을|를|은|는|가|도)?\s*(?:모두|전부|다)?\s*(?:완료|통과|됐|됨|끝)",
    r"\b(?:all\s+|the\s+)?tests?\s+(?:(?:are|is|have|has|now)\s+)?pass(?:es|ed|ing)?\b",
    r"\b(?:all\s+|the\s+)?tests?\s+(?:are|is)\s+green\b",
    r"\b(?:I|we)\s+ran\s+the\s+test\s+suite\s+and\s+everything\s+is\s+green\b",
    r"\b(?:the\s+)?test\s+suite\s+pass(?:es|ed)\b",
    r"\bTests?\s*:\s*\d+\s+passed\b",
    r"\b(?:I|we)\s+ran\s+the\s+tests?\s+and\s+they\s+(?:all\s+)?pass(?:ed)?\b",
    r"\b(?:I|we)\s+ran\s+(?:the\s+)?tests?\s*;\s*they\s+(?:all\s+)?pass(?:ed)?\b",
]
VERIFY_CLAIMS = [
    r"검증(?:을|를|은|는|이|가|도)?\s*(?:모두|전부|다)?\s*(?:완료|통과|됐|됨|끝)",
    r"\b(?:I|we)(?:\s+have|'ve)?\s+(?:verified|checked)\b",
    r"(?:^|(?<=[.!;\n]))\s*verified\b",
]

NEGATION = re.compile(
    r"(미구현|미적용|미완|미검증|안\s*됐|안\s*되|못\s*했|못\s*함|실패|불가능|아직\s*(?:안|못))",
    re.IGNORECASE,
)
PAST_REF = re.compile(
    r"(이전\s*(?:세션|phase|턴|단계)|기존에|이미\s+(?:구현|검증|완료|포함|통과|PASS|pass)"
    r"|Phase\s+[A-Z0-9]+\s*에서\s*이미|어제|아까|지난번|previously|earlier)",
    re.IGNORECASE,
)
QUESTION = re.compile(r"[?？]")

ACTIVE_EVIDENCE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ADMIN_TOOLS = {"TodoWrite", "TodoRead", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"}

CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
BACKTICK = re.compile(r"`[^`\n]+`")
QUOTED = re.compile(r'''"[^"\n]*"|“[^”\n]*”|(?<!\w)'[^'\n]+'(?!\w)|‘[^’\n]+’''')
ENGLISH_QUALIFIER = re.compile(
    r"\b(?:not(?!\s+only\b)|never|no\s+longer|haven't|hasn't|hadn't|didn't|"
    r"don't|doesn't|isn't|aren't|wasn't|weren't|cannot|can't|should|could|would|"
    r"may|might|can|will|probably|possibly|perhaps|seems?|appears?|"
    r"previously|earlier|yesterday|\w+n't)\b|\b(?:I|we)\s+(?:plan|hope|expect|intend)\b",
    re.IGNORECASE,
)
MAX_TAIL_BYTES = 8 * 1024 * 1024
MAX_SCAN_CHARS = 128 * 1024
SCAN_BUDGET_SECONDS = 0.25
HISTORY_TIME = re.compile(
    r"\b(?:last|previous)\s+(?:night|week|month|year|session|turn|step|time|run|release)\b"
    r"|\b(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b", re.I,
)


@lru_cache(maxsize=4)
def _strip_code(text: str) -> str:
    # Large replies have unknown scope. Do not inspect a truncated sentence and
    # lose its qualifier; the caller emits a bounded diagnostic instead.
    if len(text) > MAX_SCAN_CHARS:
        sys.stderr.write("[proof-of-done] reply exceeds claim scan limit; claim scope unknown\n")
        return ""
    # Bound whitespace backtracking BEFORE any claim/quote regex. Keep blank
    # paragraphs, which terminate attributed blocks, but never huge runs.
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"(?m)^[ \t]*(?:[-*+] )?[ \t]*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if any(len(line) > 4096 for line in text.splitlines()):
        sys.stderr.write("[proof-of-done] long unbroken line; claim scope unknown\n")
        return ""
    text = CODE_BLOCK.sub("", text)
    text = BACKTICK.sub("", text)
    text = re.sub(r"(?m)^[ \t]*>[^\n]*", "", text)
    text = QUOTED.sub("", text)
    text = _without_attributed_blocks(text)
    # Removing a long attributed list can itself create a huge newline run.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("’", "'").replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^[ \t]*(?:[-*+] |\d+[.)] )", "", text)
    text = re.sub(r"(?m)^[ \t]*[✅✔☑][ \t]*", "", text)
    return re.sub(r"[^\S\n]+[—–][^\S\n]+", ". ", text)


class TextBounds:
    """Index once per scan; never rescan a whole reply for every claim."""
    def __init__(self, text: str):
        self.text = text
        self.sentences = [m.start() for m in re.finditer(r"[.。!?？\n]", text)]
        clauses = list(re.finditer(r"[.!?;\n]|\b(?:but|however|and)\b", text, re.I))
        self.lefts = [m.end() for m in clauses]
        self.rights = [m.start() for m in clauses if m.group(0).lower() != "and"]

    def clause(self, start: int, end: int) -> tuple[str, str]:
        i, j = bisect_right(self.lefts, start), bisect_left(self.rights, end)
        left = self.lefts[i - 1] if i else 0
        right = self.rights[j] if j < len(self.rights) else len(self.text)
        return self.text[max(left, start - 512):start], self.text[end:min(right, end + 512)]

    def sentence(self, start: int, end: int) -> str:
        i, j = bisect_left(self.sentences, start), bisect_left(self.sentences, end)
        left = self.sentences[i - 1] + 1 if i else 0
        right = self.sentences[j] + 1 if j < len(self.sentences) else len(self.text)
        return self.text[max(left, start - 512):min(right, end + 512)]


def _without_attributed_blocks(text: str) -> str:
    """Named quotes/drafts describe artifacts, not actions in the current turn.

    A plain 'Summary:' still counts as an action report. Ordinary attributed
    blocks end at a blank paragraph. Emails may contain several paragraphs;
    their greeting, signature and closing separator delimit the draft instead.
    """
    lead = re.compile(
        r"(?:changelog|release[ -]notes|draft|초안|commit message|PR (?:description|body)|this PR|this branch|"
        r"suggested (?:commit|reply)|message you can send|recent commits|last (?:\w+ )?commits?|"
        r"commit [a-f0-9]{7,40}\b|code review findings|"
        r"(?:summary of|highlights).*(?:\bby \w+|\bof PR #?\d+|\bfor v\d)|"
        r"^[# \t]*[\w.-]{1,80}[ \t]+\d+(?:\.[\w.-]{1,40})?[ \t]+(?:changes|highlights)\b)", re.I)
    artifact_lead = re.compile(
        r"^(?:here(?:'s| is)|below is)\b.{0,180}\b(?:reply|message|email|standup|draft)\b"
        r"|^(?:here(?:'s| is)|below is)\b.{0,180}\b(?:slack update|you (?:can|could) (?:send|post))\b"
        r"|^(?:message|email|reply)\s+(?:for|to)\b"
        r"|(?:보낼|전달할)\s*(?:메시지|답장|이메일)", re.I)
    result, skipping, seen_body, mail_body, signed = [], False, False, False, False
    for line in text.splitlines(keepends=True):
        plain = line.strip().replace("**", "")
        if artifact_lead.search(line.strip()) or lead.search(line) and (":" in line or line.lstrip().startswith("#")):
            # Keep any completed sentence preceding a lead-in on the same line.
            start = max(line.rfind(". "), line.rfind("! "))
            result.append(line[:start + 1] + "\n" if start >= 0 else "\n")
            skipping, seen_body, signed = True, False, False
            mail_body = bool(re.search(r"\b(?:email|reply|message|standup)\b|메시지|답장|이메일", plain, re.I))
        elif skipping and mail_body and seen_body and re.fullmatch(r"[-*_]{3,}", plain):
            skipping = False
            result.append("\n")
        elif skipping and not line.strip():
            if signed or seen_body and not mail_body:
                skipping = False
            result.append("\n")
        elif skipping:
            greeting = bool(re.match(r"^(?:hi|hello|hey|dear)\b.{0,100}[,!:]$|^subject:", plain, re.I))
            mail_body = mail_body or greeting
            if signed:
                skipping = False  # Consume the signature name, then check prose again.
            signed = mail_body and bool(re.fullmatch(
                r"(?:best(?: regards)?|kind regards|regards|thanks|thank you|cheers|sincerely|감사합니다)[,.!]*", plain, re.I))
            seen_body = seen_body or not (greeting or plain.startswith("#") or re.fullmatch(r"[-*_]{3,}", plain))
            result.append("\n")
        else:
            result.append(line)
    return "".join(result)


def _english_qualified(text: str, start: int, end: int, bounds: TextBounds | None = None) -> bool:
    """Limit qualifiers to the clause containing this hit, not a nearby claim."""
    before, after = (bounds or TextBounds(text)).clause(start, end)
    sentence_prefix = re.split(r"[.!?;\n]", text[max(0, start - 512):start])[-1]
    if re.search(r"\b(?:I'll|we'll|will)\b.*\b(?:tell|explain|show|describe)\b.*\b(?:what|how)\s*$", sentence_prefix, re.I):
        return True  # Future explanation's embedded past clause, not a new claim.
    if ENGLISH_QUALIFIER.search(before):
        return True
    if HISTORY_TIME.search(before) or HISTORY_TIME.search(after):
        return True
    if re.search(r"according to\s+(?:the\s+)?(?:CI\s+)?(?:changelog|log|history|release notes)", before + after, re.I):
        return True
    if re.search(r"\b(?:CI (?:run|log)|test (?:log|output))\s+(?:shows?|says?|reports?)\b", before, re.I):
        return True
    if re.search(r"looking at (?:the |your )?(?:log|output)", before, re.I):
        return True
    if re.search(r"\b(?:you (?:pasted|provided|shared|sent|posted|attached)|in (?:your|the pasted) (?:message|snippet))\b", after, re.I):
        return True
    if re.match(r"\s+your\s+(?:code|log|traceback|snippet|message)\b", after, re.I):
        return True
    if re.match(r"\s+the numbers\s*:\s*\d", after, re.I):
        return True
    if re.search(r"\b(?:code|snippet|example|table|query|reply)\b.{0,100}\b(?:above|below|here)\b", after, re.I):
        return True
    if re.match(r"\s+(?:the |my |our )?(?:todo|to-do|task) (?:list|status|entry|record)\b", after, re.I):
        return True
    if re.search(r"tests?", text[start:end], re.I) and re.match(r"\s+(?:because|if|when|even|a|an|the)\b", after, re.I):
        return True  # Code semantics / argument passing, not an observed run.
    if re.match(r"\s+(?:a |the |this |short )*(?:example|steps|checklist|explanation|reply)\b[^.\n]*\bbelow\b", after, re.I):
        return True  # Explicitly refers to material in this reply.
    if re.match(r"\s+the plan\b", after, re.I) and not re.search(r"\b(?:file|disk|repository|saved)\b|\.md\b", after, re.I):
        return True
    if re.search(r"\brenamed$", text[start:end], re.I) and re.match(r"\s+to\s*:", after):
        return True  # Inline-code operands were stripped from a reply snippet.
    if re.search(r"\b(?:in|on)\s+(?:PR\s*)?#\d+\b", before + after, re.I):
        return True
    if re.search(r"\b(?:make|get|have|so|to)\b", before, re.I) and re.search(r"tests?", text[start:end], re.I):
        return True
    if re.search(r"\b(?:if|when|until|unless|once|after|before|whether|nothing|no|said|says|reported|mentioned|example|ensures?|requires?|guarantees?)\b|as soon as|make sure|let me know|so that", before, re.I):
        return True
    if re.search(r"\b(?:this|that|flaky|unit|integration|regression)\s*$", before, re.I) and re.search(r"tests?", text[start:end], re.I):
        return True
    if re.match(r"\s+(?:only when|is a prerequisite|is required|is necessary)\b", after, re.I):
        return True
    if re.match(r"(?:I|we)\b", text[start:end], re.I) and re.search(r"(?:\bincluding (?:the )?\d+|\b(?:the|those|these) (?:tests|files|changes|patches))\s*$", before, re.I):
        return True  # Relative description of existing artifacts, no time asserted.
    # History/partial-result qualifiers after a claim; modal words inside the
    # object ('fixed the bug that would crash', 'May report') are not negation.
    if re.search(r"\b(?:nothing|yesterday|previously|earlier)\b|\b(?:last|previous)\s+(?:week|session|turn)|before\s+(?:my|this|the)\s+change|\bon\s+(?:main|master|another branch)\b|\b[1-9]\d*\s+(?:failed|failing)\b", after, re.I):
        return True
    hit = text[start:end].strip(" .!;:\n").lower()
    if re.fullmatch(_DONE, hit, re.I) and re.match(r"\s+and\s+(?:verified|" + _DONE + r")\b", after, re.I):
        return False  # 'Implemented and verified' is an explicit action report.
    if re.fullmatch(_DONE + "|verified", hit, re.I):
        # Bare nouns often form headings or descriptions, not an action report.
        has_object = re.match(r"\s+(?:the|a|an|this|that|it|all|both|my|your|our|two|three|four|five|\d+)\b", after, re.I)
        has_relative = re.search(r"\b(?:that|which|who|where)\b", after, re.I)
        deployment = hit in {"deployed", "released", "committed", "pushed"} and re.match(r"\s+to\b", after, re.I)
        if after.strip() and not (has_object or has_relative or deployment):
            return True
        # A leading participle can modify a subject noun phrase. Its finite
        # predicate ('updated widgets are ...') makes it a description, unlike
        # 'Updated the widget'. Relative clauses belong to the claimed object.
        finite = re.search(r"\b(?:is|are|was|were|can|must|will|would|could|should|may|remain|remains|become|becomes|contain|contains|require|requires|need|needs|help|helps|make|makes|show|shows|lead|leads|reduce|reduces|increase|increases|enable|enables|allow|allows|affect|affects|keep|keeps|give|gives|use|uses)\b", after, re.I)
        if finite and not re.search(r"\b(?:that|which|who|where|and)\b", after[:finite.start()], re.I):
            return True
    if hit in {"added", "completed", "removed", "fixed"} and re.search(r"^\s*(?:benefit\b|pitch\b|income\b|(?:tasks|items)\s+(?:are|were)\b)", after, re.I):
        return True
    return False


def _korean_qualified(text: str, start: int, end: int) -> bool:
    tail = text[end:end + 24]
    before = text[max(0, start - 100):start]
    if re.search(r"(?:아래|위|여기|이 답변).{0,50}(?:코드|표|예제|문장)|(?:할 일|작업)\s*목록", before):
        return True
    if re.match(r"\s*(?:되|했|하|됐|었|겠|려|는|다|습|니)*?(?:여부|시점|기준|조건|다고|다는|라고|으면|면|려면|는지|겠|예정|할\s*예정|던|을\s*확인한\s*뒤|[후전뒤시때](?:\s|[,.;]|$))", tail):
        return True
    if re.search(r"(?:이|해당|그)\s*PR에서", text[max(0, start - 80):start], re.I):
        return True
    if "겠" in text[start:end]:
        return True
    # Input/signature validation describes program behaviour, not a test run.
    return "검증" in text[start:end] and bool(re.search(r"(?:입력값|입력|서명|토큰|데이터)(?:이|가|을|를)?\s*$", text[max(0, start - 30):start]))


def _sentence(text: str, start: int, end: int) -> str:
    return TextBounds(text).sentence(start, end)


def _scan(text: str, patterns: list[str]) -> list[str]:
    deadline = time.monotonic() + SCAN_BUDGET_SECONDS
    stripped = _strip_code(text)
    bounds = TextBounds(stripped)
    hits: list[str] = []
    for pat in patterns:
        # Claim patterns never consume across lines: this prevents a blank-line
        # suffix from being retried at every preceding newline (quadratic work).
        for m in re.finditer(pat.replace(r"\s", r"[^\S\n]"), stripped, re.IGNORECASE):
            if time.monotonic() > deadline:
                return []  # Unknown; never return a partial negative judgment.
            sentence = bounds.sentence(m.start(), m.end())
            if NEGATION.search(sentence):
                continue
            if PAST_REF.search(sentence):
                continue
            if QUESTION.search(sentence):
                continue
            if m.group(0).isascii() and _english_qualified(stripped, m.start(), m.end(), bounds):
                continue
            if not m.group(0).isascii() and _korean_qualified(stripped, m.start(), m.end()):
                continue
            hits.append(m.group(0).strip())
            if len(hits) >= 32:
                return hits  # Bound feedback size as well as scanning work.
        if time.monotonic() > deadline:
            return []
    return hits


def _read_tail(path: Path, max_bytes: int = MAX_TAIL_BYTES) -> list[str]:
    """Bound memory by bytes, retaining long turns and a prior prompt if present.

    A partial first event is discarded. If the last real prompt is beyond this
    cap, callers keep the existing unknown/fail-open policy.
    """
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - max_bytes)
            stream.seek(start)
            raw = stream.read(max_bytes)
            if start:
                raw = raw.partition(b"\n")[2]
            # JSONL is delimited by LF, not Unicode separators inside strings.
            return raw.decode("utf-8-sig", errors="replace").split("\n")
    except OSError:
        return []


def _content(event: dict):
    msg = event.get("message", {})
    return msg.get("content") if isinstance(msg, dict) else msg


def _user_text(event: dict) -> str:
    content = _content(event)
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text") if isinstance(content, list) else ""


def _interruption(event: dict) -> bool:
    return bool(re.fullmatch(r"\s*\[Request interrupted by user(?: for tool use)?\]\s*", _user_text(event)))


def _is_real_user_prompt(event: dict) -> bool:
    """Metadata and tool-result context are not new user requests."""
    if event.get("isMeta") or event.get("isCompactSummary") or _interruption(event):
        return False
    content = _content(event)
    if isinstance(content, str):
        return True
    if not isinstance(content, list) or not content:
        return False
    return not any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _parse_events(lines: list[str]) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if isinstance(event, dict) and not event.get("isSidechain"):
                events.append(event)
        except json.JSONDecodeError:
            continue
    return events


def _current_turn(events: list[dict]) -> list[dict]:
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if ev.get("type") == "user" and _is_real_user_prompt(ev):
            return events[i + 1:]
    return []  # Missing boundary after truncation/compaction: evidence unknown.


def _request_text(events: list[dict]) -> str:
    return next((_user_text(e) for e in reversed(events)
                 if e.get("type") == "user" and _is_real_user_prompt(e)), "")


def _draft_request(events: list[dict]) -> bool:
    """A requested message draft is an artifact, not a report of external action."""
    prompt = _request_text(events)
    first = re.split(r"[.!?;\n]", prompt, maxsplit=1)[0]
    draft = re.search(
        r"\b(?:write|draft|compose)\s+(?:[\w-]+[\s,]+){0,8}"
        r"(?:reply|email|message|standup|update|post|note)\b"
        r"|(?:메시지|답장|이메일|공지|안내문).{0,50}(?:써\s*줘|작성해\s*줘|작성해)", first, re.I)
    if not draft:
        return False
    if re.search(r"\b(?:post|email|message|reply)\s+(?:endpoint|request|route|handler|queue|consumer|function|validator|validation)\b"
                 r"|\b(?:write|draft|compose)\s+(?:a |the )?[\w/-]+\.(?:md|txt|py|js|json)\b"
                 r"|\bsave\b|저장|발송해|게시해", prompt, re.I):
        return False
    # Remove only the drafting verb and its object; a separate requested edit,
    # send, run or publish still needs evidence. Technical subjects are allowed.
    remaining = prompt[:draft.start()] + " " + prompt[draft.end():]
    return not _new_action_request(remaining)


def _new_action_request(prompt: str) -> bool:
    # Past/present observation questions may contain "also run" without asking
    # for a new run. Check later sentences separately so mixed requests remain.
    clauses = re.split(r"[.!?;\n]+", prompt)
    pending = [c for c in clauses if not re.match(
        r"\s*(?:(?:ok|okay|so|thanks|great)[,\s]+)*(?:did|have|has|is|are|was|were|does)\b", c, re.I)]
    return any(re.search(
        r"(?:^|[.!?;,\n]\s*|\b(?:and|then|also)\s+)(?:(?:now|next|please|ok)\s+)*"
        r"(?:(?:(?:can|could|would|will) you|help me|I (?:need|want) you to)\s+)?(?:please\s+)?"
        r"(?:fix|edit|change|update|add|remove|write|create|implement|run|test|commit|push|deploy|publish|"
        r"delete|save|rename|make|verify|upgrade|bump|read|install|send|post|merge|repair|resolve|do|"
        r"handle|perform|complete|compose|draft)\b"
        r"|(?:고쳐|(?:수정|추가|실행|배포|커밋|푸시|저장)(?:해|하(?:고|여|라|자))|만들어\s*줘)", c.strip(), re.I) for c in pending)


def _status_request(prompt: str) -> bool:
    if _new_action_request(prompt):
        return False
    text = re.sub(r"^\s*(?:(?:ok|okay|so|great|thanks|thank you)[,.!\s]+)+", "", prompt, flags=re.I).strip()
    if not text or re.fullmatch(r"(?:thanks|thank you|great|lgtm|continue|status|update|progress|tl;dr)[.!?\s]*", text, re.I):
        return True
    return bool(len(text) <= 160 and len(text.split()) <= 20
                or re.search(r"^(?:is|are|did|have|has|does|what|how|any)\b", text, re.I)
                and re.search(r"\b(?:done|fix\w*|finish\w*|work\w*|pass\w*|tests?|status|update|progress|commit\w*|change\w*|do)\b", text, re.I)
                or re.search(r"(?:어떻게\s*됐|다\s*됐|통과했|방금\s*뭐\s*했|진행\s*상황|완료했|끝났)", text))


def _review_request(events: list[dict]) -> bool:
    """Descriptive scope, used only with known read/admin calls and no mutation.

    The tools establish a read-only turn. No fixed list of review openings is
    needed; an explicit request to perform new work still opts into checking.
    """
    return not _new_action_request(_request_text(events))


def _pasted_artifact_request(events: list[dict]) -> bool:
    prompt = _request_text(events)
    if re.search(r"\b(?:commit|push|deploy|save|publish)\b|\b[\w/-]+\.(?:py|js|ts|md|txt|json|csv)\b|저장|배포", prompt, re.I):
        return False
    supplied = ":" in prompt and bool(re.search(
        r"\b(?:duplicates|paragraph|sentence|text|grammar|email|regex|outline|numbers|keys|line|math|def|function)\b"
        r"|문장|문단|수식|중복", prompt, re.I))
    return supplied


def _no_change_attempted(tools: list[dict]) -> bool:
    return all(_known_without_evidence(t) and not _has_active_evidence([t]) for t in tools)


def _tool_names(tools: list[dict]) -> list[str]:
    names = sorted({str(t.get("name"))[:80] for t in tools})
    return names[:16] + ([f"... {len(names) - 16} other tool types"] if len(names) > 16 else [])


def _earlier_tools(events: list[dict]) -> list[dict]:
    """Status may refer across completed conversational follow-ups to prior work.

    Stop at the preceding substantive request. A new action request never
    borrows old work; intervening thanks/status messages are not new tasks.
    """
    prompt = _request_text(events).strip()
    history = _status_request(prompt) or bool(re.fullmatch(r"(?:can you )?recap[.!?\s]*", prompt, re.I))
    if not history and not _recap_request(events):
        return []
    prompts = [i for i, e in enumerate(events) if e.get("type") == "user" and _is_real_user_prompt(e)]
    errors = _errored_tool_ids(events)
    earlier = []
    for i in range(len(prompts) - 2, -1, -1):
        start, end = prompts[i], prompts[i + 1]
        attempted = _collect_tools(events[start + 1:end])
        earlier.extend(t for t in attempted if t.get("id") not in errors)
        if attempted or not _status_request(_user_text(events[start])):
            break
    return earlier


def _tool_results(events: list[dict]) -> dict[str, dict]:
    results = {}
    for event in events:
        content = _content(event)
        if event.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str) and block["tool_use_id"]:
                    tid = block["tool_use_id"]
                    # Duplicates are ambiguous; do not pick the convenient one.
                    results[tid] = block if tid not in results else {"_ambiguous": True}
    return results


def _recap_request(events: list[dict]) -> bool:
    """Retrospective summaries have unknown historical evidence scope.

    This does not assert their truth or reuse tools from a previous turn. Plans,
    explanations of how to do something, and mixed new actions stay checked.
    """
    text = _request_text(events)
    # "Write up what we did" requests a retrospective artifact, not a file edit.
    actions = re.sub(r"\bwrite\s+up\s+(?=what\b)", "", text, flags=re.I)
    if _new_action_request(actions) or re.search(r"\b(?:plan|how to|how do)\b|계획|방법", text, re.I):
        return False
    historical = re.search(
        r"\b(?:what .*?(?:did|done|changed|accomplished)|(?:our|your|the) (?:work|changes|progress|session)|"
        r"this session|so far|earlier|today)\b|지금까지|이번\s*세션|오늘|한\s*(?:작업|일|것)", text, re.I)
    summary = re.search(r"\b(?:summari[sz]e|recap|summary|write up)\b|요약|정리", text, re.I)
    period = re.search(r"\b(?:session|today|so far|earlier|(?:log|sign) (?:off|out)|wrapping up|wrap up)\b"
                       r"|지금까지|오늘|이번\s*세션", text, re.I)
    return bool(historical and (summary or period and re.search(r"\bwhat\b|뭐|무엇", text, re.I))
                or re.fullmatch(r"\s*(?:can you\s+)?(?:please\s+)?(?:recap|summari[sz]e)[.!?\s]*", text, re.I))


def _ambiguous_boundary(events: list[dict]) -> bool:
    """A human interruption before a tool-using turn ends has unknown scope.

    Do not borrow earlier tools and call a later claim supported. Skip the
    negative-evidence check instead. Ordinary requests after a final reply still
    start a new turn. Fields absent from a trace cannot establish this boundary.
    """
    prompts = [i for i, e in enumerate(events) if e.get("type") == "user" and _is_real_user_prompt(e)]
    if len(prompts) < 2:
        return False
    between = events[prompts[-2] + 1:prompts[-1]]
    if any(_interruption(e) for e in between):
        return True  # Ambiguous continuation; never assert prior tools suffice.
    if not _collect_tools(between):
        return False
    # An observed terminal assistant text is a completed turn; a tool result
    # immediately followed by a new prompt may instead be an injected steer.
    significant = [e for e in between if e.get("type") in {"assistant", "user"}]
    if not significant:
        return False
    last = significant[-1]
    return last.get("type") == "user" and not _is_real_user_prompt(last)


def _assistant_text(events: list[dict]) -> str:
    texts: list[str] = []
    for e in events:
        if e.get("type") != "assistant":
            continue
        content = _content(e)
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                texts.append(b.get("text", ""))
    return "\n".join(texts)


def _collect_tools(events: list[dict]) -> list[dict]:
    tools: list[dict] = []
    for e in events:
        if e.get("type") != "assistant":
            continue
        content = _content(e)
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tools.append({
                    "name": b.get("name") if isinstance(b.get("name"), str) else "unknown",
                    "input": b.get("input", {}) if isinstance(b.get("input", {}), dict) else {},
                    "id": b.get("id") if isinstance(b.get("id"), str) else None,
                    "_invalid_input": not isinstance(b.get("input", {}), dict),
                })
    return tools


def _errored_tool_ids(events: list[dict]) -> set[str]:
    return {tid for tid, block in _tool_results(events).items() if block.get("is_error")}


@lru_cache(maxsize=32)
def _shell_tokens(command: str, powershell: bool) -> list[tuple[str, bool]]:
    """Small evidence lexer, never an executor. Preserve quoted punctuation."""
    if len(command) > 32 * 1024:
        raise ValueError("Large command has unknown evidence scope")
    result, word, quote = [], "", None
    heredocs: list[tuple[str, bool]] = []
    i = 0
    while i < len(command):
        c = command[i]
        if quote:
            if powershell and quote == '"' and c == "`" and i + 1 < len(command):
                i += 1
                word += command[i]
                i += 1
                continue
            if quote == '"' and command[i:i + 2] == "$(":
                # Common quoted commit-message substitution. Heredoc data has
                # its own quoting rules: a literal '"' in it cannot close the
                # surrounding argument. Never inspect its body as commands.
                header = re.match(r'''\$\(cat[ \t]+<<(-)?[ \t]*(?:'([^'\n]+)'|"([^"\n]+)"|([^\s;&|<>]+))[ \t]*\r?\n''', command[i:])
                if header:
                    delimiter = next(s for s in header.groups()[1:] if s is not None)
                    closing = re.search(r"(?m)^" + (r"\t*" if header.group(1) else "") + re.escape(delimiter) + r"\r?\n[ \t]*\)", command[i + len(header.group(0)):])
                    if not closing:
                        raise ValueError("Unclosed quoted heredoc substitution")
                    word += "__quoted_heredoc__"
                    i += len(header.group(0)) + closing.end()
                    continue
                raise ValueError("Quoted command substitution has unknown evidence scope")
            if not powershell and quote == '"' and c == "`":
                raise ValueError("Quoted command substitution has unknown evidence scope")
            if c == quote:
                quote = None
            elif c == "\\" and not powershell and quote == '"' and i + 1 < len(command) and command[i + 1] in '\\"$`':
                i += 1
                word += command[i]
            else:
                word += c
            i += 1
            continue
        if c == "\n" and heredocs:
            if word:
                result.append((word, False))
                word = ""
            result.append(("\n", True))
            i += 1
            for delimiter, tabs in heredocs:
                while i < len(command):
                    end = command.find("\n", i)
                    if end < 0:
                        end = len(command)
                    line = command[i:end].rstrip("\r")
                    i = end + 1
                    if (line.lstrip("\t") if tabs else line) == delimiter:
                        break
                else:
                    raise ValueError("Unclosed heredoc")
            heredocs = []
            continue
        if not powershell and command[i:i + 2] == "<<":
            match = re.match(r'''<<(-)?[ \t]*(?:'([^'\n]+)'|"([^"\n]+)"|([^\s;&|<>]+))''', command[i:])
            if not match:
                raise ValueError("Unknown heredoc delimiter")
            if word:
                result.append((word, False))
                word = ""
            delimiter = next(s for s in match.groups()[1:] if s is not None)
            literal = match.group(2) is not None or match.group(3) is not None
            result.extend([("<", True), (("__literal_heredoc__" if literal else "") + delimiter, False)])
            heredocs.append((delimiter, bool(match.group(1))))
            i += len(match.group(0))
            continue
        if powershell and command[i:i + 2] in {"@'", '@"'}:
            marker = command[i + 1] + "@"
            closing = re.search(r"(?m)^" + re.escape(marker), command[i + 2:])
            if not closing:
                raise ValueError("Unclosed PowerShell here-string")
            word += "__here_string__"
            i += 2 + closing.end()
            continue
        if command[i:i + 2] == "$(":
            # Expansion content is opaque data here, never a command to credit.
            depth, j = 1, i + 2
            while j < len(command) and depth:
                depth += (command[j] == "(") - (command[j] == ")")
                j += 1
            if depth:
                raise ValueError("Unclosed expansion")
            word += "__expansion__"
            i = j
            continue
        if command[i:i + 2] == "&>":
            if word:
                result.append((word, False))
                word = ""
            result.append((">", True))
            i += 2
            continue
        if c in "\"'":
            quote = c
        elif c == "\\" and not powershell and i + 1 < len(command):
            i += 1
            word += command[i]
        elif c == "`":
            if powershell and i + 1 < len(command):
                i += 1
                if command[i] not in "\r\n":
                    word += command[i]
            else:
                raise ValueError("Unquoted expansion has unknown evidence scope")
        elif (redirect := re.match(r"\d*>&\d+", command[i:])) and not word:
            i += len(redirect.group(0)) - 1
        elif c in ";&|<>\n":
            if word:
                result.append((word, False))
                word = ""
            op = c
            if c != "\n" and command[i:i + 2] in {"&&", "||", ">>", "<<"}:
                op = command[i:i + 2]
                i += 1
            result.append((op, True))
        elif c.isspace():
            if word:
                result.append((word, False))
                word = ""
        else:
            word += c
        i += 1
    if quote or heredocs:
        raise ValueError("Unclosed quote")
    if word:
        result.append((word, False))
    return result


def _command_parts(tool: dict, *, for_tests: bool = False) -> list[list[str]]:
    if tool.get("name") not in {"Bash", "PowerShell"}:
        return []
    cmd = str(tool.get("input", {}).get("command", ""))
    try:
        lexed = _shell_tokens(cmd.lstrip().removeprefix("& ") if tool["name"] == "PowerShell" else cmd, tool["name"] == "PowerShell")
    except ValueError:
        return []
    if any(op and (s in {"||", "&"} or (for_tests and s in {"|", "<"})) for s, op in lexed):
        return []  # A later successful pipeline/command can mask failure.
    parts, current = [], []
    for token, operator in lexed:
        if operator and token in {"&&", ";", "\n", "|"}:
            parts.append(current)
            current = []
        else:
            # Redirection retained for change evidence, quoted '>' stays data.
            current.append("__output_redirect__" if operator and token in {">", ">>"} else token)
    parts.append(current)
    if for_tests and any(op and s in {";", "\n"} for s, op in lexed):
        # Only a directory change or environment assignment may precede a test
        # across ';'. A trailing echo/cleanup must not turn a failed test green.
        nonempty = [part for part in parts if part]
        if any(not (_program(part) in {"cd", "set-location", "source", ".", "export", "set"} or re.match(r"^(?:\$env:)?[A-Za-z_][A-Za-z_0-9]*=", part[0]) or (tool["name"] == "PowerShell" and re.fullmatch(r"\$env:[A-Za-z_][A-Za-z_0-9]*", part[0]) and part[1:2] == ["="])) for part in nonempty[:-1]):
            return []
    cleaned = []
    for part in parts:
        if "{" in part:
            part = part[part.index("{") + 1:]
        part = [s for t in part if (s := t.strip("(){}"))]
        part = _command_prefix(part)
        if part:
            cleaned.append(part)
    return cleaned


def _program(tokens: list[str]) -> str:
    return tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")


def _command_prefix(tokens: list[str]) -> list[str]:
    """Unwrap common non-executing environment/time prefixes for recognition."""
    while tokens:
        if tokens[0] in {"do", "then", "else", "elif", "!"}:
            tokens = tokens[1:]
        elif re.match(r"^(?:\$env:)?[A-Za-z_][A-Za-z_0-9]*=", tokens[0]):
            tokens = tokens[1:]
        elif _program(tokens) in {"env", "time", "sudo", "nohup", "command", "xargs", "nice"}:
            tokens = tokens[1:]
            while tokens and tokens[0] in {"--", "-i", "--ignore-environment", "-p", "-n", "-0", "-r"}:
                tokens = tokens[1:]
        elif _program(tokens) == "timeout" and len(tokens) > 1 and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", tokens[1]):
            tokens = tokens[2:]
        else:
            break
    return tokens


def _git_args(args: list[str]) -> list[str]:
    while args:
        if args[0] in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
            args = args[2:]
        elif args[0].startswith(("--git-dir=", "--work-tree=", "--namespace=")) or args[0] in {"--no-pager", "--no-optional-locks"}:
            args = args[1:]
        else:
            break
    return args


def _unwrap_runner(tokens: list[str]) -> list[str]:
    """Recognise common package executors without executing or fetching anything."""
    if _program(tokens) in {"npx", "bunx", "uvx"}:
        tokens = tokens[1:]
    elif _program(tokens) in {"npm", "pnpm", "yarn", "bun"} and tokens[1:2] in (["exec"], ["dlx"]):
        tokens = tokens[2:]
    else:
        return tokens
    while tokens and tokens[0] in {"--", "-y", "--yes", "--no-install", "--offline"}:
        tokens = tokens[1:]
    return tokens


def _has_active_evidence(tools: list[dict]) -> bool:
    for tool in tools:
        name = tool.get("name") or ""
        if name in ACTIVE_EVIDENCE_TOOLS or (name.startswith("mcp__") and re.search(r"(?:^|[_-])(?:write|edit|apply|create|delete|move|replace|update|merge|push|add)(?:[_-]|$)", name.rsplit("__", 1)[-1])):
            return True
        for tokens in _command_parts(tool):
            tokens = _unwrap_runner(tokens)
            if not tokens:
                continue
            program, args = _program(tokens), tokens[1:]
            if program == "git":
                args = _git_args(args)
            if program in {"rm", "mv", "cp", "mkdir", "touch", "apply_patch", "remove-item", "move-item", "copy-item", "rename-item", "set-content", "add-content", "new-item", "out-file", "tee-object", "tee"}:
                return True
            if program == "gh" and args[:1] in (["pr"], ["release"], ["issue"]) and len(args) > 1 and args[1] in {"create", "merge", "edit", "close", "reopen", "delete", "comment"}:
                return True
            if program == "git" and args and args[0] in {"commit", "merge", "apply", "push", "cherry-pick", "revert", "mv", "rm", "add", "fetch", "pull", "reset", "checkout", "switch", "restore"}:
                return True
            if program == "git" and args == ["stash"]:
                return True
            if program == "git" and len(args) > 1 and args[0] in {"tag", "branch", "stash"} and not any(a in {"--list", "-l", "--show-current", "list", "show", "--all", "--remotes"} or (args[0] == "branch" and re.fullmatch(r"-[arv]+", a)) for a in args[1:]):
                return True
            if program in {"vercel", "netlify", "wrangler", "gcloud", "firebase", "npm", "pnpm", "yarn", "bun", "fly"} and "deploy" in args:
                return True
            if (program == "vercel" and "--prod" in args) or (program == "kubectl" and args[:1] in (["apply"], ["create"], ["delete"], ["patch"], ["replace"])):
                return True
            if program in {"npm", "pnpm", "yarn", "bun", "uv", "poetry", "cargo"} and args[:1] and args[0] in {"install", "add", "update", "upgrade", "remove", "uninstall", "publish"}:
                return True  # Mutation-capable call, not proof a lock file changed.
            if (program == "alembic" and args[:1] in (["upgrade"], ["downgrade"])) or (program == "flyway" and "migrate" in args) or (program == "dbmate" and args[:1] in (["up"], ["migrate"])) or (program == "prisma" and "migrate" in args):
                return True
            if program.startswith("python") and "manage.py" in args and any(a in {"migrate", "makemigrations"} for a in args):
                return True
            if (program == "terraform" and "apply" in args) or (program == "docker" and "push" in args):
                return True
            if program in {"eslint", "ruff", "prettier"} and any(a in {"--fix", "--write", "-w"} for a in args):
                return True
            if program == "psql" and any(args[i] in {"-c", "--command"} and re.match(r"\s*(?:ALTER|INSERT|UPDATE|DELETE|CREATE|DROP)\b", args[i+1], re.I) for i in range(len(args)-1)):
                return True
            if (program == "rails" and "db:migrate" in args) or (program == "php" and args[:2] == ["artisan", "migrate"]) or (program == "psql" and any(a in {"-f", "--file"} or a.startswith("--file=") for a in args)):
                return True
            if program == "find" and "-delete" in args:
                return True
            if program in {"sed", "perl"} and any(re.match(r"^-(?:[pan]*i|i)", a) for a in args):
                return True
            if "__output_redirect__" in args and args[-1] not in {"/dev/null", "NUL", "$null"}:
                return True
    return False


def _pytest_args(tokens: list[str]) -> list[str] | None:
    if not tokens:
        return None
    if _program(tokens) == "pytest":
        return tokens[1:]
    if re.fullmatch(r"(?:python(?:\d+(?:\.\d+)*)?|py)", _program(tokens)):
        args = tokens[1:]
        while args and (args[0] in {"-B", "-E", "-I", "-P", "-s", "-S", "-u"}
                        or (_program(tokens) == "py" and re.fullmatch(r"-\d+(?:\.\d+)*", args[0]))):
            args = args[1:]
        if args[:2] == ["-m", "pytest"]:
            return args[2:]
    return None


def _information_only(args: list[str]) -> bool:
    return any(a in {"--version", "--help", "-h", "--co", "--collect-only", "--no-run", "-list"} for a in args)


def _pytest_runner(tokens: list[str]) -> list[str]:
    tokens = _unwrap_runner(_command_prefix(tokens)) if tokens else []
    if tokens and _program(tokens) in {"uv", "poetry"} and tokens[1:2] == ["run"]:
        tokens = tokens[2:]
        while tokens and tokens[0] in {"--frozen", "--locked", "--no-sync", "--offline", "--"}:
            tokens = tokens[1:]
    return tokens


def _filtered_pytest(tool: dict, results: dict[str, dict]) -> bool:
    """The only supported pipeline: pytest -> numeric head/tail + same-ID summary.

    Pipeline exit status is never evidence of pytest success. This recognises
    the observed pytest final-summary format, not arbitrary runner output.
    """
    if tool.get("name") != "Bash" or not tool.get("id"):
        return False
    command = str(tool.get("input", {}).get("command", ""))
    if any(r != "2>&1" for r in re.findall(r"\d*>&\d+", command)):
        return False
    try:
        tokens = _shell_tokens(command, False)
    except ValueError:
        return False
    operators = [t for t, op in tokens if op]
    if operators not in (["|"], ["&&", "|"]):
        return False
    parts, part = [], []
    for token, op in tokens:
        if op:
            parts.append(part)
            part = []
        else:
            part.append(token)
    parts.append(part)
    if len(parts) == 3:
        if len(parts[0]) != 2 or parts[0][0] not in {"cd", "source", "."} or "__expansion__" in parts[0][1]:
            return False
        parts = parts[1:]
    runner, reducer = parts
    if not runner or not reducer:
        return False
    args = _pytest_args(_pytest_runner(runner))
    if args is None or _information_only(args) or any("__expansion__" in a for a in args):
        return False
    if _program(reducer) not in {"head", "tail"} or not (
        len(reducer) == 2 and re.fullmatch(r"-(?:n)?[1-9]\d*", reducer[1]) or
        len(reducer) == 3 and reducer[1] == "-n" and re.fullmatch(r"[1-9]\d*", reducer[2])):
        return False
    block = results.get(tool["id"], {})
    if block.get("is_error") is not False:
        return False
    output = block.get("content", "")
    if isinstance(output, list):
        output = "\n".join(b.get("text", "") for b in output if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(output, str) or len(output) > 1024 * 1024:
        return False
    if re.search(r"\b[1-9]\d*\s+(?:failed|errors?)\b", output, re.I):
        return False
    # Detect output annotations, not words in test names or pytest progress dots.
    if any(re.search(r"^(?:warning:\s*)?(?:\[|\.{3}|…)?\s*(?:output\s+truncated|truncated\s+output|"
                     r"\d+\s+(?:chars?|characters?|lines?|tokens?)\s+(?:truncated|omitted))\b|"
                     r"^\[\s*(?:output\s+)?(?:truncated|omitted)\b", line.strip(), re.I)
           for line in output.splitlines()):
        return False
    lines = output.strip().splitlines()
    # Claude Code appends this observed tool annotation after command output.
    # Ignore exactly that trailing line, never arbitrary trailing output.
    if lines and lines[-1].startswith("Shell cwd was reset to "):
        lines.pop()
    if not lines:
        return False
    summary = lines[-1].strip().strip("=").strip()
    if not re.fullmatch(r"\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)(?:, \d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?))* in \d+(?:\.\d+)?s(?: \([\d:]+\))?", summary):
        return False
    counts = re.findall(r"\b(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b", summary)
    kinds = [kind.rstrip("s") for _, kind in counts]
    return len(set(kinds)) == len(kinds) and any(int(n) > 0 and kind == "passed" for n, kind in counts)


def _has_test_evidence(tools: list[dict], errored: set[str], results: dict[str, dict] | None = None) -> bool:
    for t in tools:
        if t.get("id") and t["id"] in errored:
            continue
        if _filtered_pytest(t, results or {}):
            return True
        for tokens in _command_parts(t, for_tests=True):
            tokens = _unwrap_runner(tokens)
            if not tokens:
                continue
            program, args = _program(tokens), tokens[1:]
            if _information_only(args):
                continue
            if program in {"pytest", "jest", "vitest", "rspec", "ctest", "eslint", "ruff", "phpunit", "tox", "mocha", "mypy"}:
                return True
            if (program in {"swift", "flutter", "playwright"} and "test" in args) or (program == "cargo" and args[:2] == ["nextest", "run"]) or (program == "npm" and args[:1] == ["t"]):
                return True
            if (program.startswith("python") or program == "py") and any(args[i:i + 2] in (["-m", "pytest"], ["-m", "unittest"]) for i in range(len(args) - 1)):
                return True
            if program == "tsc" and "--noEmit" in args:
                return True
            if program in {"cargo", "go", "dotnet", "mvn", "mvnw", "gradle", "gradlew", "deno", "make", "npm", "pnpm", "yarn", "bun", "npx", "bundle", "rails", "turbo"} and any(a in {"test", "check", "typecheck", "lint", "pytest", "jest", "vitest", "tsc", "rspec"} or a.startswith("test:") for a in args):
                return True
            if (program == "node" and "--test" in args) or (program == "php" and args[:2] == ["artisan", "test"]):
                return True
            if program.startswith("python") and "manage.py" in args and "test" in args:
                return True
            if program == "php" and any(_program([a]) == "phpunit" for a in args):
                return True
            if program in {"uv", "poetry"} and args[:1] == ["run"]:
                nested = args[1:]
                while nested and nested[0] in {"--frozen", "--locked", "--no-sync", "--offline", "--"}:
                    nested = nested[1:]
                if nested and _has_test_evidence([{"name": "Bash", "input": {"command": shlex.join(nested)}}], set()):
                    return True
    return False


def _known_without_evidence(tool: dict) -> bool:
    """Only an explicit, narrow classification establishes negative evidence.

    Unknown shell languages, arbitrary scripts and MCP calls remain unknown.
    This is a transcript classifier, not a command safety or permission check.
    """
    if tool.get("_invalid_input"):
        return False
    name = tool.get("name")
    if name in {"Read", "NotebookRead", "Grep", "Glob", "WebFetch", "WebSearch"} | ACTIVE_EVIDENCE_TOOLS | ADMIN_TOOLS:
        return True
    if name not in {"Bash", "PowerShell"}:
        return False
    try:
        lexed = _shell_tokens(str(tool.get("input", {}).get("command", "")), name == "PowerShell")
    except ValueError:
        return False
    # Opaque substitutions and control structures may execute unobserved code.
    if any("__expansion__" in t or "__here_string__" in t or t in {"<<", "<<<", "&"} for t, _ in lexed):
        return False
    # Discard only redirects to a recognised null sink. Other redirections can
    # modify files and must not turn a mutating command into a read-only one.
    filtered, index = [], 0
    while index < len(lexed):
        token, op = lexed[index]
        if op and token in {">", ">>"} and index + 1 < len(lexed) and lexed[index + 1][0].lower() in {"/dev/null", "nul", "$null"}:
            if filtered and not filtered[-1][1] and filtered[-1][0].isdigit():
                filtered.pop()
            index += 2
            continue
        filtered.append((token, op))
        index += 1
    lexed = filtered
    parts, current = [], []
    for index, (token, operator) in enumerate(lexed):
        if operator and token == "<" and index + 1 < len(lexed) and lexed[index + 1][0].startswith("__literal_heredoc__"):
            continue  # Quoted heredoc is literal input, never command evidence.
        if operator:
            if token not in {"&&", "||", ";", "\n", "|"}:
                return False
            if current:
                parts.append(current)
                current = []
        else:
            current.append(token)
    if current:
        parts.append(current)
    if not parts:
        return False
    for part in parts:
        tokens = _command_prefix(part)
        if not tokens:
            continue
        program, args = _program(tokens), tokens[1:]
        if program in {"cat", "ls", "pwd", "echo", "printf", "head", "tail", "wc", "true", "false",
                       "cd", "get-content", "get-childitem", "get-location", "set-location",
                       "write-output", "select-object", "get-date", "test-path", "netstat"}:
            continue
        if program in {"rg", "grep"} and not any(a in {"--pre", "--pre-glob"} or a.startswith("--pre=") for a in args):
            continue
        if program == "git" and _git_args(args)[:1] in (["status"], ["log"], ["diff"], ["show"], ["ls-files"], ["rev-parse"]):
            continue
        if program == "git":
            git_args = _git_args(args)
            if git_args[:1] in (["branch"], ["tag"], ["stash"]) and (len(git_args) == 1 or any(
                a in {"--list", "-l", "--show-current", "list", "show", "--all", "--remotes"}
                or git_args[0] == "branch" and re.fullmatch(r"-[arv]+", a) for a in git_args[1:])):
                continue
        # Known runner calls in masking pipelines are not successful-run proof.
        # Nor is --version/--help output. Unknown runners are not put in this set.
        single = {"name": name, "input": {"command": shlex.join(tokens)}}
        if _has_test_evidence([single], set()) or _has_active_evidence([single]):
            continue
        if _information_only(args) and (program in {"pytest", "ruff", "jest", "vitest", "tsc", "eslint", "go", "cargo"}
                                       or _pytest_args(tokens) is not None):
            continue
        return False
    return True


def _evidence_budget_exceeded(tools: list[dict]) -> bool:
    return len(tools) > 4096 or sum(len(str(t.get("input", {}).get("command", ""))) for t in tools) > 128 * 1024


def _evidence_state(tools: list[dict], kind: str, results: dict[str, dict]) -> str:
    """supported / absent / unknown; unknown is allowed, never labelled proof."""
    if _evidence_budget_exceeded(tools):
        return "unknown"
    unknown = False
    for tool in tools:
        if tool.get("_invalid_input"):
            unknown = True
            continue
        result = results.get(tool.get("id"))
        if result is None or result.get("_ambiguous"):
            unknown = True  # A call without its result is incomplete evidence.
            continue
        if tool.get("name") in ADMIN_TOOLS:
            continue
        if kind == "verify":
            if re.search(r"user (?:rejected|denied)|tool use rejected|interrupted|cancelled|canceled", str(result.get("content", "")), re.I):
                continue
            return "supported"
        if result.get("is_error"):
            continue
        supported = (_has_active_evidence([tool]) if kind == "active"
                     else _has_test_evidence([tool], set(), results))
        if supported:
            return "supported"
        if not _known_without_evidence(tool):
            unknown = True
    return "unknown" if unknown else "absent"


def _turn_text(last_assistant_message: str, transcript_path: Optional[str]) -> str:
    """Prefer the hook payload; fall back to text in the current turn."""
    if last_assistant_message:
        return last_assistant_message
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    lines = _read_tail(p)
    events = _parse_events(lines)
    turn = _current_turn(events)
    return _assistant_text(turn)


def detect_test_claim(
    last_assistant_message: str = "",
    transcript_path: Optional[str] = None,
) -> bool:
    """Detect a test claim, without asserting that it is supported."""
    text = _turn_text(last_assistant_message, transcript_path)
    if not text:
        return False
    return bool(_scan(text, TEST_CLAIMS))


def check(
    last_assistant_message: str = "",
    transcript_path: Optional[str] = None,
) -> Optional[str]:
    """Return feedback for an unsupported claim, otherwise None."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None

    lines = _read_tail(p)
    events = _parse_events(lines)
    turn = _current_turn(events)
    if _ambiguous_boundary(events) or _recap_request(events) or not any(e.get("type") == "user" and _is_real_user_prompt(e) for e in events):
        return None
    if _draft_request(events):
        return None
    turn_text = _assistant_text(turn)

    text = last_assistant_message or turn_text
    if not text:
        return None

    active = _scan(text, ACTIVE_CLAIMS)
    tests = _scan(text, TEST_CLAIMS)
    verified = _scan(text, VERIFY_CLAIMS)
    if not active and not tests and not verified:
        return None

    errored = _errored_tool_ids(events)
    attempted = _collect_tools(turn)
    tools = [t for t in attempted if t.get("id") not in errored]
    tool_names = _tool_names(tools)
    earlier = _earlier_tools(events)
    results = _tool_results(events)

    problems: list[str] = []
    # 'Done' alone does not specify a disk action, even in a no-tool reply.
    active = [h for h in active if h.lower() != "done"]
    no_change = _no_change_attempted(attempted)
    if no_change and re.search(r"```|(?m:^\|.*\|$)", text) and re.search(
        r"(?i:above|below|here|corrected version)|아래|위의|:\s*```", text):
        active = []  # Inline artifact; no external file mutation is asserted.
    external_claim = any(re.search(r"commit|push|deploy|publish|커밋|푸시|배포", h, re.I) for h in active)
    if no_change and (attempted and _review_request(events) or not external_claim and _pasted_artifact_request(events)):
        active = []
    if no_change and _pasted_artifact_request(events):
        verified = []
    evidence = tools + earlier
    if active and _evidence_state(evidence, "active", results) == "absent":
        problems.append(
            f"Active claim {active!r}, but the current turn has no "
            f"recognised edit or mutating command without a reported error. "
            f"tool_use: {tool_names}"
        )
    if tests and _evidence_state(evidence, "test", results) == "absent":
        problems.append(
            f"Test/Verify claim {tests!r}, but the current turn has no "
            f"recognised test/type-check/lint command without a reported error. "
            "Pipelines require a recognised pytest run, only numeric head/tail, and its own complete passing summary. "
            f"tool_use: {tool_names}"
        )
    if verified and _evidence_state(attempted + earlier, "verify", results) == "absent":
        problems.append(f"Verify claim {verified!r}, but the current turn has no completed observation tool evidence.")
    return "\n".join(problems)[:4000] if problems else None
