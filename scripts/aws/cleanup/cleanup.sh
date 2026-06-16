#!/usr/bin/env bash
# Wrapper for AWS validation cleanup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_SCRIPT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${AWS_SCRIPT_ROOT}/lib/common.sh"

require_deployment_config
run_eml_transformer aws-cleanup-test-resources \
    --deployment "${DEPLOYMENT_CONFIG}" \
    --profile "${AWS_PROFILE}" \
    "$@"
