#!/bin/bash
#SBATCH --job-name=test_ingest
#SBATCH --output=/N/project/eml_ai_forecasting/eml_transformer/logs/test_%j.out
#SBATCH --error=/N/project/eml_ai_forecasting/eml_transformer/logs/test_%j.err
#SBATCH --time=00:10:00
#SBATCH --cpus-per-task=2

echo "JOB STARTED"
echo "Working directory:"
pwd

echo "Hostname:"
hostname

echo "Date:"
date

module load python/gpu/3.12.5

cd /N/project/eml_ai_forecasting/eml_transformer

echo "After cd:"
pwd

python --version

python -m eml_transformer.cli ingest \
    --source all \
    --config configs/dev.yaml

echo "JOB FINISHED"