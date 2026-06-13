#!/usr/bin/env bash
# phase3_infra.sh -- AWS infrastructure verification (all in-container via verify-infra)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase3"
mkdir -p "${PHASE_LOG}"
log "=== Phase 3: AWS Infrastructure Verification (in-container, sequential) ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    fail "Runtime config not found: ${RUNTIME_CONFIG}. Run phase0_preflight.sh first."
fi

# 3A: SSO token -- host check only (boto3 inside container uses the mounted creds)
check_sso_token > "${PHASE_LOG}/3a_sso.log" 2>&1
record_result "3A: SSO token valid" $?

# 3B: verify-infra runs boto3 checks inside the container
# Mounts the real runtime config so the CLI can pick up real ARNs
log "3B-3G: Running verify-infra inside container"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    verify-infra \
    --config configs/generated/aws-smoke.runtime.yaml \
    2>&1 | tee "${PHASE_LOG}/3b_verify_infra.log"
record_result "3B-G: verify-infra (S3/SQS/DynamoDB/Batch)" $?

summarize_results "Phase 3 AWS Infrastructure Verification"
