# Migrating to Proxmox (Homelab)

Moves the Used Vehicle Parts Search app off Cloudways onto a lightweight LXC
container on your Proxmox server. Traffic is exposed via a Cloudflare Tunnel —
no open router ports, no dynamic-DNS headaches, no Nginx required on the
container itself.

---

## Overview

```
Browser → Cloudflare → cloudflared (tunnel daemon) → uvicorn :8000 → FastAPI app
```

The container runs two persistent processes managed by supervisor:
- `uvps` — uvicorn serving the FastAPI app
- `cloudflared` — the Cloudflare Tunnel daemon

---

## Prerequisites

- Proxmox VE running on your homelab
- A domain managed by Cloudflare (free plan is fine)
- A Cloudflare account with the Zero Trust / Tunnel feature enabled (free)
- Your `.env` file from Cloudways (to carry over eBay credentials etc.)

---

## Step 1 — Create the LXC Container in Proxmox

In the Proxmox web UI:

1. **Download a template** — go to your storage → CT Templates → Download
   `ubuntu-22.04-standard` (or Debian 12, both work fine).

2. **Create container** — click *Create CT* and use these settings:

   | Setting         | Value                        |
   | --------------- | ---------------------------- |
   | Hostname        | `uvps`                       |
   | Password        | set a strong root password   |
   | Template        | ubuntu-22.04-standard        |
   | Disk size       | 8 GB                         |
   | CPU cores       | 1                            |
   | RAM             | 512 MB                       |
   | Swap            | 512 MB                       |
   | Network         | DHCP (or a static LAN IP)    |

3. **Start the container** and open its console in Proxmox, or SSH into it
   once it has a DHCP address.

---

## Step 2 — Prepare the Container

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git curl supervisor
```

Confirm Python version:
```bash
python3 --version   # should print 3.10.x or higher
```

Create an app user (running as root is not recommended):
```bash
useradd -m -s /bin/bash uvps
```

---

## Step 3 — Deploy the App

Switch to the app user and clone your repo:

```bash
su - uvps
git clone <your-repo-url> ~/uvps
cd ~/uvps
```

Or if you haven't pushed to git, copy the files over from Cloudways first:
```bash
# Run this from your LOCAL machine, not the container
scp -r master@your.cloudways.ip:/path/to/uvps/ ./cloudways-backup/
# Then copy to the container
scp -r ./cloudways-backup/ uvps@your.container.ip:~/uvps/
```

Set up the virtualenv and install dependencies:
```bash
cd ~/uvps
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 4 — Configure the Environment

```bash
# On the container, as the uvps user
cd ~/uvps
cp .env.example .env
nano .env
```

Fill in your settings. Key values to check:

```
EBAY_USE_API=0          # or 1 if you have API credentials
EBAY_CLIENT_ID=         # your eBay developer client ID
EBAY_CLIENT_SECRET=     # your eBay developer secret
```

---

## Step 5 — Run a Smoke Test

```bash
# As uvps user, from ~/uvps
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Visit `http://<container-lan-ip>:8000` from another machine on your network to
confirm the dashboard loads. Then `Ctrl-C` to stop.

---

## Step 6 — Make uvicorn Permanent (supervisor)

Back on the container **as root** (`exit` out of the uvps user first):

```bash
# Create the supervisor config
cat > /etc/supervisor/conf.d/uvps.conf << 'EOF'
[program:uvps]
command=/home/uvps/uvps/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
directory=/home/uvps/uvps
user=uvps
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
supervisorctl status uvps
# Should show: uvps   RUNNING   pid XXXXX, uptime 0:00:05
```

---

## Step 7 — Run the Initial Scrape

With supervisor running uvicorn, populate the database for the first time:

```bash
# As uvps user
cd ~/uvps
source .venv/bin/activate
python -m app.run_now
```

This hits Row52 and eBay and takes a few minutes. Once it finishes, your
dashboard will have data and the daily scheduler takes over from there.

---

## Step 8 — Set Up the Cloudflare Tunnel

This replaces Nginx. Cloudflare Tunnel punches outbound from your container to
Cloudflare's edge — no port forwarding on your router needed.

### 8a. Create the tunnel in the Cloudflare dashboard

1. Log into [dash.cloudflare.com](https://dash.cloudflare.com)
2. Go to **Zero Trust → Networks → Tunnels**
3. Click **Create a tunnel** → name it `uvps`
4. Choose **Cloudflared** as the connector type
5. Copy the install command shown — it will look like:
   ```
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   dpkg -i cloudflared.deb
   cloudflared service install <YOUR_TUNNEL_TOKEN>
   ```
6. Run those commands on the container **as root**

### 8b. Configure the public hostname

Still in the Cloudflare dashboard, under your tunnel's **Public Hostnames** tab:

| Field          | Value                       |
| -------------- | --------------------------- |
| Subdomain      | `parts` (or whatever you want) |
| Domain         | `yourdomain.com`            |
| Path           | *(leave blank)*             |
| Service Type   | `HTTP`                      |
| URL            | `127.0.0.1:8000`            |

Click **Save**. Cloudflare will automatically create a DNS CNAME record for
`parts.yourdomain.com` pointing to your tunnel.

### 8c. Verify the tunnel is running

```bash
# On the container
systemctl status cloudflared
```

It should show `active (running)`. The tunnel daemon was installed as a systemd
service by the install command above — it starts automatically on boot.

---

## Step 9 — Test End to End

Open `https://parts.yourdomain.com` in your browser. You should see:
- The parts search dashboard loading over HTTPS
- `/api/status` returning your config and vehicle count
- `/api/vehicles` returning your freshly scraped data

Cloudflare handles TLS termination automatically — no Certbot or SSL config
needed on your end.

---

## Step 10 — Decommission Cloudways

Once you've confirmed everything works:

1. Delete the application from your Cloudways console
2. If this was your only app on the server, delete the server too to stop billing

---

## Keeping the App Updated

To pull new code and restart:

```bash
# As uvps user on the container
cd ~/uvps
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only needed if requirements changed

# As root
supervisorctl restart uvps
```

Or use the existing `deploy/update.sh` script if it handles this already.

---

## Useful Commands

| Task                          | Command                                              |
| ----------------------------- | ---------------------------------------------------- |
| Check app status              | `supervisorctl status uvps`                          |
| View live logs                | `tail -f /var/log/supervisor/uvps-stdout.log`        |
| Restart app                   | `supervisorctl restart uvps`                         |
| Stop app                      | `supervisorctl stop uvps`                            |
| Check tunnel status           | `systemctl status cloudflared`                       |
| Restart tunnel                | `systemctl restart cloudflared`                      |
| Run a manual scrape           | `cd ~/uvps && .venv/bin/python -m app.run_now`       |
| View tunnel logs              | `journalctl -u cloudflared -f`                       |

---

## Troubleshooting

| Symptom                             | Likely cause / fix                                                         |
| ----------------------------------- | -------------------------------------------------------------------------- |
| `502 Bad Gateway` from Cloudflare   | uvicorn isn't running — check `supervisorctl status uvps`                  |
| Dashboard loads but no data         | Run the initial scrape: `python -m app.run_now`                            |
| Tunnel shows offline in dashboard   | `systemctl restart cloudflared` on the container                           |
| App crashes on startup              | Check stderr log: `tail -50 /var/log/supervisor/uvps-stderr.log`           |
| eBay scraping fails                 | Verify `.env` values and that `EBAY_USE_API` matches your credentials      |
| SQLite lock errors                  | Make sure only one uvicorn worker writes at a time — set `--workers 1` in supervisor.conf if needed |
