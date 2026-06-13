#!/usr/bin/env bash
# verify_aws.sh — AWS resource verification helpers
# Source this file: source "$(dirname "$0")/lib/verify_aws.sh"
set -euo pipefail

# Expects common.sh already sourced for AWS_PROFILE, log(), warn()

check_s3() {
    local bucket="$1"
    info "S3: checking bucket ${bucket}"
    aws s3 ls "s3://${bucket}" --profile "${AWS_PROFILE}" --output text &>/dev/null
    info "S3: bucket accessible"
}

check_sqs() {
    local queue_url="$1" attr="${2:-ApproximateNumberOfMessages}"
    info "SQS: checking queue ${queue_url}"
    aws sqs get-queue-attributes \
        --queue-url "${queue_url}" \
        --attribute-names "${attr}" \
        --profile "${AWS_PROFILE}" \
        --output text &>/dev/null
    info "SQS: queue accessible"
}

check_dynamodb() {
    local table="$1"
    info "DynamoDB: checking table ${table}"
    aws dynamodb describe-table \
        --table-name "${table}" \
        --profile "${AWS_PROFILE}" \
        --output text \
        --query "Table.TableStatus" &>/dev/null
    local status
    status="$(aws dynamodb describe-table \
        --table-name "${table}" \
        --profile "${AWS_PROFILE}" \
        --query "Table.TableStatus" \
        --output text)"
    info "DynamoDB: table ${table} status=${status}"
    [[ "${status}" == "ACTIVE" ]]
}

check_ecr_image() {
    local repo="$1" tag="${2:-smoke}"
    info "ECR: checking repository ${repo} for tag ${tag}"
    aws ecr describe-images \
        --repository-name "${repo}" \
        --image-ids "imageTag=${tag}" \
        --profile "${AWS_PROFILE}" \
        --output text &>/dev/null
    info "ECR: image ${repo}:${tag} found"
}

check_batch_queue() {
    local queue_name="$1"
    info "Batch: checking job queue ${queue_name}"
    local state
    state="$(aws batch describe-job-queues \
        --job-queues "${queue_name}" \
        --profile "${AWS_PROFILE}" \
        --query "jobQueues[0].state" \
        --output text)"
    info "Batch: queue ${queue_name} state=${state}"
    [[ "${state}" == "ENABLED" ]]
}

check_no_placeholder_arns() {
    local config="$1"
    info "Config: checking ${config} for placeholder values"
    local bad
    bad="$(grep -E 'subnet-replace-me|sg-replace-me|null|123456789012' "${config}" 2>/dev/null | grep -v '^#' || true)"
    if [[ -n "${bad}" ]]; then
        warn "Placeholder values found in ${config}:"
        echo "${bad}" >&2
        return 1
    fi
    info "Config: no placeholder values found"
}

poll_batch_job() {
    local job_id="$1" timeout_sec="${2:-300}" poll_interval="${3:-30}"
    info "Batch: polling job ${job_id} (timeout=${timeout_sec}s)"
    local elapsed=0
    while [[ "${elapsed}" -lt "${timeout_sec}" ]]; do
        local status
        status="$(aws batch describe-jobs \
            --jobs "${job_id}" \
            --profile "${AWS_PROFILE}" \
            --query "jobs[0].status" \
            --output text)"
        info "Batch: job ${job_id} status=${status} (${elapsed}s elapsed)"
        case "${status}" in
            SUCCEEDED) return 0 ;;
            FAILED)    warn "Batch: job ${job_id} FAILED"; return 1 ;;
            "None"|"")  warn "Batch: job ${job_id} not found"; return 1 ;;
        esac
        sleep "${poll_interval}"
        elapsed=$((elapsed + poll_interval))
    done
    warn "Batch: job ${job_id} timed out after ${timeout_sec}s (last status=${status})"
    return 1
}
