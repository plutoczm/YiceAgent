"""Bounded JSONL transport for owned workers; no shell and no credential output."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from workflowops.contracts import RpcRequest, RpcResponse

MAX_LINE = 4 * 1024 * 1024


class WorkerError(RuntimeError):
    pass


class JsonlWorker:
    def __init__(self, command: Sequence[str], *, env: dict[str, str] | None = None):
        self.command = tuple(command)
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        if self.process is not None:
            raise WorkerError("worker_already_started")
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.env,
            limit=MAX_LINE,
        )

    async def call(self, request: RpcRequest, timeout: float = 120) -> Any:
        async with self.lock:
            process = self.process
            if (
                process is None
                or process.returncode is not None
                or process.stdin is None
                or process.stdout is None
            ):
                raise WorkerError("worker_unavailable")
            wire = (request.model_dump_json() + "\n").encode("utf-8")
            if len(wire) > MAX_LINE:
                raise WorkerError("worker_request_too_large")
            try:
                async with asyncio.timeout(timeout):
                    process.stdin.write(wire)
                    await process.stdin.drain()
                    line = await process.stdout.readline()
                    if not line or len(line) > MAX_LINE:
                        raise WorkerError("worker_protocol_invalid")
                    response = RpcResponse.model_validate_json(line)
                    if response.request_id != request.request_id:
                        raise WorkerError("worker_response_id_mismatch")
                    if response.error:
                        raise WorkerError(response.error.code)
                    return response.result
            except (TimeoutError, asyncio.CancelledError):
                await self.close()
                raise
            except WorkerError:
                await self.close()
                raise
            except (ValueError, json.JSONDecodeError):
                await self.close()
                raise WorkerError("worker_protocol_invalid") from None

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()
