"""One cross-process lease for project GPU work; never stop unrelated programs."""

import asyncio
from contextlib import asynccontextmanager
from time import monotonic

import portalocker

from workflowops.config import RUNTIME
from workflowops.model_store import ModelError


@asynccontextmanager
async def local_compute(timeout: float = 600):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    stream = (RUNTIME / "compute.lock").open("a+b")
    acquired = False
    deadline = monotonic() + timeout
    try:
        while not acquired:
            try:
                portalocker.lock(stream, portalocker.LOCK_EX | portalocker.LOCK_NB)
                acquired = True
            except portalocker.LockException:
                if monotonic() >= deadline:
                    raise ModelError("local_compute_busy") from None
                await asyncio.sleep(0.05)
        yield
    finally:
        if acquired:
            portalocker.unlock(stream)
        stream.close()
