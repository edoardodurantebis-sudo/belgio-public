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

def test_bad_timestamp_samples_preserve_raw_evidence(tmp_path):
    import pandas as pd
    (tmp_path/'state').mkdir();(tmp_path/'data/canonical').mkdir(parents=True)
    (tmp_path/'state/DATA_HEALTH.json').write_text(json.dumps({'sources':[{'source_id':'ods160','bad_timestamps':1}]}))
    path=tmp_path/'data/canonical/ods160.parquet'
    pd.DataFrame({'datetime':['2026-09-17T12:45:00+02:00'],'delivery_start_utc':[pd.NaT]}).to_parquet(path)
    original=path.read_bytes()
    result=module.timestamp_diagnostics(tmp_path)[0]
    assert result['bad_timestamps']==1
    assert result['stored_bad_row_samples'][0]['datetime']=='2026-09-17T12:45:00+02:00'
    assert path.read_bytes()==original
