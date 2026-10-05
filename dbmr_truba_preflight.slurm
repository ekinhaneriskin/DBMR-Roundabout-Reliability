#!/bin/bash
#SBATCH --job-name=dbmr_pre100x6
#SBATCH --partition=short
#SBATCH --cpus-per-task=4
#SBATCH --time=02:00:00
#SBATCH --output=truba_logs/%x-%j.out
#SBATCH --error=truba_logs/%x-%j.err

set -euo pipefail
DBMR_PROJECT="${DBMR_PROJECT:-$SLURM_SUBMIT_DIR}"
cd "$DBMR_PROJECT"
source ./dbmr_truba_runtime.sh

"$DBMR_PYTHON" dbmr_final_campaign_preflight_v2_100x6.py
