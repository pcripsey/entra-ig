from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
        self._active_run_id: str | None = None
        self._start_lock = asyncio.Lock()

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def start(self) -> str:
        async with self._start_lock:
            if self.is_running:
                raise SyncAlreadyRunningError('A sync is already in progress.')

            run_id = uuid4().hex
            await self._run_store.create_run(run_id, 'queued')
            self._active_run_id = run_id
            self._task = asyncio.create_task(self._run(run_id))
            return run_id

    async def _run(self, run_id: str) -> None:
        try:
            await self._run_store.update_run(run_id, status='running')
            result = await self._exporter.export(run_id)
            await self._run_store.update_run(
                run_id,
                status='completed',
                completed_at=datetime.now(timezone.utc).isoformat(),
                users_count=result.users_count,
                groups_count=result.groups_count,
                memberships_count=result.memberships_count,
                users_file=result.users_file,
                groups_file=result.groups_file,
                memberships_file=result.memberships_file,
            )
            self._logger.info('Completed sync run %s', run_id)
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
