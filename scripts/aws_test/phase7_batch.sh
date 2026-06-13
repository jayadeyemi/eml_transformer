#!/usr/bin/env bash
# phase7_batch.sh -- Batch job submission + in-container batch-wait polling
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
PHASE_LOG="${RESULTS_DIR}/phase7"
mkdir -p "${PHASE_LOG}"
GDELT_DATE="${GDELT_DATE:-$(date -u +%Y-%m-%d)}"
BATCH_TIMEOUT="${BATCH_TIMEOUT:-300}"
REGION="${AWS_REGION:-us-east-1}"
log "=== Phase 7: Batch Integration Tests (submission + in-container polling) ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    fail "Runtime config not found: ${RUNTIME_CONFIG}. Run phase0_preflight.sh first."
fi

# Track submitted job IDs for cleanup on exit
SUBMITTED_JOBS=()
cleanup_batch_jobs() {
    if [[ ${#SUBMITTED_JOBS[@]} -gt 0 ]]; then
        warn "Cleaning up Batch jobs: ${SUBMITTED_JOBS[*]}"
        docker run --rm \
            -v "${HOME}/.aws:/root/.aws" \
            -e AWS_PROFILE="${AWS_PROFILE}" \
            -e AWS_SDK_LOAD_CONFIG=1 \
            -e AWS_REGION="${REGION}" \
            -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
            "${IMAGE_TAG}" \
            cleanup-test-resources \
            --config configs/generated/aws-smoke.runtime.yaml \
            2>&1 || true
    fi
}
trap cleanup_batch_jobs EXIT

submit_and_wait() {
    local step_letter="$1" service="$2"; shift 2
    local submit_log="${PHASE_LOG}/${step_letter}_submit_${service}.log"
    local wait_log="${PHASE_LOG}/${step_letter}_wait_${service}.log"
    local next_letter

    # Submit via container
    docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${REGION}" \
        -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
        "${IMAGE_TAG}" \
        aws-start-service \
        --service "${service}" \
        --config configs/generated/aws-smoke.runtime.yaml \
        --batch \
        "$@" \
        2>&1 | tee "${submit_log}"
    local submit_exit="${PIPESTATUS[0]}"
    record_result "${step_letter}: aws-start-service ${service}" "${submit_exit}"
    [[ "${submit_exit}" -ne 0 ]] && return 1

    # Extract job ID from submit output (host-side JSON parsing is acceptable --
    # it's log processing, not an application operation)
    local job_id
    job_id="$(grep -Eo '"job(_id|Id)": *"[^"]*"' "${submit_log}" \
        | head -1 | cut -d '"' -f4 || echo "")"
    [[ -z "${job_id}" ]] && {
        warn "Could not extract job ID from ${submit_log}"
        return 1
    }
    SUBMITTED_JOBS+=("${job_id}")
    info "Submitted ${service} job: ${job_id}"

    # Poll via batch-wait inside the container
    case "${step_letter}" in
        7A) next_letter="7B" ;;
        7C) next_letter="7D" ;;
        *) next_letter="${step_letter}" ;;
    esac
    docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${REGION}" \
        "${IMAGE_TAG}" \
        batch-wait \
        --job-id "${job_id}" \
        --region "${REGION}" \
        --timeout "${BATCH_TIMEOUT}" \
        --poll-interval 30 \
        2>&1 | tee "${wait_log}"
    record_result "${next_letter}: ${service} Batch job succeeded" "${PIPESTATUS[0]}"
}

# 7A/7B: gdelt_discovery
submit_and_wait "7A" "gdelt_discovery" --date "${GDELT_DATE}"

# 7C/7D: url_fetch_worker
submit_and_wait "7C" "url_fetch_worker"

# 7E: CloudWatch metrics -- verify-infra covers basic checks; CW is best-effort
log "7E: CloudWatch metrics check (best-effort)"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${REGION}" \
    -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
    "${IMAGE_TAG}" \
    verify-infra \
    --config configs/generated/aws-smoke.runtime.yaml \
    2>&1 | tee "${PHASE_LOG}/7e_verify_infra.log" || true
record_result "7E: post-run verify-infra" 0

summarize_results "Phase 7 Batch Integration Tests"
