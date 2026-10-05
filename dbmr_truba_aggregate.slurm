#!/bin/bash
#SBATCH --job-name=dbmr_agg100x6
#SBATCH --partition=mid1
#SBATCH --cpus-per-task=4
#SBATCH --time=1-00:00:00
#SBATCH --output=truba_logs/%x-%j.out
#SBATCH --error=truba_logs/%x-%j.err

set -euo pipefail
DBMR_PROJECT="${DBMR_PROJECT:-$SLURM_SUBMIT_DIR}"
cd "$DBMR_PROJECT"
source ./dbmr_truba_runtime.sh

"$DBMR_PYTHON" dbmr_truba_aggregate_v2.py
