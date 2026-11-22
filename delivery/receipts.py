from dataclasses import dataclass
from sqlite3 import Connection

from dispatch.catalog import DispatchBinding


@dataclass(frozen=True)
class Receipt:
    dispatch_id: str
    receipt_id: str
    destination: str
    report_id: str
    report_revision: int
    fingerprint: str
    request_id: str

    def matches(self, binding: DispatchBinding) -> bool:
        return (
            self.dispatch_id == binding.dispatch_id
            and self.destination == binding.destination
            and (self.report_id, self.report_revision) == binding.report_ref
            and self.fingerprint == binding.fingerprint
        )


def store_receipt(conn: Connection, binding: DispatchBinding, request_id: str) -> Receipt:
    receipt = Receipt(
        binding.dispatch_id, f'{binding.dispatch_id}/first', binding.destination,
        binding.report_id, binding.report_revision, binding.fingerprint, request_id,
    )
    conn.execute(
        'INSERT INTO receipts(dispatch_id, receipt_id, destination, report_id, report_revision, '
        'fingerprint, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (receipt.dispatch_id, receipt.receipt_id, receipt.destination, receipt.report_id,
         receipt.report_revision, receipt.fingerprint, receipt.request_id),
    )
    return receipt


def read_receipt(conn: Connection, dispatch_id: str) -> Receipt | None:
    row = conn.execute(
        'SELECT dispatch_id, receipt_id, destination, report_id, report_revision, fingerprint, '
        'request_id FROM receipts WHERE dispatch_id = ?', (dispatch_id,)
    ).fetchone()
    return None if row is None else Receipt(*row)
