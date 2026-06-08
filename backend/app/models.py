from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConfigResponse(BaseModel):
    tenant_id: str
    client_id: str
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
    sync_type: str = 'full'
    started_at: str
    completed_at: str | None = None
    users_count: int | None = None
    groups_count: int | None = None
    memberships_count: int | None = None
    roles_count: int | None = None
    role_memberships_count: int | None = None
    users_file: str | None = None
    groups_file: str | None = None
    memberships_file: str | None = None
    roles_file: str | None = None
    role_memberships_file: str | None = None
    error: str | None = None


class LiveProgressResponse(BaseModel):
    stage: str
    users_fetched: int
    groups_fetched: int
    memberships_fetched: int
    roles_fetched: int
    role_memberships_fetched: int


class SyncStatusResponse(BaseModel):
    active_run_id: str | None
    running: bool
    schedule_enabled: bool
    schedule_interval_minutes: int
    schedule_sync_type: str
    next_scheduled_run_at: str | None
    latest_run: SyncRunResponse | None
    live_progress: LiveProgressResponse | None = None


class SyncStartRequest(BaseModel):
    sync_type: Literal['full', 'incremental'] = 'full'


class SyncStartResponse(BaseModel):
    run_id: str
    status: str
    sync_type: str


class LogResponse(BaseModel):
    lines: list[str]


class ScheduleResponse(BaseModel):
    enabled: bool
    interval_minutes: int
    sync_type: str
    next_run_at: str | None
    updated_at: str | None = None


class ScheduleUpdateRequest(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=5, le=1440)
    sync_type: Literal['full', 'incremental'] = 'full'


class ConnectionTestRequest(BaseModel):
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    graph_scope: str | None = None


class ConnectionTestResponse(BaseModel):
    success: bool
    detail: str
