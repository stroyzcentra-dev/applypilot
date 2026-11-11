"""Thin wrapper over the Anthropic SDK for structured agent calls.

All agents go through `structured()`, so model choice, refusal handling,
prompt caching and fallbacks live in exactly one place.
"""

from __future__ import annotations

from typing import TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"


class LLMRefusal(RuntimeError):
    """The model declined the request; the caller should skip, not retry blindly."""


class LLMBadOutput(RuntimeError):
    """The model returned something that didn't parse into the expected schema."""


class LLM:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL):
        self.client = client or anthropic.Anthropic()
        self.model = model

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> T:
        """Run one call whose answer must validate against `schema`.

        `system` should be the stable part of the prompt (instructions plus the
        fact bank). It's marked for caching, so scoring 40 jobs against the same
        profile reads it from cache instead of paying for it 40 times.
        """
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMRefusal(f"model declined (category: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMBadOutput("response was cut off at max_tokens")
        if response.parsed_output is None:
            raise LLMBadOutput("response did not match the schema")
        return response.parsed_output
