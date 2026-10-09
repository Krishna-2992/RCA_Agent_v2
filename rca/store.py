"""Reads the details of search hits from Supabase."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


from rca.cards import PatternCard
from rca.db import Database


@dataclass(frozen=True)
class IncidentRecord:
    number: str
    event_id: str
    opened_at: datetime | None
    service: str
    short_description: str
    description: str
    root_cause: str | None
    action_taken: str | None
    resolved_by: str | None


@dataclass(frozen=True)
class RequestRecord:
    number: str
    opened_at: datetime | None
    short_description: str
    description: str
    work_notes: str


@dataclass(frozen=True)
class DocRecord:
    id: str
    source_file: str
    program: str | None
    section_path: str
    doc_type: str
    trust_tier: int
    role: str
    text: str


@dataclass(frozen=True)
class ChangeRecord:
    number: str
    closed_at: datetime | None
    short_description: str
    programs: tuple[str, ...]


@dataclass(frozen=True)
class CardHistory:
    events: int
    tickets: int
    first_seen: datetime | None
    last_seen: datetime | None
    problems: tuple[str, ...]


class Store:
    def __init__(self, conn: Database):
        self.conn = conn

    def cards(self, ids: list[str]) -> dict[str, PatternCard]:
        rows = self.conn.execute("select id, body from pattern_cards where id = any(%s)", (ids,)).fetchall()
        return {card_id: PatternCard.model_validate(body) for card_id, body in rows}

    def incidents(self, numbers: list[str]) -> dict[str, IncidentRecord]:
        rows = self.conn.execute(
            "select number, event_id, opened_at, service, short_description, coalesce(description, ''), "
            "root_cause, action_taken, resolved_by from incidents where number = any(%s)", (numbers,)
        ).fetchall()
        return {r[0]: IncidentRecord(*r) for r in rows}

    def requests(self, numbers: list[str]) -> dict[str, RequestRecord]:
        rows = self.conn.execute(
            "select number, opened_at, short_description, coalesce(description, ''), coalesce(work_notes, '') "
            "from service_requests where number = any(%s)", (numbers,)
        ).fetchall()
        return {r[0]: RequestRecord(*r) for r in rows}

    def docs(self, ids: list[str]) -> dict[str, DocRecord]:
        rows = self.conn.execute(
            "select id, source_file, program, section_path, doc_type, trust_tier, role, text "
            "from doc_chunks where id = any(%s)", (ids,)
        ).fetchall()
        return {r[0]: DocRecord(*r) for r in rows}

    def changes_for(self, programs: list[str]) -> list[ChangeRecord]:
        rows = self.conn.execute(
            "select number, closed_at, short_description, programs from changes where programs && %s::text[] "
            "order by closed_at", (programs,)
        ).fetchall()
        return [ChangeRecord(r[0], r[1], r[2], tuple(r[3])) for r in rows]

    def card_history(self, card: PatternCard) -> CardHistory:
        row = self.conn.execute(
            "select count(*), coalesce(sum(e.ticket_count), 0), min(e.first_seen), max(e.last_seen) "
            "from events e where e.id in ("
            "  select i.event_id from card_evidence c join incidents i on i.number = c.item_id "
            "  where c.card_id = %s and c.item_type = 'incident')",
            (card.id,),
        ).fetchone()
        return CardHistory(events=row[0] or 0, tickets=int(row[1] or 0), first_seen=row[2], last_seen=row[3],
                           problems=tuple(card.evidence.problems))
