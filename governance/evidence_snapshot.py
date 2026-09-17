"""Export small public diagnostics separately from the large raw-data archive."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

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
    print(json.dumps({'source_run_id':report['restored_state_source_run_id'],'health':health},default=str))

if __name__=='__main__':snapshot(Path.cwd(),Path(os.environ['RUNNER_TEMP'])/'belgium-evidence')
