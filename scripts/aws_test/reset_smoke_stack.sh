#!/usr/bin/env bash
# reset_smoke_stack.sh -- Destructively delete the smoke stack and retained artifacts.
#
# Required guard:
#   CONFIRM_STACK_DELETE=eml-transformer-smoke bash scripts/aws_test/reset_smoke_stack.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

STACK_NAME="${STACK_NAME:-eml-transformer-smoke}"
REGION="${AWS_REGION:-us-east-1}"
PHASE_LOG="${RESULTS_DIR}/reset"
mkdir -p "${PHASE_LOG}"

if [[ "${CONFIRM_STACK_DELETE:-}" != "${STACK_NAME}" ]]; then
    fail "Refusing destructive reset. Set CONFIRM_STACK_DELETE=${STACK_NAME}."
fi

ACCOUNT_ID="$(check_sso_token)"
BUCKET="${STACK_NAME}-data-${ACCOUNT_ID}"
ECR_REPO="${STACK_NAME}-collection"
TABLES=(
    "${STACK_NAME}-url-state"
    "${STACK_NAME}-run-state"
    "${STACK_NAME}-domain-throttle"
)
JOB_DEFINITIONS=(
    ingest
    standardize
    embed
    backfill
    run-all
    gdelt-discovery
    url-fetch-worker
    s3-restore-operator
)

stack_exists() {
    aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        >/dev/null 2>&1
}

delete_stack() {
    if ! stack_exists; then
        warn "CloudFormation stack ${STACK_NAME} does not exist; continuing with retained artifact cleanup"
        return
    fi

    log "Saving current stack resources before delete"
    aws cloudformation list-stack-resources \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --output json > "${PHASE_LOG}/stack_resources_before_delete.json"

    log "Deleting CloudFormation stack: ${STACK_NAME}"
    aws cloudformation delete-stack \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}"
    aws cloudformation wait stack-delete-complete \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}"
}

delete_versioned_bucket() {
    local bucket="$1"

    if ! aws s3api head-bucket --bucket "${bucket}" --region "${REGION}" --profile "${AWS_PROFILE}" >/dev/null 2>&1; then
        warn "Bucket not found: ${bucket}"
        return
    fi

    log "Deleting all versions and delete markers from s3://${bucket}"
    while true; do
        local versions_json delete_json object_count
        versions_json="${PHASE_LOG}/s3_versions.json"
        delete_json="${PHASE_LOG}/s3_delete_batch.json"

        aws s3api list-object-versions \
            --bucket "${bucket}" \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --max-items 1000 \
            --output json > "${versions_json}"

        object_count="$("${PYTHON_BIN}" - "${versions_json}" "${delete_json}" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
with open(source, "r", encoding="utf-8") as f:
    payload = json.load(f)

objects = []
for section in ("Versions", "DeleteMarkers"):
    for item in payload.get(section, []) or []:
        objects.append({"Key": item["Key"], "VersionId": item["VersionId"]})

with open(target, "w", encoding="utf-8") as f:
    json.dump({"Objects": objects, "Quiet": True}, f)

print(len(objects))
PY
)"

        if [[ "${object_count}" == "0" ]]; then
            break
        fi

        aws s3api delete-objects \
            --bucket "${bucket}" \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --delete "file://${delete_json}" \
            >/dev/null
    done

    log "Deleting bucket: ${bucket}"
    aws s3api delete-bucket \
        --bucket "${bucket}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" || true
}

delete_tables() {
    for table in "${TABLES[@]}"; do
        if aws dynamodb describe-table --table-name "${table}" --region "${REGION}" --profile "${AWS_PROFILE}" >/dev/null 2>&1; then
            log "Deleting DynamoDB table: ${table}"
            aws dynamodb delete-table --table-name "${table}" --region "${REGION}" --profile "${AWS_PROFILE}" >/dev/null
            aws dynamodb wait table-not-exists --table-name "${table}" --region "${REGION}" --profile "${AWS_PROFILE}"
        else
            warn "DynamoDB table not found: ${table}"
        fi
    done
}

delete_ecr() {
    if aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" --profile "${AWS_PROFILE}" >/dev/null 2>&1; then
        log "Deleting ECR repository: ${ECR_REPO}"
        aws ecr delete-repository \
            --repository-name "${ECR_REPO}" \
            --force \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" >/dev/null
    else
        warn "ECR repository not found: ${ECR_REPO}"
    fi
}

delete_job_definition_revisions() {
    for service in "${JOB_DEFINITIONS[@]}"; do
        local name="${STACK_NAME}-${service}"
        local arns
        arns="$(aws batch describe-job-definitions \
            --job-definition-name "${name}" \
            --status ACTIVE \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --query 'jobDefinitions[].jobDefinitionArn' \
            --output text 2>/dev/null || true)"

        for arn in ${arns}; do
            log "Deregistering Batch job definition: ${arn}"
            aws batch deregister-job-definition \
                --job-definition "${arn}" \
                --region "${REGION}" \
                --profile "${AWS_PROFILE}" >/dev/null || true
        done
    done
}

delete_stale_schedules() {
    local schedules
    schedules="$(aws scheduler list-schedules \
        --name-prefix "${STACK_NAME}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --query 'Schedules[].Name' \
        --output text 2>/dev/null || true)"

    for schedule_name in ${schedules}; do
        log "Deleting stale schedule: ${schedule_name}"
        aws scheduler delete-schedule \
            --name "${schedule_name}" \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" >/dev/null || true
    done
}

delete_log_group() {
    local log_group="/aws/batch/${STACK_NAME}/collection"

    if aws logs describe-log-groups --log-group-name-prefix "${log_group}" --region "${REGION}" --profile "${AWS_PROFILE}" --query 'logGroups[?logGroupName==`'"${log_group}"'`]' --output text | grep -q "${log_group}"; then
        log "Deleting CloudWatch log group: ${log_group}"
        aws logs delete-log-group \
            --log-group-name "${log_group}" \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" >/dev/null || true
    else
        warn "Log group not found: ${log_group}"
    fi
}

delete_stack
delete_stale_schedules
delete_versioned_bucket "${BUCKET}"
delete_tables
delete_ecr
delete_job_definition_revisions
delete_log_group

log "Smoke stack reset complete."
