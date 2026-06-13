#!/usr/bin/env bash
# phase5_gdelt.sh -- GDELT acquisition sequential flow (all in-container)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase5"
mkdir -p "${PHASE_LOG}"
log "=== Phase 5: GDELT Acquisition Flow (in-container, sequential) ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    fail "Runtime config not found: ${RUNTIME_CONFIG}. Run phase0_preflight.sh first."
fi

GDELT_DATE="${GDELT_DATE:-$(date -u +%Y-%m-%d)}"
log "GDELT date: ${GDELT_DATE}"

# 5A: dry-run (--no-enqueue) -- validates discovery, no SQS writes
log "5A: GDELT discovery dry-run (--no-enqueue)"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    gdelt-discover \
    --date "${GDELT_DATE}" \
    --config configs/generated/aws-smoke.runtime.yaml \
    --max-files 1 \
    --no-enqueue \
    2>&1 | tee "${PHASE_LOG}/5a_gdelt_dry.log"
record_result "5A: GDELT discovery dry-run" $?

# 5B: real discovery + SQS enqueue
log "5B: GDELT discovery with enqueue"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    gdelt-discover \
    --date "${GDELT_DATE}" \
    --config configs/generated/aws-smoke.runtime.yaml \
    --max-files 1 \
    2>&1 | tee "${PHASE_LOG}/5b_gdelt_enqueue.log"
record_result "5B: GDELT discovery with enqueue" $?

# 5C: verify SQS via verify-infra (in-container)
log "5C: Verifying SQS via verify-infra"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    verify-infra \
    --config configs/generated/aws-smoke.runtime.yaml \
    2>&1 | tee "${PHASE_LOG}/5c_sqs_verify.log"
record_result "5C: SQS reachable after enqueue" $?

# 5D: article fetch worker (smoke: max 5 messages)
log "5D: article-fetch-worker (max-messages=5)"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    article-fetch-worker \
    --config configs/generated/aws-smoke.runtime.yaml \
    --max-messages 5 \
    --output-format jsonl.gz \
    --output-batch-size 5 \
    --request-delay-seconds 1 \
    2>&1 | tee "${PHASE_LOG}/5d_fetch_worker.log"
record_result "5D: article-fetch-worker" $?

summarize_results "Phase 5 GDELT Acquisition Flow"
