from dataclasses import asdict
from sqlite3 import Connection

from audit.evidence import load_evidence
from audit.lineage import check_lineage
from audit.reconcile import reconcile_balances
from audit.record import ReviewRecord, save_review


def review(
    source: Connection, receiver: Connection | None, reviews: Connection, dispatch_id: str
) -> ReviewRecord:
    evidence = load_evidence(source, receiver, dispatch_id)
    lineage = check_lineage(evidence)
    balances = reconcile_balances(evidence)
    differences = list(evidence.differences)
    differences.extend(f'lineage:row:{item.report_position}' for item in lineage if not item.matches)
    for item in balances:
        if not item.matches:
            label = 'daily' if item.station_id is None else f'station:{item.station_id}'
            differences.append(f'balance:{label}')
    posted_lines = 0 if evidence.posting is None else len(evidence.posting.body.lines)
    return save_review(reviews, dispatch_id, evidence.status, {
        'destination': evidence.binding.destination,
        'report': asdict(evidence.frozen),
        'receipt': None if evidence.receipt is None else asdict(evidence.receipt),
        'posted_lines': posted_lines,
        'lineage': [asdict(item) for item in lineage],
        'balances': [asdict(item) for item in balances],
        'differences': differences,
    })
