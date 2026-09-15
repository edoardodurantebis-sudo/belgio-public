#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

artifact_id="$(
  gh api "/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=belgium-public-state&per_page=100" \
    --jq '[.artifacts[] | select(.expired == false)] | sort_by(.created_at) | reverse | .[0].id // empty'
)"

if [[ -z "${artifact_id}" ]]; then
  echo "BELGIUM_STATE_RESTORED=0" >> "${GITHUB_ENV:-/dev/null}"
  echo "No previous cumulative state artifact found; bootstrap semantics apply."
  exit 0
fi

mkdir -p .state_restore
archive=".state_restore/state.zip"
gh api "/repos/${GITHUB_REPOSITORY}/actions/artifacts/${artifact_id}/zip" > "${archive}"
unzip -oq "${archive}" -d .state_restore/unpacked

for d in data/raw data/canonical state research; do
  if [[ -e ".state_restore/unpacked/${d}" ]]; then
    mkdir -p "$(dirname "${d}")"
    rm -rf "${d}"
    cp -a ".state_restore/unpacked/${d}" "${d}"
  fi
done

echo "BELGIUM_STATE_RESTORED=1" >> "${GITHUB_ENV:-/dev/null}"
echo "Restored cumulative state from artifact ${artifact_id}."
