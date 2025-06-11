#!/bin/bash
set -ex

# cd to script directory.
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
cd "$SCRIPT_DIR" || { echo "Error: Cannot change working directory to $SCRIPT_DIR"; exit 1; }

source venv/bin/activate
python -m mcp_metricool.server