#!/usr/bin/env bash
# phase8_e2e.sh -- Full workflow, schedule, SNS, and diagnostics validation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

PHASE_LOG="${RESULTS_DIR}/phase8"
mkdir -p "${PHASE_LOG}"

REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-eml-transformer-smoke}"
BACKFILL_START_DATE="${BACKFILL_START_DATE:-2026-06-06}"
BACKFILL_END_DATE="${BACKFILL_END_DATE:-2026-06-12}"
BACKFILL_WINDOW_DAYS="${BACKFILL_WINDOW_DAYS:-7}"
SNS_CONFIRM_TIMEOUT_SECONDS="${SNS_CONFIRM_TIMEOUT_SECONDS:-900}"
SCHEDULE_MONITOR_TIMEOUT_SECONDS="${SCHEDULE_MONITOR_TIMEOUT_SECONDS:-1800}"
WORKFLOW_TIMEOUT_SECONDS="${WORKFLOW_TIMEOUT_SECONDS:-7200}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-30}"

log "=== Phase 8: Full AWS workflow, schedule, SNS, and diagnostics ==="

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    fail "Runtime config not found: ${RUNTIME_CONFIG}. Run phase0_preflight.sh first."
fi

DATA_BUCKET="$(get_data_bucket)"
URL_QUEUE_URL="$(get_queue_url)"
DLQ_URL="$(get_runtime_value "queues.article_url_dlq_url")"
URL_TABLE="$(get_runtime_value "state.url_table")"
RUN_TABLE="$(get_runtime_value "state.run_table")"
DOMAIN_TABLE="$(get_runtime_value "state.domain_throttle_table")"
GDELT_SFN_ARN="$(get_runtime_value "orchestration.state_machine_arn")"
SOURCE_SFN_ARN="$(get_runtime_value "orchestration.source_workflow_arn")"
BACKFILL_SFN_ARN="$(get_runtime_value "orchestration.backfill_workflow_arn")"
JOB_QUEUE="$(get_runtime_value "orchestration.batch_job_queue")"
SNS_TOPIC_ARN="$(get_runtime_value "notifications.sns_topic_arn")"

require_value() {
    local name="$1" value="$2"
    [[ -n "${value}" ]] || fail "Missing required runtime value: ${name}"
}

require_value "storage.bucket" "${DATA_BUCKET}"
require_value "queues.url_fetch_queue_url" "${URL_QUEUE_URL}"
require_value "orchestration.state_machine_arn" "${GDELT_SFN_ARN}"
require_value "orchestration.source_workflow_arn" "${SOURCE_SFN_ARN}"
require_value "orchestration.backfill_workflow_arn" "${BACKFILL_SFN_ARN}"
require_value "notifications.sns_topic_arn" "${SNS_TOPIC_ARN}"

wait_for_sns_confirmation() {
    local elapsed=0
    log "Waiting for SNS email subscription confirmation on ${SNS_TOPIC_ARN}"

    while [[ "${elapsed}" -le "${SNS_CONFIRM_TIMEOUT_SECONDS}" ]]; do
        aws sns list-subscriptions-by-topic \
            --topic-arn "${SNS_TOPIC_ARN}" \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --output json > "${PHASE_LOG}/sns_subscriptions.json"

        local pending confirmed
        pending="$("${PYTHON_BIN}" - "${PHASE_LOG}/sns_subscriptions.json" <<'PY'
import json, sys
subs = json.load(open(sys.argv[1], encoding="utf-8")).get("Subscriptions", [])
print(sum(1 for s in subs if s.get("SubscriptionArn") == "PendingConfirmation"))
PY
)"
        confirmed="$("${PYTHON_BIN}" - "${PHASE_LOG}/sns_subscriptions.json" <<'PY'
import json, sys
subs = json.load(open(sys.argv[1], encoding="utf-8")).get("Subscriptions", [])
print(sum(1 for s in subs if s.get("SubscriptionArn") != "PendingConfirmation"))
PY
)"

        if [[ "${confirmed}" -gt 0 && "${pending}" == "0" ]]; then
            info "SNS subscriptions confirmed."
            return 0
        fi

        warn "SNS confirmation pending (${elapsed}s elapsed). Confirm the email subscription for boadeyem@iu.edu."
        sleep "${POLL_INTERVAL_SECONDS}"
        elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
    done

    fail "SNS subscription was not confirmed within ${SNS_CONFIRM_TIMEOUT_SECONDS}s."
}

wait_execution() {
    local execution_arn="$1" label="$2" timeout="$3"
    local elapsed=0 status="UNKNOWN"

    while [[ "${elapsed}" -le "${timeout}" ]]; do
        aws stepfunctions describe-execution \
            --execution-arn "${execution_arn}" \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --output json > "${PHASE_LOG}/${label}_describe.json"

        status="$("${PYTHON_BIN}" - "${PHASE_LOG}/${label}_describe.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", "UNKNOWN"))
PY
)"
        info "${label}: ${status} (${elapsed}s)"

        case "${status}" in
            SUCCEEDED)
                aws stepfunctions get-execution-history \
                    --execution-arn "${execution_arn}" \
                    --region "${REGION}" \
                    --profile "${AWS_PROFILE}" \
                    --output json > "${PHASE_LOG}/${label}_history.json"
                return 0
                ;;
            FAILED|TIMED_OUT|ABORTED)
                aws stepfunctions get-execution-history \
                    --execution-arn "${execution_arn}" \
                    --region "${REGION}" \
                    --profile "${AWS_PROFILE}" \
                    --output json > "${PHASE_LOG}/${label}_history.json" || true
                return 1
                ;;
        esac

        sleep "${POLL_INTERVAL_SECONDS}"
        elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
    done

    warn "${label}: timed out waiting for terminal execution state"
    return 1
}

start_backfill() {
    local input_file="${PHASE_LOG}/backfill_input.json"
    local name="e2e-backfill-$(date -u +%Y%m%d%H%M%S)"

    "${PYTHON_BIN}" - "${input_file}" "${BACKFILL_START_DATE}" "${BACKFILL_END_DATE}" "${BACKFILL_WINDOW_DAYS}" <<'PY'
import json, sys
target, start, end, window = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
payload = {
    "service": "backfill",
    "parameters": {
        "source": "all",
        "start_date": start,
        "end_date": end,
        "window_days": window,
        "init_checkpoint": True,
    },
}
with open(target, "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY

    log "Starting backfill workflow for ${BACKFILL_START_DATE} through ${BACKFILL_END_DATE}" >&2
    aws stepfunctions start-execution \
        --state-machine-arn "${BACKFILL_SFN_ARN}" \
        --name "${name}" \
        --input "file://${input_file}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --output json > "${PHASE_LOG}/backfill_start.json"

    "${PYTHON_BIN}" - "${PHASE_LOG}/backfill_start.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["executionArn"])
PY
}

execution_counts_after() {
    local machine_arn="$1" since_iso="$2" output_file="$3"
    aws stepfunctions list-executions \
        --state-machine-arn "${machine_arn}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --max-items 100 \
        --output json > "${output_file}"

    "${PYTHON_BIN}" - "${output_file}" "${since_iso}" <<'PY'
import datetime as dt
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
since = dt.datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
counts = {"total": 0, "succeeded": 0, "failed": 0, "active": 0}
for execution in payload.get("executions", []):
    start = execution.get("startDate")
    if isinstance(start, str):
        start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    else:
        start_dt = start
    if start_dt >= since:
        counts["total"] += 1
        status = execution.get("status")
        if status == "SUCCEEDED":
            counts["succeeded"] += 1
        elif status in {"FAILED", "TIMED_OUT", "ABORTED"}:
            counts["failed"] += 1
        else:
            counts["active"] += 1
print(counts["total"], counts["succeeded"], counts["failed"], counts["active"])
PY
}

wait_for_scheduled_cycles() {
    local since_iso="$1"
    local elapsed=0
    local gdelt_total=0 gdelt_succeeded=0 gdelt_failed=0 gdelt_active=0
    local source_total=0 source_succeeded=0 source_failed=0 source_active=0

    log "Monitoring scheduled Step Functions executions after ${since_iso}"
    while [[ "${elapsed}" -le "${SCHEDULE_MONITOR_TIMEOUT_SECONDS}" ]]; do
        read -r gdelt_total gdelt_succeeded gdelt_failed gdelt_active < <(
            execution_counts_after "${GDELT_SFN_ARN}" "${since_iso}" "${PHASE_LOG}/gdelt_scheduled_executions.json"
        )
        read -r source_total source_succeeded source_failed source_active < <(
            execution_counts_after "${SOURCE_SFN_ARN}" "${since_iso}" "${PHASE_LOG}/source_scheduled_executions.json"
        )
        info "Scheduled executions after cutoff: gdelt=${gdelt_succeeded}/${gdelt_total} succeeded, ${gdelt_failed} failed, ${gdelt_active} active; source=${source_succeeded}/${source_total} succeeded, ${source_failed} failed, ${source_active} active (${elapsed}s)"

        if [[ "${gdelt_failed}" -gt 0 || "${source_failed}" -gt 0 ]]; then
            warn "A scheduled execution failed after the cutoff."
            return 1
        fi

        if [[ "${gdelt_succeeded}" -ge 2 && "${source_succeeded}" -ge 2 ]]; then
            return 0
        fi

        sleep "${POLL_INTERVAL_SECONDS}"
        elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
    done

    warn "Did not observe two successful scheduled GDELT and source workflow executions within ${SCHEDULE_MONITOR_TIMEOUT_SECONDS}s."
    return 1
}

disable_schedule() {
    local schedule_name="$1"
    local schedule_file="${PHASE_LOG}/${schedule_name}.json"
    local update_file="${PHASE_LOG}/${schedule_name}_disable.json"

    if ! aws scheduler get-schedule \
        --name "${schedule_name}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --output json > "${schedule_file}" 2>/dev/null; then
        warn "Schedule not found for disabling: ${schedule_name}"
        return
    fi

    "${PYTHON_BIN}" - "${schedule_file}" "${update_file}" <<'PY'
import json, sys
source, target = sys.argv[1], sys.argv[2]
payload = json.load(open(source, encoding="utf-8"))
for key in ("Arn", "CreationDate", "LastModificationDate", "ResponseMetadata"):
    payload.pop(key, None)
payload["State"] = "DISABLED"
with open(target, "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY

    aws scheduler update-schedule \
        --cli-input-json "file://${update_file}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" >/dev/null
    info "Disabled schedule: ${schedule_name}"
}

collect_diagnostics() {
    log "Collecting AWS diagnostics"

    aws s3api list-objects-v2 --bucket "${DATA_BUCKET}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/s3_objects.json" || true
    aws s3api list-object-versions --bucket "${DATA_BUCKET}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/s3_versions.json" || true

    for table in "${URL_TABLE}" "${RUN_TABLE}" "${DOMAIN_TABLE}"; do
        [[ -n "${table}" ]] || continue
        aws dynamodb describe-table --table-name "${table}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
            > "${PHASE_LOG}/dynamodb_${table}_describe.json" || true
        aws dynamodb scan --table-name "${table}" --limit 25 --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
            > "${PHASE_LOG}/dynamodb_${table}_sample.json" || true
    done

    aws sqs get-queue-attributes --queue-url "${URL_QUEUE_URL}" --attribute-names All --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/sqs_url_fetch_attrs.json" || true
    if [[ -n "${DLQ_URL}" ]]; then
        aws sqs get-queue-attributes --queue-url "${DLQ_URL}" --attribute-names All --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
            > "${PHASE_LOG}/sqs_url_fetch_dlq_attrs.json" || true
    fi

    aws scheduler list-schedules --name-prefix "${STACK_NAME}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/scheduler_schedules.json" || true
    aws sns list-subscriptions-by-topic --topic-arn "${SNS_TOPIC_ARN}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/sns_subscriptions_final.json" || true
    aws sns get-topic-attributes --topic-arn "${SNS_TOPIC_ARN}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/sns_topic_attrs.json" || true
    local metric_start metric_end
    metric_start="$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
    metric_end="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for metric in NumberOfMessagesPublished NumberOfNotificationsDelivered NumberOfNotificationsFailed; do
        aws cloudwatch get-metric-statistics \
            --namespace AWS/SNS \
            --metric-name "${metric}" \
            --dimensions "Name=TopicName,Value=${STACK_NAME}-notifications" \
            --start-time "${metric_start}" \
            --end-time "${metric_end}" \
            --period 60 \
            --statistics Sum \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --output json > "${PHASE_LOG}/sns_metric_${metric}.json" || true
    done
    aws cloudwatch describe-alarms --alarm-name-prefix "${STACK_NAME}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
        > "${PHASE_LOG}/cloudwatch_alarms.json" || true

    for machine in gdelt source backfill; do
        local arn
        case "${machine}" in
            gdelt) arn="${GDELT_SFN_ARN}" ;;
            source) arn="${SOURCE_SFN_ARN}" ;;
            backfill) arn="${BACKFILL_SFN_ARN}" ;;
        esac
        aws stepfunctions list-executions --state-machine-arn "${arn}" --max-items 25 --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
            > "${PHASE_LOG}/stepfunctions_${machine}_executions.json" || true
    done

    if [[ -n "${JOB_QUEUE}" ]]; then
        for status in SUBMITTED PENDING RUNNABLE STARTING RUNNING SUCCEEDED FAILED; do
            aws batch list-jobs --job-queue "${JOB_QUEUE}" --job-status "${status}" --region "${REGION}" --profile "${AWS_PROFILE}" --output json \
                > "${PHASE_LOG}/batch_jobs_${status}.json" || true
        done
    fi

    aws logs describe-log-streams \
        --log-group-name "/aws/batch/${STACK_NAME}/collection" \
        --order-by LastEventTime \
        --descending \
        --max-items 25 \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --output json > "${PHASE_LOG}/cloudwatch_log_streams.json" || true
    "${PYTHON_BIN}" - "${PHASE_LOG}/cloudwatch_log_streams.json" "${PHASE_LOG}/cloudwatch_log_stream_names.txt" <<'PY' || true
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
streams = [s["logStreamName"] for s in payload.get("logStreams", [])[:10]]
with open(sys.argv[2], "w", encoding="utf-8") as f:
    for stream in streams:
        f.write(stream + "\n")
PY
    local stream index=0
    while IFS= read -r stream; do
        [[ -n "${stream}" ]] || continue
        index=$((index + 1))
        aws logs get-log-events \
            --log-group-name "/aws/batch/${STACK_NAME}/collection" \
            --log-stream-name "${stream}" \
            --limit 50 \
            --region "${REGION}" \
            --profile "${AWS_PROFILE}" \
            --output json > "${PHASE_LOG}/cloudwatch_log_tail_${index}.json" || true
    done < "${PHASE_LOG}/cloudwatch_log_stream_names.txt"
}

SCHEDULES_DISABLED=0
disable_schedules_on_exit() {
    if [[ "${SCHEDULES_DISABLED}" -eq 0 ]]; then
        disable_schedule "${STACK_NAME}-gdelt-acquisition" || true
        disable_schedule "${STACK_NAME}-source-workflow-all" || true
    fi
}
trap disable_schedules_on_exit EXIT

SCHEDULE_CUTOFF="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

wait_for_sns_confirmation
BACKFILL_EXECUTION_ARN="$(start_backfill)"
if wait_execution "${BACKFILL_EXECUTION_ARN}" "backfill_execution" "${WORKFLOW_TIMEOUT_SECONDS}"; then
    record_result "8A: backfill workflow completed" 0
else
    record_result "8A: backfill workflow completed" 1
fi

if wait_for_scheduled_cycles "${SCHEDULE_CUTOFF}"; then
    record_result "8B: observed two successful scheduled GDELT/source cycles" 0
else
    record_result "8B: observed two successful scheduled GDELT/source cycles" 1
fi

collect_diagnostics
record_result "8C: diagnostics collected" 0

disable_schedule "${STACK_NAME}-gdelt-acquisition"
disable_schedule "${STACK_NAME}-source-workflow-all"
SCHEDULES_DISABLED=1
record_result "8D: accelerated schedules disabled" 0

summarize_results "Phase 8 Full AWS E2E Diagnostics"
