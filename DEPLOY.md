# Deploying

The production stack is one command on any Linux server with Docker:

```
Browser ──HTTPS──> Caddy (load balancer, automatic certificates)
                     ├─ app.<domain> ──> frontend ×2  (Next.js)
                     └─ api.<domain> ──> backend  ×2  (FastAPI, live-session WebSockets)
                                            ├─ Postgres (accounts, documents, history)
                                            └─ Redis    (sign-in limits, session tickets)
```

Any backend can serve any request, so instances can be added, removed or restarted at any time.
During a restart, live interviews reconnect to another instance and continue (see docs/TECHNICAL.md, Reliability).

Files: `docker-compose.prod.yml`, `deploy/Caddyfile`, `backend/Dockerfile`, `frontend/Dockerfile`,
`deploy/configure.sh`, `deploy/server-setup.sh`, `deploy/backup.sh`.

There are two ways to put it live:

- **Free on Vercel** (section V): no card needed. The backend runs as a Docker container that
  Vercel scales to several instances by itself. Vercel's free (Hobby) plan is for non-commercial use;
  a paid launch needs Vercel Pro (about $20/month).
- **On any server** (sections 1-3): the full stack with `docker-compose.prod.yml`, about $8-12/month
  for a server with 2 GB of memory. (`render.yaml` also describes a Render setup; Render asks for a card, even for
  its free plan.)

## V. Free on Vercel (no card)

Uses: Vercel (app and API), Neon (database, free), Upstash (Redis, free).

**How it fits Vercel.** The backend's Docker image runs as a Vercel container (`backend/vercel.json`).
Vercel starts more instances when traffic grows and stops them when idle; any instance can serve any
request because shared state is in Redis and Postgres. Vercel closes a live connection after
5 minutes, so with `SESSION_ROTATE_S=240` the server asks the browser, at a quiet moment (nobody
speaking, no answer being written), to switch to a fresh connection; the browser opens it before
closing the old one, so nothing is lost. If no quiet moment comes in time, the normal automatic
reconnect continues the same interview. After 5 idle minutes Vercel stops the backend; the next
visit starts it again in a few seconds.

1. **Neon** (database): https://neon.tech, sign up with GitHub, create a project in **AWS US East 1**,
   copy the connection string from **Connect**.
2. **Upstash** (Redis): https://upstash.com, sign up with GitHub, create a Redis database in
   **N. Virginia (us-east-1)**, free plan, copy the **TLS** URL (`rediss://...`).
3. **Secrets:** `for k in JWT_SECRET INTERNAL_API_SECRET NEXTAUTH_SECRET; do echo "$k=$(openssl rand -hex 32)"; done`
4. **API project:** https://vercel.com, sign up with GitHub, **Add New > Project**, import the
   repository, set **Root Directory** to `backend`, and add these environment variables:

   ```
   APP_ENV=production
   PORT=8000
   SESSION_ROTATE_S=240
   FRONTEND_ORIGIN=https://<app project>.vercel.app
   DATABASE_URL=<Neon connection string>
   REDIS_URL=<Upstash rediss:// URL>
   JWT_SECRET=<secret>
   INTERNAL_API_SECRET=<secret>
   ASSEMBLYAI_API_KEY=...
   GROQ_API_KEY=...
   PINECONE_API_KEY=...
   ADMIN_EMAILS=you@example.com
   ```
   Deploy, then open `https://<api project>.vercel.app/health`: it should show `"status": "ok"`.
5. **App project:** **Add New > Project** again, the same repository, **Root Directory** `frontend`
   (Vercel detects Next.js), with:

   ```
   NEXT_PUBLIC_API_URL=https://<api project>.vercel.app
   BACKEND_URL=https://<api project>.vercel.app
   NEXTAUTH_URL=https://<app project>.vercel.app
   NEXTAUTH_SECRET=<secret>
   INTERNAL_API_SECRET=<the same secret as the API>
   ```
6. If the app's address differs from the `FRONTEND_ORIGIN` you guessed, correct it in the API
   project (**Settings > Environment Variables**) and redeploy the API. If the API's address
   changes, correct the three app variables and redeploy the app (`NEXT_PUBLIC_API_URL` is built
   into the app).

Every push to the repository redeploys both projects.

Free-plan limits: Vercel Hobby (non-commercial use; 300 s per connection, handled as above;
usage quotas per month), Neon (1 GB, 100 compute hours a month), Upstash (free command quota per month).
Uploads are limited to 4 MB to stay under Vercel's 4.5 MB request limit.

## 1. Try it on your computer first

Needs Docker Desktop. Stop the development servers first (they use port 8000).

```bash
cd ~/projects/interview-copilot
deploy/configure.sh --local
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps        # 2 backend and 2 frontend containers, all "healthy"
```

Open http://localhost:8080. This stack has its own empty database, so create a new account.
Stop it with `docker compose -f docker-compose.prod.yml down` (add `-v` to also delete its data).

## 2. Server (any Ubuntu server; example: Oracle Cloud's free VM, which needs a card)

Free and always on: an Arm VM with 2 CPUs and 12 GB RAM is plenty for this stack. Sign-up needs a
card for identity verification (Always Free resources are not charged) and a phone number.

1. Create an account at https://signup.cloud.oracle.com and pick a home region near your users.
2. On your computer, make an SSH key if you have none: `ssh-keygen -t ed25519` (press Enter for each question).
3. **Compute > Instances > Create instance**
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **Ampere VM.Standard.A1.Flex**, 2 OCPUs, 12 GB memory (Always Free eligible)
   - SSH keys: upload `~/.ssh/id_ed25519.pub`
   - Create, then copy the **Public IP address**.
   - "Out of capacity" means the region has no free Arm machines right now: try another
     availability domain, or again later.
4. Open the web ports: **Networking > Virtual cloud networks > (your network) > Security Lists >
   Default Security List > Add Ingress Rules**: source `0.0.0.0/0`, IP protocol TCP, destination
   port `80,443`.
5. Copy the project and your keys to the server, then log in:
   ```bash
   scp ~/Downloads/<project>.zip ~/projects/interview-copilot/.env ubuntu@<IP>:~
   ssh ubuntu@<IP>
   sudo apt-get update && sudo apt-get install -y unzip
   mkdir -p interview-copilot && unzip -o <project>.zip -d interview-copilot && mv .env interview-copilot/
   cd interview-copilot
   ```
6. Set up the server (Docker, firewall, daily backup), then log out and back in:
   ```bash
   bash deploy/server-setup.sh
   exit
   ssh ubuntu@<IP>
   cd interview-copilot
   ```
7. Configure and start. Without a domain of your own, use the free sslip.io name of the IP
   (dots become dashes). Example for `140.238.1.2`:
   ```bash
   deploy/configure.sh 140-238-1-2.sslip.io
   docker compose -f docker-compose.prod.yml up -d --build
   ```
   The first build takes about 10 minutes. Then open `https://app.140-238-1-2.sslip.io`.
   Caddy gets the HTTPS certificates on the first visit (a few seconds).

With the owner's own domain instead, add two DNS **A** records, `app` and `api`, pointing to the
server IP, and run `deploy/configure.sh theirdomain.com`.

## 3. After the first start

- **Admins** (answer speed page): add `ADMIN_EMAILS=you@example.com` to `.env`, then
  `docker compose -f docker-compose.prod.yml up -d`.
- **Stripe** (live payments): webhook endpoint `https://api.<domain>/api/billing/webhook`; set the
  Stripe values in `.env` (see docs/TECHNICAL.md, Payments).
- **Google sign-in**: authorised redirect URI `https://app.<domain>/api/auth/callback/google`;
  set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.

After any `.env` change: `docker compose -f docker-compose.prod.yml up -d` (recreates what changed).

## Everyday operations

| Task | Command (in the project folder on the server) |
|---|---|
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Logs (live) | `docker compose -f docker-compose.prod.yml logs -f backend` |
| Answer latency lines | `docker compose -f docker-compose.prod.yml logs backend \| grep latency` |
| More instances | `docker compose -f docker-compose.prod.yml up -d --scale backend=3 --scale frontend=2` |
| Deploy a new version | unzip the new code over the folder, then `docker compose -f docker-compose.prod.yml up -d --build` |
| Back up now | `deploy/backup.sh` (also runs daily at 03:15, keeps 7 days in `backups/`) |
| Restore a backup | `gunzip -c backups/copilot-<date>.sql.gz \| docker compose -f docker-compose.prod.yml exec -T postgres psql -U copilot copilot` |

Backups stay on the server. For safety against losing the server itself, copy `backups/` off it
now and then (`scp -r ubuntu@<IP>:~/interview-copilot/backups .`).

## Troubleshooting

- **Site doesn't open:** check step 4 (ingress rules) and that `deploy/server-setup.sh` ran
  (it opens the server firewall). `docker compose -f docker-compose.prod.yml logs caddy` shows
  certificate problems.
- **A container keeps restarting:** `docker compose -f docker-compose.prod.yml logs <service>`.
  A missing setting is named in the error (e.g. `set POSTGRES_PASSWORD in .env`).
- **Oracle reclaims idle Always Free machines** (under 20% use for 7 days). A live app with users
  is not idle; for a demo-only server, log in now and then.
