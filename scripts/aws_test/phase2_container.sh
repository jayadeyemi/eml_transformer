#!/usr/bin/env bash
# phase2_container.sh -- Docker container smoke tests (no AWS creds, all in-container)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase2"
mkdir -p "${PHASE_LOG}"
log "=== Phase 2: Container Smoke Tests (parallel, no AWS) ==="

docker_run_cli "${PHASE_LOG}/2a_help.log"             --help &
PID_2A=$!
docker_run_cli "${PHASE_LOG}/2b_sources.log"           sources &
PID_2B=$!
docker_run_cli "${PHASE_LOG}/2c_deploy_matrix.log"     deployment-matrix --help &
PID_2C=$!
docker_run_cli "${PHASE_LOG}/2d_config_validate.log"   config-validate --help &
PID_2D=$!

wait "${PID_2A}"; record_result "2A: --help entry point" $?
wait "${PID_2B}"; record_result "2B: sources registry" $?
wait "${PID_2C}"; record_result "2C: deployment-matrix --help" $?
wait "${PID_2D}"; record_result "2D: config-validate --help" $?

# 2E: validate embedded smoke config inside container (sequential)
log "2E: Validating embedded smoke deployment config inside container"
docker_run_cli "${PHASE_LOG}/2e_smoke_validate.log" \
    config-validate --deployment configs/deployments/aws-smoke.yaml
record_result "2E: smoke config validates inside container" $?

summarize_results "Phase 2 Container Smoke Tests"
