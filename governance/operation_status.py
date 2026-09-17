"""Operational evidence only. Never grants scientific admission or authority."""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

SCHEMA = 'OPERATION_STATUS_V1'


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identity():
    return {key: os.environ.get(env, '') for key, env in {
        'repository':'GITHUB_REPOSITORY', 'workflow':'GITHUB_WORKFLOW',
        'run_id':'GITHUB_RUN_ID', 'run_attempt':'GITHUB_RUN_ATTEMPT', 'commit_sha':'GITHUB_SHA'}.items()}


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('NAIVE_OPERATION_TIMESTAMP')
    return result


def source_identity(root):
    root = Path(root)
    files = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True, capture_output=True).stdout.decode().split('\0')
    code, provenance = {}, {}
    for name in sorted(filter(None, files)):
        if name.endswith(('.py', '.yml', '.yaml', 'requirements.txt', 'pyproject.toml')):
            code[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if name.startswith('provenance/'):
            provenance[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if not code:
        raise ValueError('CODE_IDENTITY_MISSING')
    return {'code_sha256':stable_hash(code), 'provenance_state_sha256':stable_hash(provenance),
            'provenance_files':len(provenance)}


def artifact_name(workflow=None):
    return 'operation-status-' + stable_hash(workflow or identity()['workflow'])[:20]


def read_artifact(repo, artifact):
    if artifact['size_in_bytes'] > 2 * 1024 * 1024:
        raise ValueError('OPERATION_ARTIFACT_SIZE_LIMIT')
    raw = subprocess.run(['gh', 'api', '--allow-escape-sequences',
                          f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"], check=True, capture_output=True, timeout=60).stdout
    if artifact.get('digest') != 'sha256:' + hashlib.sha256(raw).hexdigest():
        raise ValueError('OPERATION_ARTIFACT_DIGEST_MISMATCH')
    with zipfile.ZipFile(io.BytesIO(raw)) as pack:
        member = pack.getinfo('OPERATION_STATUS.json')
        if member.file_size > 1024 * 1024:
            raise ValueError('OPERATION_STATUS_SIZE_LIMIT')
        status = json.loads(pack.read(member))
    if status.get('schema') != SCHEMA or status.get('runtime_identity', {}).get('repository') != repo:
        raise ValueError('OPERATION_ARTIFACT_IDENTITY_MISMATCH')
    return status


def previous_status(workflow=None):
    """Rolling attributable artifact history; no cache, git mutation or secrets export."""
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
        return None
    current = identity(); repo = current['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not os.environ.get('GH_TOKEN'):
        raise ValueError('OPERATION_HISTORY_ACCESS_MISSING')
    name = artifact_name(workflow)
    result = subprocess.run(['gh', 'api', f'repos/{repo}/actions/artifacts?name={name}&per_page=20'],
                            check=True, capture_output=True, text=True, timeout=30)
    listing = json.loads(result.stdout)
    for artifact in sorted(listing['artifacts'], key=lambda a: a['id'], reverse=True):
        run = artifact.get('workflow_run', {})
        if artifact['name'] != name or artifact['expired'] or run.get('head_branch') != 'main' or str(run.get('id')) == current['run_id']:
            continue
        # Never consume partially published evidence from an active run.
        meta = json.loads(subprocess.run(['gh', 'api', f"repos/{repo}/actions/runs/{run['id']}"],
                          check=True, capture_output=True, text=True, timeout=30).stdout)
        if meta['status'] != 'completed':
            continue
        status = read_artifact(repo, artifact)
        if str(status['runtime_identity']['run_id']) != str(run['id']) or status['runtime_identity']['commit_sha'] != run['head_sha']:
            raise ValueError('OPERATION_RUN_BINDING_MISMATCH')
        status['previous_artifact_id'] = artifact['id']
        if meta['conclusion'] == 'success':
            status['last_technical_success_run_id'] = meta['id']
        elif meta['conclusion'] in {'failure', 'timed_out'}:
            status['last_genuine_technical_failure_run_id'] = meta['id']
        return status
    return None


def classify_rejections(cases, expected):
    """Exit 42 alone is insufficient: require exact complete boundary evidence."""
    if len(cases) != len(expected) or {x.get('engine') for x in cases} != set(expected):
        return 'TECHNICAL_FAILURE'
    for case in cases:
        evidence = case.get('evidence', {})
        if (case.get('exit_code') != 42 or evidence.get('status') != 'FAIL'
                or evidence.get('failure') != 'ACTUAL_INPUT_PROVENANCE_CONTRACT_MISSING'
                or evidence.get('function_body_executed') is not False
                or not evidence.get('actual_boundary')
                or evidence.get('promotion_eligible') is not False
                or not evidence.get('source_hashes')):
            return 'TECHNICAL_FAILURE'
    return 'SCIENTIFIC_BLOCK'


def record(scientific_state, reason, inputs, sources, previous=None, now=None,
           operation_class='SCIENTIFIC_BLOCK', scope=None):
    if operation_class not in {'TECHNICAL_FAILURE','SCIENTIFIC_BLOCK','SUCCESS','NO_WORK_DUE'}:
        raise ValueError('UNKNOWN_OPERATION_CLASS')
    now = now or datetime.now(timezone.utc).isoformat()
    stamp(now)
    runtime = identity()
    key = {'scope':scope or runtime['workflow'], 'reason':reason, 'dataset_hashes':inputs, **sources}
    blocker = stable_hash(key) if operation_class == 'SCIENTIFIC_BLOCK' else None
    same = previous is not None and previous.get('blocker_id') == blocker and blocker is not None
    if previous is not None:
        if previous.get('schema') != SCHEMA:
            raise ValueError('MALFORMED_PREVIOUS_OPERATION_STATUS')
        if previous.get('blocker_id') and stable_hash(previous['blocker_identity']) != previous['blocker_id']:
            raise ValueError('PREVIOUS_BLOCKER_IDENTITY_MISMATCH')
        if stamp(previous['last_checked_utc']) > stamp(now):
            raise ValueError('FUTURE_PREVIOUS_OPERATION_STATUS')
    first = previous['blocker_first_seen_utc'] if same else now if blocker else None
    if first and stamp(first) > stamp(now):
        raise ValueError('INVALID_BLOCKER_FIRST_SEEN')
    return {'schema':SCHEMA, 'runtime_identity':runtime, 'operation_class':operation_class,
            'scientific_state':scientific_state, 'promotion_eligible':False,
            'blocker_id':blocker, 'blocker_reason':reason, 'blocker_identity':key,
            'blocker_first_seen_utc':first, 'last_checked_utc':now,
            'history_scope':'ROLLING_RETAINED_ARTIFACT_OBSERVATIONS; NOT_INFERRED_BEFORE_FIRST_RECORD',
            'previous_artifact_id':previous.get('previous_artifact_id') if previous else None,
            'work_decision':'NO_WORK_DUE_UNCHANGED_BLOCKER' if same else 'RECONSIDERED_STATE',
            'research_executed':False,
            'reconsider_on':['dataset bytes change','relevant source/code change','provenance state change'],
            'unblock_condition':'Independently verifiable record-level publication/vintage/label provenance and validated input admission; changed bytes or green orchestration alone never certify science.',
            'technical_attention_required':operation_class == 'TECHNICAL_FAILURE',
            'last_technical_success_run_id':previous.get('last_technical_success_run_id') if previous else None,
            'last_genuine_technical_failure_run_id':previous.get('last_genuine_technical_failure_run_id') if previous else None,
            'technical_history_scope':'PREVIOUS_COMPLETED_STATUS_ARTIFACTS; NULL_IS_NOT_A_CLAIM_OF_NO_PAST_FAILURES'}


def publish(status, destination):
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    text = json.dumps(status, indent=2) + '\n'
    (destination / 'OPERATION_STATUS.json').write_text(text, encoding='utf-8')
    # Each artifact is immutable and attributable; the manifest links the rolling
    # predecessor instead of repeatedly copying an unbounded journal.
    (destination / 'RESEARCH_LEDGER.jsonl').write_text(json.dumps(status) + '\n', encoding='utf-8')
    for name in ['ENGINE_REGISTRY.json','GLOBAL_CONTROL_ROOM.json']:
        (destination / name).write_text(json.dumps({'scope':'THIS_WORKFLOW_ONLY','engines':[status]},indent=2)+'\n',encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('operation_class=' + status['operation_class'] + '\noperation_artifact=' + artifact_name() + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write('\n## ' + status['operation_class'] + ' — ' + status['scientific_state'] +
                          '\n\nReason: ' + status['blocker_reason'] + '\n\nFirst seen: ' + str(status['blocker_first_seen_utc']) +
                          '; checked: ' + status['last_checked_utc'] + '\n\n' + status['work_decision'] +
                          '\n\nUnblock: ' + status['unblock_condition'] + '\n\nGreen orchestration is not scientific certification.\n')
    print('OPERATION_STATUS ' + json.dumps(status, sort_keys=True))
