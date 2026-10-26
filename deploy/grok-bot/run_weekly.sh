#!/usr/bin/env bash
# One scheduled pass: make sure the install is healthy, crawl, write the report.
# Prints the report path and a JSON summary; reports/latest.md is what the bot sends.
set -euo pipefail

HOME_DIR="${TRI_COACH_HOME:-/workspace/tri-coach}"
export TRI_RACE_PLANNER_DIR="${TRI_RACE_PLANNER_DIR:-/workspace/tri-coach-data}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${HERE}/bootstrap.sh"
cd "$HOME_DIR"
"${HOME_DIR}/.venv/bin/python" -m tri_coach.race_planner run "$@"
echo
echo "report: ${TRI_RACE_PLANNER_DIR}/reports/latest.md"
