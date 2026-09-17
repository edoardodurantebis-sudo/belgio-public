import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location("profit_entrypoint", Path(__file__).resolve().parents[1] / "scripts/run_profit_lab.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

@pytest.mark.parametrize("health", ["PASS", "FAIL", "BLOCKED", None])
def test_uncertified_source_never_repeats_discovery(tmp_path, health):
    (tmp_path / "research").mkdir()
    previous = tmp_path / "research/PROFIT_LAB_STATUS.json"
    previous.write_bytes(b'{"old_diagnostic_evidence": true}')
    if health is not None:
        (tmp_path / "state").mkdir()
        (tmp_path / "state/DATA_HEALTH.json").write_text(json.dumps({"overall_status":health}))
    with patch.object(entry, "ROOT", tmp_path), patch("governance.operation_status.source_identity", return_value={"code_sha256":"test","provenance_state_sha256":"missing"}), patch.object(entry, "run_two_stage_profit_lab", side_effect=AssertionError("FIT_MUST_NOT_RUN")):
        assert entry.main() == 0
    assert previous.read_bytes() == b'{"old_diagnostic_evidence": true}'
    result = json.loads((tmp_path / "research/PROFIT_COMPUTE_STATUS.json").read_text())
    assert result["status"] == "PIT_UNCERTIFIED"
    assert result["research_executed"] is False
    assert result["promotion_eligible"] is False
    assert result["collectors_and_nrt_continue"] is True
