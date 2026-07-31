#!/usr/bin/env bash
# Container entrypoint: seed sample_api git fixture into the data volume, then exec CMD.
set -euo pipefail

DATA_DIR="${PRODUCT_FACTORY_DATA_DIR:-/data}"
ROOT="${PRODUCT_FACTORY_ROOT:-/app}"
FIXTURE_SRC="${ROOT}/fixtures/sample_api"
REPO_DEST="${DATA_DIR}/repos/sample_api"

mkdir -p "${DATA_DIR}/data" "${DATA_DIR}/runs" "${DATA_DIR}/repos"

if [[ ! -d "${REPO_DEST}/.git" ]]; then
  echo "docker-entrypoint: seeding sample_api git fixture at ${REPO_DEST}"
  rm -rf "${REPO_DEST}"
  if [[ ! -d "${FIXTURE_SRC}" ]]; then
    echo "docker-entrypoint: missing fixture source ${FIXTURE_SRC}" >&2
    exit 1
  fi
  cp -a "${FIXTURE_SRC}" "${REPO_DEST}"
  if [[ ! -d "${REPO_DEST}/.git" ]]; then
    git -C "${REPO_DEST}" init -b main
    git -C "${REPO_DEST}" config user.email "fixture@example.com"
    git -C "${REPO_DEST}" config user.name "Fixture"
    git -C "${REPO_DEST}" add -A
    git -C "${REPO_DEST}" commit -m "fixture"
  fi
fi

# Keep the fixture ref deterministic even when reusing a volume seeded by an
# older harness revision whose git default branch was "master".
git -C "${REPO_DEST}" branch -M main

exec "$@"
