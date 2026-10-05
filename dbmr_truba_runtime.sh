#!/bin/bash
# Shared TRUBA runtime bootstrap for DBMR.
# Optional user overrides before submission:
#   export DBMR_PYTHON=/path/to/python
#   export SUMO_HOME=$HOME/opt/sumo-1.27.1
set -euo pipefail

module purge || true

if [[ -z "${DBMR_PYTHON:-}" ]]; then
    module load comp/python/3.12.2
    DBMR_PYTHON="$(command -v python)"
fi

if [[ -n "${SUMO_HOME:-}" ]]; then
    export PATH="${SUMO_HOME}/bin:${PATH}"
    export PYTHONPATH="${SUMO_HOME}/tools:${PYTHONPATH:-}"
elif [[ -d "$HOME/opt/sumo-1.27.1" ]]; then
    export SUMO_HOME="$HOME/opt/sumo-1.27.1"
    export PATH="${SUMO_HOME}/bin:${PATH}"
    export PYTHONPATH="${SUMO_HOME}/tools:${PYTHONPATH:-}"
fi

echo "DBMR_PROJECT=${DBMR_PROJECT:-$PWD}"
echo "DBMR_PYTHON=$DBMR_PYTHON"
"$DBMR_PYTHON" --version

command -v sumo >/dev/null || {
  echo "ERROR: sumo not on PATH. Set SUMO_HOME to a Linux SUMO 1.27.1 installation." >&2
  exit 20
}
command -v netconvert >/dev/null || {
  echo "ERROR: netconvert not on PATH." >&2
  exit 21
}

sumo --version | head -n 3
netconvert --version | head -n 2

"$DBMR_PYTHON" - <<'PY'
import numpy, pandas, tqdm
print("numpy", numpy.__version__)
print("pandas", pandas.__version__)
print("tqdm", tqdm.__version__)
import libsumo
print("libsumo import: OK")
PY
