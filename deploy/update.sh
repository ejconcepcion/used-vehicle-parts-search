#!/usr/bin/env bash
#
# update.sh — Pull new code and restart the app (Cloudways / any Debian-Ubuntu VM).
#
# Run from the project root after SSH'ing in:
#     cd /home/master/applications/<APP_NAME>/private_html/uvps
#     bash deploy/update.sh
#
# This script is intentionally shorter than deploy.sh — it assumes the server
# is already set up (venv exists, supervisord is running). If this is a
# first-time setup, run deploy/deploy.sh instead.
#
# Environment variable overrides:
#   PORT              uvicorn bind port (default: 8000)
#   VENV_DIR          Path to the virtualenv (default: <APP_DIR>/.venv)
#   SUPERVISORD_CONF  Path to supervisord.conf (default: <APP_DIR>/data/supervisord.conf)
#   SKIP_SMOKE_TEST   Set to 1 to skip the HTTP health check after restart
#
# Examples:
#   bash deploy/update.sh
#   SKIP_SMOKE_TEST=1 bash deploy/update.sh
#

set -euo pipefail

# --- Help ------------------------------------------------------------------

usage() {
    cat <<EOF
Usage: bash deploy/update.sh [--help]

Pull the latest code and restart the uvps app via supervisord.
Assumes deploy/deploy.sh has been run at least once on this server.

Environment variable overrides:
  PORT              uvicorn bind port  (default: 8000)
  VENV_DIR          Path to the venv   (default: <APP_DIR>/.venv)
  SUPERVISORD_CONF  supervisord config  (default: <APP_DIR>/data/supervisord.conf)
  SKIP_SMOKE_TEST   Set to 1 to skip HTTP health check after restart
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $arg"; usage; exit 1 ;;
    esac
done

# --- Resolve paths ---------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
PORT="${PORT:-8000}"
SUPERVISORD_CONF="${SUPERVISORD_CONF:-$APP_DIR/data/supervisord.conf}"
SUPERVISORD_PID="$APP_DIR/data/supervisord.pid"
SKIP_SMOKE_TEST="${SKIP_SMOKE_TEST:-0}"

SUPERVISORCTL_CMD="$VENV_DIR/bin/supervisorctl -c $SUPERVISORD_CONF"
SUPERVISORD_CMD="$VENV_DIR/bin/supervisord -c $SUPERVISORD_CONF"

# --- Pretty output ---------------------------------------------------------

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { echo; bold "==> $*"; }

trap 'red "Update failed at line $LINENO. See output above."' ERR

# --- Sanity checks ---------------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    red "Virtualenv not found at $VENV_DIR."
    yellow "This looks like a first-time setup. Run deploy/deploy.sh instead."
    exit 1
fi

# --- Step 1: Pull latest code ----------------------------------------------

step "1/4  Pulling latest code"

if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git pull --ff-only
    green "git pull complete."
else
    yellow "No .git directory found — skipping git pull."
    yellow "If you deploy via SFTP, upload your files now and press Enter to continue."
    read -r
fi

# --- Step 2: Sync Python dependencies --------------------------------------

step "2/4  Syncing Python dependencies"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade --quiet pip
pip install --quiet -r "$APP_DIR/requirements.txt"
pip install --quiet supervisor
green "Dependencies up to date."

# --- Step 3: Restart via supervisord ---------------------------------------

step "3/4  Restarting uvps"

if [ -f "$SUPERVISORD_PID" ] && kill -0 "$(cat "$SUPERVISORD_PID")" 2>/dev/null; then
    $SUPERVISORCTL_CMD restart uvps
    sleep 3
    $SUPERVISORCTL_CMD status uvps || true

    if $SUPERVISORCTL_CMD status uvps | grep -q RUNNING; then
        green "uvps is RUNNING."
    else
        red "uvps is not running after restart. Last 30 lines of stderr:"
        tail -n 30 "$APP_DIR/data/uvps-stderr.log" || true
        exit 1
    fi
else
    yellow "supervisord is not running — starting it now."
    $SUPERVISORD_CMD
    sleep 3
    if $SUPERVISORCTL_CMD status uvps | grep -q RUNNING; then
        green "supervisord started and uvps is RUNNING."
    else
        red "uvps failed to start. Last 30 lines of stderr:"
        tail -n 30 "$APP_DIR/data/uvps-stderr.log" || true
        exit 1
    fi
fi

# --- Step 4: Health check --------------------------------------------------

step "4/4  Health check"

if [ "$SKIP_SMOKE_TEST" != "1" ] && command -v curl >/dev/null 2>&1; then
    sleep 2
    if curl -fsS "http://127.0.0.1:$PORT/api/status" >/dev/null; then
        green "GET /api/status returned 200 OK"
    else
        yellow "GET /api/status failed — the app may still be starting. Check:"
        yellow "  tail -f $APP_DIR/data/uvps-stdout.log"
    fi
else
    yellow "Skipping HTTP check (curl not found or SKIP_SMOKE_TEST=1)."
fi

# --- Done ------------------------------------------------------------------

echo
bold "--------------------------------------------------------------"
green "Update complete."
bold "--------------------------------------------------------------"
echo
echo "Logs:    tail -f $APP_DIR/data/uvps-stdout.log"
echo "Status:  $SUPERVISORCTL_CMD status"
echo "Restart: $SUPERVISORCTL_CMD restart uvps"
echo
