from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.config import Settings


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
