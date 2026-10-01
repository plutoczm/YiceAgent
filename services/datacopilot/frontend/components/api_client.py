from functools import lru_cache

from frontend.services.backend_client import BackendClient


@lru_cache(maxsize=1)
def get_client() -> BackendClient:
    return BackendClient()
