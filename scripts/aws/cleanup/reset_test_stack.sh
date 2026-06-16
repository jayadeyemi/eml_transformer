#!/usr/bin/env bash
# Wrapper for guarded stack reset.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_SCRIPT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${AWS_SCRIPT_ROOT}/lib/common.sh"

require_deployment_config
CONFIRM_STACK="${CONFIRM_STACK_DELETE:-}"

if [[ -z "${CONFIRM_STACK}" ]]; then
    echo "CONFIRM_STACK_DELETE is required." >&2
    exit 1
fi

run_eml_transformer aws-reset-stack \
    --deployment "${DEPLOYMENT_CONFIG}" \
    --confirm-stack "${CONFIRM_STACK}" \
    --profile "${AWS_PROFILE}" \
    "$@"
