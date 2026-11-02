# Deployment

How to run Settle on your own machine and how to deploy it.

> **Draft.** The commands below describe the planned setup. Each one will be
> checked against the real code before submission, and this note will then be
> removed.

## What gets deployed

| Piece | What it is |
|---|---|
| App | One Docker container. FastAPI serves the API under `/api` and the built React app on every other path. A background worker thread in the same process handles uploads. |
| Database | PostgreSQL 16. Neon in production; Docker Compose locally. |
| AI (optional) | The Claude API, used only for column-mapping suggestions. Without a key, the app uses rule-based suggestions instead. |

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
| `DATABASE_URL` | Yes | `postgresql://recon:recon@localhost:5432/recon` | Postgres connection. Neon URLs need `?sslmode=require` |
| `SESSION_SECRET` | Yes | 64 random hex characters | Signs session and CSRF tokens. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `COOKIE_SECURE` | Yes in production | `true` | Sends cookies only over HTTPS. Set `false` for local HTTP |
| `ANTHROPIC_API_KEY` | No | `sk-ant-…` | Turns on AI mapping suggestions |
| `LLM_MODEL` | No | `claude-opus-5-5` | Model used for mapping suggestions |
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
docker compose up --build       # starts Postgres and the app on http://localhost:8000
docker compose exec app python -m scripts.seed
```

### Option B: development mode with hot reload

```bash
cp .env.example .env
docker compose up -d db                       # Postgres only

cd backend
uv sync
uv run alembic upgrade head                   # create the schema
uv run python -m scripts.seed                 # demo users, partners, rate cards, one month of orders
uv run uvicorn app.main:app --reload          # API on http://localhost:8000

cd ../frontend
npm ci
npm run dev                                   # UI on http://localhost:5173, calls the API through a proxy
```

### Sample files

`sample-data/` holds small synthetic partner files to upload in the demo. It
includes one file in a changed format, to show the AI mapping flow. To make
larger files:

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

Tests that call the real Claude API run only when `ANTHROPIC_API_KEY` is set.
Without it, those tests use a stubbed response.

## Deploy to production (Render + Neon)

1. **Database.** Create a Neon project and copy its connection string,
   including `?sslmode=require`.
2. **Web service.** In Render, create a new Web Service from this repository:
   - runtime: Docker;
   - Dockerfile: the one at the repository root;
   - health check path: `/api/health`.
3. **Environment.** In the service settings, set `DATABASE_URL`,
   `SESSION_SECRET` and `COOKIE_SECURE=true`. Add `ANTHROPIC_API_KEY` too if
   you want AI suggestions.
4. **Deploy.** The container runs `alembic upgrade head` before it starts
   serving, so the schema is always current.
5. **Seed once.** Run this from your own machine, pointed at the Neon
   database:

   ```bash
   cd backend
   DATABASE_URL="<neon url>" SEED_EXEC_PASSWORD=… SEED_COFOUNDER_PASSWORD=… uv run python -m scripts.seed
   ```

6. **Smoke test.**
   - `/api/health` returns `{"status": "ok"}`.
   - Sign in as each role.
   - Upload a sample file and watch it reach `done`.
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
| Mapping shows "AI unavailable" | No `ANTHROPIC_API_KEY`, or the call failed | Expected without a key; otherwise check the logs |
| Upload stuck in `queued` | The worker thread is not running | Check the startup logs; restart the service |
| Large upload rejected | `MAX_UPLOAD_MB` is too low | Raise it in the environment |
