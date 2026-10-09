"""Vertical progress timeline rendered into a Streamlit placeholder (from POC v1)."""

from html import escape

PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"


SPIN_SECONDS = 0.9


BADGES = {
    DONE: "&#10003;",
    RUNNING: "",
    SKIPPED: "&#8211;",
    FAILED: "!",
    PENDING: ""
}


FLOW_STYLES = """
<style>
.rca-flow-wrap {
    border: 1px solid var(--rca-border, rgba(28, 62, 112, 0.16));
    border-radius: 20px;
    padding: 1rem 1.1rem 1.15rem 1.1rem;
    margin-bottom: 1rem;
    background: linear-gradient(180deg, var(--rca-panel-from, #ffffff) 0%, var(--rca-panel-to, #f6f9fe) 100%);
    box-shadow: inset 0 1px 0 var(--rca-inset, rgba(11, 29, 52, 0.04));
}
.rca-flow-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.55rem;
    margin-bottom: 0.75rem;
    color: var(--rca-text, #12243d);
    font-size: 0.95rem;
    font-weight: 700;
}
.rca-flow-head .rca-flow-timer {
    color: var(--rca-accent-text, #12559c);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}
.rca-flow-bar {
    height: 4px;
    border-radius: 999px;
    background: var(--rca-track, rgba(28, 62, 112, 0.12));
    overflow: hidden;
    margin-bottom: 0.9rem;
}
.rca-flow-bar span {
    display: block;
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--rca-accent-strong, #1565d8) 0%, var(--rca-success, #1a8f60) 100%);
    transition: width 0.35s ease;
}
.rca-flow {
    display: flex;
    flex-direction: column;
    margin: 0;
    padding: 0;
    list-style: none;
}
.rca-node {
    position: relative;
    display: grid;
    grid-template-columns: 1.55rem minmax(0, 1fr) auto;
    align-items: start;
    column-gap: 0.8rem;
    padding: 0.6rem 0.7rem;
    border: 1px solid transparent;
    border-radius: 12px;
}
.rca-rail {
    position: relative;
    display: flex;
    justify-content: center;
    align-self: stretch;
    min-height: 1.55rem;
}
/* the line linking one step to the next; the last step has nothing to link to */
.rca-node:not(:last-child) .rca-rail::after {
    content: "";
    position: absolute;
    top: 1.75rem;
    bottom: -0.5rem;
    left: 50%;
    width: 2px;
    margin-left: -1px;
    border-radius: 999px;
    background: var(--rca-track, rgba(28, 62, 112, 0.12));
}
.rca-node.is-done:not(:last-child) .rca-rail::after,
.rca-node.is-skipped:not(:last-child) .rca-rail::after {
    background: var(--rca-success, #1a8f60);
    opacity: 0.55;
}
.rca-node-badge {
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: 1.45rem;
    height: 1.45rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--rca-text, #12243d);
    background: var(--rca-track, rgba(28, 62, 112, 0.12));
}
.rca-node-body {
    min-width: 0;
    padding-top: 0.1rem;
}
.rca-node-label {
    display: block;
    font-size: 0.98rem;
    font-weight: 600;
    line-height: 1.35;
    color: var(--rca-text, #12243d);
}
.rca-node-detail {
    display: block;
    margin-top: 0.3rem;
    font-size: 0.78rem;
    line-height: 1.45;
    color: var(--rca-text-soft, #41567a);
    overflow-wrap: anywhere;
}
.rca-node-note {
    display: block;
    margin-top: 0.45rem;
    padding: 0.55rem 0.7rem;
    border-left: 2px solid var(--rca-accent, #4c8dff);
    border-radius: 0 6px 6px 0;
    background: var(--rca-note-bg, rgba(76, 141, 255, 0.08));
    font-size: 0.78rem;
    line-height: 1.5;
    color: var(--rca-text-soft, #41567a);
    overflow-wrap: anywhere;
}
.rca-node-note b {
    display: block;
    margin-bottom: 0.25rem;
    font-size: 0.72rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--rca-text-muted, #6b7f9e);
}
.rca-node-meta {
    padding-top: 0.2rem;
    font-size: 0.76rem;
    white-space: nowrap;
    color: var(--rca-text-muted, #6b7f9e);
    font-variant-numeric: tabular-nums;
}

.rca-node.is-pending .rca-node-badge {
    color: var(--rca-text-muted, #6b7f9e);
    background: var(--rca-track, rgba(28, 62, 112, 0.12));
}
.rca-node.is-pending .rca-node-label,
.rca-node.is-skipped .rca-node-label {
    font-weight: 500;
    color: var(--rca-text-soft, #41567a);
}
.rca-node.is-done .rca-node-badge {
    background: var(--rca-success, #1a8f60);
    color: var(--rca-success-fg, #ffffff);
}
.rca-node.is-skipped {
    opacity: 0.55;
}
.rca-node.is-skipped .rca-node-badge {
    background: transparent;
    border: 1px dashed var(--rca-border-strong, rgba(28, 62, 112, 0.30));
    color: var(--rca-text-muted, #6b7f9e);
}
.rca-node.is-failed {
    border-color: var(--rca-danger, #c4302b);
    background: var(--rca-danger-bg, rgba(196, 48, 43, 0.09));
}
.rca-node.is-failed .rca-node-badge {
    background: var(--rca-danger, #c4302b);
    color: var(--rca-danger-fg, #ffffff);
}
.rca-node.is-failed .rca-node-meta {
    color: var(--rca-danger, #c4302b);
}
.rca-node.is-running {
    border-color: var(--rca-accent, #1565d8);
    background: var(--rca-running-bg, rgba(21, 101, 216, 0.10));
}
.rca-node.is-running .rca-node-label {
    color: var(--rca-running-label, #0b1d34);
}
.rca-node.is-running .rca-node-meta {
    color: var(--rca-accent-text, #12559c);
    font-weight: 600;
}
.rca-node.is-running .rca-node-badge {
    background: transparent;
    border: 2px solid var(--rca-track, rgba(28, 62, 112, 0.12));
    border-top-color: var(--rca-accent, #1565d8);
    animation: rca-spin 0.9s linear infinite;
}
@media (max-width: 680px) {
    .rca-node {
        column-gap: 0.6rem;
        padding: 0.4rem 0.35rem;
    }
    .rca-node-label {
        font-size: 0.86rem;
    }
}
@keyframes rca-spin {
    to { transform: rotate(360deg); }
}
@media (max-width: 680px) {
    .rca-node {
        min-width: 8.5rem;
    }
}
</style>
"""


def format_duration(seconds):

    if seconds is None:
        return ""

    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes = int(seconds // 60)

    remainder = int(seconds % 60)

    return f"{minutes}m {remainder:02d}s"


def _node_html(step, detail=None, note=None):

    status = step["status"]

    badge = BADGES.get(
        status,
        ""
    )

    if status == PENDING:
        badge = str(
            step["position"]
        )

    # The placeholder is repainted every few hundred ms, which remounts the
    # node and would restart the spinner from zero. A negative animation-delay
    # derived from the elapsed time keeps the rotation visually continuous.

    badge_style = ""

    if status == RUNNING:
        meta = format_duration(
            step.get("elapsed")
        )

        elapsed = step.get("elapsed") or 0.0

        badge_style = (
            f" style='animation-delay:{-(elapsed % SPIN_SECONDS):.2f}s'"
        )

    elif status == DONE:
        meta = format_duration(
            step.get("duration")
        )

    elif status == SKIPPED:
        meta = "not required"

    elif status == FAILED:
        meta = "failed"

    else:
        meta = "queued"

    # Only the step actually running says what it is doing right now; on every
    # other row it would just be noise.

    detail_html = (
        f"<span class='rca-node-detail'>{escape(detail)}</span>"
        if detail and status == RUNNING
        else ""
    )

    # Shown on any settled status, unlike `detail`. A note is a fact the stage
    # produced - the rewritten query, say - and it has to stay readable after
    # the stage finishes, which is exactly when the user wants to check it.
    note_html = (
        f"<span class='rca-node-note'><b>{escape(note[0])}</b>{escape(note[1])}</span>"
        if note
        else ""
    )

    return (
        f"<li class='rca-node is-{status}'>"
        f"<span class='rca-rail'><span class='rca-node-badge'{badge_style}>{badge}</span></span>"
        "<span class='rca-node-body'>"
        f"<span class='rca-node-label'>{escape(step['label'])}</span>"
        f"{detail_html}"
        f"{note_html}"
        "</span>"
        f"<span class='rca-node-meta'>{escape(meta)}</span>"
        "</li>"
    )


def render_flow(
    phases,
    headline,
    detail=None,
    total_elapsed=None,
    progress=None,
    notes=None
):
    """Builds the vertical progress timeline.

    `phases` are the rolled-up rows shown to the user; `progress` is an optional
    0-1 fraction taken from the underlying steps, so the bar advances smoothly
    rather than jumping a fifth at a time.
    """

    if progress is None:

        settled = sum(
            1
            for phase in phases
            if phase["status"] in (DONE, SKIPPED, FAILED)
        )

        progress = settled / max(len(phases), 1)

    percent = int(
        round(100 * progress)
    )

    notes = notes or {}

    rows = [
        _node_html(
            phase,
            detail,
            notes.get(phase["key"])
        )
        for phase in phases
    ]

    timer = (
        f"<span class='rca-flow-timer'>{escape(format_duration(total_elapsed))}</span>"
        if total_elapsed is not None
        else ""
    )

    return (
        FLOW_STYLES
        + "<div class='rca-flow-wrap'>"
        + f"<div class='rca-flow-head'><span>{escape(headline)}</span>{timer}</div>"
        + f"<div class='rca-flow-bar'><span style='width:{percent}%'></span></div>"
        + f"<ol class='rca-flow'>{''.join(rows)}</ol>"
        + "</div>"
    )
