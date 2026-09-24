"""Adaptable Azure AI Foundry client layer.

The rest of the app never hard-codes a model. Instead it asks for a *role*
(SQL generation, enrichment, embeddings) and this module resolves that role to
a concrete Azure deployment via environment variables. Swapping models — e.g.
moving SQL generation from ``gpt-4.1`` to a reasoning model like ``o4-mini`` —
is therefore a configuration change, not a code change.

Suggested Azure AI Foundry deployments per role
-----------------------------------------------
- SQL generation : gpt-4.1            (alt: gpt-4o, o4-mini for heavy reasoning)
- Enrichment     : gpt-4o-mini        (alt: gpt-4.1-mini)
- Embeddings     : text-embedding-3-large (alt: text-embedding-3-small)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from openai import AzureOpenAI

logger = logging.getLogger(__name__)


class ModelRole(str, Enum):
    """Logical purpose a model is used for, decoupled from the deployment name."""

    SQL = "sql"
    ENRICH = "enrich"
    EMBED = "embed"


# Per-role env var + sensible Foundry default. Override any of these to swap models.
_ROLE_ENV: dict[ModelRole, tuple[str, str]] = {
    ModelRole.SQL: ("AZURE_OPENAI_SQL_DEPLOYMENT", "gpt-4.1"),
    ModelRole.ENRICH: ("AZURE_OPENAI_ENRICH_DEPLOYMENT", "gpt-4o-mini"),
    ModelRole.EMBED: ("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large"),
}

# Deployment-name substrings that identify OpenAI "reasoning" models. These reject
# `temperature` and use `max_completion_tokens` instead of `max_tokens`.
_REASONING_HINTS = ("o1", "o3", "o4", "gpt-5")
_REASONING_EXCLUDES = ("gpt-5-chat",)


@dataclass(frozen=True)
class ModelSpec:
    deployment: str
    is_reasoning: bool

    @classmethod
    def resolve(cls, role: ModelRole) -> "ModelSpec":
        env_var, default = _ROLE_ENV[role]
        deployment = os.getenv(env_var, default)
        low = deployment.lower()
        is_reasoning = any(h in low for h in _REASONING_HINTS) and not any(
            x in low for x in _REASONING_EXCLUDES
        )
        return cls(deployment=deployment, is_reasoning=is_reasoning)


def build_client(client: AzureOpenAI | None = None) -> AzureOpenAI:
    """Create (or pass through) an Azure OpenAI client bound to the Foundry endpoint."""
    if client is not None:
        return client
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


class LLM:
    """Thin, model-agnostic wrapper over Azure OpenAI chat + embeddings.

    Callers select behaviour by *role*; this class hides the differences between
    standard chat models and reasoning models so higher layers stay adaptable.
    """

    def __init__(self, client: AzureOpenAI | None = None):
        self.client = build_client(client)

    def chat(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ):
        """Single chat completion. Tool-calling is passed through when ``tools`` is set."""
        spec = ModelSpec.resolve(role)
        kwargs: dict[str, Any] = {"model": spec.deployment, "messages": messages}

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_format is not None:
            kwargs["response_format"] = response_format

        # Reasoning models reject `temperature` and rename the token-limit param.
        if spec.is_reasoning:
            if max_tokens is not None:
                kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["temperature"] = temperature
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens

        return self.client.chat.completions.create(**kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        spec = ModelSpec.resolve(ModelRole.EMBED)
        resp = self.client.embeddings.create(model=spec.deployment, input=texts)
        return [d.embedding for d in resp.data]
