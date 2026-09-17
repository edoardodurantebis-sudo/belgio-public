import json
from pathlib import Path
import pytest
from belgium_public.lab import run_lab


def test_known_core_gap_is_scientific_block(tmp_path):
    health = tmp_path / 'health.json'
    health.write_text(json.dumps({'overall_status':'FAIL'}))
    result = run_lab(tmp_path, health, tmp_path / 'status.json')
    assert result['operation_class'] == 'SCIENTIFIC_BLOCK'
    assert result['scientific_state'] == 'BLOCKED_DATA_HEALTH'
    assert result['promotion_eligible'] is False


@pytest.mark.parametrize('body', ['broken JSON', '{}', '{"overall_status":"UNKNOWN"}'])
def test_malformed_health_is_technical_error(tmp_path, body):
    health = tmp_path / 'health.json'
    health.write_text(body)
    with pytest.raises(ValueError):
        run_lab(tmp_path, health, tmp_path / 'status.json')


def test_missing_health_is_technical_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_lab(tmp_path, tmp_path / 'missing.json', tmp_path / 'status.json')
