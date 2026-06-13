#!/usr/bin/env bash
# phase1_static.sh -- Static validation tests (all run INSIDE the container)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase1"
mkdir -p "${PHASE_LOG}"
log "=== Phase 1: Static Validation (in-container, parallel) ==="

docker_run_pytest "${PHASE_LOG}/1a_deployment_config.log" \
    tests/test_deployment_config.py -v --tb=short &
PID_1A=$!

docker_run_pytest "${PHASE_LOG}/1b_cdk_stack.log" \
    tests/test_cdk_stack.py -v --tb=short &
PID_1B=$!

docker_run_pytest "${PHASE_LOG}/1c_gdelt_aws.log" \
    tests/test_gdelt_aws.py -v --tb=short &
PID_1C=$!

# config-validate-all: no AWS creds needed, runs via CLI entrypoint
docker_run_cli "${PHASE_LOG}/1d_config_validate_all.log" \
    config-validate-all --directory configs/deployments &
PID_1D=$!

wait "${PID_1A}"; record_result "1A: deployment_config tests" $?
wait "${PID_1B}"; record_result "1B: cdk_stack tests" $?
wait "${PID_1C}"; record_result "1C: gdelt_aws tests" $?
wait "${PID_1D}"; record_result "1D: config-validate-all" $?

summarize_results "Phase 1 Static Validation"
