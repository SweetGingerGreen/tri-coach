#!/usr/bin/env bash
# Set up (or repair) the race planner on a Grok Bot cloud computer. Safe to run every time:
# Grok Bot keeps /workspace but treats installed packages as replaceable, so this checks
# and reinstalls what is missing instead of assuming the last install survived.
#
#   TRI_COACH_REPO     git URL            (default: the public repo)
#   TRI_COACH_BRANCH   branch to run      (default: main)
#   TRI_COACH_HOME     checkout location  (default: /workspace/tri-coach)
#   TRI_RACE_PLANNER_DIR  data directory  (default: /workspace/tri-coach-data)
set -euo pipefail

REPO="${TRI_COACH_REPO:-https://github.com/SweetGingerGreen/tri-coach.git}"
BRANCH="${TRI_COACH_BRANCH:-main}"
HOME_DIR="${TRI_COACH_HOME:-/workspace/tri-coach}"
export TRI_RACE_PLANNER_DIR="${TRI_RACE_PLANNER_DIR:-/workspace/tri-coach-data}"
VENV="${HOME_DIR}/.venv"

log() { printf '[bootstrap] %s\n' "$*"; }

command -v git >/dev/null || { log "git is missing"; exit 1; }
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || { log "python3 is missing"; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { log "need Python >= 3.10, found $("$PYTHON" --version 2>&1)"; exit 1; }

if [ -d "${HOME_DIR}/.git" ]; then
  log "updating ${HOME_DIR} (${BRANCH})"
  git -C "$HOME_DIR" fetch --quiet origin "$BRANCH"
  git -C "$HOME_DIR" checkout --quiet "$BRANCH"
  git -C "$HOME_DIR" merge --quiet --ff-only "origin/${BRANCH}"
else
  log "cloning ${REPO} (${BRANCH}) into ${HOME_DIR}"
  git clone --quiet --branch "$BRANCH" "$REPO" "$HOME_DIR"
fi

if ! "${VENV}/bin/python" -c 'import requests, bs4' 2>/dev/null; then
  log "installing packages into ${VENV}"
  "$PYTHON" -m venv "$VENV"
  "${VENV}/bin/pip" install --quiet --upgrade pip
  # Only what the race planner needs; the RAG/PDF extras in requirements.txt are not used here.
  "${VENV}/bin/pip" install --quiet "requests>=2.31" "beautifulsoup4>=4.12"
fi

mkdir -p "$TRI_RACE_PLANNER_DIR"
SEED="${TRI_RACE_PLANNER_DIR}/seed_self.json"
cd "$HOME_DIR"
if [ -f "$SEED" ]; then
  log "loading seed ${SEED}"
  "${VENV}/bin/python" -m tri_coach.race_planner seed "$SEED" >/dev/null
else
  log "no seed at ${SEED}: the report will have no athlete profile (see deploy/grok-bot/README.md)"
fi

log "checking this machine"
"${VENV}/bin/python" -m tri_coach.race_planner doctor
