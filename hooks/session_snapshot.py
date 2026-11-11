"""Session baselines of git paths using size and mtime equality. These are change-selection hints, not content hashes or proof of correctness."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import stat
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).parent.parent / ".state" / "stop-verify"


RETENTION_SECONDS = 7 * 86400
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _fingerprint(root: Path, entry: str) -> list[int] | None:
    """Return the literal path size and mtime_ns, or None for a missing path."""
    rel = entry
    try:
        st = (root / rel).stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def take(root: Path, entries: list[str]) -> dict[str, list[int] | None]:
    return {entry: _fingerprint(root, entry) for entry in entries}


def unlinked(path: Path) -> bool:
    """Refuse symlinks and Windows reparse points in every path component."""
    for part in (path.absolute(), *path.absolute().parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    return True


def _path_for(session_id: str | None, snapshot_dir: Path) -> Path | None:
    if not isinstance(session_id, str) or not _SAFE_ID.fullmatch(session_id) or not unlinked(snapshot_dir):
        return None
    path = snapshot_dir / f"{session_id}.json"
    return path if unlinked(path) else None


def save_if_absent(session_id: str | None, root: Path, entries: list[str], snapshot_dir: Path | None = None) -> bool:
    """Keep the first baseline for a session, including across resume and compaction."""
    snapshot_dir = snapshot_dir or SNAPSHOT_DIR
    path = _path_for(session_id, snapshot_dir)
    if path is None or path.exists():
        return False
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {"format": "proof-of-done-snapshot-v1", "root": str(root), "taken_at": time.time(), "entries": take(root, entries)}
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
    except FileExistsError:
        return False
    return True


def load(session_id: str | None, root: Path, snapshot_dir: Path | None = None) -> dict | None:
    """Return a valid baseline for this root, or None when unavailable."""
    path = _path_for(session_id, snapshot_dir or SNAPSHOT_DIR)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("root") != str(root) or not isinstance(payload.get("entries"), dict):
        return None
    return payload["entries"]


def changed_since(root: Path, entries: list[str], baseline: dict) -> list[str]:
    """Select paths absent from the baseline or with a different size/mtime."""
    changed = []
    for entry in entries:
        if entry not in baseline or baseline[entry] != _fingerprint(root, entry):
            changed.append(entry)
    return changed


def prune(snapshot_dir: Path | None = None, now: float | None = None) -> None:
    """Remove only expired, recognised snapshots from an unlinked directory."""
    snapshot_dir = snapshot_dir or SNAPSHOT_DIR
    if not unlinked(snapshot_dir):
        return
    cutoff = (now if now is not None else time.time()) - RETENTION_SECONDS
    try:
        for p in snapshot_dir.glob("*.json"):
            if not _SAFE_ID.fullmatch(p.stem) or not unlinked(p) or not p.is_file():
                continue
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("format") != "proof-of-done-snapshot-v1" or not isinstance(payload.get("entries"), dict) or not isinstance(payload.get("root"), str):
                continue
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except OSError as exc:
        sys.stderr.write(f"[session_snapshot] prune skipped: {exc}\n")
