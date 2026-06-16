#!/usr/bin/env bash
# Run the direct AWS validation commands in operational order.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

require_deployment_config

run_eml_transformer aws-preflight --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-static --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-container --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-infra --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-gdelt --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-pipeline --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-batch --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
run_eml_transformer aws-validate-e2e --deployment "${DEPLOYMENT_CONFIG}" --profile "${AWS_PROFILE}" "$@"
