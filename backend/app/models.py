from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ConfigResponse(BaseModel):
    tenant_id_present: bool
    client_id_present: bool
    client_secret_present: bool
    masked_client_secret: str
    graph_scope: str
    export_base_dir: str
    database_path: str
    log_file_path: str
    frontend_dist: str


class HealthResponse(BaseModel):
    status: Literal['ok', 'degraded']
    graph_configured: bool
    graph_reachable: bool
    detail: str
    latest_run_status: str | None = None


class SyncRunResponse(BaseModel):
    id: str
    status: str
    started_at: str
    completed_at: str | None = None
    users_count: int | None = None
    groups_count: int | None = None
    memberships_count: int | None = None
    users_file: str | None = None
    groups_file: str | None = None
    memberships_file: str | None = None
    error: str | None = None


class SyncStatusResponse(BaseModel):
    active_run_id: str | None
    running: bool
    latest_run: SyncRunResponse | None


class SyncStartResponse(BaseModel):
    run_id: str
    status: str


class LogResponse(BaseModel):
    lines: list[str]
