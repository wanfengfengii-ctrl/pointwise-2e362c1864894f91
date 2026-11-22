import json
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection


@dataclass(frozen=True)
class ReviewRecord:
    review_id: int
    dispatch_id: str
    status: str
    destination: str
    report: dict
    receipt: dict | None
    posted_lines: int
    lineage: list[dict]
    balances: list[dict]
    differences: list[str]


def initialize_reviews(conn: Connection) -> None:
    if conn.in_transaction:
        raise ValueError('review initialization requires an idle connection')
    schema = Path(__file__).resolve().parents[1] / 'schema' / 'audit.sql'
    with conn:
        conn.executescript('BEGIN IMMEDIATE;\n' + schema.read_text(encoding='utf-8') + '\nCOMMIT;')


def save_review(conn: Connection, dispatch_id: str, status: str, payload: dict) -> ReviewRecord:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    data = json.loads(raw)
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        cursor = conn.execute(
            'INSERT INTO review_records(dispatch_id, status, payload) VALUES (?, ?, ?)',
            (dispatch_id, status, raw),
        )
        return ReviewRecord(cursor.lastrowid, dispatch_id, status, **data)


def read_reviews(conn: Connection, dispatch_id: str) -> tuple[ReviewRecord, ...]:
    rows = conn.execute(
        'SELECT review_id, status, payload FROM review_records WHERE dispatch_id = ? '
        'ORDER BY review_id', (dispatch_id,)
    )
    return tuple(
        ReviewRecord(review_id, dispatch_id, status, **json.loads(raw))
        for review_id, status, raw in rows
    )
