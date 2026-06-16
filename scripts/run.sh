#!/bin/bash
#SBATCH -J ingestion
#SBATCH -A r01850
#SBATCH --export=ALL
#SBATCH --nodes=1
#SBATCH -o logs/run_%J.txt
#SBATCH -e logs/run_%J.err
#SBATCH --gpus 1
#SBATCH --cpus-per-task=10
#SBATCH --mem=120G
#SBATCH --time=20:00
#SBATCH --mail-user=jayeun@iu.edu
#SBATCH --mail-type=BEGIN,FAIL,END
#SBATCH -p gpu

#Set up environment
module load python/gpu/3.10.10
export OMP_NUM_THREADS=10

# Check GPUs allocated
# nvidia-smi

#Change directories
cd /N/project/eml_ai_forecasting/eml_transformer


# run
python -m eml_transformer.cli run_all \
    --config configs/hpc.yaml

# repeat every 12 hours
sbatch --begin=now+12hour /N/project/eml_ai_forecasting/eml_transformer/scripts/run.sh
