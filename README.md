# REMAN RCA Agent v2

Root-cause analysis assistant for the REMAN COBOL application support team.
Architecture: https://claude.ai/artifact/DgdLpr6iMpxbiNa8ooyRq1

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in the values
```

Client files are not in git. Put them in the project root:
`Reman_samples.xlsx`, `Reman_incidents_harness.xlsx` and the `Reman/` documentation folder.

Load the knowledge base into Supabase (embeddings are cached, so a reload only embeds new text):

```bash
.venv/bin/python -m scripts.migrate                 # apply db/migrations once each
.venv/bin/python -m scripts.ingest                  # full reload of cards, tickets, docs, IDs
```

## Run

```bash
.venv/bin/streamlit run app.py                      # the UI (port 8501 by default)
```

Every question is traced to LangSmith (project from `LANGCHAIN_PROJECT`), with a child run per
step and the Azure calls nested inside; the UI's feedback is attached to the trace.

## Commands

```bash
.venv/bin/python -m pytest -q                       # tests (never send traces)
.venv/bin/python -m scripts.validate_knowledge      # check cards, entities, error reference against the data
.venv/bin/python -m scripts.build_eval_sets         # build/eval/{regression,evaluation}.jsonl
.venv/bin/python -m scripts.eval_retrieval          # search only, on the 69 (own event hidden)
.venv/bin/python -m scripts.eval_pipeline           # full answers on the 69 regression cases
.venv/bin/python -m scripts.eval_pipeline --split evaluation   # the 45 held-out cases (milestones only)
```

Evaluation runs go to the LangSmith project "<LANGCHAIN_PROJECT> - evals".

## How a question is answered

1. **Understand** - pull out IDs (files, error numbers, abends, jobs, programs, reports).
2. **Search** - dense + BM25 + exact-ID search over cards, tickets and docs, fused with RRF.
3. **Match** - the gate decides whether a known issue (pattern card) clearly fits. An error
   number or abend the issue doesn't produce means it doesn't.
4. **Investigate** (only when nothing clearly fits) - the model plans targeted searches; code runs
   them plus fixed searches for the named reports and files; the error code reference explains
   the file status (4102 = status 41, duplicate open).
5. **Write** - one model call writes the RCA from the evidence catalogue; every citation is
   checked, with one retry.
6. **Score** - confidence is computed in code: high only for a known issue that clearly fits,
   medium at most for a worked-out diagnosis.

## Layout

| Path | What |
|---|---|
| `knowledge/pattern_cards.yaml` | The 15 known issues (draft, for SME review) |
| `knowledge/pattern_cards_draft_v0.md` | Review copy for the SME |
| `knowledge/file_status.yaml` | COBOL file status reference (draft, for SME review) |
| `knowledge/entities.yaml` | Canonical IDs and their spellings |
| `evals/labels_evaluation.yaml` | Answer key for the 45 held-out incidents (scoring only) |
| `rca/` | Pipeline: understand, search, scoring, investigate, catalogue, answer, tracing |
| `app.py`, `ui/` | Streamlit UI (POC v1 look) |
| `db/migrations/` | Supabase schema |

## Rules

- The 45 incidents that appear only in the harness are for evaluation. Nothing derived
  from them goes into the cards, the entity file, the error reference or any prompt.
  `validate_knowledge` rejects knowledge that cites them.
- The 69 sample incidents are both the knowledge base and the regression suite.
- Answers read as the support team's runbook: no card IDs, draft labels or "not documented"
  caveats on screen. Documentation gaps are kept in the trace (`knowledge_gaps`).
- The chat deployment allows 10k tokens/min: one question at a time, evals with `--workers 1`.
