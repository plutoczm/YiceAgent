import asyncio

from mcp import Client

from app.config import Settings
from app.container import build_container
from app.mcp_server import build_mcp_server


def test_mcp_tools_derive_customer_scope_from_server_context(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'mcp.db'}",
            mcp_auth_mode="disabled",
            mcp_dev_customer_id="CUST-001",
            app_env="development",
            auth_mode="dev",
            seed_demo_data=True,
        )
    )
    server = build_mcp_server(container)

    async def run():
        async with Client(server) as client:
            own = await client.call_tool("get_order", {"order_id": "ORD-1001"})
            other = await client.call_tool("get_order", {"order_id": "ORD-2001"})
            return own.structured_content, other.structured_content

    own, other = asyncio.run(run())
    assert own["found"] is True
    assert other["found"] is False
