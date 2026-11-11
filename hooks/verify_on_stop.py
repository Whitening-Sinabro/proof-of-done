#!/usr/bin/env python3
"""Project-local Stop feedback with optional, explicitly enabled test execution.

Default: inspect transcript claims only. Enable the optional runner at install
with --run-tests. Review .claude/verify.json before opting in: it can select local
executable/argument lists. Common shells and npx are refused; this is NOT a
sandbox or a complete command/fetch denylist. Custom executables can run code.
Missing runners/timeouts are unknown, not test failures.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import shlex
import shutil
import tempfile
import sys
import time
from pathlib import Path

# Installed invocations use -I -B -S: no site/.pth initialization or project imports ahead of the stdlib,
# and no bytecode writes. Also refuse extras added after installation.
_MODULE_DIR = Path(__file__).parent
if _MODULE_DIR.parent.name == "proof-of-done":
    _expected = {"claim_gate.py", "inspect_gate.py", "session_snapshot.py", "verify_on_stop.py"}
    if any(p.name not in _expected or not p.is_file() or p.is_symlink() for p in _MODULE_DIR.iterdir()):
        sys.stderr.write("[proof-of-done] unexpected installed hook files; check skipped\n")
        print(json.dumps({"systemMessage": "Proof of Done skipped: unexpected files in its hooks directory. Preserve the files and repair the installation before relying on the check."}))
        raise SystemExit(0)
    if any(not (_MODULE_DIR / name).is_file() for name in _expected):
        print(json.dumps({"systemMessage": "Proof of Done skipped: an installed hook module is missing. Run doctor and repair the installation before relying on the check."}))
        raise SystemExit(0)
sys.dont_write_bytecode = True
sys.path.append(str(_MODULE_DIR))
import session_snapshot


try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except AttributeError:
    pass

SKIP_ENV = "CLAUDE_SKIP_STOP_VERIFY"
SKIP_FILE = ".claude/skip-stop-verify"
CONFIG_FILE = ".claude/verify.json"
PER_CMD_TIMEOUT = 120  # seconds
RUN_BUDGET_SECONDS = 240  # Leaves headroom below the 400-second Stop limit.
_RUN_DEADLINE: float | None = None


def _project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        p = Path(env).resolve()
        if p.exists():
            return p
    return Path.cwd().resolve()


def _run(cmd: list[str], cwd: Path, use_shell: bool = False) -> tuple[int, str]:
    if use_shell or not isinstance(cmd, list) or not cmd:
        return 127, "[unknown] Only direct executable/argument lists are supported."
    executable = shutil.which(cmd[0])
    if executable and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        return 127, "[unknown] Shell shims are unsupported; invoke the underlying script with its runtime."
    # File-backed output avoids communicate() waiting on inherited pipe handles
    # after a timeout. subprocess terminates only the direct child it created;
    # no process-name lookup or tree termination is performed.
    with tempfile.TemporaryFile() as output:
        try:
            timeout = min(PER_CMD_TIMEOUT, max(0, _RUN_DEADLINE - time.monotonic())) if _RUN_DEADLINE else PER_CMD_TIMEOUT
            if timeout <= 0:
                return 124, "[unknown] Total runner time budget exhausted."
            result = subprocess.run(cmd, stdout=output, stderr=subprocess.STDOUT,
                                    cwd=str(cwd), shell=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, f"[unknown: timeout {timeout:.1f}s] Direct runner stopped; descendants are not managed."
        except OSError:
            return 127, "[unknown] Runner unavailable or could not be started."
        output.seek(0)
        return result.returncode, output.read(1024 * 1024).decode("utf-8", errors="replace")


def _custom_argv(value) -> list[str] | None:
    # Legacy simple strings are parsed, never passed to a shell. Prefer arrays
    # for Windows paths. Reject operators rather than reinterpret their intent.
    if isinstance(value, str):
        if any(c in value for c in ";|&><`\n") or "$(" in value:
            return None
        try:
            value = shlex.split(value)
        except ValueError:
            return None
    if not isinstance(value, list) or not value or not all(isinstance(a, str) and a for a in value):
        return None
    name = Path(value[0]).name.lower().removesuffix(".exe")
    if name in {"cmd", "sh", "bash", "powershell", "pwsh", "npx", "claude", "codex", "orca"} or name.endswith((".cmd", ".bat")):
        return None
    return value


def _run_custom(value, root: Path) -> tuple[int, str]:
    argv = _custom_argv(value)
    if argv is None:
        return 127, "[unknown] Unsupported custom command; use a direct executable/argument array."
    return _run(argv, root)


def _runner_enabled(root: Path) -> bool:
    # Changing data files cannot opt a default installation in. The flag lives
    # in Claude's hook command; settings/hook code remain trusted, not sandboxed.
    if "--run-tests" not in sys.argv[1:]:
        return False
    path = root / ".claude/proof-of-done/runner-settings.json"
    if not session_snapshot.unlinked(path):
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def _read_input() -> dict:
    stream = getattr(sys.stdin, "buffer", None)
    raw = stream.read().decode("utf-8-sig") if stream is not None else sys.stdin.read().lstrip("\ufeff")
    try:
        value = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_verify_config(root: Path) -> dict:
    path = root / CONFIG_FILE
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        return config if isinstance(config, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _changed_files(root: Path) -> list[str] | None:
    """Parse NUL-delimited paths, including rename destinations and untracked files."""
    rc, top = _run(["git", "rev-parse", "--show-toplevel"], root)
    if rc != 0 or Path(top.strip()).resolve() != root.resolve():
        return None  # Never enumerate a parent repository outside this project.
    rc, out = _run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], root)
    if rc != 0:
        return None
    files, records = [], iter(out.split("\0"))
    for record in records:
        if len(record) < 4:
            continue
        status, path = record[:2], record[3:]
        files.append(path)
        if "R" in status or "C" in status:
            next(records, None)  # With -z, destination is first, source follows.
    return files


def _scope_to_session(root: Path, changed: list[str], hook_input: dict) -> list[str]:
    """Exclude unchanged baseline entries; absent baseline uses all working-tree changes."""
    baseline = session_snapshot.load(hook_input.get("session_id"), root)
    if baseline is None:
        sys.stderr.write("[verify_on_stop.py] no session-start snapshot: checking all working-tree changes\n")
        return changed
    return session_snapshot.changed_since(root, changed, baseline)


def session_start() -> None:
    """Record an initial working-tree baseline only when test execution is opted in."""
    global _RUN_DEADLINE
    _RUN_DEADLINE = time.monotonic() + 20  # SessionStart timeout is 30 seconds.
    hook_input = _read_input()
    if os.environ.get(SKIP_ENV):
        return
    root = _project_root()
    if (root / SKIP_FILE).exists() or not _runner_enabled(root):
        return
    changed = _changed_files(root)
    if changed is None:
        return
    session_snapshot.save_if_absent(hook_input.get("session_id"), root, changed)
    session_snapshot.prune()


def _pytest_targets_for_changed(root: Path, changed_py: list[str]) -> list[str]:
    """Choose changed tests or matching test names, then a conventional test directory."""
    test_targets: set[str] = set()

    names = set()
    for f in changed_py:
        name = Path(f).name

        if name.startswith("test_") or name.endswith("_test.py"):
            full = root / f
            if full.exists():
                test_targets.add(str(full))
            continue

        stem = Path(f).stem  # foo.py → foo

        names.update((f"test_{stem}.py", f"{stem}_test.py"))

    deadline = min(_RUN_DEADLINE or float("inf"), time.monotonic() + 2)
    for count, (directory, dirs, files) in enumerate(os.walk(root, followlinks=False)) if names else ():
        if count >= 4096 or time.monotonic() > deadline:
            sys.stderr.write("[unknown] Test discovery budget exhausted; using conventional test directories.\n")
            test_targets.clear()
            break
        dirs[:] = [d for d in dirs if d not in {".venv", "venv", "node_modules", ".git", ".claude", "__pycache__"}
                   and not (Path(directory) / d).is_symlink()]
        test_targets.update(str(Path(directory) / name) for name in names.intersection(files))

    if test_targets:
        return sorted(test_targets)


    for d in ("tests", "test", "python/tests", "src/tests"):
        if (root / d).exists():
            return [str(root / d)]
    return []


def _has_pytest_config(root: Path) -> bool:
    return any((root / m).exists() for m in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"))


def _has_typescript(root: Path) -> bool:
    return (root / "tsconfig.json").exists()


def _has_vitest(root: Path) -> bool:
    if (root / "vitest.config.ts").exists() or (root / "vitest.config.js").exists():
        return True
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            deps = {**data.get("devDependencies", {}), **data.get("dependencies", {})}
            return "vitest" in deps
        except (json.JSONDecodeError, OSError):
            return False
    return False


def _has_jest(root: Path) -> bool:
    return (
        (root / "jest.config.js").exists()
        or (root / "jest.config.ts").exists()
        or (root / "jest.config.json").exists()
    )


def _pytest_strict_targets(root: Path) -> list[str]:
    """Return existing conventional test directories for an opted-in test claim."""
    dirs = []
    for d in ("tests", "test", "python/tests", "src/tests"):
        if (root / d).exists():
            dirs.append(str(root / d))
    return dirs


def _run_pytest(
    root: Path,
    changed_py: list[str],
    config: dict,
    strict: bool = False,
) -> tuple[bool, str]:
    """Pytest exit 1/2 means failed tests/collection; custom runners use nonzero exits.

    Unavailable or timed-out custom runs (127/124) remain unknown.
    """
    if config.get("skip_py"):
        return False, ""

    # Custom command override
    custom = config.get("test_cmd")
    if custom:

        custom_strict = config.get("test_cmd_strict") if strict else None
        rc, out = _run_custom(custom_strict or custom, root)
        return rc not in (0, 124, 127) and rc > 0, out


    if not _has_pytest_config(root) and not any(
        (root / d).exists() for d in ("tests", "test", "python/tests")
    ):
        return False, ""  # no pytest env

    targets = _pytest_strict_targets(root) if strict else _pytest_targets_for_changed(root, changed_py)
    if not targets:
        return False, ""

    local_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter = str(local_python) if local_python.is_file() else sys.executable
    # The hook is isolated with -S, so its import lookup cannot determine whether
    # the explicitly opted-in runner interpreter has pytest. Run that interpreter.
    cmd = [interpreter, "-B", "-m", "pytest", *targets, "-q", "--tb=no", "-p", "no:cacheprovider"]
    skip = config.get("skip_tests")
    if skip and isinstance(skip, list) and all(isinstance(s, str) for s in skip):
        expr = " or ".join(skip)
        cmd.extend(["-k", f"not ({expr})"])

    rc, out = _run(cmd, root)
    if re.search(r"No module named ['\"]?pytest\b", out):
        return False, "[unknown] pytest is not installed for the selected runner interpreter."
    if rc not in (0, 1, 2, 5):
        return False, f"[unknown] pytest did not finish normally (exit {rc})."
    return rc in (1, 2), out


def _run_node(root: Path, relative: str, args: list[str]) -> tuple[int, str]:
    node, script = shutil.which("node"), root / "node_modules" / relative
    if not node or not script.is_file():
        return 127, "[unknown] Local Node runner is unavailable; no packages were downloaded."
    return _run([node, str(script), *args], root)


def _run_tsc(root: Path, config: dict) -> tuple[bool, str]:
    if config.get("skip_ts"):
        return False, ""

    custom = config.get("typecheck_cmd")
    if custom:
        rc, out = _run_custom(custom, root)
        return rc not in (0, 124, 127), out

    if not _has_typescript(root):
        return False, ""
    rc, out = _run_node(root, "typescript/bin/tsc", ["--noEmit"])
    return rc not in (0, 124, 127), out


def _run_vitest_or_jest(root: Path, config: dict) -> tuple[bool, str]:
    if config.get("skip_vitest"):
        return False, ""

    custom = config.get("vitest_cmd")
    if custom:
        rc, out = _run_custom(custom, root)
        return rc not in (0, 124, 127), out

    if _has_vitest(root):
        rc, out = _run_node(root, "vitest/vitest.mjs", ["run", "--reporter=dot"])
        return rc not in (0, 124, 127), out
    if _has_jest(root):
        rc, out = _run_node(root, "jest/bin/jest.js", ["--no-coverage"])
        return rc not in (0, 124, 127), out
    return False, ""


def _run_claim_gate(hook_input: dict, fails: list[str], sections: list[str]) -> str | None:
    """Isolate claim-check errors without discarding other findings."""
    try:
        from claim_gate import check as _claim_check
        problem = _claim_check(
            last_assistant_message=hook_input.get("last_assistant_message", ""),
            transcript_path=hook_input.get("transcript_path"),
        )
        if problem:
            fails.append("claim-gate")
            sections.append("[claim-gate]\n" + problem[:4000])
    except Exception as exc:  # Isolate this gate; retain other findings.
        sys.stderr.write(f"[verify_on_stop.py] claim_gate error (skip): {exc}\n")
        return "Proof of Done skipped claim_gate because it could not run. Run doctor and repair the installation before relying on this check."


def _run_inspect_gate(hook_input: dict, fails: list[str], sections: list[str]) -> str | None:
    """Isolate inspection-check errors without discarding other findings."""
    try:
        from inspect_gate import check as _inspect_check
        problem = _inspect_check(
            last_assistant_message=hook_input.get("last_assistant_message", ""),
            transcript_path=hook_input.get("transcript_path"),
        )
        if problem:
            fails.append("inspect-gate")
            sections.append("[inspect-gate]\n" + problem[:4000])
    except Exception as exc:  # Isolate this gate; retain other findings.
        sys.stderr.write(f"[verify_on_stop.py] inspect_gate error (skip): {exc}\n")
        return "Proof of Done skipped inspect_gate because it could not run. Run doctor and repair the installation before relying on this check."


def _detect_strict(hook_input: dict) -> bool:
    """Only a whole-suite claim justifies expanding to the whole suite."""
    try:
        from claim_gate import detect_test_claim, _strip_code, _turn_text
        claimed = detect_test_claim(
            last_assistant_message=hook_input.get("last_assistant_message", ""),
            transcript_path=hook_input.get("transcript_path"),
        )
        text = _strip_code(_turn_text(hook_input.get("last_assistant_message", ""),
                                     hook_input.get("transcript_path")))
        return claimed and bool(re.search(
            r"\b(?:all(?:\s+\d+)?\s+tests?|(?:full|entire|whole)\s+(?:test\s+)?suite|the test suite)\b"
            r"|(?:^|[.!?\n])\s*(?:the )?tests\s+(?:are\s+)?(?:pass|passing|passed)\b"
            r"|(?:모든|전체)\s*테스트|테스트.{0,20}(?:모두|전부)", text, re.I))
    except Exception as exc:  # Strict detection must not discard a gate failure.
        sys.stderr.write(f"[verify_on_stop.py] strict detect error (skip): {exc}\n")
        return False


def _run_test_suite(
    root: Path,
    changed: list[str],
    fails: list[str],
    sections: list[str],
    strict: bool = False,
) -> None:
    """Select local checks by changed extensions, or all supported checks for a test claim."""
    config = _load_verify_config(root)
    changed_py = [f for f in changed if f.endswith(".py") and not f.startswith(".venv/")]
    has_ts = any(f.endswith((".ts", ".tsx", ".js", ".jsx")) for f in changed)
    has_test_ts = any(
        f.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx",
                    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx"))
        for f in changed
    )

    if changed_py or strict:
        failed, out = _run_pytest(root, changed_py, config, strict=strict)
        if not failed and out.startswith("[unknown]"):
            sys.stderr.write(out + "\n")
        if failed:
            label = "pytest(strict)" if strict else "pytest"
            fails.append(label)
            sections.append(f"[{label}]\n" + out.strip()[-1200:])

    if has_ts or strict:
        failed, out = _run_tsc(root, config)
        if not failed and out.startswith("[unknown]"):
            sys.stderr.write(out + "\n")
        if failed:
            fails.append("tsc")
            sections.append("[tsc]\n" + out.strip()[:1200])

    if has_test_ts or strict:
        failed, out = _run_vitest_or_jest(root, config)
        if not failed and out.startswith("[unknown]"):
            sys.stderr.write(out + "\n")
        if failed:
            fails.append("vitest/jest")
            sections.append("[vitest/jest]\n" + out.strip()[-1200:])


def main() -> None:
    global _RUN_DEADLINE
    _RUN_DEADLINE = time.monotonic() + RUN_BUDGET_SECONDS
    hook_input = _read_input()

    if hook_input.get("stop_hook_active"):
        return

    if os.environ.get(SKIP_ENV):
        return

    root = _project_root()
    if (root / SKIP_FILE).exists():
        return

    fails: list[str] = []
    sections: list[str] = []


    notices = [notice for notice in (_run_claim_gate(hook_input, fails, sections),
                                     _run_inspect_gate(hook_input, fails, sections)) if notice]

    if _runner_enabled(root):
        try:
            strict = _detect_strict(hook_input)
            from claim_gate import _scan, _turn_text, ACTIVE_CLAIMS
            active = _scan(_turn_text(hook_input.get("last_assistant_message", ""),
                                      hook_input.get("transcript_path")), ACTIVE_CLAIMS)
            if strict or active:
                changed = _scope_to_session(root, _changed_files(root) or [], hook_input)
                if strict or changed:
                    _run_test_suite(root, changed, fails, sections, strict=strict)
        except Exception as exc:
            sys.stderr.write(f"[verify_on_stop.py] test suite error (skip): {type(exc).__name__}\n")

    if not fails:
        if notices:
            print(json.dumps({"systemMessage": "\n".join(notices)}))
        return

    # Stop uses decision/reason to return feedback to the agent. continue:false
    # would end processing entirely, rather than let the agent correct the claim.
    payload = {
        "decision": "block",
        "reason": (
            "Proof of Done found unsupported claims or failed checks: "
            + ", ".join(fails)
            + ". Reconcile the report with the evidence below. If work already "
              "happened outside the recognised evidence scope, state that limit; "
              "do not repeat commits, deployments or other side effects just to satisfy this check. "
              "Do not claim that this heuristic "
              "proves correctness, deployment, or complete coverage.\n\n"
            + "\n\n".join(sections)
        ),
    }
    if notices:
        payload["systemMessage"] = "\n".join(notices)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    try:
        if "--session-start" in sys.argv[1:]:
            session_start()
        else:
            main()
    except Exception as exc:
        sys.stderr.write(f"[verify_on_stop.py] hook error (check skipped): {exc}\n")
        sys.exit(0)
