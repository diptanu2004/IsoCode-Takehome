# Deployment

How to run Settle on your own machine and how to deploy it.

## What gets deployed

| Piece | What it is |
|---|---|
| App | One Docker container. FastAPI serves the API under `/api` and the built React app on every other path. A background worker thread in the same process handles uploads. |
| HTTPS | Caddy in front of the app on the VM. It gets and renews a Let's Encrypt certificate by itself. |
| Database | PostgreSQL 16, in a container on the same VM in production; Docker Compose locally. |
| AI (optional) | Groq's API (or Anthropic's), used only for column-mapping suggestions. Without a key, the app uses rule-based suggestions instead. |

Nothing else is needed: no Redis, no object storage, no separate worker
service.

## Demo accounts

Live demo: https://settle-recon.southeastasia.cloudapp.azure.com (an Azure VM
in Singapore, always on).

The seed script creates one account per role, using the emails and passwords
in the environment variables below. On the live demo:

| Role | Email | Password |
|---|---|---|
| Finance executive | `exec@demo.local` | In the copy of this file sent with the submission; never stored in the repository |
| Co-founder | `cofounder@demo.local` | As above |

## Environment variables

Copy `.env.example` to `.env` and fill it in. Never commit `.env`.

| Variable | Required | Example | Purpose |
|---|---|---|---|
| `DATABASE_URL` | Yes | `postgresql://recon:recon@localhost:5433/recon` | Postgres connection. Hosted Postgres such as Neon needs `?sslmode=require` |
| `SESSION_SECRET` | Yes | 64 random hex characters | Signs session and CSRF tokens. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `COOKIE_SECURE` | Yes in production | `true` | Sends cookies only over HTTPS. Set `false` for local HTTP |
| `LLM_PROVIDER` | No | `groq` | `groq` (default) or `anthropic` |
| `GROQ_API_KEY` | No | `gsk_…` | Turns on AI mapping suggestions when the provider is Groq. Free key at console.groq.com |
| `ANTHROPIC_API_KEY` | No | `sk-ant-…` | Same, when the provider is Anthropic |
| `LLM_MODEL` | No | empty | Leave empty for the provider default (`openai/gpt-oss-120b` / `claude-opus-5-5`) |
| `SEED_EXEC_EMAIL` / `SEED_EXEC_PASSWORD` | For seeding | | Demo finance executive |
| `SEED_COFOUNDER_EMAIL` / `SEED_COFOUNDER_PASSWORD` | For seeding | | Demo co-founder |
| `MAX_UPLOAD_MB` | No | `50` | Largest file accepted |
| `UPLOAD_DIR` | No | `./var/uploads` | Where uploads are kept until their rows are loaded |

## Run locally

Prerequisites: Docker Desktop, Python 3.12 with [uv](https://docs.astral.sh/uv/),
and Node.js 22.

### Option A: everything in Docker

```bash
cp .env.example .env            # then fill in SESSION_SECRET and the seed passwords
docker compose up --build -d    # Postgres, then the app on http://localhost:8000 (runs migrations)
docker compose exec app python -m scripts.seed --as-of 2026-11-06   # demo data, about a minute
```

The app image is the one deployed to production (`Dockerfile`): it builds the
React app, installs the Python dependencies with uv, runs as an unprivileged
user, and applies migrations before it starts serving.

### Option B: development mode with hot reload

```bash
cp .env.example .env
docker compose up -d db                       # Postgres only

cd backend
uv sync
uv run alembic upgrade head                   # create the schema
uv run python -m scripts.seed --as-of 2026-11-06   # demo setup, then 60k orders of partner files (~1 min)
uv run uvicorn app.main:app --reload          # API on http://localhost:8000

cd ../frontend
npm ci
npm run dev                                   # UI on http://localhost:5173, calls the API through a proxy
```

`--as-of 2026-11-06` makes the data match the files in `sample-data/`. Any
date works; the generator then writes matching demo files to `var/data/demo/`.
After seeding, the app re-checks waiting orders once a day, so statuses move on
with the calendar.

### Sample files

`sample-data/` holds two small synthetic partner files to upload in the demo
(see its README): Marketplace 1's report in a changed format, to show the AI
mapping flow, and late COD remittances that close open claims. To make
full-size files:

```bash
cd backend
uv run python -m scripts.generate_data --orders 100000 --out ../sample-data/generated
```

## Tests and benchmark

```bash
cd backend
uv run pytest                                     # unit, integration and access-control tests
uv run python -m scripts.benchmark --rows 100000  # times each stage of a 1 lakh-row run

cd ../frontend
npm test
```

Tests never call the AI provider (they set `AI_ENABLED=false`), except one accuracy check
that runs only when the provider's key is in `.env`. To score the AI yourself:
`uv run python -m scripts.eval_mapping --ai`.
Without it, those tests use a stubbed response.

## Deploy to production (Azure VM)

The live demo runs on one Azure VM (Ubuntu 24.04, B2als v2: 2 vCPU, 4 GB) in
Southeast Asia: Postgres 16, the app and Caddy as three containers. The files
are in `deploy/azure/`. Postgres publishes no port outside Docker.

1. **VM.** With the Azure CLI signed in (`az login`), run:

   ```bash
   deploy/azure/create-vm.sh <dns-label> ~/.ssh/<key>.pub <your-public-ip>
   ```

   It creates the VM with SSH open only to your address and ports 80 and 443
   open to everyone, and cloud-init installs Docker and Compose. The app's
   address is `https://<dns-label>.southeastasia.cloudapp.azure.com`.
2. **Code and settings.** `git clone` the repository to `/opt/settle`. Then
   create `/opt/settle/deploy/azure/.env` (mode 600) with:
   - `SITE_ADDRESS`: the VM's DNS name;
   - `POSTGRES_PASSWORD`: a random value, e.g. `openssl rand -hex 24`;
   - `DATABASE_URL=postgresql://settle:<POSTGRES_PASSWORD>@db:5432/settle`;
   - `SESSION_SECRET`, `LLM_PROVIDER`, `GROQ_API_KEY`;
   - `SEED_EXEC_EMAIL`, `SEED_EXEC_PASSWORD`, `SEED_COFOUNDER_EMAIL`,
     `SEED_COFOUNDER_PASSWORD` for the demo accounts.
3. **Start.** On the VM:

   ```bash
   cd /opt/settle/deploy/azure && docker compose up -d --build
   ```

   The app waits for Postgres to be healthy, then runs `alembic upgrade head`
   before it starts serving, so the schema is created on the first start and
   kept current after that. Caddy gets the HTTPS certificate on its first
   request. All three containers restart on failure and after a reboot.
4. **Seed once.** On the VM (about 30 seconds):

   ```bash
   docker compose exec -T app python -m scripts.seed --reset --as-of 2026-11-06
   ```

   It loads the partner files the same way an upload does. The seed passwords
   can be removed from `.env` afterwards.
5. **No SSH?** `az vm run-command invoke -g settle-rg -n settle-vm
   --command-id RunShellScript --scripts "<commands>"` runs commands through
   the Azure agent. It helps when your network's public address changes and
   no longer matches the SSH rule (common on mobile and carrier-NAT networks).
6. **Smoke test.**
   - `/api/health` returns `{"status": "ok"}`.
   - Sign in as each role.
   - Upload `sample-data/marketplace1_settlement_new_format.csv` as
     Marketplace 1 · Settlement, approve the suggested mapping, and watch it
     reach Loaded.
   - Open the three sample orders (ORD-W-204118, ORD-M-88120, ORD-W-204377)
     and check that they show the results in TDD §8.8.

### Alternative: Render

`render.yaml` describes the same image as a Render web service (New →
Blueprint; paste `DATABASE_URL` and `GROQ_API_KEY` when asked). It was the
first deployment. Its free instance sleeps after 15 idle minutes, and during
setup it twice failed to start a new container with nothing in the logs, so
the demo moved to the VM.

### Earlier: Neon for the database

Until 25 November the database was Neon's free plan (Postgres 16, Singapore).
The upload worker checks the `uploads` table for work every second, so Neon's
compute never went idle long enough to suspend, and the free plan's monthly
compute ran out after about two weeks. Every database request then failed
with "Your account or project has exceeded the quota". A hosted database that
bills by active time needs a worker that waits for a notification (Postgres
`LISTEN/NOTIFY`) instead of polling; on the VM, polling a local database costs
nothing.

## Updating and rolling back

- **Update.** `git pull` in `/opt/settle`, then
  `docker compose up -d --build` in `deploy/azure`. The new container runs
  migrations on start.
- **Code rollback.** `git checkout <earlier commit>` and run the same command.
  Migrations are written to work with the previous release (columns are added
  before they are used), so rolling back code does not need a database
  rollback.
- **Costs.** `az vm deallocate -g settle-rg -n settle-vm` stops the VM's
  compute charges; `az vm start` brings it back with the same address. The
  database volume is kept.
- **Backups.** The demo data can be recreated with the seed command. For real
  data, add a nightly `pg_dump` to Azure Blob Storage.
- **Mapping rollback.** This is done inside the app (Mapping → Versions), not
  through a deployment.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Sign-in works locally but not in production | `COOKIE_SECURE` does not match the protocol | `true` on HTTPS, `false` on local HTTP |
| `SSL connection is required` | Neon URL is missing `sslmode` | Add `?sslmode=require` |
| Mapping shows "AI unavailable" | No key for the chosen `LLM_PROVIDER`, or the call failed | Expected without a key; otherwise check the logs |
| Every request returns 500 and health fails | The database is unreachable (with Neon: the free compute quota ran out) | `docker compose ps` and `docker compose logs db`; see "Earlier: Neon" above |
| Upload stuck in `queued` | The worker thread is not running | Check the startup logs; restart the service |
| Large upload rejected | `MAX_UPLOAD_MB` is too low | Raise it in the environment |
| HTTPS fails right after the first start | Caddy is still getting the certificate, or port 80 is closed | Wait a minute; check `docker compose logs caddy` and the VM's network rules |
| Render shows Live, but every request hangs and the logs stay empty | The free instance did not start a container | Manual Deploy → Deploy latest commit |
| `failed to resolve host` when seeding Neon from your machine | Some home routers' DNS refuse Neon host names | Use another DNS server, or add `&hostaddr=<IPv4 of the host>` to the URL for that command |
