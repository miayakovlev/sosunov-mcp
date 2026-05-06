"""Настройки приложения из окружения (ConfigMap / Secret в OpenShift)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    gateway_token: str
    tx_agent_url: str
    mcp_host: str
    mcp_port: int
    mcp_issuer_url: str
    mcp_resource_server_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        gateway_token=os.environ.get(
            "GATEWAY_TOKEN",
            "Ew88G9YHkI1Br8nT1Kk7FfH2RZFSEzU_iTVAxHow3bo",
        ),
        tx_agent_url=os.environ.get("TX_AGENT_URL", "http://127.0.0.1:9200/mcp"),
        mcp_host=os.environ.get("MCP_HOST", "127.0.0.1"),
        mcp_port=int(os.environ.get("MCP_PORT", "9101")),
        mcp_issuer_url=os.environ.get("MCP_ISSUER_URL", "https://myday.local"),
        mcp_resource_server_url=os.environ.get(
            "MCP_RESOURCE_SERVER_URL",
            "https://myday.local",
        ),
    )
