"""Import the shipped copy, never a user's installed hooks."""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))


@pytest.fixture(autouse=True)
def contain_legacy_temp_helpers(monkeypatch, tmp_path):
    """Keep legacy mkdtemp/mkstemp fixtures under pytest's managed directory."""
    scratch = tmp_path / "legacy-temp"
    scratch.mkdir()
    # A hook process has one deadline; in-process unit tests need a fresh one.
    if "verify_on_stop" in sys.modules:
        monkeypatch.setattr(sys.modules["verify_on_stop"], "_RUN_DEADLINE", None)
    directory, file = tempfile.mkdtemp, tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda suffix=None, prefix=None, dir=None: directory(suffix=suffix, prefix=prefix, dir=dir or scratch))
    monkeypatch.setattr(tempfile, "mkstemp", lambda suffix=None, prefix=None, dir=None, text=False: file(suffix=suffix, prefix=prefix, dir=dir or scratch, text=text))
