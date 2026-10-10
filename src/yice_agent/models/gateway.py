"""One normalized dispatch; no provider fallback, tool execution, summaries or agent loop."""

import asyncio
import shutil
from typing import Literal

from pydantic import Field

from yice_agent.compute import local_compute
from yice_agent.config import ROOT, Settings
from yice_agent.contracts import Closed, GenerationRequest, GenerationResult, RpcRequest
from yice_agent.inference import Inference
from yice_agent.model_store import ModelError, ModelStore
from yice_agent.models.http import HttpAdapter, validate_request
from yice_agent.models.node_env import node_environment
from yice_agent.rpc import JsonlWorker


class Intent(Closed):
    intent: Literal[
        "knowledge",
        "order_lookup",
        "place_order",
        "pay",
        "cancel",
        "refund",
        "ticket",
        "memory",
        "clarify",
    ]
    needs_clarification: bool
    order_id: str | None = Field(default=None, max_length=64)
    question: str | None = Field(default=None, max_length=500)


class PiAdapter:
    def __init__(self):
        self.worker = None

    async def get_worker(self):
        if self.worker is None:
            env = node_environment()
            node = shutil.which("node")
            if node is None:
                raise ModelError("node_unavailable")
            self.worker = JsonlWorker([node, str(ROOT / "bridge/bridge.mjs")], env=env)
            await self.worker.start()
        return self.worker

    async def generate(self, request: GenerationRequest, model_id: str, scope: str):
        worker = await self.get_worker()
        try:
            result = await worker.call(
                RpcRequest(
                    method="generate",
                    payload={
                        "request": request.model_dump(mode="json"),
                        "model_id": model_id,
                        "scope": scope,
                    },
                ),
                timeout=request.timeout_seconds + 5,
            )
            return GenerationResult.model_validate(result)
        except Exception as error:
            self.worker = None
            await worker.close()
            code = str(error)
            raise ModelError(
                code
                if code
                in {
                    "oauth_login_required",
                    "model_unavailable",
                    "provider_timeout",
                    "provider_request_failed",
                    "unrequested_tool",
                    "provider_output_limit",
                }
                else "chatgpt_bridge_failed"
            ) from None

    async def models(self, account_probe=False):
        worker = await self.get_worker()
        return await worker.call(
            RpcRequest(method="models", payload={"account_probe": account_probe}), timeout=20
        )

    async def close(self):
        if self.worker:
            await self.worker.close()
            self.worker = None


class Gateway:
    def __init__(self, settings: Settings, store: ModelStore, inference: Inference | None = None):
        self.settings, self.store, self.inference = settings, store, inference
        self.pi = PiAdapter()

    async def generate(self, channel: str, request: GenerationRequest, scope: str):
        if channel not in {"qwen", "deepseek", "chatgpt"}:
            raise ModelError("unknown_model_channel")
        validate_request(request)
        if channel == "deepseek" and (
            not self.settings.deepseek_key or not self.settings.paid_calls_enabled
        ):
            raise ModelError("paid_channel_not_enabled")
        if channel == "chatgpt" and not self.settings.chatgpt_model:
            raise ModelError("chatgpt_model_required")
        model_id = (
            self.settings.ollama_model
            if channel == "qwen"
            else self.settings.deepseek_model
            if channel == "deepseek"
            else self.settings.chatgpt_model
        )
        call_id = self.store.begin(scope, channel, model_id, request, self.settings)
        result = None
        error = None
        try:
            if channel == "qwen":
                async with local_compute(request.timeout_seconds):
                    if self.inference:
                        await (
                            self.inference.close()
                        )  # Only our private BGE workers; never Ollama/other programs.
                    result = await HttpAdapter(self.settings, channel).generate(request)
            elif channel == "deepseek":
                result = await HttpAdapter(self.settings, channel).generate(request)
            else:
                result = await self.pi.generate(request, model_id, scope)
            return result
        except asyncio.CancelledError:
            error = "model_cancelled"
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, ModelError) else "model_request_failed"
            raise ModelError(error) from None
        finally:
            charged = self.store.finish(
                call_id, result.usage if result else None, self.settings, error
            )
            if result and channel == "deepseek":
                result.usage.estimated_cost_cny = str(charged)

    async def close(self):
        await self.pi.close()
