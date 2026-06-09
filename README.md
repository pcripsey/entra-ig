# entra-ig

Production-ready Microsoft Entra ID export service for OpenText Identity Governance CSV collection.

![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178C6?logo=typescript&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-multi--stage-2496ED?logo=docker&logoColor=white)

## What it does

- Connects to Microsoft Graph using the official Python `msgraph-sdk`
- Authenticates with client credentials via `azure.identity.aio.ClientSecretCredential`
- Exports `users.csv`, `groups.csv`, `memberships.csv`, `roles.csv`, and `role_memberships.csv` with deterministic column ordering
- Sanitizes carriage returns and line feeds so OpenText CSV ingestion is not broken by multiline values
- Replaces null Entra attributes with empty strings
- Handles large tenant pagination with the native Microsoft Graph `PageIterator`
- Retries Graph throttling responses (`HTTP 429`) with exponential backoff
- Supports **full sync** (re-fetch everything) and **incremental sync** (Graph delta queries, changes only)
- Provides a React-based admin console for configuration visibility, connection monitoring, sync execution, and log review
- Includes connection test controls so admins can validate tenant/client/scope values from the UI before running exports
- Supports a configurable automatic refresh schedule with selectable sync type managed from the admin console
- Ships as a Docker container with a multi-stage build

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Graph API client | `msgraph-sdk`, `azure-identity` |
| Database | SQLite via `aiosqlite` |
| Configuration | `pydantic-settings` |
| Frontend | React 19, TypeScript ~6.0, Vite 8 |
| Container | Docker multi-stage (Node 24 → Python 3.12-slim) |
| Registry | GitHub Container Registry (GHCR) |

## Quick start

### Pre-built image (recommended)

```bash
cp .env.example .env
# Edit .env with your Entra ID credentials
docker compose up
```

Open `http://localhost:8000` to access the admin console.

### Bare-metal

```bash
# Backend
python -m pip install -e .[dev]
python -m uvicorn app.main:app --app-dir backend --reload

# Frontend (separate terminal)
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000/api npm run dev
```

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
│  ┌────────────────────────┐   ┌──────────────────────────────┐  │
│  │   start(sync_type)     │   │      _scheduler_loop         │  │
│  │   creates DB run row   │   │  fires on interval, passes   │  │
│  │   spawns async task    │   │  schedule_sync_type to start()│  │
│  └──────────┬─────────────┘   └──────────────────────────────┘  │
└─────────────┼───────────────────────────────────────────────────┘
              │  export(run_id, sync_type, run_store)
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  GraphExportService                             │
│                                                                 │
│   sync_type == "full"            sync_type == "incremental"     │
│   ┌──────────────────────┐       ┌───────────────────────────┐  │
│   │ GET /users           │       │ no stored delta tokens?   │  │
│   │ GET /groups          │       │  └─► fall back to full    │  │
│   │ GET /groups/*/members│       │ GET /users/delta?token    │  │
│   │ write CSVs           │       │ GET /groups/delta?token   │  │
│   │ store delta tokens   │       │ load latest/ CSVs         │  │
│   └──────────────────────┘       │ merge adds/updates/deletes│  │
│                                  │ GET /groups/*/members(all)│  │
│                                  │ write CSVs                │  │
│                                  │ store new delta tokens    │  │
│                                  └───────────────────────────┘  │
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
│  <run_id>/roles.csv                                             │
│  <run_id>/role_memberships.csv                                  │
│  latest/users.csv      ◄── always reflects most recent run     │
│  latest/groups.csv                                              │
│  latest/memberships.csv                                         │
│  latest/roles.csv                                               │
│  latest/role_memberships.csv                                    │
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

## Entra ID attributes

The following Microsoft Graph attributes are requested on each sync. Null values are replaced with empty strings in all CSV output.

### Users (`/users` — `$select` + `$expand=manager($select=id)`)

| Attribute | Description |
|---|---|
| `id` | Object ID (GUID) |
| `userPrincipalName` | UPN / primary login name |
| `displayName` | Full display name |
| `mail` | Primary SMTP address |
| `jobTitle` | Job title |
| `department` | Department |
| `accountEnabled` | Account enabled state (`true` / `false`) |
| `givenName` | First name |
| `surname` | Last name |
| `mailNickname` | Exchange alias |
| `employeeId` | Employee ID |
| `employeeType` | Employee type classification |
| `companyName` | Company name |
| `streetAddress` | Street address |
| `officeLocation` | Office / building location |
| `businessPhones` | List of business phone numbers; first entry is used |
| `mobilePhone` | Mobile phone number |
| `preferredLanguage` | Preferred language / locale |
| `country` | Country |
| `city` | City |
| `state` | State / province |
| `onPremisesDistinguishedName` | On-premises Active Directory distinguished name |
| `onPremisesImmutableId` | On-premises immutable ID (objectGUID base64) |
| `userType` | `Member` or `Guest` |
| `otherMails` | Secondary SMTP addresses (`;`-joined in CSV) |
| `onPremisesExtensionAttributes` | Extension attributes object; `extensionAttribute1` is read as `sapId` |
| `manager.id` | Manager's object ID (via `$expand=manager($select=id)`) |

### Groups (`/groups` — `$select`)

| Attribute | Description |
|---|---|
| `id` | Object ID (GUID) |
| `displayName` | Group display name |
| `description` | Group description |
| `securityEnabled` | Security group flag (`true` / `false`) |
| `mailEnabled` | Mail-enabled flag (`true` / `false`) |
| `mailNickname` | Exchange alias |
| `onPremisesObjectIdentifier` | On-premises AD objectGUID |
| `onPremisesDistinguishedName` | On-premises Active Directory distinguished name |

### Group members and owners (`/groups/{id}/members`, `/groups/{id}/owners`)

Only `id` is selected from each member / owner object. Only `#microsoft.graph.user` objects contribute to user membership and owner lists. `#microsoft.graph.group` objects are captured separately to populate the nested-group parent-child relationship file.

### Directory Roles (`/directoryRoles` — `$select`)

| Attribute | Description |
|---|---|
| `id` | Activated role object ID |
| `roleTemplateId` | Role template ID |
| `displayName` | Role display name |
| `description` | Role description |

### Role members (`/directoryRoles/{id}/members`)

Only `id` is selected. Only `#microsoft.graph.user` objects are retained.

## CSV output

Each sync writes timestamped collector files under `data/exports/<run_id>/` and also refreshes `data/exports/latest/`.

### users.csv

Raw Entra user attributes — one row per user.

Columns:
`id,userPrincipalName,displayName,mail,jobTitle,department,accountEnabled,givenName,surname,mailNickname,employeeId,employeeType,companyName,streetAddress,officeLocation,businessPhone,mobilePhone,preferredLanguage,country,city,state,onPremisesDistinguishedName,onPremisesImmutableId,userType,otherMails,sapId,managerId`

| Column | Entra ID source |
|---|---|
| `id` | `id` |
| `userPrincipalName` | `userPrincipalName` |
| `displayName` | `displayName` |
| `mail` | `mail` |
| `jobTitle` | `jobTitle` |
| `department` | `department` |
| `accountEnabled` | `accountEnabled` |
| `givenName` | `givenName` |
| `surname` | `surname` |
| `mailNickname` | `mailNickname` |
| `employeeId` | `employeeId` |
| `employeeType` | `employeeType` |
| `companyName` | `companyName` |
| `streetAddress` | `streetAddress` |
| `officeLocation` | `officeLocation` |
| `businessPhone` | `businessPhones[0]` (first entry) |
| `mobilePhone` | `mobilePhone` |
| `preferredLanguage` | `preferredLanguage` |
| `country` | `country` |
| `city` | `city` |
| `state` | `state` |
| `onPremisesDistinguishedName` | `onPremisesDistinguishedName` |
| `onPremisesImmutableId` | `onPremisesImmutableId` |
| `userType` | `userType` |
| `otherMails` | `otherMails` (`;`-joined) |
| `sapId` | `onPremisesExtensionAttributes.extensionAttribute1` |
| `managerId` | `manager.id` (expanded) |

### groups.csv

Raw Entra group attributes — one row per group.

Columns:
`id,displayName,description,securityEnabled,mailEnabled,mailNickname,onPremisesObjectIdentifier,onPremisesDistinguishedName`

| Column | Entra ID source |
|---|---|
| `id` | `id` |
| `displayName` | `displayName` |
| `description` | `description` |
| `securityEnabled` | `securityEnabled` |
| `mailEnabled` | `mailEnabled` |
| `mailNickname` | `mailNickname` |
| `onPremisesObjectIdentifier` | `onPremisesObjectIdentifier` |
| `onPremisesDistinguishedName` | `onPremisesDistinguishedName` |

### memberships.csv

Columns:
`group_id,user_id`

One row per user–group membership. Only `#microsoft.graph.user` members are included; service principals and devices are excluded.

### roles.csv

Columns:
`id,roleTemplateId,displayName,description`

Rows represent activated directory roles in the tenant (roles that have been assigned to at least one principal).

### role_memberships.csv

Columns:
`role_id,user_id`

> [!NOTE]
> The app registration used with `GRAPH_SCOPE=https://graph.microsoft.com/.default` must include `RoleManagement.Read.Directory` in addition to `User.Read.All` and `Group.Read.All`.

### IG collection files

The following OpenText Identity Governance collection CSV files are derived from the raw Entra data on every sync run.

#### Identity.csv

| Column | Entra ID source |
|---|---|
| `identityId` | `id` |
| `employeeNumber` | `employeeId` |
| `company` | `companyName` |
| `street` | `streetAddress` |
| `cn` | `displayName` |
| `AzureUserID` | `id` |
| `AzureMailNickname` | `mailNickname` |
| `hrEmpNumber` | `employeeId` |
| `department` | `department` |
| `ldapDN` | `onPremisesDistinguishedName` |
| `email` | `mail` |
| `employeeType` | `employeeType` |
| `firstName` | `givenName` |
| `fullName` | `displayName` |
| `objectGUID` | `onPremisesImmutableId` |
| `phoneHome` | *(empty)* |
| `jobCode` | `jobTitle` |
| `lastName` | `surname` |
| `location` | `officeLocation` |
| `middleName` | *(empty)* |
| `phoneOffice` | `businessPhones[0]` |
| `phoneMobile` | `mobilePhone` |
| `preferredLocale` | `preferredLanguage` |
| `provisioningID` | `userPrincipalName` |
| `secondarySupervisorId` | *(empty)* |
| `primarySupervisorId` | `manager.id` |
| `affiliatedIdentity` | *(empty)* |
| `employeeStatus` | `accountEnabled` → `active` / `inactive` |
| `country` | `country` |
| `city` | `city` |
| `state` | `state` |
| `geoLocation` | *(empty)* |
| `userRisk` | *(empty)* |
| `workforceID` | `employeeId` |
| `idmDN` | *(empty)* |
| `idmTreeName` | *(empty)* |
| `loginAttribute` | `userPrincipalName` |
| `title` | `jobTitle` |

#### ig_account_import.csv

| Column | Entra ID source |
|---|---|
| `accountId` | `id` |
| `displayName` | `displayName` |
| `description` | *(empty)* |
| `type` | `userType` (default: `Member`) |
| `risk` | *(empty)* |
| `cost` | *(empty)* |
| `SAP_ID` | `onPremisesExtensionAttributes.extensionAttribute1` |
| `aliases` | `otherMails` (`;`-joined) |
| `connectedAccountProvisioningID` | *(empty)* |
| `disabled` | `accountEnabled` inverted → `true` / `false` |
| `privileged` | derived: `true` if user holds any directory role |
| `state` | `accountEnabled` → `active` / `disabled` |
| `accountProvisioningID` | `userPrincipalName` |
| `accountUserMapping` | `id` |
| `accountCustodianMapping` | `manager.id` |
| `idmAccountID` | *(empty)* |
| `provisioningDriverID` | *(empty)* |
| `provisioningDriverLogicalID` | *(empty)* |

#### ig_group_import.csv

| Column | Entra ID source |
|---|---|
| `groupId` | group `id` |
| `groupOwners` | `/groups/{id}/owners` — user IDs (`;`-joined) |
| `objectGUID` | `onPremisesObjectIdentifier` |
| `groupMembers` | `/groups/{id}/members` — user IDs (`;`-joined) |
| `name` | `displayName` |
| `longDescription` | `description` |
| `ldapDN` | `onPremisesDistinguishedName` |
| `alternateName` | `mailNickname` |
| `shortDescription` | `description` (truncated to 255 chars) |

#### ig_group_to_user_membership.csv

Columns: `groupId,members`

One row per user–group membership pairing (flat join of group ID → user ID).

#### ig_parent_group_to_child_group.csv

Columns: `parentId,childId`

One row per nested group relationship detected in `/groups/{id}/members`.

#### ig_permission_import.csv

| Column | Entra ID source |
|---|---|
| `permissionId` | role `id` |
| `displayName` | role `displayName` |
| `description` | role `description` |
| `type` | `DirectoryRole` (hardcoded) |
| `assignable` | `true` (hardcoded) |
| `owner` | *(empty)* |
| `risk` | *(empty)* |
| `cost` | *(empty)* |
| `holder` | count of role members |
| `childPermissionId` | *(empty)* |
| `parentPermissionId` | *(empty)* |
| `hiddenFromCatalog` | `false` (hardcoded) |
| `provisioningTargetAttribute` | `roleTemplateId` (hardcoded) |
| `provisionedByThisPermission` | `false` (hardcoded) |
| `nativeValueForProvisioning` | role `roleTemplateId` |
| `uniqueApplicationID` | *(empty)* |
| `staticPermissionFlag` | `false` (hardcoded) |
| `provisioningDriverID` | *(empty)* |
| `provisioningApplicationLogicalID` | *(empty)* |

#### ig_holder_to_permissions_mapping.csv

Columns: `assignmentId,accountId,permissionId,usage,risk,revocable,assignmentType,assignmentValue`

One row per role membership. `assignmentId` = `{user_id}_{role_id}`, `assignmentType` = `DIRECT`.

#### ig_permission_to_holders_mapping.csv

Columns: `permissionId,accountId,assignmentId,assignmentType,assignmentRisk,usage,revocable,assignmentValue`

Inverse view of `ig_holder_to_permissions_mapping.csv`.

#### ig_permission_hierarchy_child_parent.csv / ig_permission_hierarchy_parent_child.csv

Columns: `permissionId,parentPermissionId,assignmentType` / `permissionId,childPermissionId,assignmentType`

Reserved for permission hierarchies; always written empty (no role hierarchy modelled in Entra).

## API reference

All endpoints are served under `/api`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check; reports Graph reachability and latest run status |
| `GET` | `/api/config` | Active configuration (secrets masked) |
| `POST` | `/api/connection/test` | Test Graph connectivity with supplied credentials |
| `GET` | `/api/status` | Sync status, live progress counters, and schedule state |
| `POST` | `/api/sync` | Start a sync (`{"sync_type":"full"}` or `{"sync_type":"incremental"}`) |
| `GET` | `/api/runs` | List the 20 most recent sync runs |
| `GET` | `/api/runs/{run_id}` | Get a specific sync run |
| `DELETE` | `/api/runs/{run_id}` | Delete a completed run and its export files |
| `GET` | `/api/logs` | Tail the application log (`?lines=N`, default 100) |
| `GET` | `/api/schedule` | Get the automatic refresh schedule |
| `PUT` | `/api/schedule` | Update the schedule (`enabled`, `interval_minutes`, `sync_type`) |
| `GET` | `/api/retry-config` | Get Graph retry settings |
| `PUT` | `/api/retry-config` | Update `max_retry_attempts` and `max_retry_delay_seconds` |

## Environment variables

Copy `.env.example` to `.env` and set:

| Variable | Default | Description |
|---|---|---|
| `TENANT_ID` | _(required)_ | Azure AD / Entra tenant ID |
| `CLIENT_ID` | _(required)_ | App registration client ID |
| `CLIENT_SECRET` | _(required)_ | App registration client secret |
| `GRAPH_SCOPE` | `https://graph.microsoft.com/.default` | OAuth2 scope for Microsoft Graph |
| `GRAPH_PAGE_SIZE` | `999` | Page size for Graph list requests (max 999) |
| `MAX_RETRY_ATTEMPTS` | `5` | Maximum retries for throttled (`HTTP 429`) Graph requests |
| `MAX_RETRY_DELAY_SECONDS` | `32` | Maximum backoff delay per retry (seconds) |
| `MEMBERSHIP_CONCURRENCY` | `4` | Number of concurrent group membership fetches |
| `SCHEDULE_ENABLED` | `false` | Enable automatic refresh schedule on startup |
| `SCHEDULE_INTERVAL_MINUTES` | `60` | Interval between scheduled syncs (minutes) |
| `EXPORT_BASE_DIR` | `data/exports` | Root directory for CSV output |
| `DATABASE_PATH` | `data/app.db` | SQLite database file path |
| `LOG_FILE_PATH` | `logs/app.log` | Application log file path |
| `FRONTEND_DIST` | `frontend/dist` | Path to compiled React assets served by FastAPI |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Project structure

```
entra-ig/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes.py          # FastAPI route handlers
│   │   ├── services/
│   │   │   ├── graph_exporter.py  # Microsoft Graph export and delta logic
│   │   │   ├── sync_service.py    # Sync orchestration and auto-scheduler
│   │   │   └── sanitizer.py       # CSV value sanitization (CR/LF stripping)
│   │   ├── config.py              # pydantic-settings configuration
│   │   ├── database.py            # SQLite RunStore via aiosqlite
│   │   ├── logging_config.py      # Logging setup
│   │   ├── models.py              # Pydantic request/response models
│   │   └── main.py                # FastAPI application entry point
│   └── tests/
│       ├── test_graph_exporter.py
│       └── test_sanitizer.py
├── frontend/
│   └── src/
│       ├── App.tsx                # Single-page admin console (React)
│       └── main.tsx               # React entry point
├── .env.example
├── Dockerfile                     # Multi-stage build (Node 24 + Python 3.12-slim)
├── docker-compose.yml
└── pyproject.toml
```

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

## CI/CD

A GitHub Actions workflow (`.github/workflows/docker.yml`) builds and publishes the Docker image to GHCR on every push to `main`. The image is tagged `latest`.
