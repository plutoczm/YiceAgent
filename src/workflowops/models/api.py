"""Authenticated local diagnostics; no arbitrary tools, provider URLs or credential reads."""

import asyncio
import secrets
from dataclasses import dataclass
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, SecretStr
from sqlalchemy.engine import Engine

from workflowops.business.api import COOKIE
from workflowops.config import CONFIG, Settings
from workflowops.contracts import Closed, GenerationRequest, Message
from workflowops.identity import Principal, Sessions
from workflowops.inference import Inference
from workflowops.knowledge.answer import answer
from workflowops.knowledge.retrieval import Retrieval
from workflowops.knowledge.store import Store
from workflowops.knowledge.vector import Vectors
from workflowops.model_store import ModelStore
from workflowops.models.gateway import Gateway, Intent

CHATGPT_SELECTION_TIMEOUT_SECONDS = 50


class Query(Closed):
    query: str = Field(min_length=1, max_length=2000)
    channel: Literal["qwen", "deepseek", "chatgpt"] = "qwen"


class ModelChoice(Closed):
    model_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")


class KeyInput(Closed):
    api_key: SecretStr = Field(min_length=16, max_length=500)
    enable_paid: bool = True


def save_deepseek(body: KeyInput):
    import json
    import os
    from datetime import datetime
    from uuid import uuid4
    from zoneinfo import ZoneInfo

    import portalocker

    with portalocker.Lock(str(CONFIG.with_suffix(".lock")), timeout=5):
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        data.update(
            deepseek_key=body.api_key.get_secret_value(),
            paid_calls_enabled=body.enable_paid,
            billing_cap_cny="15.00",
            input_price_cny_per_million="2",
            output_price_cny_per_million="8",
            pricing_checked_on=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            pricing_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        )
        value = Settings.model_validate(data)
        temp = CONFIG.with_name("settings-" + uuid4().hex + ".tmp")
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
        os.replace(temp, CONFIG)
    return value.safe_report()


@dataclass
class Services:
    engine: Engine
    inference: Inference
    retrieval: Retrieval

    @classmethod
    def create(cls, engine: Engine, settings: Settings):
        inference = Inference(settings)
        return cls(engine, inference, Retrieval(Store(engine), Vectors(settings), inference))

    async def close(self):
        await self.inference.close()


def router(resources: Services, sessions: Sessions) -> APIRouter:
    api = APIRouter(prefix="/api/v2")

    def current(request: Request) -> Principal:
        try:
            session = sessions.get(request.cookies.get(COOKIE))
        except PermissionError:
            raise HTTPException(401, "demo_session_required") from None
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("X-WorkflowOps-CSRF", ""), session.csrf
        ):
            raise HTTPException(403, "csrf_required")
        return session.principal

    Actor = Annotated[Principal, Depends(current)]

    @api.post("/models/deepseek/configure")
    def configure(body: KeyInput, actor: Actor):
        if not actor.operator:
            raise HTTPException(403, "operator_required")
        return save_deepseek(body)

    @api.post("/models/chatgpt/select")
    async def select_model(body: ModelChoice, actor: Actor):
        if not actor.operator:
            raise HTTPException(403, "operator_required")
        import json
        import os
        from uuid import uuid4

        import portalocker

        from workflowops.model_store import ModelError
        from workflowops.models.gateway import PiAdapter

        pi = PiAdapter()
        settings = Settings.load()
        store = ModelStore(resources.engine)
        scope = actor.tenant_id + ":" + actor.subject
        call_id = None
        response = None
        error = None
        try:
            async with asyncio.timeout(CHATGPT_SELECTION_TIMEOUT_SECONDS):
                catalog = await pi.models(account_probe=True)
                if not catalog["logged_in"]:
                    raise ModelError("oauth_login_required")
                if body.model_id not in {m["id"] for m in catalog["models"]}:
                    raise ModelError("model_unavailable")
                probe = GenerationRequest(
                    messages=[Message(role="user", content="Reply only OK.")],
                    max_output_tokens=128,
                    timeout_seconds=30,
                )
                call_id = store.begin(scope, "chatgpt", body.model_id, probe, settings)
                response = await pi.generate(probe, body.model_id, scope)
                if response.finish_reason != "stop" or response.message.tool_calls:
                    raise ModelError("model_validation_incomplete")
            with portalocker.Lock(str(CONFIG.with_suffix(".lock")), timeout=5):
                data = json.loads(CONFIG.read_text(encoding="utf-8"))
                data["chatgpt_model"] = body.model_id
                Settings.model_validate(data)
                temp = CONFIG.with_name("settings-" + uuid4().hex + ".tmp")
                with temp.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(data, indent=2) + "\n")
                os.replace(temp, CONFIG)
            return {
                "model_id": response.model_id,
                "configured_id": body.model_id,
                "account_access_verified": True,
                "verification": "actual_generation",
                "usage": response.usage.model_dump(),
                "billing_kind": "subscription",
            }
        except TimeoutError:
            error = "provider_timeout"
            raise ModelError(error) from None
        except asyncio.CancelledError:
            error = "model_cancelled"
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, ModelError) else "model_validation_failed"
            raise ModelError(error) from None
        finally:
            if call_id:
                store.finish(call_id, response.usage if response else None, settings, error)
            await pi.close()

    @api.get("/models")
    async def status(actor: Actor):
        settings = Settings.load()
        ollama = {
            "channel": "qwen",
            "model_id": settings.ollama_model,
            "ready": False,
            "reason": "ollama_unavailable",
        }
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.post(
                    "http://127.0.0.1:11434/api/show", json={"model": settings.ollama_model}
                )
                response.raise_for_status()
                value = response.json()
                ollama.update(
                    ready=True,
                    reason="installed_not_acceptance",
                    context_window=value.get("model_info", {}).get("qwen3.context_length"),
                    capabilities=value.get("capabilities", []),
                )
        except (httpx.HTTPError, ValueError):
            pass
        from workflowops.models.gateway import PiAdapter

        pi = PiAdapter()
        try:
            chatgpt = await pi.models()
        finally:
            await pi.close()
        scoped = ModelStore(resources.engine).status(actor.tenant_id + ":" + actor.subject)
        if not actor.operator:
            scoped.pop("conservative_used_or_reserved_cny", None)
        return {
            "phase": "models-rag",
            "acceptance": "IN_PROGRESS",
            "channels": [
                ollama,
                {
                    "channel": "deepseek",
                    "model_id": settings.deepseek_model,
                    "ready": bool(settings.deepseek_key and settings.paid_calls_enabled),
                    "reason": "configured_unverified" if settings.deepseek_key else "key_required",
                },
                {
                    "channel": "chatgpt",
                    "model_id": settings.chatgpt_model,
                    "ready": bool(chatgpt["logged_in"] and settings.chatgpt_model),
                    "reason": "configured_unverified" if chatgpt["logged_in"] else "login_required",
                    "catalog": chatgpt,
                },
            ],
            "budget": scoped,
        }

    @api.get("/knowledge")
    def knowledge(actor: Actor):
        return resources.retrieval.store.status(actor)

    @api.post("/knowledge/search")
    async def search(body: Query, actor: Actor):
        return await resources.retrieval.search(actor, body.query)

    @api.post("/knowledge/ask")
    async def ask(body: Query, actor: Actor):
        retrieved = await resources.retrieval.search(actor, body.query)
        gateway = Gateway(Settings.load(), ModelStore(resources.engine), resources.inference)
        try:
            result = await answer(
                gateway, body.channel, actor.tenant_id + ":" + actor.subject, body.query, retrieved
            )
            return {**result, "retrieval": retrieved}
        finally:
            await gateway.close()

    @api.post("/models/classify")
    async def classify(body: Query, actor: Actor):
        request = GenerationRequest(
            messages=[
                Message(
                    role="system",
                    content="Classify intent. Missing order or operation scope requires clarification; JSON only. Classification never authorizes business writes.",
                ),
                Message(role="user", content=body.query),
            ],
            output_schema=Intent.model_json_schema(),
        )
        gateway = Gateway(Settings.load(), ModelStore(resources.engine), resources.inference)
        try:
            response = await gateway.generate(
                body.channel, request, actor.tenant_id + ":" + actor.subject
            )
            try:
                classification = Intent.model_validate_json(response.message.content or "")
            except ValueError:
                from workflowops.model_store import ModelError

                raise ModelError("intent_schema_invalid") from None
            return {
                "intent": classification.model_dump(),
                "model_id": response.model_id,
                "usage": response.usage.model_dump(),
            }
        finally:
            await gateway.close()

    return api
