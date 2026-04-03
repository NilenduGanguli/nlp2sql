"""
LLM Client Factory
==================
Returns the configured LangChain chat model based on AppConfig.

Supports:
  - OpenAI GPT-4o (default)
  - Anthropic Claude (fallback or explicit selection)
  - Google Vertex AI (Gemini via genai.Client — works with org proxies)

Falls back gracefully when provider libraries are not installed.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, PrivateAttr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vertex AI: minimal wrapper around google.genai.Client
# ---------------------------------------------------------------------------

class _VertexGenAIChat(BaseChatModel):
    """
    Thin LangChain BaseChatModel that delegates to google.genai.Client directly.

    Using client.models.generate_content() instead of LangChain's own Google
    wrappers makes this work with org proxies where those wrappers force
    credential patterns that don't apply.

    The underlying genai.Client is recreated every `ttl_seconds` (default 14 min)
    so that proxy-managed auth tokens are always fresh.
    """

    _client: Any = PrivateAttr(default=None)
    _client_created: float = PrivateAttr(default=0.0)
    _client_factory: Any = PrivateAttr()
    _ttl: int = PrivateAttr(default=14 * 60)
    model: str
    temperature: float = 0.0
    max_output_tokens: int = 4096
    thinking_budget: int = 0

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, *, client_factory: Any, ttl_seconds: int = 14 * 60, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client_factory = client_factory
        self._ttl = ttl_seconds
        # Eagerly create the first client so the first query is not slower
        self._client = client_factory()
        self._client_created = time.monotonic()

    def _get_client(self) -> Any:
        """Return the current client, rebuilding it if the TTL has expired."""
        if time.monotonic() - self._client_created > self._ttl:
            self._client = self._client_factory()
            self._client_created = time.monotonic()
            logger.info("Vertex AI genai.Client refreshed (TTL=%ds)", self._ttl)
        return self._client

    @property
    def _llm_type(self) -> str:
        return "genai-vertex"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        from google.genai import types

        system_text = ""
        contents: List[Any] = []

        for msg in messages:
            if msg.type == "system":
                system_text = str(msg.content)
            elif msg.type == "human":
                contents.append(
                    types.Content(role="user", parts=[types.Part(text=str(msg.content))])
                )
            elif msg.type == "ai":
                contents.append(
                    types.Content(role="model", parts=[types.Part(text=str(msg.content))])
                )

        cfg = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget),
        )
        if system_text:
            cfg.system_instruction = system_text

        client = self._get_client()
        response = client.models.generate_content(
            model=self.model,
            contents=contents,
            config=cfg,
        )

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=response.text or ""))]
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_llm(config):
    """
    Return the configured LangChain BaseChatModel instance.

    Parameters
    ----------
    config : AppConfig
        Application configuration with llm_provider, llm_model, llm_api_key.
        For Vertex AI, also vertex_project and vertex_location are used.

    Returns
    -------
    BaseChatModel
        A LangChain chat model ready for .invoke() calls.

    Raises
    ------
    ImportError
        If the required provider library is not installed.
    ValueError
        If required credentials are missing or cannot be loaded.
    """
    provider = (config.llm_provider or "openai").lower()
    api_key = config.llm_api_key or ""

    # ── Anthropic ─────────────────────────────────────────────────────────────
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ImportError(
                "langchain-anthropic is required for Anthropic provider. "
                "Install it with: pip install langchain-anthropic"
            ) from exc

        model_name = config.llm_model or "claude-sonnet-4-6"
        logger.info("Using Anthropic provider: model=%s", model_name)
        return ChatAnthropic(
            model=model_name,
            anthropic_api_key=api_key or None,
            temperature=0,
            max_tokens=4096,
        )

    # ── Google Vertex AI ──────────────────────────────────────────────────────
    if provider == "vertex":
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "google-genai is required for Vertex AI provider. "
                "Install with: pip install google-genai"
            ) from exc

        model_name = config.llm_model or "gemini-2.5-pro"
        project = getattr(config, "vertex_project", None) or os.getenv("VERTEX_PROJECT", "")
        location = getattr(config, "vertex_location", None) or os.getenv("VERTEX_LOCATION", "us-central1")
        thinking_budget = int(
            getattr(config, "vertex_thinking_budget", None)
            or os.getenv("VERTEX_THINKING_BUDGET", "8192")
        )

        # Credentials are optional — omit GOOGLE_APPLICATION_CREDENTIALS when
        # using an org proxy that handles auth transparently.
        credentials = None
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if creds_path:
            try:
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    creds_path,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                logger.info("Vertex AI: loaded service account from %s", creds_path)
            except Exception as exc:
                raise ValueError(
                    f"GOOGLE_APPLICATION_CREDENTIALS is set to '{creds_path}' "
                    f"but the file could not be loaded: {exc}"
                ) from exc
        else:
            logger.info("Vertex AI: no GOOGLE_APPLICATION_CREDENTIALS — using proxy/ADC")

        # Capture locals in the factory closure so each refresh uses the same config
        _project, _location, _credentials = project, location, credentials

        def _make_client() -> Any:
            return genai.Client(
                vertexai=True,
                project=_project or None,
                location=_location,
                credentials=_credentials,
            )

        logger.info(
            "Vertex AI configured: project=%s, location=%s, model=%s, client_ttl=14min",
            project or "(default)", location, model_name,
        )
        return _VertexGenAIChat(
            client_factory=_make_client,
            ttl_seconds=14 * 60,
            model=model_name,
            temperature=0.0,
            max_output_tokens=8192,
            thinking_budget=thinking_budget,
        )

    # ── OpenAI (default) ──────────────────────────────────────────────────────
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "langchain-openai is required for OpenAI provider. "
            "Install it with: pip install langchain-openai"
        ) from exc

    model_name = config.llm_model or "gpt-4o"
    logger.info("Using OpenAI provider: model=%s", model_name)
    return ChatOpenAI(
        model=model_name,
        openai_api_key=api_key or None,
        temperature=0,
        max_tokens=4096,
    )
