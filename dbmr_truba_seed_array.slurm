#!/bin/bash
#SBATCH --job-name=dbmr100x6
#SBATCH --partition=mid1
#SBATCH --cpus-per-task=4
#SBATCH --time=12:00:00
#SBATCH --array=0-99%48
#SBATCH --output=truba_logs/%x-%A_%a.out
#SBATCH --error=truba_logs/%x-%A_%a.err

set -euo pipefail
DBMR_PROJECT="${DBMR_PROJECT:-$SLURM_SUBMIT_DIR}"
cd "$DBMR_PROJECT"
source ./dbmr_truba_runtime.sh

"$DBMR_PYTHON" dbmr_truba_seed_array_worker_v2.py \
  --seed-index "$SLURM_ARRAY_TASK_ID" \
  --workers "$SLURM_CPUS_PER_TASK"
