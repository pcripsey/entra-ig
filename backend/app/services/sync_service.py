from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.database import RunStore
from app.logging_config import LOGGER_NAME
from app.services.graph_exporter import GraphExportService

import logging


class SyncAlreadyRunningError(RuntimeError):
    pass


class SyncService:
    def __init__(self, run_store: RunStore, exporter: GraphExportService):
        self._run_store = run_store
        self._exporter = exporter
        self._logger = logging.getLogger(LOGGER_NAME)
        self._task: asyncio.Task[None] | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._active_run_id: str | None = None
        self._start_lock = asyncio.Lock()
        self._schedule_change_event = asyncio.Event()
        self._schedule_enabled = False
        self._schedule_interval_minutes = 60
        self._schedule_sync_type = 'full'
        self._schedule_updated_at: str | None = None
        self._next_scheduled_run_at: str | None = None

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    @property
    def schedule_enabled(self) -> bool:
        return self._schedule_enabled

    @property
    def schedule_interval_minutes(self) -> int:
        return self._schedule_interval_minutes

    @property
    def schedule_sync_type(self) -> str:
        return self._schedule_sync_type

    @property
    def schedule_updated_at(self) -> str | None:
        return self._schedule_updated_at

    @property
    def next_scheduled_run_at(self) -> str | None:
        return self._next_scheduled_run_at

    async def initialize(self) -> None:
        schedule = await self._run_store.get_schedule()
        self._schedule_enabled = schedule['enabled']
        self._schedule_interval_minutes = schedule['interval_minutes']
        self._schedule_sync_type = schedule.get('sync_type', 'full')
        self._schedule_updated_at = schedule['updated_at']
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def shutdown(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            await asyncio.gather(self._scheduler_task, return_exceptions=True)

    async def update_schedule(self, *, enabled: bool, interval_minutes: int, sync_type: str = 'full') -> None:
        await self._run_store.update_schedule(
            enabled=enabled, interval_minutes=interval_minutes, sync_type=sync_type
        )
        schedule = await self._run_store.get_schedule()
        self._schedule_enabled = schedule['enabled']
        self._schedule_interval_minutes = schedule['interval_minutes']
        self._schedule_sync_type = schedule.get('sync_type', 'full')
        self._schedule_updated_at = schedule['updated_at']
        self._schedule_change_event.set()

    async def start(self, sync_type: str = 'full') -> str:
        async with self._start_lock:
            if self.is_running:
                raise SyncAlreadyRunningError('A sync is already in progress.')

            run_id = uuid4().hex
            await self._run_store.create_run(run_id, 'queued', sync_type=sync_type)
            self._active_run_id = run_id
            self._task = asyncio.create_task(self._run(run_id, sync_type))
            return run_id

    async def _run(self, run_id: str, sync_type: str) -> None:
        try:
            await self._run_store.update_run(run_id, status='running')
            result = await self._exporter.export(run_id, sync_type=sync_type, run_store=self._run_store)
            await self._run_store.update_run(
                run_id,
                status='completed',
                completed_at=datetime.now(timezone.utc).isoformat(),
                users_count=result.users_count,
                groups_count=result.groups_count,
                memberships_count=result.memberships_count,
                roles_count=result.roles_count,
                role_memberships_count=result.role_memberships_count,
                users_file=result.users_file,
                groups_file=result.groups_file,
                memberships_file=result.memberships_file,
                roles_file=result.roles_file,
                role_memberships_file=result.role_memberships_file,
            )
            self._logger.info('Completed %s sync run %s', sync_type, run_id)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception('Sync run %s failed', run_id)
            await self._run_store.update_run(
                run_id,
                status='failed',
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )
        finally:
            self._active_run_id = None
            self._task = None

    async def _scheduler_loop(self) -> None:
        try:
            while True:
                if not self._schedule_enabled:
                    self._next_scheduled_run_at = None
                    self._schedule_change_event.clear()
                    await self._schedule_change_event.wait()
                    continue

                next_run_at = datetime.now(timezone.utc) + timedelta(minutes=self._schedule_interval_minutes)
                self._next_scheduled_run_at = next_run_at.isoformat()
                wait_seconds = max((next_run_at - datetime.now(timezone.utc)).total_seconds(), 1)
                self._schedule_change_event.clear()

                try:
                    await asyncio.wait_for(self._schedule_change_event.wait(), timeout=wait_seconds)
                    continue
                except asyncio.TimeoutError:
                    self._logger.info('Starting scheduled %s sync.', self._schedule_sync_type)
                    try:
                        await self.start(sync_type=self._schedule_sync_type)
                    except SyncAlreadyRunningError:
                        self._logger.info('Skipped scheduled sync because another run is active.')
        except asyncio.CancelledError:
            self._logger.info('Scheduler stopped.')
            raise
