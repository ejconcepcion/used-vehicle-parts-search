# Deploying on Cloudways

Cloudways is a managed-hosting layer over a real VM (DigitalOcean / AWS / Vultr / Linode / GCP). Their UI is built around PHP apps, but you have full SSH access — which is all this app needs. We'll spin up an "application slot" purely to get a domain and an Nginx vhost, then run the FastAPI app on a private port behind their Nginx.

Before you start, you need:

- A Cloudways server (any size; the smallest 1 GB plan is fine).
- An **application** on that server — create a fresh one via *Add Application* → *PHP Stack*. We won't use the PHP runtime; we just want the URL routing and the per-app Linux user that comes with it. Note the application's *master* SSH credentials and its app folder path (something like `/home/master/applications/abcdef/`).
- A domain or subdomain pointed at the server's public IP (optional but cleaner than `:8000` URLs).

## 1. SSH in and confirm Python

Connect with the **Master Credentials** shown in *Server Management → Master Credentials*:

```bash
ssh master_user@your.server.ip
python3 --version
```

Cloudways ships Python 3.10. Confirm with `python3 --version` — if it returns 3.10.x you're good to go. If it's missing or older:

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip
```

## 2. Place the app

Pick a location that **isn't** the application's `public_html` (we don't want Apache to try to serve `.py` files). The app's `private_html` is convenient because it's already owned by your app user:

```bash
APP_NAME=abcdef       # your Cloudways application folder name
cd /home/master/applications/$APP_NAME/private_html
git clone <your-repo-url> uvps
# OR upload via SFTP from your local Used Vehicle Parts Search folder.
cd uvps
```

## 3. Create venv and install dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. First run + smoke test

```bash
# Populate the DB with one full pipeline run (takes a few minutes).
python -m app.run_now

# Start the server in the foreground to make sure it boots cleanly.
uvicorn app.main:app --host 127.0.0.1 --port 8000
# Ctrl-C when you've confirmed it starts without errors.
```

## 5. Make uvicorn permanent (supervisor)

Cloudways ships supervisor on most stacks. Copy the config in `deploy/supervisor.conf`, fill in the placeholders, and install:

```bash
# Replace the placeholders {APP_USER}, {APP_DIR}, {VENV_DIR}, {PORT}.
sudo cp deploy/supervisor.conf /etc/supervisor/conf.d/uvps.conf
sudo nano /etc/supervisor/conf.d/uvps.conf   # edit placeholders

sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status uvps
# Should show: uvps    RUNNING   pid 12345, uptime 0:00:05

# Tail logs:
sudo tail -f /var/log/supervisor/uvps-stdout.log
```

The app will now restart automatically on crash and on server reboot.

## 6. Expose it through Cloudways' Nginx

You have two choices:

### Path A — let Cloudways support do it (cleanest)

Open a support ticket from your Cloudways console with this text:

> Please add an Nginx reverse-proxy upstream for application **{APP_NAME}** pointing at `127.0.0.1:8000`. Use the snippet in `deploy/nginx-reverse-proxy.conf`. The app's `public_html` is empty — all traffic should be proxied to the upstream.

They typically do this within a few hours, free.

### Path B — edit the vhost yourself via SSH

```bash
sudo nano /etc/nginx/sites-available/$APP_NAME
```

Inside the existing `server { ... }` block, paste the contents of `deploy/nginx-reverse-proxy.conf` (replace `{PORT}` with `8000`). Then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

> Cloudways may overwrite this file when it regenerates configs (e.g., when you change app domains). If that happens, re-apply the change or open a ticket so they make it permanent.

### Path C — skip Nginx, run on a port

Quickest but ugliest: bind uvicorn to `0.0.0.0:8000` instead of `127.0.0.1:8000` (edit `deploy/supervisor.conf` accordingly), open port 8000 in the Cloudways firewall (*Server → Security → Firewall* or directly via `sudo ufw allow 8000`), and visit `http://your.server.ip:8000`. **Don't do this in production** — there's no HTTPS and no host validation.

## 7. Daily run via cron (optional)

The FastAPI app already has APScheduler triggering a daily run at the configured hour. If you'd rather rely on cron (more robust against worker crashes), see `deploy/crontab-entry.txt`. Add the cron line via *Server → Manage Services → Cron Job Management* in the Cloudways UI, **and** disable the in-app scheduler by commenting out `scheduler.start()` inside `app/main.py::lifespan` so they don't both fire.

## 8. HTTPS

If you pointed a domain at the server, enable HTTPS via Cloudways' Let's Encrypt UI: *Application Management → SSL Certificate → Let's Encrypt → Install Certificate*. Their Nginx will terminate TLS in front of your reverse-proxy block, so no app-side change is required.

## 9. eBay credentials (when you have them)

```bash
cd /home/master/applications/$APP_NAME/private_html/uvps
cp .env.example .env
nano .env
# Set EBAY_USE_API=1 and add EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.

sudo supervisorctl restart uvps
```

## Troubleshooting

| Symptom                                | Likely cause                                         |
| -------------------------------------- | ---------------------------------------------------- |
| `502 Bad Gateway` from your domain     | uvicorn isn't running; check `supervisorctl status`. |
| `permission denied` writing `data/`    | Wrong owner on the app dir. `sudo chown -R master:master /home/master/applications/$APP_NAME/private_html/uvps` |
| `disk I/O error` from SQLite           | App dir is on an NFS / share that doesn't support SQLite locking. Move the project into the local filesystem. |
| Cron line runs but nothing in DB       | Cron uses its own minimal `$PATH`. Use absolute paths to `python` (the venv binary), as the example crontab line does. |
| Cloudways regenerated my Nginx vhost   | Re-apply the snippet, or open a support ticket so they preserve it. |
