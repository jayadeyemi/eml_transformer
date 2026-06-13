#!/usr/bin/env bash
# cleanup.sh -- Cancel orphaned Batch jobs and purge SQS queue after test run.
# Called automatically by run_all.sh EXIT trap.
# All cleanup runs inside the container; no host AWS CLI dependency.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

DRY_RUN="${DRY_RUN:-0}"
log "=== Cleanup: cancelling Batch jobs and purging SQS ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    warn "Runtime config not found (${RUNTIME_CONFIG}); skipping cloud cleanup"
    exit 0
fi

DRY_RUN_FLAG=""
[[ "${DRY_RUN}" == "1" ]] && DRY_RUN_FLAG="--dry-run"

docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    cleanup-test-resources \
    --config configs/generated/aws-smoke.runtime.yaml \
    ${DRY_RUN_FLAG} \
    2>&1
log "Cleanup complete."
