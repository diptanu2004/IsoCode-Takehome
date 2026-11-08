# Deployment

How to run Settle on your own machine and how to deploy it.

## What gets deployed

| Piece | What it is |
|---|---|
| App | One Docker container. FastAPI serves the API under `/api` and the built React app on every other path. A background worker thread in the same process handles uploads. |
| Database | PostgreSQL 16. Neon in production; Docker Compose locally. |
| AI (optional) | Groq's API (or Anthropic's), used only for column-mapping suggestions. Without a key, the app uses rule-based suggestions instead. |

Nothing else is needed: no Redis, no object storage, no separate worker
service.

## Demo accounts

The seed script creates one account per role, using the emails and passwords
in the environment variables below. On the live demo:

| Role | Email | Password |
|---|---|---|
| Finance executive | `exec@demo.local` | TBD (given with the submission, not stored in the repo) |
| Co-founder | `cofounder@demo.local` | TBD |

## Environment variables

Copy `.env.example` to `.env` and fill it in. Never commit `.env`.

| Variable | Required | Example | Purpose |
|---|---|---|---|
| `DATABASE_URL` | Yes | `postgresql://recon:recon@localhost:5433/recon` | Postgres connection. Neon URLs need `?sslmode=require` |
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

## Deploy to production (Render + Neon)

1. **Database.** Create a Neon project with Postgres 16 in AWS Asia Pacific
   (Singapore), close to Render's Singapore region. Copy its connection
   string; it ends in `?sslmode=require`.
2. **Web service.** In Render, choose **New → Blueprint** and pick this
   repository. `render.yaml` describes the service:
   - runtime Docker, built from the `Dockerfile` at the repository root;
   - region Singapore, health check `/api/health`;
   - `SESSION_SECRET` generated by Render, `COOKIE_SECURE=true`.

   Render asks for the two values the file leaves blank: paste the Neon
   connection string as `DATABASE_URL`, and a Groq key as `GROQ_API_KEY`
   (or leave it empty for rule-based mapping suggestions only).
3. **Deploy.** The container runs `alembic upgrade head` before it starts
   serving, so the schema is created on the first deploy and kept current
   after that.
4. **Seed once.** Run this from your own machine, pointed at the Neon
   database. The demo passwords come from your `.env`:

   ```bash
   cd backend
   DATABASE_URL="<neon url>" uv run python -m scripts.seed --as-of 2026-11-06
   ```

   It loads the partner files the same way an upload does, so it also shows
   how long matching takes against Neon.
5. **Smoke test.**
   - `/api/health` returns `{"status": "ok"}`.
   - Sign in as each role.
   - Upload `sample-data/marketplace1_settlement_new_format.csv` as
     Marketplace 1 · Settlement, approve the suggested mapping, and watch it
     reach Loaded.
   - Open the three sample orders (ORD-W-204118, ORD-M-88120, ORD-W-204377)
     and check that they show the results in TDD §8.8.

**About free tiers.** Free instances go to sleep when idle, so the first
request afterwards is slow. They also have little CPU. The 1 lakh-row
benchmark in the TDD states which instance type it was measured on.

## Updating and rolling back

- **Update.** Push to the main branch. Render rebuilds the image and runs
  migrations on start.
- **Code rollback.** Redeploy an earlier commit from the Render dashboard.
  Migrations are written to work with the previous release (columns are added
  before they are used), so rolling back code does not need a database
  rollback.
- **Mapping rollback.** This is done inside the app (Mapping → Versions), not
  through a deployment.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Sign-in works locally but not in production | `COOKIE_SECURE` does not match the protocol | `true` on HTTPS, `false` on local HTTP |
| `SSL connection is required` | Neon URL is missing `sslmode` | Add `?sslmode=require` |
| Mapping shows "AI unavailable" | No key for the chosen `LLM_PROVIDER`, or the call failed | Expected without a key; otherwise check the logs |
| Upload stuck in `queued` | The worker thread is not running | Check the startup logs; restart the service |
| Large upload rejected | `MAX_UPLOAD_MB` is too low | Raise it in the environment |
