from __future__ import annotations

import asyncio
import sqlite3

from app.config import Settings
from app.database import RunStore


def test_sync_runs_table_has_failed_stage_column(tmp_path) -> None:
    database_path = tmp_path / 'app.db'
    store = RunStore(Settings(DATABASE_PATH=database_path))

    asyncio.run(store.initialize())

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute('PRAGMA table_info(sync_runs)').fetchall()
    finally:
        connection.close()

    assert any(row[1] == 'failed_stage' for row in rows)


def test_update_run_persists_failed_stage(tmp_path) -> None:
    store = RunStore(Settings(DATABASE_PATH=tmp_path / 'app.db'))

    asyncio.run(store.initialize())
    asyncio.run(store.create_run('run-1', 'queued'))
    asyncio.run(store.update_run('run-1', status='failed', failed_stage='Fetching memberships'))

    run = asyncio.run(store.get_run('run-1'))
    assert run is not None
    assert run['failed_stage'] == 'Fetching memberships'
