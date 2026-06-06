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
- Supports **full sync** (re-fetch everything) and **incremental sync** (Graph delta queries, changes only)
- Provides a React-based admin console for configuration visibility, connection monitoring, sync execution, and log review
- Includes connection test controls so admins can validate tenant/client/scope values from the UI before running exports
- Supports a configurable automatic refresh schedule with selectable sync type managed from the admin console
- Ships as a Docker container with a multi-stage build

## Architecture and flow

### Component overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Admin console (React)                    │
│  ┌──────────────┐  ┌──────────────────────┐  ┌──────────────┐  │
│  │  Run export  │  │   Refresh schedule   │  │   Run log /  │  │
│  │  [Full|Incr] │  │ [Full|Incr] interval │  │   history    │  │
│  └──────┬───────┘  └──────────┬───────────┘  └──────────────┘  │
└─────────┼────────────────────┼─────────────────────────────────┘
          │  POST /api/sync     │  PUT /api/schedule
          ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI  (routes.py)                         │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SyncService                                  │
│  ┌────────────────────────┐   ┌───────────────────────────────┐ │
│  │   start(sync_type)     │   │      _scheduler_loop          │ │
│  │   creates DB run row   │   │  fires on interval, passes    │ │
│  │   spawns async task    │   │  schedule_sync_type to start()│ │
│  └──────────┬─────────────┘   └───────────────────────────────┘ │
└─────────────┼───────────────────────────────────────────────────┘
              │  export(run_id, sync_type, run_store)
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  GraphExportService                             │
│                                                                 │
│   sync_type == "full"            sync_type == "incremental"     │
│   ┌──────────────────────┐       ┌────────────────────────────┐ │
│   │ GET /users           │       │ no stored delta tokens?    │ │
│   │ GET /groups          │       │  └─► fall back to full     │ │
│   │ GET /groups/*/members│       │ GET /users/delta?token     │ │
│   │ write CSVs           │       │ GET /groups/delta?token    │ │
│   │ store delta tokens   │       │ load latest/ CSVs          │ │
│   └──────────────────────┘       │ merge adds/updates/deletes │ │
│                                  │ GET /groups/*/members(all) │ │
│                                  │ write CSVs                 │ │
│                                  │ store new delta tokens     │ │
│                                  └────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│              SQLite  (RunStore / database.py)                   │
│  sync_runs     schedule_config     delta_tokens                 │
│  id            enabled             resource (users|groups)      │
│  status        interval_minutes    token  (delta link URL)      │
│  sync_type     sync_type           updated_at                   │
│  started_at    updated_at                                       │
│  completed_at                                                   │
│  users_count                                                    │
│  …                                                              │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│               File system  (data/exports/)                      │
│  <run_id>/users.csv                                             │
│  <run_id>/groups.csv                                            │
│  <run_id>/memberships.csv                                       │
│  latest/users.csv      ◄── always reflects most recent run     │
│  latest/groups.csv                                              │
│  latest/memberships.csv                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Sync type decision flow

```
User/scheduler triggers sync
          │
          ▼
    sync_type == "incremental"?
    ┌── yes ──────────────────────────────────────────────────────┐
    │  delta tokens stored?  ──no──► fall back to full sync       │
    │         │ yes                                               │
    │         ▼                                                   │
    │  latest/ CSVs exist?   ──no──► fall back to full sync       │
    │         │ yes                                               │
    │         ▼                                                   │
    │  fetch /users/delta (token)  ──410 expired──► full sync     │
    │  fetch /groups/delta (token)                                │
    │         │                                                   │
    │         ▼                                                   │
    │  load latest/users.csv + latest/groups.csv                  │
    │  apply adds / updates / deletes from delta responses        │
    │         │                                                   │
    │         ▼                                                   │
    │  fetch all group memberships for current group set          │
    │  write <run_id>/ CSVs, refresh latest/, store new tokens    │
    └─────────────────────────────────────────────────────────────┘
    └── no ───────────────────────────────────────────────────────┐
    │  fetch all users, groups, memberships from Graph            │
    │  write <run_id>/ CSVs, refresh latest/                      │
    │  store delta tokens for future incremental syncs            │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    update sync_runs row (completed / failed)
```

### Incremental sync behavior

| Scenario | Behaviour |
|---|---|
| First ever sync | Full sync (no baseline) |
| Incremental with valid token | Delta fetch + merge; memberships re-fetched for accuracy |
| Delta token expired (HTTP 410) | Automatic fall-back to full sync; new tokens stored |
| Incremental with missing `latest/` | Automatic fall-back to full sync |

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

Pre-built images are published to the GitHub Container Registry on every push to `main`:

```
ghcr.io/pcripsey/entra-ig:latest
```

### Run (pre-built image)

```bash
docker run --rm -p 8000:8000 --env-file .env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  ghcr.io/pcripsey/entra-ig:latest
```

### Docker Compose

```bash
docker compose up
```

The admin console is served by FastAPI at `http://localhost:8000/`, and the API is served under `/api`.

### Unraid

1. In the Unraid UI go to **Docker → Add Container**.
2. Set **Repository** to `ghcr.io/pcripsey/entra-ig:latest`.
3. Add the following **Port Mapping**: Container port `8000` → Host port `8000`.
4. Add two **Path** mappings:
   - Container path `/app/data` → Host path `/mnt/user/appdata/entra-ig/data`
   - Container path `/app/logs` → Host path `/mnt/user/appdata/entra-ig/logs`
5. Add each environment variable from `.env.example` as a **Variable** entry.
6. Click **Apply**. Unraid will pull the image and start the container without building anything locally.

### Build locally (development)

```bash
docker build -t entra-ig .
docker run --rm -p 8000:8000 --env-file .env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  entra-ig
```
