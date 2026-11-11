"""Use only disposable project-local settings; never a user's live settings."""
import json
from pathlib import Path

import pytest
import install
import doctor


def existing(tmp_path):
    path = tmp_path / ".claude/settings.local.json"
    path.parent.mkdir()
    payload = {"permissions": {"allow": ["Read"]}, "unknownFutureSetting": {"nested": [1]},
               "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo keep-me"}]}]}}
    raw = json.dumps(payload).encode()
    path.write_bytes(raw)
    return path, payload, raw


def test_merge_preserves_settings_hooks_and_original_backup(tmp_path):
    path, original, raw = existing(tmp_path)
    install.install(tmp_path)
    current = json.loads(path.read_bytes())
    assert current["permissions"] == original["permissions"]
    assert current["unknownFutureSetting"] == original["unknownFutureSetting"]
    assert current["hooks"]["Stop"][0] == original["hooks"]["Stop"][0]
    assert len(current["hooks"]["Stop"]) == 2
    assert next((path.parent / "proof-of-done/backups").glob("*.backup-*")).read_bytes() == raw


def test_install_is_idempotent_and_uninstall_preserves_foreign_changes(tmp_path):
    path, original, _ = existing(tmp_path)
    install.install(tmp_path)
    before = path.read_bytes()
    assert install.install(tmp_path)["hooks_added"] == []
    assert path.read_bytes() == before
    data = json.loads(before)
    data["permissions"]["allow"].append("Edit")
    path.write_text(json.dumps(data), encoding="utf-8")
    install.uninstall(tmp_path)
    after = json.loads(path.read_bytes())
    assert after["permissions"]["allow"] == ["Read", "Edit"]
    assert after["hooks"]["Stop"] == original["hooks"]["Stop"]
    assert (tmp_path / ".claude/proof-of-done/hooks/claim_gate.py").exists()


@pytest.mark.parametrize("payload", ['{broken', '[]', '{"hooks":[]}', '{"hooks":{"Stop":{}}}', '{"hooks":{"Stop":[{}]}}'])
def test_invalid_settings_are_never_overwritten(tmp_path, payload):
    path = tmp_path / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        install.install(tmp_path)
    assert path.read_text(encoding="utf-8") == payload
    assert not (path.parent / "proof-of-done").exists()


def test_dry_run_has_no_side_effects(tmp_path):
    assert install.install(tmp_path, True)["status"] == "dry-run"
    assert not (tmp_path / ".claude").exists()


def test_modified_module_is_not_overwritten(tmp_path):
    install.install(tmp_path)
    path = tmp_path / ".claude/proof-of-done/hooks/claim_gate.py"
    path.write_text("# user's customization\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        install.install(tmp_path)
    assert path.read_text(encoding="utf-8") == "# user's customization\n"


def test_doctor_invokes_both_synthetic_hook_paths(tmp_path):
    install.install(tmp_path)
    result = doctor.diagnose(tmp_path)
    assert result["status"] == "pass"
    assert result["synthetic_false_claim"] == "blocked"
    assert result["synthetic_claim_with_edit"] == "allowed"
    assert result["real_claude_session"] == "not tested"


def test_doctor_detects_off_switch(tmp_path):
    install.install(tmp_path)
    (tmp_path / ".claude/skip-stop-verify").touch()
    with pytest.raises(ValueError, match="off switch"):
        doctor.diagnose(tmp_path)


def test_doctor_rejects_wrong_path_hook(tmp_path):
    install.install(tmp_path)
    path = tmp_path / ".claude/settings.local.json"
    data = json.loads(path.read_bytes())
    data["hooks"]["Stop"][0]["hooks"][0]["command"] = 'python "elsewhere/verify_on_stop.py"'
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="not connected"):
        doctor.diagnose(tmp_path)


def test_settings_concurrent_change_is_preserved(tmp_path):
    path, _, before = existing(tmp_path)
    path.write_text('{"other":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed during"):
        install.write_settings(path, {"new": True}, before)
    assert json.loads(path.read_bytes()) == {"other": True}


def test_wrapper_uses_runtime_project_root_and_explicit_bash():
    block = install.hook_blocks(Path("folder with spaces/dollar$quote'/sample"), "python")
    entry = block["Stop"]["hooks"][0]
    assert entry["shell"] == "bash"
    assert '"$CLAUDE_PROJECT_DIR/.claude/proof-of-done/hooks/verify_on_stop.py"' in entry["command"]
    assert "folder with spaces" not in entry["command"]
