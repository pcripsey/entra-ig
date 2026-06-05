from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.database import RunStore
from app.logging_config import configure_logging
from app.services.graph_exporter import GraphExportService
from app.services.sync_service import SyncService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger = configure_logging(settings)
    settings.export_base_dir.mkdir(parents=True, exist_ok=True)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_file_path.parent.mkdir(parents=True, exist_ok=True)

    run_store = RunStore(settings)
    await run_store.initialize()
    exporter = GraphExportService(settings)
    sync_service = SyncService(run_store, exporter)
    await sync_service.initialize()

    app.state.settings = settings
    app.state.logger = logger
    app.state.run_store = run_store
    app.state.exporter = exporter
    app.state.sync_service = sync_service
    yield
    await sync_service.shutdown()


app = FastAPI(
    title='Entra ID OpenText Governance Exporter',
    version='0.1.0',
    lifespan=lifespan,
)
app.include_router(router, prefix=get_settings().api_prefix)
app.mount(
    '/assets',
    StaticFiles(directory=str(Path(get_settings().frontend_dist) / 'assets'), check_dir=False),
    name='frontend-assets',
)


@app.get('/', include_in_schema=False)
async def serve_frontend_root():
    settings = get_settings()
    dist_path = Path(settings.frontend_dist).resolve()
    index_file = (dist_path / 'index.html').resolve()
    if index_file.is_file():
        return FileResponse(index_file)

    raise HTTPException(status_code=404, detail='Frontend build assets are not available yet.')


@app.get('/{full_path:path}', include_in_schema=False)
async def serve_frontend_app(full_path: str):
    settings = get_settings()
    if full_path.startswith(settings.api_prefix.lstrip('/')) or full_path.startswith('assets/'):
        raise HTTPException(status_code=404, detail='Not found')
    return await serve_frontend_root()
