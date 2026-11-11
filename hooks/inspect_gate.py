"""Heuristic checks for whole-file inspection and monitoring claims. Evidence presence does not establish completeness or persistent monitoring."""
from __future__ import annotations

import re
import time
from typing import Optional

from claim_gate import (
    NEGATION,
    PAST_REF,
    QUESTION,
    _assistant_text,
    _collect_tools,
    _current_turn,
    _parse_events,
    _read_tail,
    _sentence,
    _strip_code,
    _english_qualified,
    _korean_qualified,
    _is_real_user_prompt,
    _errored_tool_ids,
    _ambiguous_boundary,
    _recap_request,
    TextBounds,
    _earlier_tools,
    SCAN_BUDGET_SECONDS,
    _command_parts,
    _program,
    _tool_results,
    _known_without_evidence,
    _evidence_budget_exceeded,
    _draft_request,
    _tool_names,
)
from pathlib import Path



HEDGE = re.compile(
    r"(?:단정|확신|장담|판단|말|볼|알)\s*(?:할|하기)?\s*수\s*(?:는)?\s*없"
    r"|모르겠|불확실|추정|가설|일\s*수(?:도)?\s*있"
    r"|아닐\s*수(?:도)?\s*있|섣부|속단",
)



READ_CLAIMS = [
    r"(전체|전부|끝까지|처음부터\s*끝까지)(?:을|를|은|는|도)?\s*(?:다\s*|모두\s*)?"
    r"(?:읽|훑|확인|검토|살펴)",
    r"(?:파일|코드|소스|본문|전문)(?:을|를|은|는)?\s*(?:모두|전부|다)\s*(?:읽|확인|검토)",
    r"\b(?:read|reviewed|inspected)\s+the\s+(?:whole|entire|full)\s+file\b",
]

READ_EVIDENCE_TOOLS = {"Read", "NotebookRead"}
SHALLOW_TOOLS = {"Grep", "Glob"}
SHALLOW_BASH = [
    r"(?:^|[\s&;|(])(?:grep|rg|head|tail|sed\s+-n|awk)\b",
    r"\bwc\s+-l\b",
]


WATCH_CLAIMS = [
    r"(실시간|계속|지속)(?:으로|적으로)?\s*(?:지켜보|감시|모니터링|확인하)",
    r"(감시|모니터링)\s*(?:하고\s*있|중이|중입|하겠)",
    r"(?:지켜보|주시하)(?:고\s*있|겠)",
    r"\b(?:I\s+am|I'm|we\s+are|we're)\s+(?:monitoring|watching)\s+(?:it|this|the)\b",
]

WATCH_EVIDENCE_TOOLS = {
    "BashOutput", "TaskOutput", "Monitor", "TaskGet", "TaskList",
    "mcp__claude-in-chrome__computer",
    "mcp__claude-in-chrome__get_page_text",
    "mcp__claude-in-chrome__read_console_messages",
    "mcp__claude-in-chrome__browser_batch",
}


VERDICT_CLAIMS = [
    r"(회귀|버그|원인)\s*(?:입니다|이다|임|맞습니다)",
    r"(경쟁|수요|문제|이슈|영향)\s*(?:가|이)?\s*(?:거의\s*)?없(?:습니다|다|음)",
    r"(?:확실|분명)(?:합니다|하다|함)",
    r"(?:정상|안전|무해)(?:입니다|이다|임|합니다)",
    r"(?:검증|확인)(?:됐|되었)(?:습니다|다|음)",
]

CLAIM_MAX_CHARS = 60


def _scan(text: str, patterns: list[str]) -> list[str]:
    """Find unquoted, unqualified claims; prefer missed claims over false blocks."""
    deadline = time.monotonic() + SCAN_BUDGET_SECONDS
    stripped = _strip_code(text)
    bounds = TextBounds(stripped)
    hits: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat.replace(r"\s", r"[^\S\n]"), stripped, re.IGNORECASE):
            if time.monotonic() > deadline:
                return []
            sentence = bounds.sentence(m.start(), m.end())
            if patterns is WATCH_CLAIMS and not m.group(0).isascii() and not re.search(
                r"(?:제가|저는|나는|내가|우리가|우리는)|(?:하고|보고|하며)\s*있|중(?:입|이)", sentence):
                continue  # Software behaviour / future plans are not live observation.
            if NEGATION.search(sentence) or PAST_REF.search(sentence):
                continue
            if QUESTION.search(sentence) or HEDGE.search(sentence):
                continue
            if m.group(0).isascii() and _english_qualified(stripped, m.start(), m.end(), bounds):
                continue
            if not m.group(0).isascii() and _korean_qualified(stripped, m.start(), m.end()):
                continue
            hits.append(m.group(0).strip())
            if len(hits) >= 32:
                return hits
        if time.monotonic() > deadline:
            return []
    return hits


def _bash_commands(tools: list[dict]) -> list[str]:
    return [
        str(t.get("input", {}).get("command", ""))
        for t in tools
        if t.get("name") == "Bash"
    ]


def _has_read_evidence(tools: list[dict]) -> bool:
    """Check for a recognised reading tool; this does not prove whole-file coverage."""
    return any(t.get("name") in READ_EVIDENCE_TOOLS for t in tools)


def _only_shallow_lookups(tools: list[dict]) -> bool:
    """Detect searching or sampling without a recognised reading tool."""
    if _has_read_evidence(tools):
        return False
    if any(t.get("name") in SHALLOW_TOOLS for t in tools):
        return True
    parts = [part for tool in tools for part in _command_parts(tool)]
    if any(_program(part) in {"grep", "rg", "head", "tail", "sed", "awk"} or (_program(part) == "wc" and "-l" in part) for part in parts):
        return True
    return False


def _has_watch_evidence(tools: list[dict]) -> bool:
    return any(t.get("name") in WATCH_EVIDENCE_TOOLS for t in tools)


def _short(hits: list[str]) -> list[str]:
    return [h[:CLAIM_MAX_CHARS] for h in hits[:10]]


def _turn(transcript_path: Optional[str]) -> tuple[str, list[dict]]:
    """Return current-turn text and tool calls without a reported error."""
    if not transcript_path:
        return "", []
    p = Path(transcript_path)
    if not p.exists():
        return "", []
    events = _parse_events(_read_tail(p))
    turn = _current_turn(events)
    errors = _errored_tool_ids(turn)
    return _assistant_text(turn), [t for t in _collect_tools(turn) if t.get("id") not in errors]


def check(
    last_assistant_message: str = "",
    transcript_path: Optional[str] = None,
    *, check_verdicts: bool = False,
) -> Optional[str]:
    """Return feedback when the recognised claim has no required evidence."""
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    events = _parse_events(_read_tail(Path(transcript_path)))
    if _ambiguous_boundary(events) or _recap_request(events) or not any(e.get("type") == "user" and _is_real_user_prompt(e) for e in events):
        return None
    if _draft_request(events):
        return None
    turn_text, tools = _turn(transcript_path)
    earlier = _earlier_tools(events)
    results = _tool_results(events)
    if _evidence_budget_exceeded(tools + earlier):
        return None
    if any(t.get("id") not in results or results.get(t.get("id"), {}).get("_ambiguous") or not _known_without_evidence(t)
           and t.get("name") not in WATCH_EVIDENCE_TOOLS for t in tools + earlier):
        return None  # Missing results / opaque tools (including Agent/Task) are unknown.
    text = last_assistant_message or turn_text
    if not text:
        return None

    tool_names = _tool_names(tools)
    problems: list[str] = []


    read_hits = _scan(text, READ_CLAIMS)
    if re.search(r"(?:테스트|명령|실행)\s*(?:출력|결과)", text) and not re.search(r"파일|코드|소스|본문|전문", text):
        read_hits = []  # A bounded command-output report is not a file-read claim.
    if re.search(r"호출부|참조\s*위치|검색\s*결과", text) and not re.search(r"파일|코드|소스|본문|전문", text):
        read_hits = []  # Search coverage, not a statement about a whole file.
    if read_hits and _only_shallow_lookups(tools) and not _has_read_evidence(earlier):
        problems.append(
            f"[read-scope] Whole-file claim {_short(read_hits)!r}, but the current turn "
            f"only searched or sampled without Read. Read the file or state the "
            f"limited scope. tool_use: {tool_names}"
        )


    watch_hits = _scan(text, WATCH_CLAIMS)
    if watch_hits and not _has_watch_evidence(tools) and not _has_watch_evidence(earlier):
        problems.append(
            f"[monitoring] Monitoring claim {_short(watch_hits)!r}, but the current turn "
            f"has no polling call. State that monitoring has not been performed. "
            f"tool_use: {tool_names}"
        )


    # Generic advice is not an evidence claim. Legacy callers can explicitly
    # request the narrower zero-tool verdict check; the installed hook does not.
    verdict_hits = _scan(text, VERDICT_CLAIMS) if check_verdicts else []
    if verdict_hits and not tools and not earlier:
        problems.append(
            f"[unsupported-verdict] Verdict {_short(verdict_hits)!r}, but the current "
            f"turn has no tool calls. Check the evidence or label the uncertainty."
        )

    return "\n".join(problems)[:4000] if problems else None
