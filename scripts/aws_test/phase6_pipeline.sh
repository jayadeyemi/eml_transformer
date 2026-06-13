#!/usr/bin/env bash
# phase6_pipeline.sh -- Standardize (parallel) then embed (serial, best-effort)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase6"
mkdir -p "${PHASE_LOG}"
log "=== Phase 6: Pipeline Continuation (standardize -> embed, all in-container) ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    fail "Runtime config not found: ${RUNTIME_CONFIG}. Run phase0_preflight.sh first."
fi

run_standardize() {
    local source="$1"
    docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${AWS_REGION:-us-east-1}" \
        -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
        "${IMAGE_TAG}" \
        standardize \
        --source "${source}" \
        --config configs/generated/aws-smoke.runtime.yaml \
        2>&1 | tee "${PHASE_LOG}/6a_standardize_${source}.log"
    return "${PIPESTATUS[0]}"
}

# 6A: Standardize generic source outputs in parallel. GDELT is validated by the
# dedicated acquisition and Batch phases; it is not a generic registry source.
log "6A: Standardizing generic sources (parallel containers)"
PIPELINE_SOURCES=(iem_afos weather_alerts miso_notifications)
if [[ -n "${NEWSAPI_KEY:-}" ]]; then
    PIPELINE_SOURCES+=(newsapi)
else
    warn "NEWSAPI_KEY not set -- skipping newsapi standardization"
fi

for source in "${PIPELINE_SOURCES[@]}"; do
    run_standardize "${source}" &
done

STANDARDIZE_CODE=0
for pid in $(jobs -p); do
    wait "${pid}" || STANDARDIZE_CODE=$?
done
record_result "6A: standardize all sources" "${STANDARDIZE_CODE}"

# 6B: Embed (serial, best-effort -- modeling extras are not installed in smoke)
log "6B: Embedding generic sources (serial, best-effort)"
: > "${PHASE_LOG}/6b_embed.log"
for source in "${PIPELINE_SOURCES[@]}"; do
    docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${AWS_REGION:-us-east-1}" \
        -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
        "${IMAGE_TAG}" \
        embed \
        --source "${source}" \
        --config configs/generated/aws-smoke.runtime.yaml \
        2>&1 | tee -a "${PHASE_LOG}/6b_embed.log" || true
done
record_result "6B: embed all sources (best-effort)" 0

summarize_results "Phase 6 Pipeline Continuation"
