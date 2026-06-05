from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.models import (
    ConfigResponse,
    ConnectionTestRequest,
    ConnectionTestResponse,
    HealthResponse,
    LogResponse,
    ScheduleResponse,
    ScheduleUpdateRequest,
    SyncRunResponse,
    SyncStartResponse,
    SyncStatusResponse,
)
from app.services.sync_service import SyncAlreadyRunningError

router = APIRouter()


@router.get('/config', response_model=ConfigResponse)
async def get_config(request: Request) -> ConfigResponse:
    settings = request.app.state.settings
    return ConfigResponse(
        tenant_id=settings.tenant_id or '',
        client_id=settings.client_id or '',
        tenant_id_present=bool(settings.tenant_id),
        client_id_present=bool(settings.client_id),
        client_secret_present=bool(settings.client_secret),
        masked_client_secret=settings.masked_client_secret,
        graph_scope=settings.graph_scope,
        export_base_dir=str(settings.export_base_dir),
        database_path=str(settings.database_path),
        log_file_path=str(settings.log_file_path),
        frontend_dist=str(settings.frontend_dist),
    )


@router.get('/health', response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    exporter = request.app.state.exporter
    latest_runs = await request.app.state.run_store.list_runs(limit=1)
    graph_reachable, detail = await exporter.check_connection()
    status = 'ok' if graph_reachable else 'degraded'
    latest_run_status = latest_runs[0]['status'] if latest_runs else None
    return HealthResponse(
        status=status,
        graph_configured=request.app.state.settings.graph_configured,
        graph_reachable=graph_reachable,
        detail=detail,
        latest_run_status=latest_run_status,
    )


@router.post('/connection/test', response_model=ConnectionTestResponse)
async def test_connection(request: Request, payload: ConnectionTestRequest) -> ConnectionTestResponse:
    exporter = request.app.state.exporter
    success, detail = await exporter.check_connection(
        overrides={
            'tenant_id': payload.tenant_id,
            'client_id': payload.client_id,
            'client_secret': payload.client_secret,
            'graph_scope': payload.graph_scope,
        }
    )
    return ConnectionTestResponse(success=success, detail=detail)


@router.get('/status', response_model=SyncStatusResponse)
async def get_status(request: Request) -> SyncStatusResponse:
    latest_runs = await request.app.state.run_store.list_runs(limit=1)
    latest_run = SyncRunResponse(**latest_runs[0]) if latest_runs else None
    sync_service = request.app.state.sync_service
    return SyncStatusResponse(
        active_run_id=sync_service.active_run_id,
        running=sync_service.is_running,
        schedule_enabled=sync_service.schedule_enabled,
        schedule_interval_minutes=sync_service.schedule_interval_minutes,
        next_scheduled_run_at=sync_service.next_scheduled_run_at,
        latest_run=latest_run,
    )


@router.post('/sync', response_model=SyncStartResponse, status_code=202)
async def start_sync(request: Request) -> SyncStartResponse:
    sync_service = request.app.state.sync_service
    try:
        run_id = await sync_service.start()
    except SyncAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SyncStartResponse(run_id=run_id, status='queued')


@router.get('/runs', response_model=list[SyncRunResponse])
async def list_runs(request: Request) -> list[SyncRunResponse]:
    runs = await request.app.state.run_store.list_runs(limit=20)
    return [SyncRunResponse(**run) for run in runs]


@router.get('/runs/{run_id}', response_model=SyncRunResponse)
async def get_run(run_id: str, request: Request) -> SyncRunResponse:
    run = await request.app.state.run_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='Run not found.')
    return SyncRunResponse(**run)


@router.get('/logs', response_model=LogResponse)
async def get_logs(request: Request, lines: int = 100) -> LogResponse:
    log_file_path = Path(request.app.state.settings.log_file_path)
    if not log_file_path.exists():
        return LogResponse(lines=[])

    content = log_file_path.read_text(encoding='utf-8').splitlines()
    return LogResponse(lines=content[-lines:])


@router.get('/schedule', response_model=ScheduleResponse)
async def get_schedule(request: Request) -> ScheduleResponse:
    sync_service = request.app.state.sync_service
    return ScheduleResponse(
        enabled=sync_service.schedule_enabled,
        interval_minutes=sync_service.schedule_interval_minutes,
        next_run_at=sync_service.next_scheduled_run_at,
        updated_at=sync_service.schedule_updated_at,
    )


@router.put('/schedule', response_model=ScheduleResponse)
async def update_schedule(request: Request, payload: ScheduleUpdateRequest) -> ScheduleResponse:
    sync_service = request.app.state.sync_service
    await sync_service.update_schedule(enabled=payload.enabled, interval_minutes=payload.interval_minutes)
    return ScheduleResponse(
        enabled=sync_service.schedule_enabled,
        interval_minutes=sync_service.schedule_interval_minutes,
        next_run_at=sync_service.next_scheduled_run_at,
        updated_at=sync_service.schedule_updated_at,
    )
