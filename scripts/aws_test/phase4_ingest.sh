#!/usr/bin/env bash
# phase4_ingest.sh -- Source ingestion (4 parallel containers)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase4"
mkdir -p "${PHASE_LOG}"
log "=== Phase 4: Source Ingestion Tests (4 parallel containers) ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    fail "Runtime config not found: ${RUNTIME_CONFIG}. Run phase0_preflight.sh first."
fi

run_ingest() {
    local source="$1"
    local log_file="${PHASE_LOG}/4_ingest_${source}.log"
    info "Starting ingest --source ${source}"
    # Mount real runtime config; env vars from CDK override local values
    docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${AWS_REGION:-us-east-1}" \
        -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
        "${IMAGE_TAG}" \
        ingest --source "${source}" \
        --config configs/generated/aws-smoke.runtime.yaml \
        2>&1 | tee "${log_file}"
    return "${PIPESTATUS[0]}"
}

run_ingest "iem_afos"          & PID_4A=$!
run_ingest "weather_alerts"    & PID_4B=$!
run_ingest "miso_notifications" & PID_4C=$!

if [[ -n "${NEWSAPI_KEY:-}" ]]; then
    (docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${AWS_REGION:-us-east-1}" \
        -e "NEWSAPI_KEY=${NEWSAPI_KEY}" \
        -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
        "${IMAGE_TAG}" \
        ingest --source newsapi \
        --config configs/generated/aws-smoke.runtime.yaml \
        2>&1 | tee "${PHASE_LOG}/4_ingest_newsapi.log") &
    PID_4D=$!
else
    warn "NEWSAPI_KEY not set -- skipping newsapi ingestion"
    PID_4D=""
fi

wait "${PID_4A}"; record_result "4A: ingest iem_afos" $?
wait "${PID_4B}"; record_result "4B: ingest weather_alerts" $?
wait "${PID_4C}"; record_result "4C: ingest miso_notifications" $?
[[ -n "${PID_4D}" ]] && { wait "${PID_4D}"; record_result "4D: ingest newsapi" $?; }

summarize_results "Phase 4 Source Ingestion"
