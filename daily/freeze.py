from collections.abc import Iterable
from sqlite3 import Connection

from daily.body import ReportBody, decode_report, encode_report
from daily.select import select_revisions
from daily.totals import calculate_totals


def freeze(conn: Connection, report_id: str, selections: Iterable[tuple[str, int]]) -> ReportBody:
    if not isinstance(report_id, str) or not report_id.strip():
        raise ValueError('report_id must be nonempty text')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        parents, lines = select_revisions(conn, selections)
        revision = conn.execute(
            'SELECT COALESCE(MAX(revision), 0) + 1 FROM frozen_reports WHERE report_id = ?',
            (report_id,),
        ).fetchone()[0]
        report = ReportBody(report_id, revision, parents, lines, calculate_totals(lines))
        conn.execute(
            'INSERT INTO frozen_reports(report_id, revision, material) VALUES (?, ?, ?)',
            (report_id, revision, encode_report(report)),
        )
        return report


def read_frozen(conn: Connection, ref: tuple[str, int]) -> ReportBody:
    if (not isinstance(ref, (tuple, list)) or len(ref) != 2
            or not isinstance(ref[0], str) or not ref[0].strip()
            or type(ref[1]) is not int or ref[1] < 1):
        raise ValueError('a report reference requires a report id and positive revision')
    row = conn.execute(
        'SELECT material FROM frozen_reports WHERE report_id = ? AND revision = ?', tuple(ref)
    ).fetchone()
    if row is None:
        raise KeyError(tuple(ref))
    report = decode_report(row[0])
    if report.ref != tuple(ref):
        raise ValueError('stored report identity differs from its reference')
    return report
