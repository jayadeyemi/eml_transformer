#!/usr/bin/env bash
# run_all.sh -- Master orchestrator for the full phased AWS test suite.
#
# Usage:
#   AWS_PROFILE=episb bash scripts/aws_test/run_all.sh [--skip-phase0] [--skip-batch]
#
# Env vars:
#   AWS_PROFILE   AWS SSO profile (default: episb)
#   AWS_REGION    AWS region (default: us-east-1)
#   IMAGE_TAG     Docker image tag (default: eml-transformer:smoke)
#   BUILD_EXTRAS  pip extras for Docker build (default: aws,test)
#   GDELT_DATE    Date for GDELT tests (default: today UTC)
#   NEWSAPI_KEY   NewsAPI key (optional; skipped if absent)
#   NEWSAPI_SECRET_NAME Secrets Manager secret name containing the NewsAPI key
#   BATCH_TIMEOUT Seconds to poll Batch jobs (default: 300)
#   SKIP_PHASE0   Set to 1 to skip pre-flight (assumes CDK already deployed)
#   SKIP_BATCH    Set to 1 to skip Phase 7 Batch job submission
#   RESET_STACK   Set to 1 to run guarded destructive stack reset first
#   SKIP_E2E      Set to 1 to skip Phase 8 full workflow/schedule diagnostics
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

SKIP_PHASE0="${SKIP_PHASE0:-0}"
SKIP_BATCH="${SKIP_BATCH:-0}"
RESET_STACK="${RESET_STACK:-0}"
SKIP_E2E="${SKIP_E2E:-0}"

for arg in "$@"; do
    case "${arg}" in
        --skip-phase0) SKIP_PHASE0=1 ;;
        --skip-batch)  SKIP_BATCH=1 ;;
        --reset-stack) RESET_STACK=1 ;;
        --skip-e2e)    SKIP_E2E=1 ;;
        *) warn "Unknown argument: ${arg}" ;;
    esac
done

OVERALL_LOG="${RESULTS_DIR}/run_all_$(date -u +%Y%m%d_%H%M%S).log"
mkdir -p "${RESULTS_DIR}"
exec > >(tee "${OVERALL_LOG}") 2>&1

# ── Cleanup on any exit (cancel Batch jobs, purge SQS) ───────────────────────
cleanup_on_exit() {
    local exit_code=$?
    if [[ "${exit_code}" -ne 0 ]]; then
        warn "Exit code ${exit_code} -- running cleanup to avoid orphaned AWS costs"
    else
        log "Run complete -- running cleanup"
    fi
    bash "${SCRIPT_DIR}/cleanup.sh" || true
}
trap cleanup_on_exit EXIT

log "=================================================="
log "  eml_transformer AWS Test Suite"
log "  Profile: ${AWS_PROFILE}  Region: ${AWS_REGION:-us-east-1}"
log "  Image:   ${IMAGE_TAG}  Extras: ${BUILD_EXTRAS}"
log "=================================================="

declare -A PHASE_EXIT

run_phase() {
    local name="$1" script="$2"
    log ""
    log ">>> Starting ${name}"
    if bash "${SCRIPT_DIR}/${script}"; then
        PHASE_EXIT["${name}"]=0
        log "  PASS ${name}"
    else
        PHASE_EXIT["${name}"]=$?
        warn "  FAIL ${name} (exit ${PHASE_EXIT[${name}]})"
    fi
}

# Optional destructive reset before any deploy.
if [[ "${RESET_STACK}" -eq 1 ]]; then
    run_phase "Phase R: Reset Smoke Stack" "reset_smoke_stack.sh"
    if [[ "${PHASE_EXIT[Phase R: Reset Smoke Stack]}" -ne 0 ]]; then
        fail "Reset failed -- cannot proceed."
    fi
fi

# Phase 0: Pre-flight (blocking)
if [[ "${SKIP_PHASE0}" -eq 0 ]]; then
    run_phase "Phase 0: Pre-flight" "phase0_preflight.sh"
    if [[ "${PHASE_EXIT[Phase 0: Pre-flight]}" -ne 0 ]]; then
        fail "Phase 0 failed -- cannot proceed."
    fi
else
    log "Skipping Phase 0 (SKIP_PHASE0=1)"
    PHASE_EXIT["Phase 0: Pre-flight"]=0
fi

# Phases 1 + 2: Static + container smoke (parallel, no AWS deploy needed)
log ""
log ">>> Starting Phase 1 (static) and Phase 2 (container) in parallel"
bash "${SCRIPT_DIR}/phase1_static.sh" &
PID_P1=$!
bash "${SCRIPT_DIR}/phase2_container.sh" &
PID_P2=$!

wait "${PID_P1}" && PHASE_EXIT["Phase 1: Static"]=0  || PHASE_EXIT["Phase 1: Static"]=$?
wait "${PID_P2}" && PHASE_EXIT["Phase 2: Container"]=0 || PHASE_EXIT["Phase 2: Container"]=$?

# Phase 3: AWS infra verification (sequential, blocks phases 4-7)
run_phase "Phase 3: AWS Infra" "phase3_infra.sh"
if [[ "${PHASE_EXIT[Phase 3: AWS Infra]}" -ne 0 ]]; then
    fail "Phase 3 failed -- AWS infrastructure not ready."
fi

# Phases 4, 5, 7: Parallel after Phase 3
log ""
log ">>> Starting Phase 4 (ingestion), Phase 5 (GDELT), Phase 7 (Batch) in parallel"
bash "${SCRIPT_DIR}/phase4_ingest.sh" &
PID_P4=$!
bash "${SCRIPT_DIR}/phase5_gdelt.sh" &
PID_P5=$!

if [[ "${SKIP_BATCH}" -eq 0 ]]; then
    bash "${SCRIPT_DIR}/phase7_batch.sh" &
    PID_P7=$!
else
    log "Skipping Phase 7 (SKIP_BATCH=1)"
    PID_P7=""
fi

wait "${PID_P4}" && PHASE_EXIT["Phase 4: Ingestion"]=0 || PHASE_EXIT["Phase 4: Ingestion"]=$?
wait "${PID_P5}" && PHASE_EXIT["Phase 5: GDELT"]=0     || PHASE_EXIT["Phase 5: GDELT"]=$?
[[ -n "${PID_P7}" ]] && {
    wait "${PID_P7}" && PHASE_EXIT["Phase 7: Batch"]=0 || PHASE_EXIT["Phase 7: Batch"]=$?
} || PHASE_EXIT["Phase 7: Batch"]=0

# Phase 6: Pipeline (needs Phase 4+5 data)
run_phase "Phase 6: Pipeline" "phase6_pipeline.sh"

# Phase 8: full Step Functions, schedule, SNS, and diagnostics path
if [[ "${SKIP_E2E}" -eq 0 ]]; then
    run_phase "Phase 8: Full AWS E2E" "phase8_e2e.sh"
else
    log "Skipping Phase 8 (SKIP_E2E=1)"
    PHASE_EXIT["Phase 8: Full AWS E2E"]=0
fi

# Final summary
echo ""
echo "=================================================="
echo "  FINAL RESULTS"
echo "=================================================="
OVERALL_FAIL=0
for phase in \
    "Phase R: Reset Smoke Stack" \
    "Phase 0: Pre-flight" \
    "Phase 1: Static" \
    "Phase 2: Container" \
    "Phase 3: AWS Infra" \
    "Phase 4: Ingestion" \
    "Phase 5: GDELT" \
    "Phase 6: Pipeline" \
    "Phase 7: Batch" \
    "Phase 8: Full AWS E2E"
do
    code="${PHASE_EXIT[${phase}]:-0}"
    if [[ "${code}" -eq 0 ]]; then
        echo "  PASS  ${phase}"
    else
        echo "  FAIL  ${phase}  (exit ${code})"
        OVERALL_FAIL=1
    fi
done
echo "=================================================="
echo "  Full log: ${OVERALL_LOG}"
echo "  Results:  ${RESULTS_DIR}/"
echo "=================================================="

exit "${OVERALL_FAIL}"
