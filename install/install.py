#!/usr/bin/env python3
"""Install or disconnect the checker in one explicitly selected project."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import stat

MODULES = ("claim_gate.py", "inspect_gate.py", "session_snapshot.py", "verify_on_stop.py")
REPO = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def linked_ancestor(path: Path) -> Path | None:
    for part in (path.absolute(), *path.absolute().parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return part
    return None


def unlinked(path: Path) -> bool:
    return linked_ancestor(path) is None


def validate_modules(folder: Path, sources: dict[str, bytes], *, complete: bool = False) -> None:
    """Validate the entire executable directory, including cached bytecode."""
    if not unlinked(folder):
        raise ValueError(f"Refusing a linked hook directory: {folder}")
    if folder.exists():
        for path in folder.iterdir():
            if path.name not in sources or not unlinked(path) or not path.is_file():
                raise ValueError(f"Unexpected file or directory in installed hooks: {path}")
            if path.read_bytes() != sources[path.name]:
                raise ValueError(f"An installed module differs: {path}. Disconnect its hooks first, then preserve the entire installation directory {folder.parent} outside that path before installing this version. No file was overwritten.")
    if complete and any(not (folder / name).is_file() for name in sources):
        raise ValueError("An installed module is missing.")


def locations(project: Path) -> tuple[Path, Path]:
    project = project.absolute()
    if not project.is_dir() or project.resolve() == Path.home().resolve():
        raise ValueError("Choose an existing project directory, not your home directory.")
    # Do not follow a settings symlink/junction into another project or user config.
    for p in (project / ".claude", project / ".claude/proof-of-done",
              project / ".claude/proof-of-done/hooks", project / ".claude/settings.local.json"):
        if (linked := linked_ancestor(p)) is not None:
            raise ValueError(f"Refusing linked path: {linked} (on the installation path {p}).")
    return project / ".claude/settings.local.json", project / ".claude/proof-of-done"


def read_settings(path: Path) -> tuple[dict, bytes | None]:
    raw = path.read_bytes() if path.exists() else None
    data = json.loads(raw.decode("utf-8-sig")) if raw else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("Settings must be an object with an object-valued hooks field.")
    for event in ("SessionStart", "Stop"):
        entries = data.get("hooks", {}).get(event, [])
        if not isinstance(entries, list) or any(
            not isinstance(b, dict) or not isinstance(b.get("hooks"), list)
            or any(not isinstance(h, dict) for h in b["hooks"]) for b in entries
        ):
            raise ValueError("Existing hook entries have an unsupported shape; nothing was changed.")
    return data, raw


def project_hooks_disabled(project: Path) -> bool:
    """Report known project off switches; global/managed policy is not read."""
    path = project / ".claude/settings.json"
    if not unlinked(path):
        raise ValueError("Refusing linked project settings.")
    if not path.exists():
        return False
    settings, _ = read_settings(path)
    return bool(settings.get("disableAllHooks"))


def trusted_python(project: Path) -> str:
    """Never start the hook with a project-owned interpreter or venv config."""
    root = project.resolve()
    values = (sys.executable, sys.prefix, getattr(sys, "_base_executable", sys.executable))
    for value in values:
        if Path(value).absolute().is_relative_to(project.absolute()) or Path(value).resolve().is_relative_to(root):
            raise ValueError("Use a Python interpreter installed outside the selected project, not its virtual environment.")
    return str(Path(values[-1]).absolute())


def bootstrap_source() -> str:
    """Pin executable identity in the trusted command, not a mutable manifest.

    Only verified bytes are compiled: a checkout between validation and import
    cannot substitute another file. Settings themselves remain a trust boundary.
    """
    expected = {name: digest((REPO / "hooks" / name).read_bytes()) for name in MODULES}
    return '''import hashlib, json, stat, sys, types
from pathlib import Path
expected = __EXPECTED_HASHES__
def skipped(detail):
    print(json.dumps({"systemMessage": "Proof of Done skipped: " + detail + ". Run doctor and repair the installation before relying on this check."}))
    raise SystemExit(0)
script = Path(sys.argv[1]).absolute()
folder = script.parent
try:
    for path in (folder, *folder.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            skipped("linked installation path")
    if {p.name for p in folder.iterdir()} != set(expected):
        skipped("unexpected or missing installed modules")
    verified = {}
    for name, checksum in expected.items():
        path = folder / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            skipped("linked or non-file module " + name)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != checksum:
            skipped("installed module integrity mismatch: " + name)
        verified[name] = content
except Exception:
    skipped("could not validate installed modules")
sys.dont_write_bytecode = True
sys.argv = sys.argv[1:]
try:
    for name in ("session_snapshot", "claim_gate", "inspect_gate"):
        module = types.ModuleType(name)
        module.__file__ = str(folder / (name + ".py"))
        sys.modules[name] = module
        exec(compile(verified[name + ".py"], module.__file__, "exec"), module.__dict__)
    namespace = {"__name__": "__main__", "__file__": str(script)}
    exec(compile(verified["verify_on_stop.py"], str(script), "exec"), namespace)
except Exception:
    skipped("validated hook could not run")
'''.replace("__EXPECTED_HASHES__", repr(expected))


def hook_blocks(base: Path, python: str, run_tests: bool = False) -> dict:
    # Explicit Bash shell: no dependence on a Windows PowerShell fallback.
    # Expand the root at runtime, preserving spaces, apostrophes and non-ASCII.
    script = '"$CLAUDE_PROJECT_DIR/.claude/proof-of-done/hooks/verify_on_stop.py"'
    python_command = shlex.quote(Path(python).as_posix())
    bootstrap = shlex.quote(bootstrap_source())
    def block(arguments: str, timeout: int) -> dict:
        opt_in = " --run-tests" if run_tests else ""
        notice = shlex.quote(json.dumps({"systemMessage": "Proof of Done skipped: its interpreter or hook file is unavailable. Run doctor and repair the installation before relying on it."}))
        report = "printf '%s\\n' " + notice
        command = f'if [ -f {script} ]; then {python_command} -I -B -S -c {bootstrap} {script}{arguments}{opt_in} || {report}; elif [ -d "$CLAUDE_PROJECT_DIR/.claude/proof-of-done" ]; then {report}; fi; exit 0'
        return {"hooks": [{"type": "command", "shell": "bash", "command": command, "timeout": timeout}]}
    return {"SessionStart": block(" --session-start", 30), "Stop": block("", 400)}


def write_settings(path: Path, data: dict, before: bytes | None, backup_dir: Path | None = None) -> bool:
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if before is not None and (json.loads(before.decode("utf-8-sig")) if before.strip() else {}) == data:
        return False
    current = path.read_bytes() if path.exists() else None
    if current != before:
        raise ValueError("Settings changed during installation; retry after checking the file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        backup_dir = backup_dir or path.parent / "proof-of-done/backups"
        if not unlinked(backup_dir):
            raise ValueError("Refusing a linked backup directory.")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (path.name + ".backup-" + str(time.time_ns()))
        with backup.open("xb") as stream:
            stream.write(before)
    fd, tmp = tempfile.mkstemp(prefix=".proof-of-done-", dir=path.parent / "proof-of-done")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return True


def install(project: Path, dry_run: bool = False, run_tests: bool = False) -> dict:
    python = trusted_python(project)
    settings_path, base = locations(project)
    settings, before = read_settings(settings_path)
    if settings.get("disableAllHooks") or project_hooks_disabled(project):
        raise ValueError("This project's settings disable all hooks; resolve that before installing.")
    manifest = base / "install-manifest.json"
    if not unlinked(manifest):
        raise ValueError("Refusing a linked manifest.")
    for private in (base / ".gitignore", base / "backups", base / "runner-settings.json", base / ".state"):
        if not unlinked(private):
            raise ValueError("Refusing a linked private installation path.")
    state = base / ".state"
    if not manifest.exists() and state.exists() and (not state.is_dir() or any(state.iterdir())):
        raise ValueError("Pre-existing state has no installation owner; preserve it outside this installation before proceeding.")
    ignore = base / ".gitignore"
    if ignore.exists() and ignore.read_text(encoding="utf-8").strip() != "*":
        raise ValueError("The installation ignore file was edited; preserve it before reinstalling.")
    sources = {name: (REPO / "hooks" / name).read_bytes() for name in MODULES}
    validate_modules(base / "hooks", sources)
    runner = base / "runner-settings.json"
    if runner.exists():
        value = json.loads(runner.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("enabled") is not run_tests:
            raise ValueError("Runner opt-in differs; review runner-settings.json explicitly before changing it.")
    blocks = hook_blocks(base, python, run_tests)
    if manifest.exists():
        old = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(old, dict) or old.get("blocks") != blocks:
            raise ValueError("Existing installation uses another interpreter or path; disconnect it first.")
    updated = copy.deepcopy(settings)
    hooks = updated.setdefault("hooks", {})
    added = []
    for event, block in blocks.items():
        entries = hooks.setdefault(event, [])
        for entry in entries:
            if entry != block and any(_owned_hook(h, block) or "proof-of-done" in str(h.get("command", "")) for h in entry["hooks"]):
                raise ValueError("An installed hook was edited; disconnect it before reinstalling.")
        if block not in entries:
            entries.append(block)
            added.append(event)
    if dry_run:
        return {"status": "dry-run", "hooks_to_add": added}
    (base / "hooks").mkdir(parents=True, exist_ok=True)
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    if not runner.exists():
        runner.write_text(json.dumps({"enabled": run_tests}) + "\n", encoding="utf-8")
    for name, content in sources.items():
        target = base / "hooks" / name
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(content)
    write_settings(settings_path, updated, before, base / "backups")
    record = {"version": 8, "blocks": blocks, "files": {n: digest(b) for n, b in sources.items()}}
    if not manifest.exists():
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)
    return {"status": "installed", "hooks_added": added, "scope": "project-local"}


def _owned_hook(hook: dict, block: dict) -> bool:
    # Timeout/outer matcher edits do not change the registered executable.
    # A changed command is ambiguous: report it instead of claiming disconnect.
    return any(hook.get("type") == owned.get("type") and hook.get("command") == owned.get("command") and hook.get("args") == owned.get("args") for owned in block.get("hooks", []))


def uninstall(project: Path, dry_run: bool = False) -> dict:
    settings_path, base = locations(project)
    manifest = base / "install-manifest.json"
    if not unlinked(manifest):
        raise ValueError("Refusing a linked manifest.")
    if manifest.exists():
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("blocks"), dict):
            raise ValueError("Invalid installation manifest.")
    # A missing folder must not prevent disconnection. Ownership still comes
    # only from the recomputed exact command below, never a substring match.
    settings, before = read_settings(settings_path)
    updated = copy.deepcopy(settings)
    removed = []
    for event in ("SessionStart", "Stop"):
        # The mutable manifest cannot expand our authority to foreign hooks.
        # Recompute trusted command identity for this interpreter and version.
        blocks = [hook_blocks(base, trusted_python(project), opt_in)[event] for opt_in in (False, True)]
        entries = updated.get("hooks", {}).get(event, [])
        kept = []
        for entry in entries:
            remaining = [h for h in entry["hooks"] if not any(_owned_hook(h, b) for b in blocks)]
            if len(remaining) != len(entry["hooks"]):
                removed.append(event)
            if any("proof-of-done" in str(h.get("command", "")) for h in remaining):
                raise ValueError("An installed command differs from this version/interpreter; manual disconnection is required. Nothing was changed. Remove only its proof-of-done command entries from SessionStart and Stop in .claude/settings.local.json, keeping all other hooks.")
            if remaining or not entry["hooks"]:
                kept.append({**entry, "hooks": remaining})
        if event in updated.get("hooks", {}):
            if kept:
                updated["hooks"][event] = kept
            else:
                del updated["hooks"][event]
    if not dry_run and before is not None:
        write_settings(settings_path, updated, before, base / "backups")
    # Keep all installed files, snapshots and backups. Remove only exact owned blocks.
    return {"status": "dry-run" if dry_run else "disconnected", "hooks_removed": removed,
            "files_preserved": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-tests", action="store_true", help="Opt in to running trusted project tests and .claude/verify.json commands.")
    parser.add_argument("--uninstall", action="store_true", help="Disconnect exact installed hooks; retain files and backups.")
    args = parser.parse_args()
    try:
        result = uninstall(args.project, args.dry_run) if args.uninstall else install(args.project, args.dry_run, args.run_tests)
    except (OSError, ValueError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
