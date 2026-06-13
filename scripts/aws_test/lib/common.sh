#!/usr/bin/env bash
# common.sh -- shared utilities for all aws_test phase scripts
# Source this file: source "$(dirname "$0")/lib/common.sh"
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-episb}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
RESULTS_DIR="${REPO_ROOT}/scripts/aws_test/results"
# Build with aws,test extras so pytest + eml_transformer are both available in-container
IMAGE_TAG="${IMAGE_TAG:-eml-transformer:smoke}"
BUILD_EXTRAS="${BUILD_EXTRAS:-aws,test}"

mkdir -p "${RESULTS_DIR}"

log()  { echo "[$(date -u +%H:%M:%S)] $*"; }
info() { echo "[$(date -u +%H:%M:%S)] INFO  $*"; }
warn() { echo "[$(date -u +%H:%M:%S)] WARN  $*" >&2; }
fail() { echo "[$(date -u +%H:%M:%S)] FAIL  $*" >&2; exit 1; }

declare -A PHASE_RESULTS

record_result() {
    local name="$1" code="$2"
    PHASE_RESULTS["${name}"]="${code}"
    if [[ "${code}" -eq 0 ]]; then
        info "PASS  ${name}"
    else
        warn "FAIL  ${name} (exit ${code})"
    fi
}

summarize_results() {
    local phase="${1:-phase}"
    local pass=0 fail=0
    echo ""
    echo "=========================================="
    echo "  ${phase} RESULTS"
    echo "=========================================="
    for name in "${!PHASE_RESULTS[@]}"; do
        local code="${PHASE_RESULTS[${name}]}"
        if [[ "${code}" -eq 0 ]]; then
            echo "  PASS  ${name}"
            pass=$((pass + 1))
        else
            echo "  FAIL  ${name}  (exit ${code})"
            fail=$((fail + 1))
        fi
    done
    echo "------------------------------------------"
    echo "  ${pass} passed, ${fail} failed"
    echo "=========================================="
    [[ "${fail}" -eq 0 ]]
}

# SSO check runs on host (CDK and ECR push require it)
check_sso_token() {
    info "Checking AWS SSO token for profile: ${AWS_PROFILE}"
    if ! aws sts get-caller-identity --profile "${AWS_PROFILE}" --output text &>/dev/null; then
        warn "SSO token expired. Running: aws sso login --profile ${AWS_PROFILE}"
        aws sso login --profile "${AWS_PROFILE}"
    fi
    local account
    account="$(aws sts get-caller-identity --profile "${AWS_PROFILE}" --query Account --output text)"
    info "Authenticated as account: ${account}"
    echo "${account}"
}

RUNTIME_CONFIG="${REPO_ROOT}/configs/generated/aws-smoke.runtime.yaml"

# All application-level operations run inside the container.
# AWS creds: ~/.aws volume mount + AWS_PROFILE + AWS_SDK_LOAD_CONFIG=1
# Usage: docker_run_aws <log_file> [extra_docker_flags...] -- <eml_transformer_args...>
docker_run_aws() {
    local log_file="$1"; shift
    local docker_args=()
    local container_args=()
    local past_sep=false
    for arg in "$@"; do
        if [[ "${arg}" == "--" ]]; then
            past_sep=true
        elif [[ "${past_sep}" == false ]]; then
            docker_args+=("${arg}")
        else
            container_args+=("${arg}")
        fi
    done

    docker run --rm \
        -v "${HOME}/.aws:/root/.aws" \
        -e AWS_PROFILE="${AWS_PROFILE}" \
        -e AWS_SDK_LOAD_CONFIG=1 \
        -e AWS_REGION="${AWS_REGION:-us-east-1}" \
        "${docker_args[@]+"${docker_args[@]}"}" \
        "${IMAGE_TAG}" \
        "${container_args[@]}" \
        2>&1 | tee "${log_file}"
    return "${PIPESTATUS[0]}"
}

# Run pytest inside the container (overrides entrypoint)
# Usage: docker_run_pytest <log_file> [pytest_args...]
docker_run_pytest() {
    local log_file="$1"; shift
    docker run --rm \
        --entrypoint python \
        "${IMAGE_TAG}" \
        -m pytest "$@" \
        2>&1 | tee "${log_file}"
    return "${PIPESTATUS[0]}"
}

# Run CLI commands that don't need AWS creds (config-validate, --help, sources)
# Usage: docker_run_cli <log_file> <cli_args...>
docker_run_cli() {
    local log_file="$1"; shift
    docker run --rm \
        "${IMAGE_TAG}" \
        "$@" \
        2>&1 | tee "${log_file}"
    return "${PIPESTATUS[0]}"
}

# Parse runtime config YAML in-container (no host python3 dependency)
# Usage: get_runtime_value "aws.infra_stack"
get_runtime_value() {
    local key="$1"
    docker run --rm \
        -v "${RUNTIME_CONFIG}:/app/configs/generated/aws-smoke.runtime.yaml:ro" \
        --entrypoint python \
        "${IMAGE_TAG}" \
        -c "
import yaml
with open('configs/generated/aws-smoke.runtime.yaml') as f:
    cfg = yaml.safe_load(f)
parts = '${key}'.split('.')
val = cfg
for p in parts:
    val = val.get(p, '') if isinstance(val, dict) else ''
print(val or '')
" 2>/dev/null || echo ""
}

get_stack_name()  { get_runtime_value "aws.infra_stack" || echo "eml-transformer-smoke"; }
get_data_bucket() { get_runtime_value "storage.bucket"; }
get_queue_url()   { get_runtime_value "queues.url_fetch_queue_url"; }
