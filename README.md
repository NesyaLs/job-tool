# jobtool

A job application assistant. It collects job, internship and apprenticeship offers from
several sources, then generates a CV and a cover letter tailored to each offer from your
profile. Everything is driven from a web interface.

Core principle: **the LLM proposes, the code verifies, a loop corrects.** The model selects
and rephrases. The code enforces the hard limits: lengths, a single page, and every fact in
the CV traced back to an entry of your profile. A bounded loop sends violations back to the
model until everything complies. Nothing is made up.

> The user interface, CLI messages and design docs are in French.

## Features

- **Offer collection**: France Travail, Adzuna, SmartRecruiters and Workday. Offers are
  normalised (location, contract type), deduplicated across sources, then stored in a
  single catalogue.
- **Profile**: experiences, achievements, skills, languages and photo. An LLM-guided
  interview helps fill in the profile, and a gauge shows what is missing.
- **CV and cover letter per offer**: background generation, 7 CV templates in Typst,
  field-by-field manual editing, PDF export.
- **Web interface**: plain HTML, CSS and native JS modules, no framework and no build step.
  Search and filter offers, triage them quickly, track favourites and applications, plus
  admin screens.
- **Per-account API keys**: each user enters their own keys in the interface (see below).
- **Two modes, same code**: single-user (local SQLite, no account) or multi-user
  (Postgres/Supabase, e-mail sign-in). In multi-user mode the catalogue is shared;
  everything else belongs to each account.

## Installation

Requirements: Python ≥ 3.11, [Typst](https://github.com/typst/typst/releases) and
`poppler-utils` (`pdfinfo`, `pdftotext`), both used to render the PDFs.

```bash
sudo apt install poppler-utils           # Debian/Ubuntu; Typst: cargo install typst-cli
git clone https://github.com/NesyaLs/job-tool.git && cd job-tool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                  # editable: the web interface is served from the repo
cp .env.example .env                     # optional: server-side keys
cp data/base.example.yaml data/moi.yaml  # your profile (or fill it in from the interface)
pytest                                   # should pass, with no key and no network
jobtool api                              # http://127.0.0.1:8000
```

Run every command from the repository root: the default paths (`data/`, `output/`) are
relative to the current directory. On the first render, Typst downloads its layout
packages (network access required).

## API keys

| Service | Used for | Get a key |
|---|---|---|
| Mistral | CVs, cover letters, guided interview | [console.mistral.ai](https://console.mistral.ai) |
| France Travail | offer collection | [francetravail.io](https://francetravail.io) (Offres d'emploi v2 API) |
| Adzuna | offer collection | [developer.adzuna.com](https://developer.adzuna.com) |

SmartRecruiters and Workday need no key. No key is needed to get started: a source without
a key is simply skipped.

Two ways to provide a key:

1. **In the interface**, under *Réglages → Clés d'API* (Settings → API keys). The key is
   used only by the account that entered it and overrides the server's. It is encrypted in
   the database (Fernet) and never shown again: to change it, enter it again.
2. **In `.env`**, server-side. These keys are the fallback for every account that has not
   entered its own.

The encryption key comes from `JOBTOOL_SECRET_KEY`. In single-user mode, if it is missing,
it is generated in `data/.secret_key` (mode 600, ignored by git). In multi-user mode it is
**required**:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If you change this key, the keys already stored become unreadable: accounts must enter
them again.

## Multi-user mode (optional)

As long as these variables are absent from `.env`, the app stays in single-user mode.

```
DATABASE_URL=postgresql+psycopg://…
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_JWKS_URL=https://<project>.supabase.co/auth/v1/.well-known/jwks.json
SUPABASE_PUBLISHABLE_KEY=sb_publishable_…
JOBTOOL_SECRET_KEY=…
```

```bash
alembic upgrade head                      # the Postgres schema is managed by Alembic
python scripts/set_admin.py <e-mail>      # first admin (after their first sign-in)
jobtool api --local                       # force single-user despite a multi-user .env
```

Tokens are verified by asymmetric signature against the project's public JWKS: no shared
secret is needed. The *publishable* key is the only value sent to the browser.

## Command line

```bash
jobtool aggregate [--source adzuna] [--max-results 5]   # collect offers
jobtool cv data/offer.example.txt --template compact    # one CV -> output/
jobtool letter --offre data/offer.example.txt           # one cover letter
jobtool batch --offres data/offres_exemples/            # CV + letter for each offer in a folder
jobtool catalogue stats | doublons | dedoublonner       # catalogue maintenance
jobtool <command> --help
```

Searches and enabled sources are configured in `data/aggregator/config.yaml`.

## Architecture

```
Interface/   cli/          interfaces (thin)
api/                       HTTP: routing, identity, injection (FastAPI)
services/                  use cases
aggregator/  documents/    domains: offer collection; CVs and letters
core/                      LLM, Typst rendering, profile, SQL database, API keys
```

Dependencies only point downwards. Services never print. Secrets go through
`settings.py`: never hard-coded, never logged. The account identity arrives through
`Depends`. For the reasoning behind the design, see `project_ctx.md` and `docs/`.

## Development

```bash
pytest          # no network, no key: connectors and the LLM are mocked
ruff check .
mypy src
```

Personal data: `data/moi.yaml` (your profile), `.env`, `data/.secret_key` and the local
databases are ignored by git. The tests use a fictional profile
(`tests/fixtures/profil_demo.yaml`).

## Authors

Built by **Arthur Dufetrelle** and **Théophane Loloum**.

## License

[MIT](LICENSE) © 2026 Arthur Dufetrelle
