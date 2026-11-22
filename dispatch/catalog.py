from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from daily.body import body_fingerprint, encode_report
from daily.freeze import read_frozen


@dataclass(frozen=True)
class DispatchBinding:
    dispatch_id: str
    report_id: str
    report_revision: int
    destination: str
    fingerprint: str

    @property
    def report_ref(self) -> tuple[str, int]:
        return self.report_id, self.report_revision


def initialize_source(conn: Connection) -> None:
    if conn.in_transaction:
        raise ValueError('source initialization requires an idle connection')
    schema = Path(__file__).resolve().parents[1] / 'schema' / 'source.sql'
    conn.execute('PRAGMA foreign_keys = ON')
    with conn:
        conn.executescript('BEGIN IMMEDIATE;\n' + schema.read_text(encoding='utf-8') + '\nCOMMIT;')


def read_binding(conn: Connection, dispatch_id: str) -> DispatchBinding:
    row = conn.execute(
        'SELECT dispatch_id, report_id, report_revision, destination, fingerprint '
        'FROM dispatches WHERE dispatch_id = ?', (dispatch_id,)
    ).fetchone()
    if row is None:
        raise KeyError(dispatch_id)
    return DispatchBinding(*row)


def dispatch(
    conn: Connection, dispatch_id: str, report_ref: tuple[str, int], destination: str
) -> DispatchBinding:
    if not isinstance(dispatch_id, str) or not dispatch_id.strip():
        raise ValueError('dispatch_id must be nonempty text')
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError('destination must be nonempty text')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        try:
            existing = read_binding(conn, dispatch_id)
        except KeyError:
            existing = None
        if existing is not None:
            if (isinstance(report_ref, (tuple, list)) and len(report_ref) == 2
                    and type(report_ref[1]) is int and existing.report_ref == tuple(report_ref)
                    and existing.destination == destination):
                return existing
            raise ValueError(f'dispatch parameters conflict for {dispatch_id}')
        report = read_frozen(conn, report_ref)
        fingerprint = body_fingerprint(encode_report(report))
        conn.execute(
            'INSERT INTO dispatches(dispatch_id, report_id, report_revision, destination, fingerprint) '
            'VALUES (?, ?, ?, ?, ?)',
            (dispatch_id, report.report_id, report.revision, destination, fingerprint),
        )
        return DispatchBinding(dispatch_id, report.report_id, report.revision, destination, fingerprint)


def read_dispatch_material(conn: Connection, dispatch_id: str) -> tuple[DispatchBinding, str]:
    binding = read_binding(conn, dispatch_id)
    material = encode_report(read_frozen(conn, binding.report_ref))
    if body_fingerprint(material) != binding.fingerprint:
        raise ValueError('stored material differs from the dispatch binding')
    return binding, material
