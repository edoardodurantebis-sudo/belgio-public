#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

artifacts_json="$(gh api "/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=belgium-public-state&per_page=100")"
artifact_id=""
source_run_id=""

while IFS=$'\t' read -r aid run_id created_at; do
  [[ -n "${aid}" && -n "${run_id}" ]] || continue
  conclusion="$(gh api "/repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}" --jq '.conclusion // ""' 2>/dev/null || true)"
  if [[ "${conclusion}" == "success" ]]; then
    artifact_id="${aid}"
    source_run_id="${run_id}"
    break
  fi
  echo "Skipping state artifact ${aid} from non-success run ${run_id} conclusion=${conclusion:-unknown}."
done < <(
  printf '%s' "${artifacts_json}" | jq -r '
    [.artifacts[] | select(.expired == false and .workflow_run.id != null)]
    | sort_by(.created_at) | reverse
    | .[] | [.id, .workflow_run.id, .created_at] | @tsv'
)

if [[ -z "${artifact_id}" ]]; then
  echo "BELGIUM_STATE_RESTORED=0" >> "${GITHUB_ENV:-/dev/null}"
  echo "No certified successful cumulative state artifact found; bootstrap semantics apply."
  exit 0
fi

rm -rf .state_restore
mkdir -p .state_restore
archive=".state_restore/state.zip"
gh api "/repos/${GITHUB_REPOSITORY}/actions/artifacts/${artifact_id}/zip" > "${archive}"
unzip -oq "${archive}" -d .state_restore/unpacked

manifest=".state_restore/unpacked/state/RUN_MANIFEST.json"
if [[ ! -s "${manifest}" ]]; then
  echo "BELGIUM_STATE_RESTORED=0" >> "${GITHUB_ENV:-/dev/null}"
  echo "Certified artifact ${artifact_id} is malformed: missing state/RUN_MANIFEST.json; refusing restore."
  exit 0
fi

python - <<'PY'
import json
from pathlib import Path
p=Path('.state_restore/unpacked/state/RUN_MANIFEST.json')
m=json.loads(p.read_text())
bad=[f for f in m.get('failures', []) if f.get('tier') in {'core','core_pre_mari'}]
if bad:
    raise SystemExit(f'RESTORE_REFUSED_CORE_FAILURES:{bad}')
if not m.get('selected_sources'):
    raise SystemExit('RESTORE_REFUSED_NO_SELECTED_SOURCES')
print('RESTORE_MANIFEST_CERTIFIED', m.get('mode'), m.get('health'), len(m.get('selected_sources', [])))
PY

for d in data/raw data/canonical state research; do
  if [[ -e ".state_restore/unpacked/${d}" ]]; then
    mkdir -p "$(dirname "${d}")"
    rm -rf "${d}"
    cp -a ".state_restore/unpacked/${d}" "${d}"
  fi
done

echo "BELGIUM_STATE_RESTORED=1" >> "${GITHUB_ENV:-/dev/null}"
echo "BELGIUM_STATE_SOURCE_RUN_ID=${source_run_id}" >> "${GITHUB_ENV:-/dev/null}"
echo "Restored certified cumulative state from artifact ${artifact_id}, successful workflow run ${source_run_id}."
