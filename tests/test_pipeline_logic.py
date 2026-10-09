"""Pipeline logic that runs without the database or Azure."""

from rca.answer import check
from rca.catalogue import Catalogue, Item
from rca.cards import load_cards
from rca.schema import LlmAnswer, LlmEvidence, LlmStep
from rca.scoring import GateDecision, confidence
from rca.understand import clarifying_questions, understand


def _catalogue() -> Catalogue:
    deck = load_cards().by_id()
    catalogue = Catalogue(cards={"PC-01": deck["PC-01"]})
    catalogue.add(Item("PC-01", "card", "card", "", ""))
    catalogue.add(Item("INC10205898", "incident", "inc", "", ""))
    catalogue.add(Item("DOC:technical_xml:F8RH0071:x:0", "doc", "doc", "", ""))
    return catalogue


def _answer(**overrides) -> LlmAnswer:
    base = dict(matched_pattern="PC-01", root_cause="IN0011.MST is corrupted.",
                resolution_steps=[LlmStep(text="Recover the file", source="card", evidence_ids=["PC-01"])],
                preventive_actions=[], evidence=[LlmEvidence(evidence_id="INC10205898", why="same error")],
                escalation="none", missing_information=[], judgement="high")
    base.update(overrides)
    return LlmAnswer(**base)


def test_check_passes_clean_answer():
    assert check(_answer(), _catalogue()) == []


def test_check_rejects_invented_citations_and_unsourced_steps():
    answer = _answer(
        evidence=[LlmEvidence(evidence_id="INC99999999", why="made up")],
        resolution_steps=[LlmStep(text="Run fixit.exe", source="ticket", evidence_ids=[]),
                          LlmStep(text="Use the manual", source="doc", evidence_ids=["INC10205898"])],
    )
    errors = check(answer, _catalogue())
    assert any("INC99999999" in e for e in errors)
    assert any("step 1" in e and "cites no evidence" in e for e in errors)
    assert any("step 2" in e and "documentation" in e for e in errors)


def test_bracketed_ids_are_normalised_before_checking():
    from rca.answer import normalise
    answer = normalise(_answer(matched_pattern="[PC-01]",
                               evidence=[LlmEvidence(evidence_id="[INC10205898]", why="same error")],
                               resolution_steps=[LlmStep(text="Recover", source="card", evidence_ids=[" [PC-01] "])]))
    assert check(answer, _catalogue()) == []


def test_the_model_cannot_write_unsourced_steps():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LlmStep(text="Ask how locks are closed", source="sme", evidence_ids=[])


def test_answers_read_as_a_runbook_without_review_caveats():
    from rca.pipeline import plain
    assert plain("Close all locks on the file. The method for closing these locks is not documented.") \
        == "Close all locks on the file."
    assert plain("Recover the file from Reman Index Option 34. Confirm with the support team that Option 34 "
                 "is the approved recovery path for .MOV files.") == "Recover the file from Reman Index Option 34."
    assert plain("Find out why STKORD hangs (needs SME input).") == "Find out why STKORD hangs."
    assert plain("The method for finding missed tickets is not documented.") == ""


def test_card_prompt_carries_no_review_notes():
    from rca.catalogue import _card_text
    deck = load_cards().by_id()
    for card in deck.values():
        text = _card_text(card, full=True)
        assert "needs SME" not in text and "SME input" not in text and "draft" not in text
        steps = text.split("Steps:")[1].split("Verify:")[0]
        assert "PC-" not in steps, card.id


def test_confidence_is_capped_without_a_match_or_after_failed_checks():
    strong = GateDecision(strong=True, top="PC-01", margin=0.6, id_agreement=1.0, supporting_events=3)
    assert confidence(strong, "high", matched=True, checks_failed=False).level == "high"
    assert confidence(strong, "high", matched=False, checks_failed=False).score <= 0.35
    assert confidence(strong, "high", matched=True, checks_failed=True).score <= 0.40


def test_confidence_never_claims_certainty():
    perfect = GateDecision(strong=True, top="PC-01", margin=1.0, id_agreement=1.0, supporting_events=9)
    assert confidence(perfect, "high", matched=True, checks_failed=False).score == 0.95


def test_card_ids_never_reach_support_staff():
    from rca.pipeline import plain
    assert plain("The files match PC-02.") == "The files match the known issue."
    assert plain("Recover the file (see PC-02, INC10071252).") == "Recover the file."
    assert plain("This fits pattern card PC-01.") == "This fits the known issue."
    assert plain("Seen before in INC10071252.") == "Seen before in INC10071252."


def test_understand_routes_alerts_to_batch():
    understood, entities = understand("Incident AJS002E ABEND=S000 U4005  F8RH0430(JOB42942)  JCL=A.F8RH0410  Z1SCH190001")
    assert understood.system == "batch"
    assert understood.ids["abends"] == ["U4005"]


def test_vague_reports_get_questions_specific_ones_do_not():
    _, vague = understand("help, not working")
    assert clarifying_questions("help, not working", vague)
    query = "LMS error 984 IN0011.MST"
    _, specific = understand(query)
    assert clarifying_questions(query, specific) == []


# -- diagnosis of issues no known issue covers ------------------------------------------------

def test_error_numbers_map_to_cobol_file_status():
    from rca.references import lookup, status_of
    assert status_of("4102") == "41" and status_of("984") == "98" and status_of("9302") == "93"
    assert status_of("1234") is None
    assert [ref.status for ref, _ in lookup([], "LMS shows No file position on IN0011")] == ["46"]


def test_an_error_number_the_issue_does_not_produce_blocks_a_strong_match():
    from rca.entities import extract
    from rca.scoring import gate
    from rca.search import CardHit, SearchResult
    entities = extract("Duplicate Open adding a hot part. Error # 4102 REMAN.NDX, Duplicate Open")
    result = SearchResult(query="", entities=entities, service_requests=[], docs=[],
                          incidents=[], cards=[CardHit("PC-08", 0.13, 0.1, support={"INC9876850": 0.05},
                                                       agreement=0.17, matched_ids=["REMAN.NDX"]),
                                               CardHit("PC-15", 0.05, 0.05)])
    decision = gate(result, load_cards().by_id(), {"INC9876850": "EV-1"})
    assert not decision.strong and decision.unexplained == ["4102"]


def test_reference_steps_must_cite_the_reference():
    catalogue = _catalogue()
    catalogue.add(Item("REF-FS-41", "reference", "File status 41", "", ""))
    good = _answer(matched_pattern=None, resolution_steps=[
        LlmStep(text="Sign out and back in", source="reference", evidence_ids=["REF-FS-41"])])
    bad = _answer(matched_pattern=None, resolution_steps=[
        LlmStep(text="Sign out and back in", source="reference", evidence_ids=["INC10205898"])])
    assert check(good, catalogue) == []
    assert any("REF-" in e for e in check(bad, catalogue))


def test_doc_excerpts_centre_on_the_passage_that_matters():
    from rca.catalogue import focus
    text = "Unrelated menu handling. " * 80 + "9001-OPEN-INDEX opens REMAN-INDEX after closing REMAN-INDEX. " \
        + "Unrelated printing. " * 80
    excerpt = focus(text, {"9001-open-index", "reman-index"}, 300)
    assert "9001-OPEN-INDEX opens REMAN-INDEX" in excerpt and len(excerpt) <= 306


def test_worked_out_diagnoses_reach_medium_confidence_at_most():
    weak = GateDecision(strong=False, top="PC-08", margin=0.6, id_agreement=0.6)
    diagnosed = confidence(weak, "high", matched=False, checks_failed=False,
                           reference_cited=True, docs_cited=3, has_steps=True)
    assert diagnosed.level == "medium" and diagnosed.score == 0.6
    docs_only = confidence(weak, "medium", matched=False, checks_failed=False, docs_cited=4, has_steps=True)
    assert docs_only.level == "low"
    unexplained = confidence(weak, "high", matched=False, checks_failed=False)
    assert unexplained.score <= 0.35
