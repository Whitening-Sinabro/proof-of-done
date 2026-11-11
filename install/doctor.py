#!/usr/bin/env python3
"""Check project wiring and invoke the installed hook with synthetic transcripts.

This is a local hook smoke test, not evidence of a real Claude conversation.
No network, model, API credits, or third-party packages are used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.append(str(Path(__file__).parent))
from install import MODULES, REPO, hook_blocks, locations, read_settings, validate_modules, unlinked, trusted_python, project_hooks_disabled, bootstrap_source


def event(role: str, content) -> dict:
    return {"type": role, "message": {"role": role, "content": content}}


def probe(script: Path, real_edit: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="proof-of-done-한글-café-") as folder:
        root = Path(folder)
        events = [event("user", "Change the demonstration file.")]
        if real_edit:
            (root / "sample.txt").write_text("checked\n", encoding="utf-8")
            events.extend([
                event("assistant", [{"type": "tool_use", "name": "Write", "id": "write-1", "input": {"file_path": "sample.txt", "content": "checked\n"}}]),
                event("user", [{"type": "tool_result", "tool_use_id": "write-1", "content": "File written", "is_error": False}]),
            ])
        events.append(event("assistant", [{"type": "text", "text": "I’ve fixed the demonstration file."}]))
        transcript = root / "synthetic.jsonl"
        transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")
        payload = {"session_id": "doctor", "stop_hook_active": False,
                   "last_assistant_message": "I’ve fixed the demonstration file.", "transcript_path": str(transcript)}
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(root)
        env.pop("CLAUDE_SKIP_STOP_VERIFY", None)
        env.update(PYTHONIOENCODING="cp949", PYTHONUTF8="0")
        result = subprocess.run([trusted_python(root), "-I", "-B", "-S", "-c", bootstrap_source(), str(script)], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                capture_output=True, env=env, cwd=root, timeout=30)
        if result.returncode:
            raise ValueError("Installed hook subprocess failed.")
        return json.loads(result.stdout) if result.stdout.strip() else {}


def diagnose(project: Path) -> dict:
    settings_path, base = locations(project)
    settings, _ = read_settings(settings_path)
    manifest_path = base / "install-manifest.json"
    if not manifest_path.exists() or not unlinked(manifest_path):
        raise ValueError("Install the checker in this project first.")
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if settings.get("disableAllHooks") or project_hooks_disabled(project) or os.environ.get("CLAUDE_SKIP_STOP_VERIFY") or (project / ".claude/skip-stop-verify").exists():
        raise ValueError("A local off switch is active. The installed hook will not run.")
    for event_name in ("SessionStart", "Stop"):
        expected = [hook_blocks(base, trusted_python(project), opt_in)[event_name] for opt_in in (False, True)]
        if not any(block in settings.get("hooks", {}).get(event_name, []) for block in expected):
            raise ValueError(f"The exact installed {event_name} hook is not connected.")
    sources = {name: (REPO / "hooks" / name).read_bytes() for name in MODULES}
    validate_modules(base / "hooks", sources, complete=True)
    script = base / "hooks/verify_on_stop.py"
    false_result = probe(script, False)
    true_result = probe(script, True)
    if false_result.get("decision") != "block" or "Active claim" not in false_result.get("reason", ""):
        raise ValueError("Unsupported English claim was not blocked.")
    if true_result:
        raise ValueError("English claim with edit evidence was not allowed.")
    return {"status": "pass", "wiring": "project-local", "synthetic_false_claim": "blocked",
            "synthetic_claim_with_edit": "allowed", "real_claude_session": "not tested",
            "global_or_managed_settings": "not checked"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = diagnose(args.project.absolute())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Doctor failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
