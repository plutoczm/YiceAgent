from functools import lru_cache
from pathlib import Path

from backend.app.core.settings import Settings


def load_settings(env_file: str | Path | tuple[str | Path, ...] | None = None) -> Settings:
    if env_file is None:
        return Settings()
    return Settings(_env_file=env_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
