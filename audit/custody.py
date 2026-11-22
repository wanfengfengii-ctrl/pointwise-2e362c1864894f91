import sqlite3
from pathlib import Path

from daily.body import body_fingerprint, decode_report

_SCHEMA = Path(__file__).resolve().parents[1] / 'schema' / 'custody.sql'

_OPERATIONS = ('confirm', 'recovery')
_REQUIRED_FIELDS = (
    'dispatch_id', 'report_id', 'report_revision', 'destination', 'fingerprint', 'material',
    'first_request_id', 'request_ids',
)


class Custody:
    """Independent SQLite custody store for one fixed external receiver site."""

    def __init__(self, path, destination: str):
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError('destination must be nonempty text')
        self.destination = destination
        self.conn = sqlite3.connect(path)
        self.conn.execute('PRAGMA foreign_keys = ON')
        with self.conn:
            self.conn.executescript(
                'BEGIN IMMEDIATE;\n' + _SCHEMA.read_text(encoding='utf-8') + '\nCOMMIT;'
            )
        with self.conn:
            self.conn.execute(
                'INSERT INTO custody_site(singleton, destination) VALUES (1, ?) '
                'ON CONFLICT(singleton) DO NOTHING', (destination,)
            )
            row = self.conn.execute(
                'SELECT destination FROM custody_site WHERE singleton = 1'
            ).fetchone()
            if row is None or row[0] != destination:
                raise ValueError('custody store belongs to a different receiver site')

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> 'Custody':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __call__(self, operation: str, payload: dict) -> dict:
        if operation not in _OPERATIONS:
            raise ValueError(f'unknown custody operation: {operation}')
        if operation == 'confirm':
            return self.confirm(payload)
        return self.recovery(payload)

    def _validate_fact(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or any(field not in payload for field in _REQUIRED_FIELDS):
            raise ValueError('custody fact is missing required fields')
        dispatch_id = payload['dispatch_id']
        report_id = payload['report_id']
        report_revision = payload['report_revision']
        destination = payload['destination']
        fingerprint = payload['fingerprint']
        material = payload['material']
        first_request_id = payload['first_request_id']
        request_ids = payload['request_ids']
        if not all(isinstance(value, str) and value.strip()
                   for value in (dispatch_id, report_id, destination, fingerprint,
                                 material, first_request_id)):
            raise ValueError('custody fact contains invalid text fields')
        if type(report_revision) is not int or report_revision < 1:
            raise ValueError('custody fact contains an invalid report revision')
        if (not isinstance(request_ids, (tuple, list)) or not request_ids
                or any(not isinstance(value, str) or not value.strip() for value in request_ids)
                or len(set(request_ids)) != len(tuple(request_ids))
                or request_ids[0] != first_request_id):
            raise ValueError('custody fact contains invalid claim request bindings')
        if destination != self.destination:
            raise ValueError('custody fact is addressed to a different receiver site')
        if body_fingerprint(material) != fingerprint:
            raise ValueError('custody fact fingerprint does not match the report material')
        report = decode_report(material)
        if report.ref != (report_id, report_revision):
            raise ValueError('custody fact report identity does not match its material')
        return {
            'dispatch_id': dispatch_id,
            'report_id': report_id,
            'report_revision': report_revision,
            'destination': destination,
            'fingerprint': fingerprint,
            'material': material,
            'first_request_id': first_request_id,
            'request_ids': tuple(request_ids),
        }

    def _read_fact(self, dispatch_id: str) -> dict | None:
        row = self.conn.execute(
            'SELECT dispatch_id, report_id, report_revision, destination, fingerprint, material, '
            'first_request_id FROM custody_confirmations WHERE dispatch_id = ?',
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None
        requests = tuple(
            item[0] for item in self.conn.execute(
                'SELECT request_id FROM custody_requests WHERE dispatch_id = ? ORDER BY ordinal',
                (dispatch_id,),
            )
        )
        return {
            'dispatch_id': row[0], 'report_id': row[1], 'report_revision': row[2],
            'destination': row[3], 'fingerprint': row[4], 'material': row[5],
            'first_request_id': row[6], 'request_ids': requests,
        }

    def confirm(self, payload: dict) -> dict:
        """Persist the confirmation; repeated facts never rewrite a settled binding."""
        fact = self._validate_fact(payload)
        self.conn.execute('BEGIN IMMEDIATE')
        with self.conn:
            existing = self._read_fact(fact['dispatch_id'])
            if existing is None:
                self.conn.execute(
                    'INSERT INTO custody_confirmations(dispatch_id, report_id, report_revision, '
                    'destination, fingerprint, material, first_request_id) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (fact['dispatch_id'], fact['report_id'], fact['report_revision'],
                     fact['destination'], fact['fingerprint'], fact['material'],
                     fact['first_request_id']),
                )
                self.conn.executemany(
                    'INSERT INTO custody_requests(dispatch_id, request_id, ordinal) '
                    'VALUES (?, ?, ?)',
                    tuple((fact['dispatch_id'], request_id, ordinal)
                          for ordinal, request_id in enumerate(fact['request_ids'], 1)),
                )
            else:
                immutable = {
                    key: existing[key]
                    for key in ('dispatch_id', 'report_id', 'report_revision', 'destination',
                                'fingerprint', 'material', 'first_request_id')
                }
                expected = {
                    key: fact[key]
                    for key in ('dispatch_id', 'report_id', 'report_revision', 'destination',
                                'fingerprint', 'material', 'first_request_id')
                }
                if immutable != expected:
                    raise ValueError(
                        f'custody confirmation conflicts with the settled fact for '
                        f"{fact['dispatch_id']}"
                    )
                known = set(existing['request_ids'])
                extra = tuple(
                    request_id for request_id in fact['request_ids'] if request_id not in known
                )
                if extra:
                    next_ordinal = len(existing['request_ids']) + 1
                    self.conn.executemany(
                        'INSERT INTO custody_requests(dispatch_id, request_id, ordinal) '
                        'VALUES (?, ?, ?)',
                        tuple((fact['dispatch_id'], request_id, next_ordinal + offset)
                              for offset, request_id in enumerate(extra)),
                    )
            return self._read_fact(fact['dispatch_id'])

    def recovery(self, payload: dict) -> list:
        if not isinstance(payload, dict) or payload.get('destination') != self.destination:
            raise ValueError('recovery request is addressed to a different receiver site')
        rows = self.conn.execute(
            'SELECT dispatch_id FROM custody_confirmations ORDER BY dispatch_id'
        ).fetchall()
        return [self._read_fact(row[0]) for row in rows]
