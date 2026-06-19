from __future__ import annotations

import asyncio

from app.services.graph_exporter import LiveProgress
from app.services.sync_service import SyncService


def test_failed_sync_records_failed_stage() -> None:
    class FakeRunStore:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, object]] = []

        async def update_run(self, run_id: str, **fields) -> None:
            payload = {'run_id': run_id}
            payload.update(fields)
            self.update_calls.append(payload)

    class FakeExporter:
        async def export(self, run_id: str, *, sync_type: str, run_store, progress):
            raise RuntimeError('boom')

    run_store = FakeRunStore()
    service = SyncService(run_store, FakeExporter())
    progress = LiveProgress(stage='Fetching memberships')

    asyncio.run(service._run('run-1', 'full', progress))

    assert len(run_store.update_calls) == 2
    assert run_store.update_calls[1]['status'] == 'failed'
    assert run_store.update_calls[1]['failed_stage'] == 'Fetching memberships'
