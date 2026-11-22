from dataclasses import dataclass
from itertools import zip_longest

from audit.evidence import AuditEvidence
from daily.select import ReportLine


@dataclass(frozen=True)
class LineageFinding:
    report_position: int
    frozen: ReportLine | None
    posted: ReportLine | None
    origin: ReportLine | None
    matches: bool


def check_lineage(evidence: AuditEvidence) -> tuple[LineageFinding, ...]:
    if evidence.posting is None:
        return ()
    origins = []
    for index, batch in enumerate(evidence.parents):
        if batch is None:
            origins.extend(None for line in evidence.frozen.lines if line.selection_index == index)
            continue
        for observation in batch.observations:
            origins.append(ReportLine(
                index, batch.batch_id, batch.revision, observation.position, batch.station_id,
                observation.liters, observation.missing, observation.source,
            ))
    return tuple(
        LineageFinding(position, frozen, posted, origin, origin is not None and frozen == posted == origin)
        for position, (frozen, posted, origin) in enumerate(
            zip_longest(evidence.frozen.lines, evidence.posting.body.lines, origins), 1
        )
    )
