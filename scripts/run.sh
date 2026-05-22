#!/bin/bash
#SBATCH -J test_job
#SBATCH -A r01850
#SBATCH --nodes=1
#SBATCH -o output/run_%J.txt
#SBATCH -e output/run_%J.err
#SBATCH --gpus 1
#SBATCH --cpus-per-task=10
#SBATCH --mem=120G
#SBATCH --time=20:00
#SBATCH --mail-user=username@iu.edu
#SBATCH --mail-type=BEGIN,FAIL,END
#SBATCH -p gpu-debug

#Set up environment
module load python/gpu/3.12.5
export OMP_NUM_THREADS=10


# Check GPUs allocated
# nvidia-smi

#Change directories
cd /N/project/eml_ai_forecasting/eml_transformer


# run
python -m eml_transformer.cli ingest \
    --source all \
    --config configs/dev.yaml


# repeat every 12 hours
sbatch --begin=now+12hour /N/project/eml_ai_forecasting/eml_transformer/scripts/run_ingest.sh