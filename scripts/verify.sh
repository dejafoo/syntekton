#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Keep local verify aligned with the SD5 CI ladder: frozen Python deps and
# lockfile-faithful npm installs before package checks.
if [[ -f uv.lock ]]; then
  uv sync --frozen --extra dev >/dev/null
fi
npm --prefix dashboard ci
npm --prefix dashboard test -- --run
npm --prefix dashboard run check
npm --prefix dashboard run build
npm --prefix integrations/opencode-plugin ci
npm --prefix integrations/opencode-plugin test -- --run
npm --prefix integrations/opencode-plugin run check
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest -q -m "not integration"
uv run product-factory --help
bash scripts/package_smoke.sh

# Connector policy, injection, and grant harness (P4.B–D). Covered by the run
# above; kept explicit so a regression here is attributed to connectors rather
# than buried in the full suite. Every case is offline — connectors are disabled
# by default and the providers run in mock mode.
# PM5: include hermetic fake_git_ci / fake_ops_read / fake_deploy coverage.
uv run pytest -q tests/connectors tests/contract/test_connector_audit.py

# PM5.E / PMX hermetic spotlight (also covered by the default gate above).
uv run pytest -q \
  tests/security/test_remote_ingress.py \
  tests/security/test_remote_uploads.py \
  tests/unit/test_backup_restore.py \
  tests/unit/test_pmx_corpus_gates.py \
  tests/connectors/test_connector_disable_evidence.py \
  tests/unit/test_python_executable_resolution.py \
  tests/unit/test_pm5_foundation.py \
  tests/unit/test_pm5a_release_validators.py \
  tests/unit/test_pm5b_deployment_receipts.py \
  tests/unit/test_pm5c_composition_gates.py \
  tests/security/test_deployment_authority.py \
  tests/security/test_ops_injection.py \
  tests/security/test_domain_pack_authority.py

# Live connector smokes are opt-in: they need credentials or network egress, so
# they stay out of the default gate and fail loudly when explicitly requested.
if [[ "${TAVILY_INTEGRATION:-}" == "1" ]]; then
  uv run pytest -q tests/connectors/test_tavily_connector.py -k live_search
fi
if [[ "${MCP_FILESYSTEM_INTEGRATION:-}" == "1" ]]; then
  uv run pytest -q tests/connectors/test_filesystem_mcp.py -k real_filesystem
fi

# Optional OpenCode plugin smoke (P3.G.D): skips when `opencode` is absent
# unless OPENCODE_INTEGRATION=1 is set (then missing binary fails).
bash scripts/opencode_plugin_smoke.sh

# Optional OpenCode remote smoke (PM3): loads the plugin against Docker compose
# remote; soft-skips when opencode/docker are absent unless OPENCODE_INTEGRATION=1.
bash scripts/opencode_remote_smoke.sh

# Optional Docker remote HTTP integration (PM3.0 / PM5.E): soft-skips unless
# DOCKER_INTEGRATION=1 (then missing/unhealthy Docker/compose fails).
uv run pytest -q tests/integration/test_remote_docker.py

# Opt-in staging deploy smoke (PM5.B): soft-skips unless DEPLOY_INTEGRATION=1.
uv run pytest -q tests/integration/test_deploy_staging_live.py

# Opt-in backup/restore drill (PM5.E): soft-skips unless BACKUP_INTEGRATION=1.
uv run pytest -q tests/integration/test_backup_restore.py

echo "verify OK"
