from fastapi import APIRouter

from backend.app.presentation.api.routes import (
    agent,
    business_analytics,
    chat,
    config,
    health,
    knowledge,
    sql_review,
    text2sql,
    warehouse_design,
)


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(knowledge.router)
api_router.include_router(chat.router)
api_router.include_router(text2sql.router)
api_router.include_router(sql_review.router)
api_router.include_router(warehouse_design.router)
api_router.include_router(agent.router)
api_router.include_router(business_analytics.router)
