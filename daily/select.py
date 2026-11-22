from collections.abc import Iterable
from dataclasses import dataclass
from sqlite3 import Connection

from intake.revisions import read_revision


@dataclass(frozen=True)
class ReportLine:
    selection_index: int
    batch_id: str
    batch_revision: int
    position: int
    station_id: str
    liters: int | None
    missing: str | None
    source: str

    @property
    def parent(self) -> tuple[str, int]:
        return self.batch_id, self.batch_revision


def select_revisions(
    conn: Connection, selections: Iterable[tuple[str, int]]
) -> tuple[tuple[tuple[str, int], ...], tuple[ReportLine, ...]]:
    parents = []
    lines = []
    for selection_index, ref in enumerate(selections):
        batch = read_revision(conn, ref)
        parents.append(batch.ref)
        lines.extend(
            ReportLine(selection_index, batch.batch_id, batch.revision, row.position,
                       batch.station_id, row.liters, row.missing, row.source)
            for row in batch.observations
        )
    return tuple(parents), tuple(lines)
