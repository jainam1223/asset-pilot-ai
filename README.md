# AssetPilot AI

A FastAPI service that answers plain-English IT asset questions by turning
them into safe, read-only SQL against Postgres and replying in Markdown.
No UI here — this is the backend an admin or employee chatbot talks to.

## How a request flows

```
POST /chat or /chat/employee
        │
        ▼
 role fixed by which endpoint was called (never trusted from the request body)
        │
        ▼
 LLM #1: question + role-scoped DB schema  →  one SELECT statement, or a refusal
        │
        ▼
 code-level safety check: SELECT-only, correct tables for this role
        │
        ▼
 run the SELECT against pooled, read-only Postgres
        │
        ▼
 LLM #2: raw rows  →  human-readable Markdown answer
        │
        ▼
 {"answer": "...", "refused": false}
```

Every failure at any step (bad question, DB down, LLM outage, out-of-scope
request) turns into a fixed, human-facing message — never a raw error,
SQL fragment, or table name.

## Roles

- **IT admin** (`/chat`) — full access to all 8 tables.
- **Employee / manager** (`/chat/employee`) — devices and categories only.
  Specific lookups ("is X available", "who has Y") are fine; inventory-wide
  counts and reports are admin-only.

There's no login in this build — the endpoint you call *is* the role. A
request can't claim a different role for itself.

## Stack

| Library | Why |
|---|---|
| **FastAPI** | the HTTP layer — async-native, so LLM calls and DB queries never block the server |
| **psycopg[binary] + psycopg_pool** | async Postgres driver + a connection pool, so requests reuse warm connections instead of paying a fresh handshake each time |
| **openai / groq / cerebras SDKs** | LLM providers — Azure OpenAI is primary, the others are automatic fallbacks if it's rate-limited or down |
| **pydantic** | validates the request body and rejects malformed input before it reaches any LLM or DB call |
| **loguru** | structured logging for debugging without ever putting internals in the API response |
| **python-dotenv** | loads `.env` locally; in Azure, app settings play the same role |

## Repo layout

```
ai_service/       core logic — DB, LLM providers, prompts, error messages, role scoping
app/               the FastAPI app itself — routes, request/response schemas
Dockerfile         container build for deployment
terraform/         Azure infra (App Service, pulls from ACR)
.github/workflows/ CI: build → push to ACR → deploy to Azure on every push to main
```

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in POSTGRES_URL + at least one LLM provider key
uvicorn app.main:app --reload --port 8000
```

Then: `POST http://localhost:8000/chat` or `/chat/employee` with
`{"query": "how many laptops are available?"}`.

## Deploy

Push to `main` — GitHub Actions (`.github/workflows/deploy.yml`) builds the
Docker image, pushes it to Azure Container Registry, and deploys it to the
Azure Web App. Infra itself (the App Service, its plan, ACR wiring) is
defined in `terraform/` and provisioned separately from the app deploy.

Required secrets in GitHub: `AZURE_CREDENTIALS`, `ACR_USERNAME`,
`ACR_PASSWORD`. Required app settings on the Azure Web App: everything in
`.env.example`.