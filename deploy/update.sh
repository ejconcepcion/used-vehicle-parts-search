#!/usr/bin/env bash
#
# update.sh — Pull new code and restart the app on a DigitalOcean Droplet.
#
# Run as root after SSH'ing into the Droplet:
#     bash deploy/update.sh
#
# Assumes deploy/deploy.sh has been run at least once on this server.
#
# Environment variable overrides:
#   PORT              uvicorn bind port        (default: 8000)
#   APP_USER          Linux user               (default: uvps)
#   SKIP_SMOKE_TEST   1 = skip HTTP check      (default: 0)
#   SKIP_DB_BACKUP    1 = skip SQLite backup   (default: 0)
#

set -euo pipefail

PORT="${PORT:-8000}"
APP_USER="${APP_USER:-uvps}"
APP_DIR="/home/$APP_USER/uvps"
VENV_DIR="$APP_DIR/.venv"
SKIP_SMOKE_TEST="${SKIP_SMOKE_TEST:-0}"
SKIP_DB_BACKUP="${SKIP_DB_BACKUP:-0}"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { echo; bold "==> $*"; }

trap 'red "Update failed at line $LINENO — see output above."' ERR

if [[ "$(id -u)" -ne 0 ]]; then
    red "This script must be run as root."
    yellow "Try: sudo bash deploy/update.sh"
    exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
    red "Virtualenv not found at $VENV_DIR."
    yellow "Looks like a first-time setup — run deploy/deploy.sh instead."
    exit 1
fi

step "1/5  Database backup"

DB_FILE="$APP_DIR/data/app.db"

if [[ "$SKIP_DB_BACKUP" == "1" ]]; then
    yellow "Skipping database backup (SKIP_DB_BACKUP=1)."
elif [[ -f "$DB_FILE" ]]; then
    BACKUP_FILE="${DB_FILE}.backup-$(date +%Y%m%d-%H%M%S)"
    cp "$DB_FILE" "$BACKUP_FILE"
    green "Database backed up to $BACKUP_FILE"
    ls -t "${DB_FILE}.backup-"* 2>/dev/null | tail -n +6 | xargs rm -f || true
    green "Old backups pruned (keeping 5 most recent)."
else
    yellow "No database file found at $DB_FILE — skipping backup."
fi

step "2/5  Pulling latest code"

if [[ -d "$APP_DIR/.git" ]]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
    green "Code updated."
else
    yellow "No .git directory found — skipping git pull."
    yellow "Upload your updated files via SCP/SFTP, then press Enter to continue."
    read -r
fi

step "3/5  Syncing Python dependencies"

sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade --quiet pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
green "Dependencies up to date."

step "4/5  Restarting uvps"

supervisorctl restart uvps
sleep 3
supervisorctl status uvps || true

if supervisorctl status uvps | grep -q RUNNING; then
    green "uvps is RUNNING."
else
    red "uvps is not running after restart. Last 30 lines of stderr:"
    tail -n 30 /var/log/supervisor/uvps-stderr.log || true
    exit 1
fi

step "5/5  Health check"

if [[ "$SKIP_SMOKE_TEST" != "1" ]]; then
    sleep 2
    if curl -fsS "http://127.0.0.1:$PORT/api/status" >/dev/null; then
        green "GET /api/status -> 200 OK"
    else
        yellow "Health check failed — the app may still be starting."
        yellow "Check: tail -f /var/log/supervisor/uvps-stdout.log"
    fi
else
    yellow "Skipping health check (SKIP_SMOKE_TEST=1)."
fi

echo
bold "--------------------------------------------------------------"
green " Update complete."
bold "--------------------------------------------------------------"
echo
echo "  App status:  supervisorctl status uvps"
echo "  Live logs:   tail -f /var/log/supervisor/uvps-stdout.log"
echo "  Error logs:  tail -f /var/log/supervisor/uvps-stderr.log"
echo "  Restart:     supervisorctl restart uvps"
echo
