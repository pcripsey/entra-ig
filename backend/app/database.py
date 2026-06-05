from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import Settings


class RunStore:
    def __init__(self, settings: Settings):
        self._database_path = settings.database_path
        self._default_schedule_enabled = settings.schedule_enabled
        self._default_schedule_interval_minutes = settings.schedule_interval_minutes

    async def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                '''
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    users_count INTEGER,
                    groups_count INTEGER,
                    memberships_count INTEGER,
                    users_file TEXT,
                    groups_file TEXT,
                    memberships_file TEXT,
                    error TEXT
                )
                '''
            )
            await db.execute(
                '''
                CREATE TABLE IF NOT EXISTS schedule_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                '''
            )
            cursor = await db.execute('SELECT COUNT(*) FROM schedule_config')
            row = await cursor.fetchone()
            if row[0] == 0:
                await db.execute(
                    'INSERT INTO schedule_config (id, enabled, interval_minutes, updated_at) VALUES (1, ?, ?, ?)',
                    (
                        int(self._default_schedule_enabled),
                        self._default_schedule_interval_minutes,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            await db.commit()

    async def create_run(self, run_id: str, status: str) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                'INSERT INTO sync_runs (id, status, started_at) VALUES (?, ?, ?)',
                (run_id, status, started_at),
            )
            await db.commit()

    async def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return

        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f'{key} = ?')
            values.append(value)
        values.append(run_id)

        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                f"UPDATE sync_runs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            await db.commit()

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                'SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?',
                (limit,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT * FROM sync_runs WHERE id = ?', (run_id,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_schedule(self) -> dict[str, Any]:
        async with aiosqlite.connect(self._database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT enabled, interval_minutes, updated_at FROM schedule_config WHERE id = 1')
            row = await cursor.fetchone()
        if row is None:
            return {
                'enabled': False,
                'interval_minutes': 60,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
        return {
            'enabled': bool(row['enabled']),
            'interval_minutes': row['interval_minutes'],
            'updated_at': row['updated_at'],
        }

    async def update_schedule(self, *, enabled: bool, interval_minutes: int) -> None:
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                '''
                UPDATE schedule_config
                SET enabled = ?, interval_minutes = ?, updated_at = ?
                WHERE id = 1
                ''',
                (int(enabled), interval_minutes, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
