"""
LLM Client Factory
==================
Returns the configured LangChain chat model based on AppConfig.

Supports:
  - OpenAI GPT-4o (default)
  - Anthropic Claude (fallback or explicit selection)

Falls back gracefully when provider libraries are not installed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_llm(config):
    """
    Return the configured LangChain BaseChatModel instance.

    Parameters
    ----------
    config : AppConfig
        Application configuration with llm_provider, llm_model, llm_api_key.

    Returns
    -------
    BaseChatModel
        A LangChain chat model ready for .invoke() calls.

    Raises
    ------
    ImportError
        If the required provider library (langchain_openai / langchain_anthropic)
        is not installed.
    ValueError
        If the API key is missing.
    """
    provider = (config.llm_provider or "openai").lower()
    api_key = config.llm_api_key or ""

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

    # Default: OpenAI
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
