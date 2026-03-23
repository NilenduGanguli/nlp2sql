"""
LLM Client Factory
==================
Returns the configured LangChain chat model based on AppConfig.

Supports:
  - OpenAI GPT-4o (default)
  - Anthropic Claude (fallback or explicit selection)
  - Google Vertex AI (Gemini models via Application Default Credentials)

Falls back gracefully when provider libraries are not installed.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_llm(config):
    """
    Return the configured LangChain BaseChatModel instance.

    Parameters
    ----------
    config : AppConfig
        Application configuration with llm_provider, llm_model, llm_api_key.
        For Vertex AI, also vertex_project and vertex_location are used;
        authentication relies on Application Default Credentials (ADC).

    Returns
    -------
    BaseChatModel
        A LangChain chat model ready for .invoke() calls.

    Raises
    ------
    ImportError
        If the required provider library is not installed.
    ValueError
        If required credentials are missing.
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
            from langchain_google_vertexai import ChatVertexAI
        except ImportError as exc:
            raise ImportError(
                "langchain-google-vertexai is required for Vertex AI provider. "
                "Install it with: pip install langchain-google-vertexai"
            ) from exc

        model_name = config.llm_model or "gemini-1.5-pro"
        project = getattr(config, "vertex_project", None) or os.getenv("VERTEX_PROJECT", "")
        location = getattr(config, "vertex_location", None) or os.getenv("VERTEX_LOCATION", "us-central1")

        logger.info(
            "Using Vertex AI provider: project=%s, location=%s, model=%s",
            project or "(ADC default)", location, model_name,
        )
        # Authentication via google-auth Application Default Credentials:
        #   - GOOGLE_APPLICATION_CREDENTIALS → service account JSON file path
        #   - `gcloud auth application-default login` (local dev)
        #   - Workload Identity (GKE / Cloud Run)
        return ChatVertexAI(
            model_name=model_name,
            project=project or None,
            location=location,
            temperature=0,
            max_output_tokens=4096,
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
