from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.config import Settings
from app.database import RunStore


def create_test_client(log_file_path: Path, *, backup_count: int = 5) -> tuple[TestClient, logging.Logger]:
    app = FastAPI()
    app.include_router(router)

    logger = logging.getLogger(f'test_logger_{log_file_path.name}')
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    file_handler = RotatingFileHandler(log_file_path, maxBytes=1024, backupCount=backup_count)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

    app.state.logger = logger
    app.state.settings = Settings(LOG_FILE_PATH=log_file_path)

    return TestClient(app), logger


def test_clear_logs_truncates_active_file_and_removes_backups(tmp_path) -> None:
    log_file_path = tmp_path / 'app.log'
    client, logger = create_test_client(log_file_path, backup_count=2)

    try:
        logger.info('first log line')
        for handler in logger.handlers:
            handler.flush()

        for index in range(1, 4):
            (tmp_path / f'app.log.{index}').write_text(f'backup-{index}', encoding='utf-8')

        response = client.delete('/logs')

        assert response.status_code == 204
        assert log_file_path.read_text(encoding='utf-8') == ''
        for index in range(1, 3):
            assert not (tmp_path / f'app.log.{index}').exists()
        assert (tmp_path / 'app.log.3').exists()
    finally:
        client.close()
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


def test_log_level_endpoints_return_and_update_runtime_level(tmp_path) -> None:
    log_file_path = tmp_path / 'app.log'
    client, logger = create_test_client(log_file_path)

    try:
        response = client.get('/log-level')
        assert response.status_code == 200
        assert response.json() == {'log_level': 'INFO'}

        response = client.put('/log-level', json={'log_level': 'DEBUG'})
        assert response.status_code == 200
        assert response.json() == {'log_level': 'DEBUG'}
        assert logger.level == logging.DEBUG
        assert all(handler.level == logging.DEBUG for handler in logger.handlers)
    finally:
        client.close()
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


def _create_test_client_with_run_store(tmp_path: Path) -> TestClient:
    """Create a TestClient wired up with a real RunStore and a stub sync_service."""
    db_path = tmp_path / 'test.db'
    settings = Settings(DATABASE_PATH=db_path, LOG_FILE_PATH=tmp_path / 'app.log')
    run_store = RunStore(settings)
    asyncio.run(run_store.initialize())

    sync_service = MagicMock()
    sync_service.active_run_id = None
    sync_service.is_running = False
    sync_service.live_progress = None
    sync_service.schedule_enabled = False
    sync_service.schedule_interval_minutes = 60
    sync_service.schedule_sync_type = 'full'
    sync_service.next_scheduled_run_at = None
    sync_service.schedule_updated_at = None

    logger = logging.getLogger(f'test_runs_{tmp_path.name}')
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.logger = logger
    app.state.run_store = run_store
    app.state.sync_service = sync_service

    return TestClient(app)


ALL_COUNTER_FIELDS = [
    'users_count',
    'groups_count',
    'memberships_count',
    'roles_count',
    'role_memberships_count',
    'nested_groups_count',
]


def test_list_runs_includes_all_counters(tmp_path: Path) -> None:
    """GET /runs must return every counter field including nested_groups_count."""
    client = _create_test_client_with_run_store(tmp_path)
    run_store = client.app.state.run_store  # type: ignore[attr-defined]

    async def _setup() -> None:
        await run_store.create_run('abc123', 'completed')
        await run_store.update_run(
            'abc123',
            status='completed',
            users_count=10,
            groups_count=5,
            memberships_count=20,
            roles_count=3,
            role_memberships_count=7,
            nested_groups_count=2,
        )

    asyncio.run(_setup())

    response = client.get('/runs')

    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 1
    run = runs[0]
    for field in ALL_COUNTER_FIELDS:
        assert field in run, f"Counter field '{field}' missing from /runs response"
    assert run['nested_groups_count'] == 2
    assert run['users_count'] == 10
    assert run['groups_count'] == 5
    assert run['memberships_count'] == 20
    assert run['roles_count'] == 3
    assert run['role_memberships_count'] == 7


def test_get_run_includes_all_counters(tmp_path: Path) -> None:
    """GET /runs/{run_id} must return every counter field including nested_groups_count."""
    client = _create_test_client_with_run_store(tmp_path)
    run_store = client.app.state.run_store  # type: ignore[attr-defined]

    async def _setup() -> None:
        await run_store.create_run('def456', 'completed')
        await run_store.update_run(
            'def456',
            status='completed',
            users_count=1,
            groups_count=2,
            memberships_count=3,
            roles_count=4,
            role_memberships_count=5,
            nested_groups_count=6,
        )

    asyncio.run(_setup())

    response = client.get('/runs/def456')

    assert response.status_code == 200
    run = response.json()
    for field in ALL_COUNTER_FIELDS:
        assert field in run, f"Counter field '{field}' missing from /runs/{{run_id}} response"
    assert run['nested_groups_count'] == 6


@pytest.mark.parametrize('field', ALL_COUNTER_FIELDS)
def test_run_counter_field_defaults_to_null_when_unset(tmp_path: Path, field: str) -> None:
    """Counter fields that were never set must be null (not absent) in the response."""
    client = _create_test_client_with_run_store(tmp_path)
    run_store = client.app.state.run_store  # type: ignore[attr-defined]

    asyncio.run(run_store.create_run('ghi789', 'queued'))

    response = client.get('/runs/ghi789')

    assert response.status_code == 200
    run = response.json()
    assert field in run, f"Counter field '{field}' missing from response"
    assert run[field] is None, f"Expected '{field}' to be null when unset, got {run[field]!r}"
