from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    tenant_id: str | None = Field(default=None, alias='TENANT_ID')
    client_id: str | None = Field(default=None, alias='CLIENT_ID')
    client_secret: str | None = Field(default=None, alias='CLIENT_SECRET')
    graph_scope: str = Field(default='https://graph.microsoft.com/.default', alias='GRAPH_SCOPE')
    graph_page_size: int = Field(default=999, alias='GRAPH_PAGE_SIZE')
    max_retry_attempts: int = Field(default=5, alias='MAX_RETRY_ATTEMPTS')
    max_retry_delay_seconds: int = Field(default=32, alias='MAX_RETRY_DELAY_SECONDS')
    membership_concurrency: int = Field(default=4, alias='MEMBERSHIP_CONCURRENCY')
    export_base_dir: Path = Field(default=Path('data/exports'), alias='EXPORT_BASE_DIR')
    database_path: Path = Field(default=Path('data/app.db'), alias='DATABASE_PATH')
    log_file_path: Path = Field(default=Path('logs/app.log'), alias='LOG_FILE_PATH')
    frontend_dist: Path = Field(default=Path('frontend/dist'), alias='FRONTEND_DIST')
    api_prefix: str = Field(default='/api', alias='API_PREFIX')
    log_level: str = Field(default='INFO', alias='LOG_LEVEL')

    @property
    def graph_configured(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    @property
    def masked_client_secret(self) -> str:
        if not self.client_secret:
            return ''
        visible = self.client_secret[-4:] if len(self.client_secret) >= 4 else self.client_secret
        return f"{'*' * max(4, len(self.client_secret) - len(visible))}{visible}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
