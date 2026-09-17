"""Export small public diagnostics separately from the large raw-data archive."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

def timestamp_diagnostics(root):
    """Read only failing series' timestamp columns; preserve every stored row."""
    import pandas as pd
    import pyarrow.parquet as pq
    root=Path(root)
    health=json.loads((root/'state/DATA_HEALTH.json').read_text())
    reports=[]
    for source in health.get('sources',[]):
        if not source.get('bad_timestamps'):continue
        sid=source['source_id']
        if not sid.replace('_','').isalnum():raise ValueError('INVALID_SOURCE_ID')
        path=root/'data/canonical'/f'{sid}.parquet'
        if not path.exists():
            reports.append({'source_id':sid,'status':'CANONICAL_MISSING'});continue
        names=pq.read_schema(path).names
        columns=[x for x in ['datetime','dateTimeUtc','timestamp','delivery_start_utc','_retrieved_at_utc','_source_url'] if x in names]
        frame=pd.read_parquet(path,columns=columns)
        if 'delivery_start_utc' not in frame:
            reports.append({'source_id':sid,'status':'DELIVERY_COLUMN_MISSING'});continue
        bad=pd.to_datetime(frame.delivery_start_utc,utc=True,errors='coerce').isna()
        sample=frame.loc[bad].head(20).astype('string').fillna('<NULL>').to_dict('records')
        reports.append({'source_id':sid,'canonical_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':len(frame),'bad_timestamps':int(bad.sum()),'sample_limit':20,'stored_bad_row_samples':sample,'status':'DIAGNOSTIC_ONLY_NO_REPAIR_OR_CERTIFICATION'})
    return reports

def snapshot(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if os.environ.get('BELGIUM_STATE_RESTORED')!='1':raise RuntimeError('NO_RESTORED_STATE_EVIDENCE')
    files=list((root/'state').glob('*.json'))
    files+=list((root/'research').glob('**/*STATUS.json'))
    files+=list((root/'research').glob('**/READINESS.json'))
    files+=list((root/'research/case_studies').glob('*REGISTRY.json'))
    records=[]
    for path in sorted(set(files)):
        raw=path.read_bytes()
        if len(raw)>10*1024*1024:continue
        json.loads(raw)
        relative=path.relative_to(root);target=out/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        records.append({'path':relative.as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    if not any(x['path']=='state/DATA_HEALTH.json' for x in records):raise RuntimeError('HEALTH_REPORT_MISSING')
    identity={k:os.environ.get(v,'') for k,v in {'repository':'GITHUB_REPOSITORY','workflow':'GITHUB_WORKFLOW','run_id':'GITHUB_RUN_ID','run_attempt':'GITHUB_RUN_ATTEMPT','commit_sha':'GITHUB_SHA'}.items()}
    report={'schema':'BELGIUM_PUBLIC_EVIDENCE_SNAPSHOT_V1','created_at':datetime.now(timezone.utc).isoformat(),'runtime_identity':identity,'audited_source_commit_sha':os.environ.get('AUDITED_SOURCE_SHA'),'restored_state_source_run_id':os.environ.get('BELGIUM_STATE_SOURCE_RUN_ID'),'files':records,'scope':'PUBLIC_DATA_DIAGNOSTICS_ONLY; NO_PIT_OR_EDGE_CERTIFICATION','promotion_eligible':False}
    (out/'SNAPSHOT_MANIFEST.json').write_text(json.dumps(report,indent=2)+'\n')
    health=json.loads((root/'state/DATA_HEALTH.json').read_text())
    diagnostics=timestamp_diagnostics(root)
    (out/'TIMESTAMP_DIAGNOSTICS.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
    print(json.dumps({'source_run_id':report['restored_state_source_run_id'],'health':health},default=str))

if __name__=='__main__':snapshot(Path.cwd(),Path(os.environ['RUNNER_TEMP'])/'belgium-evidence')
