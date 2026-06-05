from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import Settings


class RunStore:
    def __init__(self, settings: Settings):
        self._database_path = settings.database_path

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
