"""Connect the agents to the read-only MCP server (mcp_server/server.py) and read its replies."""

import json
import os
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient

from src import config

_tools = None


def server_connection() -> dict:
    """Start the MCP server as a small background process that talks over stdin/stdout.
    Only the settings it needs are passed on (which data set, and where the database is)."""
    env = {"DATA_SET": config.DATA_SET, "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    if os.getenv("DB_PATH"):
        env["DB_PATH"] = os.environ["DB_PATH"]
    return {"command": sys.executable, "args": ["-m", "mcp_server.server"], "transport": "stdio",
            "cwd": str(config.ROOT), "env": env}


async def get_tools() -> dict:
    """The MCP tools as LangChain tools, by name. Loaded once per process."""
    global _tools
    if _tools is None:
        client = MultiServerMCPClient({"erp": server_connection()})
        _tools = {t.name: t for t in await client.get_tools()}
    return _tools


def parse(result) -> dict:
    """MCP replies arrive as a list of content blocks; the tool's dict is the JSON text inside."""
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
    else:
        text = str(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text[:300]}


async def call(name: str, **args) -> dict:
    tools = await get_tools()
    return parse(await tools[name].ainvoke(args))
