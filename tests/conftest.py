"""Tests never send traces to LangSmith (set before rca.config loads .env, which doesn't override)."""

import os

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
