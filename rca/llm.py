"""Azure OpenAI clients: the model router for chat, a separate deployment for embeddings."""

from __future__ import annotations

import time
from collections import deque
from functools import lru_cache
from typing import Callable

from langsmith.wrappers import wrap_openai
from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from rca.config import env, require_env


EMBED_BATCH = 32
# This machine's resolver fails to resolve the Azure host in bursts lasting up to
# a minute or two (POC v1 saw the same with Qdrant). Retry long enough to outlast one.
EMBED_ATTEMPTS = 12
EMBED_MAX_CHARS = 24_000          # ~6k tokens, well under the model's 8k limit

# The embedding deployment allows 150k tokens per minute. Bursting past it makes
# the endpoint drop connections, so requests are paced below the limit instead.
EMBED_TOKENS_PER_MINUTE = int(env("EMBEDDING_TPM", "150000"))
PACING_HEADROOM = 0.85
CHARS_PER_TOKEN = 3.0             # conservative for code-heavy text

TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


@lru_cache(maxsize=1)
def embedding_client() -> OpenAI:
    return OpenAI(
        api_key=require_env("EMBEDDING_MODEL_KEY"),
        base_url=require_env("EMBEDDING_MODEL_ENDPOINT"),
        timeout=float(env("EMBEDDING_TIMEOUT", "60")),
        max_retries=2,
    )


@lru_cache(maxsize=1)
def chat_client() -> OpenAI:
    # Wrapped so every chat call shows up in LangSmith under the pipeline step that made it.
    return wrap_openai(OpenAI(
        api_key=require_env("LLM_MODEL_KEY"),
        base_url=require_env("LLM_MODEL_ENDPOINT"),
        timeout=float(env("LLM_TIMEOUT", "180")),
        max_retries=int(env("MODEL_MAX_RETRIES", "3")),
    ), chat_name="azure_openai_chat")


def chat_model() -> str:
    return require_env("LLM_MODEL_DEPLOYMENT_NAME")


def embedding_model() -> str:
    return require_env("EMBEDDING_MODEL_DEPLOYMENT_NAME")


def _embed_batch(texts: list[str]) -> list[list[float]]:
    response = embedding_client().embeddings.create(
        model=embedding_model(),
        input=[text[:EMBED_MAX_CHARS] or " " for text in texts],
    )
    return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def with_retry(call: Callable, attempts: int = EMBED_ATTEMPTS):
    """Retries transient failures (DNS blips, dropped connections, throttling) with linear backoff."""

    for attempt in range(1, attempts + 1):
        try:
            return call()
        except TRANSIENT:
            if attempt == attempts:
                raise
            time.sleep(min(30, 5 * attempt))
    raise RuntimeError("unreachable")


def _embed_with_retry(texts: list[str]) -> list[list[float]]:
    return with_retry(lambda: _embed_batch(texts))


class _TokenPacer:
    """Sliding one-minute window of estimated tokens sent."""

    def __init__(self, per_minute: int):
        self.budget = per_minute * PACING_HEADROOM
        self.sent: deque[tuple[float, float]] = deque()

    def wait_for(self, tokens: float) -> None:
        while True:
            now = time.monotonic()
            while self.sent and now - self.sent[0][0] > 60:
                self.sent.popleft()
            used = sum(t for _, t in self.sent)
            if not self.sent or used + tokens <= self.budget:
                self.sent.append((now, tokens))
                return
            time.sleep(max(0.5, 60 - (now - self.sent[0][0])))


def embed(
    texts: list[str],
    on_batch: Callable[[int, list[list[float]]], None] | None = None,
) -> list[list[float]]:
    """Embeds many texts in order, paced under the deployment's token limit.

    `on_batch(index, vectors)` runs after each batch, so a caller can save
    progress as it goes.
    """

    pacer = _TokenPacer(EMBED_TOKENS_PER_MINUTE)
    vectors: list[list[float]] = []
    for index in range(0, len(texts), EMBED_BATCH):
        batch = texts[index:index + EMBED_BATCH]
        pacer.wait_for(sum(min(len(t), EMBED_MAX_CHARS) for t in batch) / CHARS_PER_TOKEN)
        result = _embed_with_retry(batch)
        vectors.extend(result)
        if on_batch:
            on_batch(index // EMBED_BATCH, result)
    return vectors


def embed_one(text: str) -> list[float]:
    return _embed_with_retry([text])[0]
