from dataclasses import dataclass
from sqlite3 import Connection

from daily.body import ReportBody, decode_report
from daily.checks import validate_report_material
from dispatch.catalog import DispatchBinding


@dataclass(frozen=True)
class Posting:
    dispatch_id: str
    fingerprint: str
    body: ReportBody


def append_posting(conn: Connection, binding: DispatchBinding, material: str) -> Posting:
    """Write the whole fixed report as the formal book entry in the claim transaction."""
    report = validate_report_material(material, binding.report_ref, binding.fingerprint)
    conn.execute(
        'INSERT INTO postings(dispatch_id, fingerprint, body) VALUES (?, ?, ?)',
        (binding.dispatch_id, binding.fingerprint, material),
    )
    return Posting(binding.dispatch_id, binding.fingerprint, report)


def read_posting(conn: Connection, dispatch_id: str) -> Posting | None:
    row = conn.execute(
        'SELECT dispatch_id, fingerprint, body FROM postings WHERE dispatch_id = ?', (dispatch_id,)
    ).fetchone()
    return None if row is None else Posting(row[0], row[1], decode_report(row[2]))
