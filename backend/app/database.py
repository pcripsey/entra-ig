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
        self._default_max_retry_attempts = settings.max_retry_attempts
        self._default_max_retry_delay_seconds = settings.max_retry_delay_seconds

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
                    roles_count INTEGER,
                    role_memberships_count INTEGER,
                    nested_groups_count INTEGER,
                    identity_file TEXT,
                    account_file TEXT,
                    group_file TEXT,
                    group_membership_file TEXT,
                    parent_child_group_file TEXT,
                    permission_file TEXT,
                    holder_to_permission_file TEXT,
                    permission_to_holder_file TEXT,
                    permission_hierarchy_cp_file TEXT,
                    permission_hierarchy_pc_file TEXT,
                    users_file TEXT,
                    groups_file TEXT,
                    memberships_file TEXT,
                    roles_file TEXT,
                    role_memberships_file TEXT,
                    error TEXT,
                    sync_type TEXT NOT NULL DEFAULT 'full'
                )
                '''
            )
            # Migrate existing installs that lack the sync_type column
            try:
                await db.execute("ALTER TABLE sync_runs ADD COLUMN sync_type TEXT NOT NULL DEFAULT 'full'")
            except aiosqlite.OperationalError as exc:
                if 'duplicate column name' not in str(exc).lower():
                    raise

            for col in (
                'roles_count INTEGER',
                'role_memberships_count INTEGER',
                'nested_groups_count INTEGER',
                'identity_file TEXT',
                'account_file TEXT',
                'group_file TEXT',
                'group_membership_file TEXT',
                'parent_child_group_file TEXT',
                'permission_file TEXT',
                'holder_to_permission_file TEXT',
                'permission_to_holder_file TEXT',
                'permission_hierarchy_cp_file TEXT',
                'permission_hierarchy_pc_file TEXT',
                'roles_file TEXT',
                'role_memberships_file TEXT',
            ):
                try:
                    await db.execute(f'ALTER TABLE sync_runs ADD COLUMN {col}')
                except aiosqlite.OperationalError as exc:
                    if 'duplicate column name' not in str(exc).lower():
                        raise

            await db.execute(
                '''
                CREATE TABLE IF NOT EXISTS schedule_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    sync_type TEXT NOT NULL DEFAULT 'full'
                )
                '''
            )
            # Migrate existing installs that lack the sync_type column in schedule_config
            try:
                await db.execute("ALTER TABLE schedule_config ADD COLUMN sync_type TEXT NOT NULL DEFAULT 'full'")
            except aiosqlite.OperationalError as exc:
                if 'duplicate column name' not in str(exc).lower():
                    raise

            await db.execute(
                '''
                CREATE TABLE IF NOT EXISTS delta_tokens (
                    resource TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                '''
            )

            await db.execute(
                '''
                CREATE TABLE IF NOT EXISTS retry_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    max_retry_attempts INTEGER NOT NULL,
                    max_retry_delay_seconds INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                '''
            )

            cursor = await db.execute('SELECT COUNT(*) FROM schedule_config')
            row = await cursor.fetchone()
            if row[0] == 0:
                await db.execute(
                    'INSERT INTO schedule_config (id, enabled, interval_minutes, updated_at, sync_type) VALUES (1, ?, ?, ?, ?)',
                    (
                        int(self._default_schedule_enabled),
                        self._default_schedule_interval_minutes,
                        datetime.now(timezone.utc).isoformat(),
                        'full',
                    ),
                )
            await db.commit()

    async def create_run(self, run_id: str, status: str, sync_type: str = 'full') -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                'INSERT INTO sync_runs (id, status, started_at, sync_type) VALUES (?, ?, ?, ?)',
                (run_id, status, started_at, sync_type),
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

    async def delete_run(self, run_id: str) -> bool:
        async with aiosqlite.connect(self._database_path) as db:
            cursor = await db.execute('DELETE FROM sync_runs WHERE id = ?', (run_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def get_schedule(self) -> dict[str, Any]:
        async with aiosqlite.connect(self._database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                'SELECT enabled, interval_minutes, updated_at, sync_type FROM schedule_config WHERE id = 1'
            )
            row = await cursor.fetchone()
        if row is None:
            return {
                'enabled': False,
                'interval_minutes': 60,
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'sync_type': 'full',
            }
        return {
            'enabled': bool(row['enabled']),
            'interval_minutes': row['interval_minutes'],
            'updated_at': row['updated_at'],
            'sync_type': row['sync_type'],
        }

    async def update_schedule(self, *, enabled: bool, interval_minutes: int, sync_type: str = 'full') -> None:
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                '''
                UPDATE schedule_config
                SET enabled = ?, interval_minutes = ?, updated_at = ?, sync_type = ?
                WHERE id = 1
                ''',
                (int(enabled), interval_minutes, datetime.now(timezone.utc).isoformat(), sync_type),
            )
            await db.commit()

    async def get_delta_tokens(self) -> dict[str, str]:
        async with aiosqlite.connect(self._database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT resource, token FROM delta_tokens')
            rows = await cursor.fetchall()
        return {row['resource']: row['token'] for row in rows}

    async def update_delta_tokens(self, tokens: dict[str, str]) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._database_path) as db:
            for resource, token in tokens.items():
                await db.execute(
                    '''
                    INSERT INTO delta_tokens (resource, token, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(resource) DO UPDATE SET token = excluded.token, updated_at = excluded.updated_at
                    ''',
                    (resource, token, updated_at),
                )
            await db.commit()

    async def get_retry_config(self) -> dict[str, Any]:
        async with aiosqlite.connect(self._database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                'SELECT max_retry_attempts, max_retry_delay_seconds, updated_at FROM retry_config WHERE id = 1'
            )
            row = await cursor.fetchone()
        if row is None:
            return {
                'max_retry_attempts': self._default_max_retry_attempts,
                'max_retry_delay_seconds': self._default_max_retry_delay_seconds,
                'updated_at': None,
            }
        return {
            'max_retry_attempts': row['max_retry_attempts'],
            'max_retry_delay_seconds': row['max_retry_delay_seconds'],
            'updated_at': row['updated_at'],
        }

    async def update_retry_config(self, *, max_retry_attempts: int, max_retry_delay_seconds: int) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                '''
                INSERT INTO retry_config (id, max_retry_attempts, max_retry_delay_seconds, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    max_retry_attempts = excluded.max_retry_attempts,
                    max_retry_delay_seconds = excluded.max_retry_delay_seconds,
                    updated_at = excluded.updated_at
                ''',
                (max_retry_attempts, max_retry_delay_seconds, updated_at),
            )
            await db.commit()
