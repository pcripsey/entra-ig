from __future__ import annotations

import logging
import re
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

_RUN_ID_RE = re.compile(r'^[0-9a-f]{32}$')

from app.models import (
    ConfigResponse,
    ConnectionTestRequest,
    ConnectionTestResponse,
    HealthResponse,
    LiveProgressResponse,
    LogLevelResponse,
    LogLevelUpdateRequest,
    LogResponse,
    RetryConfigResponse,
    RetryConfigUpdateRequest,
    ScheduleResponse,
    ScheduleUpdateRequest,
    SyncRunResponse,
    SyncStartRequest,
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
    lp = sync_service.live_progress
    live_progress = (
        LiveProgressResponse(
            stage=lp.stage,
            users_fetched=lp.users_fetched,
            groups_fetched=lp.groups_fetched,
            memberships_fetched=lp.memberships_fetched,
            group_owners_fetched=lp.group_owners_fetched,
            nested_groups_fetched=lp.nested_groups_fetched,
            roles_fetched=lp.roles_fetched,
            role_memberships_fetched=lp.role_memberships_fetched,
            throttle_count=lp.throttle_count,
            last_throttled_at=lp.last_throttled_at,
            last_throttled_operation=lp.last_throttled_operation,
        )
        if lp is not None
        else None
    )
    return SyncStatusResponse(
        active_run_id=sync_service.active_run_id,
        running=sync_service.is_running,
        schedule_enabled=sync_service.schedule_enabled,
        schedule_interval_minutes=sync_service.schedule_interval_minutes,
        schedule_sync_type=sync_service.schedule_sync_type,
        next_scheduled_run_at=sync_service.next_scheduled_run_at,
        latest_run=latest_run,
        live_progress=live_progress,
    )


@router.post('/sync', response_model=SyncStartResponse, status_code=202)
async def start_sync(request: Request, payload: SyncStartRequest = SyncStartRequest()) -> SyncStartResponse:
    logger = request.app.state.logger
    logger.debug('Sync requested: sync_type=%s', payload.sync_type)
    sync_service = request.app.state.sync_service
    try:
        run_id = await sync_service.start(sync_type=payload.sync_type)
    except SyncAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.debug('Sync run %s queued (type=%s)', run_id, payload.sync_type)
    return SyncStartResponse(run_id=run_id, status='queued', sync_type=payload.sync_type)


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


@router.delete('/runs/{run_id}', status_code=204)
async def delete_run(run_id: str, request: Request) -> None:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=404, detail='Run not found.')
    sync_service = request.app.state.sync_service
    if sync_service.active_run_id == run_id:
        raise HTTPException(status_code=409, detail='Cannot delete an active run.')
    run_store = request.app.state.run_store
    run = await run_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='Run not found.')
    await run_store.delete_run(run_id)
    export_base = Path(request.app.state.settings.export_base_dir).resolve()
    export_dir = export_base / run_id
    if export_dir.exists():
        try:
            shutil.rmtree(export_dir)
        except OSError:
            request.app.state.logger.warning('Could not remove export directory %s', export_dir, exc_info=True)


@router.get('/logs', response_model=LogResponse)
async def get_logs(request: Request, lines: int = 100) -> LogResponse:
    log_file_path = Path(request.app.state.settings.log_file_path)
    if not log_file_path.exists():
        return LogResponse(lines=[])

    content = log_file_path.read_text(encoding='utf-8').splitlines()
    return LogResponse(lines=content[-lines:])


@router.delete('/logs', status_code=204)
async def clear_logs(request: Request) -> None:
    """Truncate the active log file and remove any rotated backup files."""
    logger = request.app.state.logger
    active_log_path = Path(request.app.state.settings.log_file_path).resolve()
    backup_count = 0

    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            if Path(handler.baseFilename).resolve() != active_log_path:
                continue
            backup_count = max(backup_count, handler.backupCount)
            handler.acquire()
            try:
                handler.flush()
                handler.stream.seek(0)
                handler.stream.truncate()
            finally:
                handler.release()

    for i in range(1, backup_count + 1):
        backup = Path(f'{active_log_path}.{i}')
        if backup.exists():
            try:
                backup.unlink()
            except OSError:
                logger.warning('Could not remove log backup %s', backup, exc_info=True)


@router.get('/log-level', response_model=LogLevelResponse)
async def get_log_level(request: Request) -> LogLevelResponse:
    logger = request.app.state.logger
    return LogLevelResponse(log_level=logging.getLevelName(logger.level))


@router.put('/log-level', response_model=LogLevelResponse)
async def update_log_level(request: Request, payload: LogLevelUpdateRequest) -> LogLevelResponse:
    logger = request.app.state.logger
    logger.setLevel(payload.log_level)
    for handler in logger.handlers:
        handler.setLevel(payload.log_level)
    logger.debug('Log level changed to %s', payload.log_level)
    return LogLevelResponse(log_level=payload.log_level)


@router.get('/schedule', response_model=ScheduleResponse)
async def get_schedule(request: Request) -> ScheduleResponse:
    sync_service = request.app.state.sync_service
    return ScheduleResponse(
        enabled=sync_service.schedule_enabled,
        interval_minutes=sync_service.schedule_interval_minutes,
        sync_type=sync_service.schedule_sync_type,
        next_run_at=sync_service.next_scheduled_run_at,
        updated_at=sync_service.schedule_updated_at,
    )


@router.put('/schedule', response_model=ScheduleResponse)
async def update_schedule(request: Request, payload: ScheduleUpdateRequest) -> ScheduleResponse:
    logger = request.app.state.logger
    logger.debug('Schedule update requested: enabled=%s, interval=%d, sync_type=%s', payload.enabled, payload.interval_minutes, payload.sync_type)
    sync_service = request.app.state.sync_service
    await sync_service.update_schedule(
        enabled=payload.enabled,
        interval_minutes=payload.interval_minutes,
        sync_type=payload.sync_type,
    )
    return ScheduleResponse(
        enabled=sync_service.schedule_enabled,
        interval_minutes=sync_service.schedule_interval_minutes,
        sync_type=sync_service.schedule_sync_type,
        next_run_at=sync_service.next_scheduled_run_at,
        updated_at=sync_service.schedule_updated_at,
    )


@router.get('/retry-config', response_model=RetryConfigResponse)
async def get_retry_config(request: Request) -> RetryConfigResponse:
    config = await request.app.state.run_store.get_retry_config()
    return RetryConfigResponse(**config)


@router.put('/retry-config', response_model=RetryConfigResponse)
async def update_retry_config(request: Request, payload: RetryConfigUpdateRequest) -> RetryConfigResponse:
    logger = request.app.state.logger
    logger.debug('Retry config update: max_attempts=%d, max_delay=%ds', payload.max_retry_attempts, payload.max_retry_delay_seconds)
    run_store = request.app.state.run_store
    await run_store.update_retry_config(
        max_retry_attempts=payload.max_retry_attempts,
        max_retry_delay_seconds=payload.max_retry_delay_seconds,
    )
    exporter = request.app.state.exporter
    exporter.update_retry_config(
        max_retry_attempts=payload.max_retry_attempts,
        max_retry_delay_seconds=payload.max_retry_delay_seconds,
    )
    config = await run_store.get_retry_config()
    return RetryConfigResponse(**config)
