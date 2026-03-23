"""
Application-level configuration for the KnowledgeQL NLP-to-SQL system.

All values are read from environment variables (or a .env file).
The AppConfig composes the lower-level OracleConfig and GraphConfig from
knowledge_graph/config.py and adds LLM provider settings and query limits.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from knowledge_graph.config import GraphConfig, OracleConfig

try:
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class LLMProvider(str, Enum):
        OPENAI = "openai"
        ANTHROPIC = "anthropic"

    class AppConfig(BaseSettings):
        """Top-level application configuration."""

        # --- LLM provider settings ---
        llm_provider: str = Field(default="openai", validation_alias="LLM_PROVIDER")
        llm_model: str = Field(default="gpt-4o", validation_alias="LLM_MODEL")
        llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
        llm_fallback_provider: str = Field(
            default="anthropic", validation_alias="LLM_FALLBACK_PROVIDER"
        )

        # --- Query execution limits ---
        max_result_rows: int = Field(
            default=10000, validation_alias="MAX_RESULT_ROWS"
        )
        query_timeout_seconds: int = Field(
            default=30, validation_alias="QUERY_TIMEOUT_SECONDS"
        )
        max_sql_retries: int = Field(default=3)
        token_budget: int = Field(default=4000)

        # --- Demo / live mode ---
        demo_mode: bool = Field(default=True, validation_alias="DEMO_MODE")

        # --- Composed sub-configs (not settable via env directly) ---
        oracle: OracleConfig = Field(default_factory=OracleConfig)
        graph: GraphConfig = Field(default_factory=GraphConfig)

        model_config = {"env_file": ".env", "extra": "ignore"}

        def __init__(self, **data):
            super().__init__(**data)
            # Resolve LLM API key from multiple possible env vars if not set
            if not self.llm_api_key:
                if self.llm_provider.lower() == "anthropic":
                    self.llm_api_key = os.getenv("ANTHROPIC_API_KEY", "")
                else:
                    self.llm_api_key = os.getenv("OPENAI_API_KEY", "")
                # Last resort: try both
                if not self.llm_api_key:
                    self.llm_api_key = (
                        os.getenv("OPENAI_API_KEY", "")
                        or os.getenv("ANTHROPIC_API_KEY", "")
                    )

except ImportError:
    # Graceful degradation if pydantic-settings is not installed
    class LLMProvider(str, Enum):  # type: ignore[no-redef]
        OPENAI = "openai"
        ANTHROPIC = "anthropic"

    class AppConfig:  # type: ignore[no-redef]
        """Fallback AppConfig when pydantic-settings is unavailable."""

        def __init__(self, **kwargs):
            self.llm_provider = kwargs.get(
                "llm_provider", os.getenv("LLM_PROVIDER", "openai")
            )
            self.llm_model = kwargs.get(
                "llm_model", os.getenv("LLM_MODEL", "gpt-4o")
            )
            self.llm_api_key = kwargs.get(
                "llm_api_key",
                os.getenv("LLM_API_KEY")
                or os.getenv("OPENAI_API_KEY", "")
                or os.getenv("ANTHROPIC_API_KEY", ""),
            )
            self.llm_fallback_provider = kwargs.get(
                "llm_fallback_provider",
                os.getenv("LLM_FALLBACK_PROVIDER", "anthropic"),
            )
            self.max_result_rows = int(
                kwargs.get("max_result_rows", os.getenv("MAX_RESULT_ROWS", "10000"))
            )
            self.query_timeout_seconds = int(
                kwargs.get(
                    "query_timeout_seconds",
                    os.getenv("QUERY_TIMEOUT_SECONDS", "30"),
                )
            )
            self.max_sql_retries = int(kwargs.get("max_sql_retries", 3))
            self.token_budget = int(kwargs.get("token_budget", 4000))
            self.demo_mode = bool(
                kwargs.get(
                    "demo_mode",
                    os.getenv("DEMO_MODE", "true").lower() in ("true", "1", "yes"),
                )
            )
            self.oracle = kwargs.get("oracle", OracleConfig())
            self.graph = kwargs.get("graph", GraphConfig())
