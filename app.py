"""RCA Agent v2 - Streamlit UI.

Layout and theme follow POC v1, which management approved. The backend is the v2
pipeline (rca/pipeline.py).

Run:  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import time

import psycopg
import streamlit as st

from rca import tracing
from rca.pipeline import Pipeline
from rca.runs import save_feedback, save_run
from rca.schema import RcaResponse, TraceNode
from ui.evidence import render_evidence_item, render_step
from ui.flow import DONE, FAILED, PENDING, RUNNING, SKIPPED, render_flow
from ui.theme import inject_theme


st.set_page_config(page_title="Agentic RCA Assistant", page_icon="🔎", layout="wide")
st.title("🔎 Agentic Root Cause Analysis Assistant")
st.caption("REMAN COBOL support · ServiceNow incident history and AWS Transform documentation")
inject_theme()


PHASES = [
    ("understand", "Understand the report", ("normalise", "rewrite")),
    ("search", "Search tickets and documentation", ("search",)),
    ("match", "Match a known issue", ("gate",)),
    ("investigate", "Investigate further", ("investigate",)),
    ("write", "Write the root cause analysis", ("write_rca", "write_rca_retry")),
    ("score", "Check citations and score confidence", ("score",)),
]
PHASE_OF = {node: key for key, _, nodes in PHASES for node in nodes}

ESCALATION_NOTICE = {
    "code_change": ("warning", "This looks like a program logic fault. No operator procedure will fix it; "
                               "it needs a developer and a code change."),
    "code_change_if_repeated": ("info", "If it fails again from a fresh session, it is a program fault that "
                                        "needs a developer and a code change."),
    "not_reproducible": ("info", "The evidence cannot separate this from a transient condition. "
                                 "Retry or reopen first; investigate further only if it recurs."),
    "unknown": ("warning", "The evidence does not establish a cause. Treat the analysis below as a lead, "
                           "not a conclusion."),
}

@st.cache_resource(show_spinner=False)
def get_pipeline() -> Pipeline:
    return Pipeline()


def run_pipeline(query: str, on_node, trace_id) -> RcaResponse:
    """Runs the pipeline, reconnecting once if the database connection went stale."""
    extra = {"run_id": trace_id, "tags": ["ui"]}
    try:
        return get_pipeline().run(query, on_node=on_node, langsmith_extra=extra)
    except psycopg.OperationalError:
        get_pipeline.clear()
        extra["run_id"] = tracing.new_trace_id()       # the failed attempt already used the first ID
        return get_pipeline().run(query, on_node=on_node, langsmith_extra=extra)


# ---------------------------------------------------------------------------
# Live progress
# ---------------------------------------------------------------------------


class Progress:
    def __init__(self, slot):
        self.slot = slot
        self.started = time.monotonic()
        self.state = {key: {"status": PENDING, "duration": None} for key, _, _ in PHASES}
        self.state["understand"]["status"] = RUNNING
        self.notes: dict[str, tuple[str, str]] = {}
        self.html = ""
        self.paint("Starting investigation")

    def node(self, node: TraceNode) -> None:
        key = PHASE_OF.get(node.name)
        if key is None:
            return
        phase = self.state[key]
        phase["duration"] = (phase["duration"] or 0.0) + node.ms / 1000
        phase["status"] = DONE

        if node.name == "normalise" and node.detail.get("ids"):
            ids = "; ".join(f"{k}: {', '.join(v)}" for k, v in node.detail["ids"].items())
            self.notes["understand"] = (f"{node.detail.get('system', '')} · ", ids)
        if node.name == "gate" and node.detail.get("top_title"):
            if node.detail.get("strong"):
                self.notes["match"] = ("Best match ", node.detail["top_title"].split(":")[0])
            else:
                self.notes["match"] = ("", "No known issue clearly fits, so investigating further")
        if node.name == "investigate":
            searches = node.detail.get("searches", [])
            found = sum(len(s.get("docs", [])) for s in searches)
            self.notes["investigate"] = (f"{len(searches)} targeted searches · ",
                                         f"{found} documentation sections found")

        keys = [k for k, _, _ in PHASES]
        position = keys.index(key)
        if position + 1 < len(keys) and self.state[keys[position + 1]]["status"] == PENDING:
            self.state[keys[position + 1]]["status"] = RUNNING
        self.paint(f"Investigating · stage {min(position + 2, len(keys))} of {len(keys)}")

    def finish(self, response: RcaResponse) -> None:
        for phase in self.state.values():
            if phase["status"] in (PENDING, RUNNING):
                phase["status"] = SKIPPED
        self.paint("Investigation complete" if response.status != "needs_input" else "More detail needed")

    def fail(self) -> None:
        for phase in self.state.values():
            if phase["status"] == RUNNING:
                phase["status"] = FAILED
        self.paint("Investigation failed")

    def paint(self, headline: str) -> None:
        phases = []
        for position, (key, label, _) in enumerate(PHASES, 1):
            s = self.state[key]
            phases.append({"key": key, "label": label, "position": position, "status": s["status"],
                           "elapsed": s["duration"], "duration": s["duration"] if s["status"] == DONE else None})
        self.html = render_flow(phases, headline, None, time.monotonic() - self.started, notes=self.notes)
        self.slot.markdown(self.html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


def render_result(response: RcaResponse, latency_ms: int, run_id: str | None, trace_id: str | None) -> None:
    if response.status == "needs_input":
        st.warning("I need a little more detail before I can search the incident history:\n\n"
                   + "\n".join(f"• {q}" for q in response.clarifying_questions))
        return

    if response.status == "diagnosed":
        st.info(f"Analysis completed in {latency_ms / 1000:.1f}s. No past incident matches this report, so the "
                "analysis is worked out from the error code and the program documentation.")
    elif response.status == "no_match":
        st.warning(f"Analysis finished in {latency_ms / 1000:.1f}s, but no known issue matches this report. "
                   "Treat the analysis below as a lead to check.")
    else:
        st.success(f"Analysis completed in {latency_ms / 1000:.1f}s")

    st.subheader("Root cause")
    st.write(response.root_cause)

    notice = ESCALATION_NOTICE.get(response.escalation)
    if notice:
        getattr(st, notice[0])(notice[1])

    # Pattern-card IDs and draft status are internal; support staff see the issue title only.
    evidence = [item for item in response.evidence if item.kind != "card"]
    recurrence = response.recurrence

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("RCA confidence", f"{response.confidence.level.title()} · {round(response.confidence.score * 100)}%")
    with col2:
        st.metric("Similar past incidents", recurrence.events if recurrence else 0)
    with col3:
        st.metric("Evidence cited", len(evidence))

    if response.pattern:
        st.markdown(f"**Matches known issue:** {response.pattern.title}")
    with st.expander("Why this confidence"):
        for reason in response.confidence.reasons:
            st.markdown(f"- {reason}")

    if recurrence and recurrence.events > 1:
        problems = f" Problem record {', '.join(recurrence.problems)} is open." if recurrence.problems else ""
        st.warning(f"**Recurring failure** — {recurrence.events} past events ({recurrence.tickets} tickets) "
                   f"between {recurrence.first_seen} and {recurrence.last_seen}.{problems} "
                   "A fault seen this often needs a permanent fix, not another restart.")

    st.subheader("Evidence")
    for item in evidence:
        st.markdown(render_evidence_item(item), unsafe_allow_html=True)

    st.subheader("Resolution steps")
    for number, step in enumerate(response.resolution_steps, 1):
        st.markdown(render_step(number, step), unsafe_allow_html=True)

    st.subheader("Preventive actions")
    for action in response.preventive_actions:
        st.markdown(f"🔒 {action}")

    if response.missing_information:
        st.subheader("What to check to confirm")
        for item in response.missing_information:
            st.markdown(f"❓ {item}")

    if response.alternatives:
        with st.expander("Other possible causes"):
            for alt in response.alternatives:
                st.markdown(f"- {alt.title}")

    render_feedback(run_id, trace_id)

    with st.expander("View agent trace"):
        rows = [{"step": n.name, "ms": n.ms, "model": n.model or "", "tokens": n.tokens} for n in response.trace]
        st.dataframe(rows, hide_index=True, width="stretch")
        st.json(response.model_dump(mode="json"), expanded=False)
        if trace_id and tracing.enabled():
            st.caption(f"LangSmith project “{tracing.project()}”, trace ID {trace_id}")


def render_feedback(run_id: str | None, trace_id: str | None) -> None:
    if not run_id:
        return
    if st.session_state.get("feedback_sent") == run_id:
        st.info("Thanks, your feedback was saved.")
        return
    with st.form(key=f"feedback-{run_id}"):
        st.markdown("**Was this analysis right?**")
        verdict = st.radio("Verdict", ["correct", "partly", "wrong"], horizontal=True, label_visibility="collapsed")
        actual_fix = st.text_input("What actually fixed it (optional)")
        if st.form_submit_button("Save feedback"):
            try:
                save_feedback(get_pipeline().searcher.conn, run_id, verdict, actual_fix)
                tracing.feedback(trace_id, verdict, actual_fix)
                st.session_state["feedback_sent"] = run_id
                st.rerun()
            except Exception as error:
                st.error(f"Could not save feedback: {type(error).__name__}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


with st.sidebar:
    st.header("Workflow")
    st.markdown("\n".join(f"{i}. {label}" for i, (_, label, _) in enumerate(PHASES, 1)))

query = st.text_area("Describe the production incident", height=160, placeholder=(
    "Example:\nLMS won't open for all operators, error # 984 IN0011.MST\n\n"
    "or paste an AJS alert:\nIncident AJS002E ABEND=S000 U4005  F8RH0430(JOB42942)  JCL=A.F8RH0410"))

if st.button("Generate RCA"):
    if not query.strip():
        st.warning("Please enter the incident details.")
        st.stop()

    progress = Progress(st.empty())
    started = time.monotonic()
    trace_id = tracing.new_trace_id()
    try:
        response = run_pipeline(query, progress.node, trace_id)
    except Exception as error:
        progress.fail()
        st.error(f"The investigation failed: {type(error).__name__}: {error}")
        st.stop()
    latency_ms = int((time.monotonic() - started) * 1000)
    progress.finish(response)

    run_id = None
    if response.status != "needs_input":
        try:
            run_id = save_run(get_pipeline().searcher.conn, response, latency_ms)
        except Exception:
            run_id = None
    st.session_state["last"] = {"response": response.model_dump(mode="json"), "latency_ms": latency_ms,
                                "run_id": run_id, "trace_id": str(trace_id), "flow": progress.html}
    render_result(response, latency_ms, run_id, str(trace_id))

elif "last" in st.session_state:
    last = st.session_state["last"]
    st.markdown(last["flow"], unsafe_allow_html=True)
    render_result(RcaResponse.model_validate(last["response"]), last["latency_ms"], last["run_id"],
                  last.get("trace_id"))
