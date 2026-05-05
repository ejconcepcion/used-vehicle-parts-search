#!/usr/bin/env bash
#
# deploy.sh — Full first-time deployment for a DigitalOcean Droplet (Ubuntu 22.04).
#
# Run as root after SSH'ing into the Droplet:
#     bash deploy/deploy.sh
#
# Safe to re-run — idempotent. For subsequent code updates use update.sh instead.
#
# Environment variable overrides:
#   PORT      uvicorn bind port  (default: 8000)
#   APP_USER  Linux user         (default: uvps)
#

set -euo pipefail

PORT="${PORT:-8000}"
APP_USER="${APP_USER:-uvps}"
APP_DIR="/home/$APP_USER/uvps"
VENV_DIR="$APP_DIR/.venv"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { echo; bold "==> $*"; }

trap 'red "Deploy failed at line $LINENO — see output above."' ERR

if [[ "$(id -u)" -ne 0 ]]; then
    red "This script must be run as root."
    yellow "Try: sudo bash deploy/deploy.sh"
    exit 1
fi

step "1/7  System packages"

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl supervisor nginx
green "System packages installed."

step "2/7  Create app user"

if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$APP_USER"
    green "Created user: $APP_USER"
else
    green "User $APP_USER already exists."
fi

step "3/7  Copy app files"

if [[ ! -d "$APP_DIR/.git" ]]; then
    yellow "No git repo at $APP_DIR — copying current directory."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    rsync -a --exclude='.venv' --exclude='data/*.db' --exclude='__pycache__' \
        "$SRC_DIR/" "$APP_DIR/"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    green "Files copied to $APP_DIR"
else
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
    green "Code updated via git."
fi

step "4/7  Python virtualenv + dependencies"

if [[ ! -d "$VENV_DIR" ]]; then
    sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
    green "Virtualenv created."
fi

sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade --quiet pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
green "Python dependencies installed."

step "5/7  Data directory"

sudo -u "$APP_USER" mkdir -p "$APP_DIR/data"
green "Data directory ready."

step "6/7  Supervisor config"

SUPERVISOR_CONF="/etc/supervisor/conf.d/uvps.conf"
cat > "$SUPERVISOR_CONF" << EOF
[program:uvps]
command=$VENV_DIR/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT --workers 2
directory=$APP_DIR
user=$APP_USER
autostart=true
autorestart=true
startsecs=5
stopwaitsecs=20
stdout_logfile=/var/log/supervisor/uvps-stdout.log
stderr_logfile=/var/log/supervisor/uvps-stderr.log
environment=PYTHONUNBUFFERED="1"
EOF

supervisorctl reread
supervisorctl update
sleep 3

if supervisorctl status uvps | grep -q RUNNING; then
    green "uvps is RUNNING on port $PORT."
else
    red "uvps failed to start. Check:"
    tail -n 30 /var/log/supervisor/uvps-stderr.log || true
    exit 1
fi

step "7/7  Nginx config"

NGINX_CONF="/etc/nginx/sites-available/uvps"
cat > "$NGINX_CONF" << EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 90s;
    }
}
EOF

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/uvps
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
green "Nginx configured and reloaded."

echo
bold "--------------------------------------------------------------"
green " Deploy complete."
bold "--------------------------------------------------------------"
echo
echo "  App:     http://$(curl -s ifconfig.me 2>/dev/null || echo '<droplet-ip>')"
echo "  Status:  supervisorctl status uvps"
echo "  Logs:    tail -f /var/log/supervisor/uvps-stdout.log"
echo
echo "Next steps:"
echo "  1. Copy your .env file to $APP_DIR/.env"
echo "  2. supervisorctl restart uvps"
echo "  3. Open the app in your browser and click 'Run now'"
echo
