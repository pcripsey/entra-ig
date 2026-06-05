# entra-ig

Production-ready Microsoft Entra ID export service for OpenText Identity Governance CSV collection.

## What it does

- Connects to Microsoft Graph using the official Python `msgraph-sdk`
- Authenticates with client credentials via `azure.identity.aio.ClientSecretCredential`
- Exports `users.csv`, `groups.csv`, and `memberships.csv` with deterministic column ordering
- Sanitizes carriage returns and line feeds so OpenText CSV ingestion is not broken by multiline values
- Replaces null Entra attributes with empty strings
- Handles large tenant pagination with the native Microsoft Graph `PageIterator`
- Retries Graph throttling responses (`HTTP 429`) with exponential backoff
- Provides a React-based admin console for configuration visibility, connection monitoring, sync execution, and log review
- Includes connection test controls so admins can validate tenant/client/scope values from the UI before running exports
- Supports a configurable automatic refresh schedule managed from the admin console
- Ships as a Docker container with a multi-stage build

## CSV output

Each sync writes timestamped collector files under `data/exports/<run_id>/` and also refreshes `data/exports/latest/`.

### users.csv

Columns:
`id,userPrincipalName,displayName,mail,jobTitle,department,accountEnabled`

### groups.csv

Columns:
`id,displayName,description,securityEnabled,mailEnabled`

### memberships.csv

Columns:
`group_id,user_id`

## Environment variables

Copy `.env.example` to `.env` and set:

- `TENANT_ID`
- `CLIENT_ID`
- `CLIENT_SECRET`
- `GRAPH_SCOPE`
- `GRAPH_PAGE_SIZE`
- `MAX_RETRY_ATTEMPTS`
- `MAX_RETRY_DELAY_SECONDS`
- `MEMBERSHIP_CONCURRENCY`
- `SCHEDULE_ENABLED`
- `SCHEDULE_INTERVAL_MINUTES`
- `EXPORT_BASE_DIR`
- `DATABASE_PATH`
- `LOG_FILE_PATH`
- `FRONTEND_DIST`
- `LOG_LEVEL`

## Local development

### Backend

```bash
python -m pip install -e .[dev]
python -m uvicorn app.main:app --app-dir backend --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Use `VITE_API_BASE_URL=http://localhost:8000/api` when running the frontend separately.

## Validation

```bash
python -m pytest
python -m compileall backend/app
cd frontend && npm run lint && npm run build
```

## Docker

### Build

```bash
docker build -t entra-ig .
```

### Run

```bash
docker run --rm -p 8000:8000 --env-file .env -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs entra-ig
```

### Docker Compose

```bash
docker compose up --build
```

The admin console is served by FastAPI at `http://localhost:8000/`, and the API is served under `/api`.
