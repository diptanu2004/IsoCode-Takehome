# Settle

Partner payment reconciliation for a D2C brand that sells on its own website,
marketplaces and quick-commerce apps.

Upload every partner's reports and see, for each order, whether the brand was
paid the right amount. Anything short, late or overcharged becomes a claim
with evidence attached.

- **Repository:** https://github.com/diptanu2004/IsoCode-Takehome
- **Live app:** TODO
- **Design document:** [docs/TDD.md](docs/TDD.md)
- **Run and deploy:** [deployment.md](deployment.md)
- **Client brief:** [docs/brief.md](docs/brief.md)

## What it does

- **Upload partner files.** Orders, settlements, COD remittances, freight
  invoices and warehouse return scans, as CSV or Excel. A column mapping is
  saved for each source.
- **Suggest mappings with AI.** When a partner changes its file format, the
  app proposes a new mapping. You check it on a 20-row preview and approve
  it. Every version is kept and can be rolled back.
- **Compute expected amounts.** Each order's expected payment comes from
  versioned rate cards for each channel and courier.
- **Match every order.** Each order gets a status: settled, short-paid, unpaid
  past due, freight overcharged or return not received. Statutory deductions
  (TCS and TDS) are kept separate and never counted as leakage.
- **Track claims.** Claims are grouped by partner and can be exported with
  their evidence. A money-at-risk view breaks them down by partner and age.
- **Two roles.** Finance executive and co-founder, with permissions enforced
  on the server.
- **Fast.** A 1 lakh-row file is processed in under a minute.

## Build status

- [x] Scope, design document and deployment guide
- [x] Schema, sign-in and roles (server-side sessions, CSRF, lockout, permission matrix tests)
- [x] Seed and sample-data generator (60k orders, 1.5% planted leakage, answer key)
- [x] Frontend shell: sign-in, role-aware navigation
- [x] Upload pipeline and Uploads screen (bulk load, duplicate and overlap safety, rejected-row download)
- [x] Column mapping: rules + AI suggestions, 20-row preview, approval, versions, rollback
- [x] Matching engine: expected amounts from rate cards, statuses, automatic claims; 3,042 of 3,042 planted problems found; 1 lakh rows in about 40 s
- [x] Reconciliation and Claims screens: order detail with source rows, claim lifecycle, evidence ZIP, raise all
- [x] Rate cards: versions in plain words, new versions (co-founder) that re-check the affected orders, a flag when a rate seems to have changed
- [x] Settings: partner terms (re-check on change), people and roles, the audit log
- [x] Docker image, Render blueprint, demo files
- [x] Overview: money at risk by partner and age (cells open the claims), recovered vs ₹30 L, auto-match rate vs 98%, within tolerance, TCS/TDS
- [ ] Deployed

## Stack

| Layer | Tools |
|---|---|
| Frontend | React, TypeScript, Vite, TanStack Query, React Router, Tailwind CSS |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy Core, psycopg 3, Alembic, Polars |
| Database | PostgreSQL 16 |
| AI | Groq (`openai/gpt-oss-120b`, strict structured outputs), with a rule-based fallback; Anthropic as an option |
| Hosting | One Docker container on Render, with Neon Postgres |

The reasons for each choice are in [TDD §5](docs/TDD.md#5-architecture).

## Repository layout

```
backend/        FastAPI app, migrations, scripts (seed, data generator, benchmark), tests
frontend/       React app
sample-data/    small synthetic partner files for the demo
docs/           design document and client brief
deployment.md   how to run, test and deploy
```

## Data

All data in this repository is synthetic. Partners are named generically
(Marketplace 1–3, Quick-commerce 1–2, Courier A and B). Customer names, phone
numbers and addresses are never stored. See
[TDD §12.2](docs/TDD.md#122-personal-data-dpdp-act-2023).
