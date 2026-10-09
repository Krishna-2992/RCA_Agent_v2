"""The RCA pipeline (architecture Fig. 2): one query in, one RcaResponse out.

    normalise -> enough to search? -> [rewrite] -> hybrid search -> gate
              -> [agent, for weak matches - not built yet] -> write RCA -> check -> score

Every step appends a TraceNode, and `on_node` lets the UI stream progress.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from rca import catalogue as catalogue_mod
from rca.answer import write
from rca.investigate import focus_terms, investigate
from rca.references import lookup as lookup_references
from rca.schema import (Alternative, Confidence, EvidenceItem, Pattern, RcaResponse, Recurrence, Step,
                        TraceNode)
from rca.scoring import confidence, gate
from rca.search import Searcher
from rca.store import Store
from rca.tracing import annotate, trace, traceable
from rca.understand import clarifying_questions, rewrite, understand


EVIDENCE_KIND = (("INC", "incident"), ("SCTASK", "service_request"), ("CHG", "change"),
                 ("PRB", "problem"), ("DOC:", "doc"), ("DOC-", "doc"), ("PC-", "card"), ("REF-", "reference"))


ALTERNATIVE_MIN = 0.35        # show another issue only if it scores at least 35% of the top one

# The answer reads as the team's runbook: no pattern-card IDs and no "not documented / confirm with
# the support team" caveats. The prompt forbids both; this is the backstop.
_CARD_IN_BRACKETS = re.compile(r"\s*\([^()]*\bPC-\d{2}\b[^()]*\)")
_CARD_ID = re.compile(r"\b(?:the\s+)?(?:pattern\s+)?(?:card\s+)?PC-\d{2}\b", re.I)
_CARD_WORD = re.compile(r"\bpattern card\b", re.I)
_SME_NOTE = re.compile(r"\s*\((?:needs|need) SME input\)", re.I)
_CAVEAT = re.compile(
    r"\b(?:not (?:been |yet )?documented|undocumented|not specified in the documentation|needs? SME|SME input"
    r"|(?:confirm|check|verify) with (?:the |your )?(?:support team|SME)|ask (?:the |your )?(?:support team|SME)"
    r"|support (?:team )?(?:must|should|needs to) (?:supply|provide|confirm))\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def plain(text: str) -> str:
    text = _CARD_IN_BRACKETS.sub("", text)
    text = _CARD_ID.sub("the known issue", text)
    text = _CARD_WORD.sub("known issue", text)
    text = _SME_NOTE.sub("", text)
    return " ".join(s for s in _SENTENCE.split(text) if not _CAVEAT.search(s)).strip()


def _retrieved(result) -> list[dict]:
    """Search hits in LangSmith's retriever format, so the trace shows what was found and why."""
    documents = [{"page_content": f"pattern card {c.id}", "type": "Document",
                  "metadata": {"score": round(c.score, 4), "own": round(c.own, 4), "agreement": c.agreement,
                               "matched_ids": c.matched_ids, "conflicting_ids": c.conflicting_ids,
                               "support": {k: round(v, 4) for k, v in c.support.items()}}}
                 for c in result.cards[:5]]
    for kind, hits, n in (("incident", result.incidents, 5), ("service request", result.service_requests, 3),
                          ("document", result.docs, 4)):
        documents += [{"page_content": f"{kind} {h.id}", "type": "Document",
                       "metadata": {"score": round(h.score, 4), "ranks": h.ranks}} for h in hits[:n]]
    return documents


def _kind(evidence_id: str) -> str:
    return next((kind for prefix, kind in EVIDENCE_KIND if evidence_id.startswith(prefix)), "doc")


class Pipeline:
    def __init__(self, use_rewrite: bool = False, unclear_full_cards: int = 1):
        self.searcher = Searcher()
        self.store = Store(self.searcher.conn)
        self.use_rewrite = use_rewrite
        # Known issues shown in full when the match is unclear. Tried 3 on 2026-10-09: same accuracy on the
        # 15 unclear regression cases (11/15), 4 citation failures instead of 0, +29% tokens. Kept at 1.
        self.unclear_full_cards = unclear_full_cards

    def close(self) -> None:
        self.searcher.close()

    @traceable(name="RCA", run_type="chain", tags=["rca", "v2"],
               process_inputs=lambda inputs: {"query": inputs.get("query"),
                                              "exclude_events": sorted(inputs.get("exclude_events") or [])},
               process_outputs=lambda response: response.model_dump(mode="json", exclude={"trace"})
               if isinstance(response, RcaResponse) else {"output": response})
    def run(
        self,
        query: str,
        *,
        exclude_events: frozenset[str] = frozenset(),
        on_node: Callable[[TraceNode], None] | None = None,
    ) -> RcaResponse:
        """Callers may pass langsmith_extra={"run_id", "tags", "metadata", "project_name"} for the trace."""
        trace_nodes: list[TraceNode] = []

        def record(node: TraceNode, span=None) -> None:
            trace_nodes.append(node)
            if span is not None:
                span.end(outputs=node.detail)
            if on_node:
                on_node(node)

        # 1 · normalise
        started = time.monotonic()
        with trace("normalise", run_type="parser", inputs={"query": query}) as span:
            understood, entities = understand(query)
            questions = clarifying_questions(query, entities)
            record(TraceNode(name="normalise", ms=int((time.monotonic() - started) * 1000), detail={
                "system": understood.system, "ids": understood.ids, "applications": understood.applications,
                "alert": entities.alert is not None, "enough_to_search": not questions}), span)

        if questions:
            annotate(status="needs_input")
            return RcaResponse(
                query=query, status="needs_input", understood=understood, clarifying_questions=questions,
                confidence=Confidence(score=0.0, level="low", reasons=["Not enough detail to search yet."]),
                trace=trace_nodes)

        # 2 · rewrite (free text only, and only when enabled)
        search_text = query
        if self.use_rewrite and not entities.alert:
            with trace("rewrite", run_type="chain", inputs={"query": query}) as span:
                rewritten, node = rewrite(query)
                record(node, span)
            search_text = f"{query}\n{rewritten.symptom}\n{rewritten.search_text}"

        # 3 · hybrid search
        started = time.monotonic()
        with trace("hybrid_search", run_type="retriever",
                   inputs={"query": search_text, "ids": understood.ids,
                           "excluded_events": sorted(exclude_events)}) as span:
            result = self.searcher.search(search_text, exclude_events=exclude_events, entities=entities)
            node = TraceNode(name="search", ms=int((time.monotonic() - started) * 1000), detail={
                "cards": [(c.id, round(c.score, 4)) for c in result.cards[:5]],
                "incidents": [h.id for h in result.incidents[:5]],
                "service_requests": [h.id for h in result.service_requests[:3]],
                "docs": [h.id for h in result.docs[:4]]})
            span.end(outputs={"documents": _retrieved(result)})
            record(node)

        # 4 · gate
        started = time.monotonic()
        with trace("gate", run_type="chain", inputs={"cards": node.detail["cards"]}) as span:
            top_cards = self.store.cards([c.id for c in result.cards[:3]])
            decision = gate(result, top_cards, self.searcher.event_of)
            record(TraceNode(name="gate", ms=int((time.monotonic() - started) * 1000), detail={
                "strong": decision.strong, "top": decision.top,
                "top_title": top_cards[decision.top].title if decision.top in top_cards else None,
                "margin": round(decision.margin, 2),
                "id_agreement": decision.id_agreement, "supporting_events": decision.supporting_events,
                "reasons": decision.reasons}), span)

        # 5 · investigate - only when no known issue clearly fits. The error code reference joins the
        # evidence then too: a clear match's known issue already explains its error numbers.
        references = []
        investigation = None
        if not decision.strong:
            references = lookup_references(entities.error_codes, query)
            investigation = self._investigate(query, understood, result, decision, references,
                                              exclude_events, record)

        # 6-7 · write and check
        catalogue, answer, errors = self._write(query, understood, result, decision, references,
                                                investigation, record)
        if answer.matched_pattern is None and investigation is None:
            # Search looked sure, but the analysis rejected the match: investigate, then write again.
            references = lookup_references(entities.error_codes, query)
            investigation = self._investigate(query, understood, result, decision, references,
                                              exclude_events, record)
            catalogue, answer, errors = self._write(query, understood, result, decision, references,
                                                    investigation, record)

        # 8 · score
        started = time.monotonic()
        with trace("score", run_type="chain", inputs={"judgement": answer.judgement, "gate": decision.reasons}) as span:
            matched = answer.matched_pattern if answer.matched_pattern in catalogue.cards else None
            cited = {e.evidence_id for e in answer.evidence} | {i for s in answer.resolution_steps
                                                                for i in s.evidence_ids}
            reference_cited = any(c.startswith("REF-") for c in cited)
            docs_cited = len({c for c in cited if c.startswith("DOC:")})
            conf = confidence(decision, answer.judgement, matched is not None, bool(errors),
                              reference_cited=reference_cited, docs_cited=docs_cited,
                              has_steps=bool(answer.resolution_steps))
            status = ("answered" if matched else
                      "diagnosed" if answer.resolution_steps and (reference_cited or docs_cited) else "no_match")
            record(TraceNode(name="score", ms=int((time.monotonic() - started) * 1000),
                             detail={"score": conf.score, "level": conf.level, "status": status,
                                     "reasons": conf.reasons}), span)

        llm_nodes = [n for n in trace_nodes if n.model]
        annotate(status=status, matched_pattern=matched, investigated=investigation is not None,
                 confidence=conf.score, level=conf.level, gate_strong=decision.strong,
                 check_failed=bool(errors), llm_calls=len(llm_nodes),
                 models=sorted({n.model for n in llm_nodes}),
                 tokens=sum(n.tokens or 0 for n in llm_nodes))

        return self._response(query, understood, result, catalogue, answer, matched, conf, trace_nodes, status)

    def _investigate(self, query, understood, result, decision, references, exclude_events, record):
        with trace("investigate", run_type="chain",
                   inputs={"query": query, "why": decision.reasons,
                           "references": [ref.ref_id for ref, _ in references]}) as span:
            investigation = investigate(query, understood, result, decision, references, self.searcher,
                                        exclude_events)
            record(investigation.node, span)
        return investigation

    def _write(self, query, understood, result, decision, references, investigation, record):
        clear = decision.strong and investigation is None
        full_cards = 1 if clear else self.unclear_full_cards
        with trace("build_catalogue", run_type="chain",
                   inputs={"top_card": decision.top, "investigated": investigation is not None,
                           "full_cards": full_cards}) as span:
            catalogue = catalogue_mod.build(result, self.store, references=references,
                                            investigation=investigation, full_cards=full_cards,
                                            focus_terms=focus_terms(query, *result.entities.ids()))
            prompt = catalogue.prompt()
            span.end(outputs={"items": {kind: [i.id for i in catalogue.items.values() if i.kind == kind]
                                        for kind in sorted({i.kind for i in catalogue.items.values()})},
                              "doc_sources": catalogue.sources,
                              "prompt_chars": len(prompt), "approx_tokens": len(prompt) // 4})
        hypotheses = investigation.plan.hypotheses if investigation else None
        with trace("write_rca", run_type="chain", inputs={"query": query, "top_card": decision.top}) as span:
            answer, errors, nodes = write(query, understood, catalogue, decision, hypotheses)
            span.end(outputs={"answer": answer.model_dump(mode="json"), "check_errors": errors})
        for node in nodes:
            record(node)
        return catalogue, answer, errors

    # -- assembling the response ----------------------------------------------

    def _response(self, query, understood, result, catalogue, answer, matched, conf, trace_nodes,
                  status) -> RcaResponse:
        card = catalogue.cards.get(matched) if matched else None

        recurrence = None
        if card:
            history = self.store.card_history(card)
            recurrence = Recurrence(
                events=history.events, tickets=history.tickets,
                first_seen=history.first_seen.date().isoformat() if history.first_seen else None,
                last_seen=history.last_seen.date().isoformat() if history.last_seen else None,
                problems=list(history.problems))

        cited = [e.evidence_id for e in answer.evidence]
        evidence = []
        for item in answer.evidence:
            known = catalogue.items.get(item.evidence_id)
            evidence.append(EvidenceItem(
                id=item.evidence_id, kind=known.kind if known else _kind(item.evidence_id),
                title=known.title if known else item.evidence_id,
                excerpt=known.excerpt if known else "", why=plain(item.why)))

        best = result.cards[0].score if result.cards else 0.0
        alternatives = [
            Alternative(pattern_id=c.id, title=catalogue.cards[c.id].title, score=round(c.score, 4))
            for c in result.cards[:4]
            if c.id != matched and c.id in catalogue.cards and c.score >= ALTERNATIVE_MIN * best
        ][:2]

        return RcaResponse(
            query=query,
            status=status,
            understood=understood,
            pattern=Pattern(id=card.id, title=card.title, status=card.status) if card else None,
            root_cause=plain(answer.root_cause),
            confidence=conf,
            resolution_steps=[Step(text=text, source=s.source, evidence_ids=s.evidence_ids)
                              for s in answer.resolution_steps if (text := plain(s.text))],
            preventive_actions=[text for a in answer.preventive_actions if (text := plain(a))],
            escalation=answer.escalation,
            missing_information=[text for m in answer.missing_information if (text := plain(m))],
            # What the documentation lacks for this issue: for LangSmith and the client conversation only.
            knowledge_gaps=[step.needs_sme for step in card.steps if step.needs_sme] if card else [],
            evidence=evidence,
            recurrence=recurrence,
            alternatives=alternatives,
            trace=trace_nodes,
        )
