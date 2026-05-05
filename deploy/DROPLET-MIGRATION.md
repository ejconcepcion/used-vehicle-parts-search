# Migrating to a DigitalOcean Droplet

Moves the Used Vehicle Parts Search app off Cloudways onto a DigitalOcean
Droplet. No domain or SSL needed — the app is accessed directly via the
Droplet's public IP on port 8000.

---

## Overview

```
Browser → http://<droplet-ip>:8000 → uvicorn → FastAPI app
```

One process managed by supervisor:
- `uvps` — uvicorn serving the FastAPI app on 0.0.0.0:8000

---

## Prerequisites

- A DigitalOcean account
- Your `.env` file from Cloudways (eBay credentials etc.)
- SSH key added to your DigitalOcean account (recommended) or use password auth

---

## Step 1 — Create the Droplet

1. Log into [cloud.digitalocean.com](https://cloud.digitalocean.com)
2. Click **Create → Droplets**
3. Use these settings:

   | Setting       | Value                              |
   | ------------- | ---------------------------------- |
   | Region        | closest to you                     |
   | Image         | Ubuntu 22.04 LTS x64               |
   | Size          | Basic → Regular → **$6/mo** (1GB RAM, 1 vCPU, 25GB SSD) |
   | Authentication| SSH key (recommended) or password  |
   | Hostname      | `uvps`                             |

4. Click **Create Droplet** and wait ~30 seconds for it to provision
5. Note the Droplet's **public IP address**

---

## Step 2 — Connect and Prepare the Server

SSH in as root:
```bash
ssh root@<droplet-ip>
```

Update packages and install dependencies:
```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git curl supervisor
```

Confirm versions:
```bash
python3 --version      # should print 3.10.x or higher
git --version
supervisord --version
```

Create an app user:
```bash
useradd -m -s /bin/bash uvps
```

Enable supervisor on boot:
```bash
systemctl enable supervisor
systemctl start supervisor
```

---

## Step 3 — Deploy the App

Switch to the app user and clone your repo:
```bash
su - uvps
git clone <your-repo-url> ~/uvps
cd ~/uvps
```

If your repo is private or you haven't pushed to git yet, copy from your local
machine instead. Run this from your **local machine**:
```bash
scp -r /path/to/used-vehicle-parts-search/ uvps@<droplet-ip>:~/uvps/
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
# Still as uvps user, from ~/uvps
cp .env.example .env
nano .env
```

Key values to set:
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
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<droplet-ip>:8000` in your browser. If the dashboard loads,
hit `Ctrl-C` to stop and move on.

---

## Step 6 — Open Port 8000 in the DigitalOcean Firewall

By default, DigitalOcean Droplets only expose ports 22, 80, and 443.

**Option A — DigitalOcean Cloud Firewall (recommended):**
1. In the DO dashboard go to **Networking → Firewalls → Create Firewall**
2. Add an inbound rule: **TCP port 8000, from Any**
3. Apply the firewall to your `uvps` Droplet

**Option B — UFW on the Droplet:**
```bash
# As root
ufw allow 8000/tcp
ufw enable
ufw status
```

---

## Step 7 — Make uvicorn Permanent (supervisor)

Back on the Droplet **as root** (`exit` out of the uvps user first):

```bash
cat > /etc/supervisor/conf.d/uvps.conf << 'EOF'
[program:uvps]
command=/home/uvps/uvps/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
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

## Step 8 — Run the Initial Scrape

```bash
# As uvps user
cd ~/uvps
source .venv/bin/activate
python -m app.run_now
```

This hits Row52 and eBay and takes a few minutes. Once it finishes your
dashboard will have data and the daily scheduler takes over from there.

---

## Step 9 — Test End to End

Open `http://<droplet-ip>:8000` in your browser. You should see:
- The parts search dashboard with data
- `/api/status` returning your config and vehicle count
- `/api/vehicles` returning your freshly scraped results

---

## Step 10 — Decommission Cloudways

Once you've confirmed everything works:

1. Delete the application from your Cloudways console
2. If this was your only app on the server, delete the server too to stop billing

---

## Keeping the App Updated

```bash
# As uvps user
cd ~/uvps
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only if requirements changed

# As root
supervisorctl restart uvps
```

---

## Useful Commands

| Task                 | Command                                                    |
| -------------------- | ---------------------------------------------------------- |
| Check app status     | `supervisorctl status uvps`                                |
| View live logs       | `tail -f /var/log/supervisor/uvps-stdout.log`              |
| Restart app          | `supervisorctl restart uvps`                               |
| Stop app             | `supervisorctl stop uvps`                                  |
| Run a manual scrape  | `cd ~/uvps && .venv/bin/python -m app.run_now`             |
| View error logs      | `tail -50 /var/log/supervisor/uvps-stderr.log`             |

---

## Adding a Domain + SSL Later

When you're ready to add a domain:

1. Point your domain's A record at the Droplet's public IP
2. Install Nginx: `apt install -y nginx`
3. Install Certbot: `apt install -y certbot python3-certbot-nginx`
4. Create an Nginx vhost using `deploy/nginx-reverse-proxy.conf` as a template
5. Run `certbot --nginx -d yourdomain.com` to get a free SSL certificate
6. Update supervisor to bind uvicorn back to `127.0.0.1:8000` instead of `0.0.0.0:8000`

---

## Troubleshooting

| Symptom                        | Likely cause / fix                                                    |
| ------------------------------ | --------------------------------------------------------------------- |
| Can't reach the app in browser | Port 8000 not open — recheck Step 6                                   |
| Dashboard loads but no data    | Run the initial scrape: `python -m app.run_now`                       |
| App crashes on startup         | Check stderr: `tail -50 /var/log/supervisor/uvps-stderr.log`          |
| eBay scraping fails            | Verify `.env` values and that `EBAY_USE_API` matches your credentials |
| SQLite lock errors             | Set `--workers 1` in supervisor.conf and restart                      |
