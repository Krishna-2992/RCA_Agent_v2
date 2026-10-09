"""Groups duplicate tickets into events.

One batch failure can raise a dozen alerts. Counting them separately would make
a single bad night look like a dozen recurrences and fill every search slot with
the same failure. Two rules, both visible in the data:

- a child incident belongs to its parent's event;
- AJS alerts for the same JCL on the same day are one event.

Online incidents are not merged on similarity alone: two "LMS won't open" reports
on one day can have different causes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rca.entities import parse_ajs_alert
from rca.servicenow import Incident


@dataclass(frozen=True)
class Event:
    id: str
    kind: str                    # parent_child | alert_same_day | single
    members: tuple[str, ...]
    first_seen: datetime | None
    last_seen: datetime | None

    @property
    def ticket_count(self) -> int:
        return len(self.members)


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def group_events(incidents: list[Incident]) -> list[Event]:
    by_number = {incident.number: incident for incident in incidents}
    union = _UnionFind()
    kinds: dict[str, str] = {}

    for incident in incidents:
        union.find(incident.number)
        if incident.parent:
            union.union(incident.number, incident.parent)
            kinds[incident.number] = kinds[incident.parent] = "parent_child"

    alert_groups: dict[tuple[str, str], list[str]] = {}
    for incident in incidents:
        alert = parse_ajs_alert(incident.short_description)
        if alert and incident.opened_at:
            key = (alert.jcl, incident.opened_at.date().isoformat())
            alert_groups.setdefault(key, []).append(incident.number)
    for members in alert_groups.values():
        for number in members[1:]:
            union.union(number, members[0])
        if len(members) > 1:
            for number in members:
                kinds.setdefault(number, "alert_same_day")

    components: dict[str, list[str]] = {}
    for number in by_number:
        components.setdefault(union.find(number), []).append(number)

    events = []
    for members in components.values():
        parents = [n for n in members if any(by_number[m].parent == n for m in members)]
        if parents:
            event_id = parents[0]
        else:
            event_id = min(members, key=lambda n: (by_number[n].opened_at or datetime.max, n))
        times = [by_number[n].opened_at for n in members if by_number[n].opened_at]
        kind = "single" if len(members) == 1 else kinds.get(event_id, "alert_same_day")
        events.append(Event(
            id=event_id,
            kind=kind,
            members=tuple(sorted(members)),
            first_seen=min(times) if times else None,
            last_seen=max(times) if times else None,
        ))

    return sorted(events, key=lambda e: (e.first_seen or datetime.max, e.id))


def event_of(events: list[Event]) -> dict[str, str]:
    """incident number -> event id"""
    return {member: event.id for event in events for member in event.members}
