"""LangSmith tracing, configured by the LANGCHAIN_* variables in .env.

One RCA is one trace: a root "RCA" run, a child run per pipeline step, and the
Azure chat calls nested under the step that made them (with prompts, model and
token counts). If tracing is off or LangSmith is unreachable, these helpers do
nothing and the RCA runs as normal.
"""

from __future__ import annotations

import logging
import warnings
from functools import lru_cache
from uuid import UUID, uuid4

from langsmith import get_current_run_tree, trace, traceable
from langsmith.run_trees import get_cached_client
from langsmith.utils import tracing_is_enabled

from rca.config import env      # importing rca.config loads .env before LangSmith reads it


__all__ = ["traceable", "trace", "annotate", "feedback", "flush", "new_trace_id", "project", "eval_project",
           "enabled"]

log = logging.getLogger(__name__)

# LangSmith serialises the SDK's ParsedChatCompletion, whose generic `parsed` field makes pydantic
# warn on every structured-output call. The trace is recorded correctly; the warning is only noise.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning)

VERDICT_SCORE = {"correct": 1.0, "partly": 0.5, "wrong": 0.0}


def enabled() -> bool:
    return bool(tracing_is_enabled())


def project() -> str:
    return env("LANGCHAIN_PROJECT", "default")


def eval_project() -> str:
    """Evaluation runs go to their own project so they don't bury the demo's traces."""
    return f"{project()} - evals"


def new_trace_id() -> UUID:
    return uuid4()


def annotate(**metadata) -> None:
    """Adds metadata to the run in progress (shown and filterable in LangSmith)."""
    run = get_current_run_tree()
    if run is not None:
        run.add_metadata(metadata)


def feedback(trace_id: UUID | str | None, verdict: str, comment: str = "") -> None:
    """Attaches the support engineer's verdict to the trace, so wrong answers can be found later."""
    if not trace_id or not enabled():
        return
    try:
        client = get_cached_client()
        client.create_feedback(trace_id, key="user_verdict", score=VERDICT_SCORE.get(verdict), value=verdict,
                               comment=comment or None, trace_id=trace_id, session_id=_project_id())
    except Exception as error:                       # feedback must never break the UI
        log.warning("LangSmith feedback failed: %s", error)


@lru_cache(maxsize=1)
def _project_id():
    return get_cached_client().read_project(project_name=project()).id


def flush() -> None:
    """Sends queued runs before a script exits."""
    if not enabled():
        return
    try:
        get_cached_client().flush()
    except Exception as error:
        log.warning("LangSmith flush failed: %s", error)
