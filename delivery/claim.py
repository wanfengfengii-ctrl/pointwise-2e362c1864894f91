from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection

from daily.body import ReportBody, body_fingerprint, encode_report
from delivery.ledger import append_posting, read_posting
from delivery.receipts import Receipt, read_receipt, store_receipt
from delivery.register import DeliveryEntry, read_admission, record_disposition


@dataclass(frozen=True)
class ClaimResult:
    status: str
    report: ReportBody | None = None
    receipt: Receipt | None = None


def _bind_request(conn: Connection, request_id: str, dispatch_id: str) -> None:
    conn.execute(
        'INSERT INTO claim_requests(request_id, dispatch_id) VALUES (?, ?) '
        'ON CONFLICT(request_id) DO NOTHING', (request_id, dispatch_id)
    )
    recorded = conn.execute(
        'SELECT dispatch_id FROM claim_requests WHERE request_id = ?', (request_id,)
    ).fetchone()[0]
    if recorded != dispatch_id:
        raise ValueError(f'claim request parameters conflict for {request_id}')


def _replay(conn: Connection, entry: DeliveryEntry) -> ClaimResult:
    binding = entry.binding
    posting = read_posting(conn, binding.dispatch_id)
    receipt = read_receipt(conn, binding.dispatch_id)
    if posting is None or receipt is None or not receipt.matches(binding):
        raise ValueError('formal posting and first receipt are incomplete')
    if (posting.fingerprint != binding.fingerprint or posting.body.ref != binding.report_ref
            or body_fingerprint(encode_report(posting.body)) != binding.fingerprint):
        raise ValueError('formal posting differs from the admitted binding')
    return ClaimResult('claimed', posting.body, receipt)


def claim(
    conn: Connection,
    dispatch_id: str,
    request_id: str,
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> ClaimResult:
    if not isinstance(dispatch_id, str) or not dispatch_id.strip():
        raise ValueError('dispatch_id must be nonempty text')
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('claim request_id must be nonempty text')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        _bind_request(conn, request_id, dispatch_id)
        entry = read_admission(conn, dispatch_id)
        if entry is None or entry.status == 'pending':
            return ClaimResult('pending')
        if entry.status == 'withdrawn':
            return ClaimResult('withdrawn')
        if entry.status == 'claimed':
            return _replay(conn, entry)
        record_disposition(conn, entry, 'claimed')
        posting = append_posting(conn, entry.binding, entry.material)
        if checkpoint is not None:
            checkpoint('after_posting')
        receipt = store_receipt(conn, entry.binding, request_id)
        result = ClaimResult('claimed', posting.body, receipt)
    if checkpoint is not None:
        checkpoint('after_claim_commit')
    return result
