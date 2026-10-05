#!/bin/bash
set -euo pipefail
DBMR_PROJECT="${DBMR_PROJECT:-$(pwd)}"
cd "$DBMR_PROJECT"
source ./dbmr_truba_runtime.sh
echo
echo "SLURM"
sinfo -o "%P %a %l %D %c" | head -n 30
echo
echo "ENVIRONMENT_CHECK_OK: True"
