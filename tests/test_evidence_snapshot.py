import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('evidence_snapshot',Path(__file__).resolve().parents[1]/'governance/evidence_snapshot.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def test_failed_health_is_preserved_not_certified(tmp_path):
    root=tmp_path/'repo';out=tmp_path/'out';(root/'state').mkdir(parents=True)
    (root/'state/DATA_HEALTH.json').write_text(json.dumps({'overall_status':'FAIL'}))
    with patch.dict('os.environ',{'BELGIUM_STATE_RESTORED':'1','BELGIUM_STATE_SOURCE_RUN_ID':'123'}):
        module.snapshot(root,out)
    assert json.loads((out/'state/DATA_HEALTH.json').read_text())['overall_status']=='FAIL'
    result=json.loads((out/'SNAPSHOT_MANIFEST.json').read_text())
    assert result['restored_state_source_run_id']=='123'
    assert result['promotion_eligible'] is False
