from collections.abc import Callable, Sequence
from dataclasses import dataclass
from sqlite3 import Connection

from daily.body import ReportBody, body_fingerprint, encode_report
from daily.checks import validate_report_material
from delivery.ledger import append_posting, read_posting
from delivery.receipts import Receipt, read_receipt, store_receipt
from delivery.register import DeliveryEntry, read_admission, record_disposition
from dispatch.catalog import DispatchBinding


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
        if _is_protected(conn, dispatch_id):
            raise ValueError('dispatch is on the protection roster; use claim_protected')
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


def _site_destination(conn: Connection) -> str:
    row = conn.execute('SELECT destination FROM receiver_site WHERE singleton = 1').fetchone()
    if row is None:
        raise ValueError('delivery database is not initialized for a receiver site')
    return row[0]


def _is_protected(conn: Connection, dispatch_id: str) -> bool:
    return conn.execute(
        'SELECT 1 FROM protection_roster WHERE dispatch_id = ?', (dispatch_id,)
    ).fetchone() is not None


def _protection_ready(conn: Connection) -> bool:
    row = conn.execute('SELECT ready FROM protection_state WHERE singleton = 1').fetchone()
    return row is not None and row[0] == 1


def _roster_ids(conn: Connection) -> tuple[str, ...]:
    return tuple(
        row[0] for row in conn.execute(
            'SELECT dispatch_id FROM protection_roster ORDER BY dispatch_id'
        )
    )


def configure_protection(conn: Connection, dispatch_ids: Sequence[str]) -> None:
    """Fix the nonempty protection roster for this receiver site before opening claims."""
    ids = tuple(dispatch_ids)
    if (not ids or any(not isinstance(value, str) or not value.strip() for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('protection roster must be a nonempty sequence of unique dispatch ids')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        existing = _roster_ids(conn)
        if existing:
            if set(existing) != set(ids):
                raise ValueError('the protection roster of this receiver site is fixed')
            return
        conn.executemany(
            'INSERT INTO protection_roster(dispatch_id) VALUES (?)',
            tuple((dispatch_id,) for dispatch_id in ids),
        )
        conn.execute('INSERT INTO protection_state(singleton, ready) VALUES (1, 0)')


def _validate_custody_fact(conn: Connection, fact: object) -> dict:
    required = (
        'dispatch_id', 'report_id', 'report_revision', 'destination', 'fingerprint', 'material',
        'first_request_id', 'request_ids',
    )
    if not isinstance(fact, dict) or any(field not in fact for field in required):
        raise ValueError('custody fact is missing required fields')
    if not isinstance(fact['request_ids'], (list, tuple)):
        raise ValueError('custody fact contains invalid claim request bindings')
    request_ids = tuple(fact['request_ids'])
    if (not request_ids or fact['first_request_id'] != request_ids[0]
            or any(not isinstance(value, str) or not value.strip() for value in request_ids)
            or len(set(request_ids)) != len(request_ids)):
        raise ValueError('custody fact contains invalid claim request bindings')
    if fact['destination'] != _site_destination(conn):
        raise ValueError('custody fact is addressed to a different receiver site')
    report = validate_report_material(
        fact['material'], (fact['report_id'], fact['report_revision']), fact['fingerprint']
    )
    binding = DispatchBinding(
        fact['dispatch_id'], fact['report_id'], fact['report_revision'],
        fact['destination'], fact['fingerprint'],
    )
    return {'binding': binding, 'material': fact['material'], 'report': report,
            'first_request_id': fact['first_request_id'], 'request_ids': request_ids}


def _bind_protected_request(
    conn: Connection, dispatch_id: str, request_id: str, ordinal: int
) -> None:
    _bind_request(conn, request_id, dispatch_id)
    conn.execute(
        'INSERT INTO protected_requests(dispatch_id, request_id, ordinal) VALUES (?, ?, ?) '
        'ON CONFLICT(request_id) DO NOTHING', (dispatch_id, request_id, ordinal)
    )
    recorded = conn.execute(
        'SELECT dispatch_id, ordinal FROM protected_requests WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    if recorded != (dispatch_id, ordinal):
        raise ValueError(f'protected claim request parameters conflict for {request_id}')


def _install_fact(conn: Connection, raw_fact: object) -> None:
    """Install one confirmed fact inside the caller's transaction, all or nothing."""
    fact = _validate_custody_fact(conn, raw_fact)
    binding = fact['binding']
    dispatch_id = binding.dispatch_id
    if not _is_protected(conn, dispatch_id):
        raise ValueError(f'{dispatch_id} is not on the protection roster')
    entry = read_admission(conn, dispatch_id)
    if entry is None:
        conn.execute(
            'INSERT INTO inbox(dispatch_id, report_id, report_revision, destination, fingerprint, '
            "material, status) VALUES (?, ?, ?, ?, ?, ?, 'claimed')",
            (dispatch_id, binding.report_id, binding.report_revision, binding.destination,
             binding.fingerprint, fact['material']),
        )
    else:
        if entry.binding != binding or entry.material != fact['material']:
            raise ValueError(f'custody fact conflicts with the admitted delivery for {dispatch_id}')
        if entry.status == 'withdrawn':
            raise ValueError(f'custody fact conflicts with the withdrawn delivery {dispatch_id}')
        if entry.status == 'pending':
            raise ValueError(f'custody fact cannot settle the pending delivery {dispatch_id}')
        if entry.status != 'claimed':
            changed = conn.execute(
                "UPDATE inbox SET status = 'claimed' WHERE dispatch_id = ? AND status != 'claimed'",
                (dispatch_id,),
            )
            if changed.rowcount != 1:
                raise ValueError(f'cannot settle the protected delivery {dispatch_id}')
    posting = read_posting(conn, dispatch_id)
    if posting is None:
        append_posting(conn, binding, fact['material'])
    elif posting.fingerprint != binding.fingerprint or posting.body != fact['report']:
        raise ValueError(f'formal posting conflicts with the custody fact for {dispatch_id}')
    for ordinal, request_id in enumerate(fact['request_ids'], 1):
        _bind_protected_request(conn, dispatch_id, request_id, ordinal)
    receipt = read_receipt(conn, dispatch_id)
    expected_receipt = Receipt(
        dispatch_id, f'{dispatch_id}/first', binding.destination, binding.report_id,
        binding.report_revision, binding.fingerprint, fact['first_request_id'],
    )
    if receipt is None:
        conn.execute(
            'INSERT INTO receipts(dispatch_id, receipt_id, destination, report_id, '
            'report_revision, fingerprint, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (expected_receipt.dispatch_id, expected_receipt.receipt_id,
             expected_receipt.destination, expected_receipt.report_id,
             expected_receipt.report_revision, expected_receipt.fingerprint,
             expected_receipt.request_id),
        )
    elif receipt != expected_receipt:
        raise ValueError(f'first receipt conflicts with the custody fact for {dispatch_id}')


def restore_protected(
    conn: Connection,
    call_custody: Callable[[str, dict], object],
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> None:
    """Restore confirmed protected facts from custody, then open protected claims atomically."""
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if not _roster_ids(conn):
            raise ValueError('protection roster must be configured before restore')
        destination = _site_destination(conn)
        facts = call_custody('recovery', {'destination': destination})
        if not isinstance(facts, (list, tuple)):
            raise ValueError('custody recovery must return the complete confirmed facts')
        roster = set(_roster_ids(conn))
        for raw_fact in facts:
            if not isinstance(raw_fact, dict) or raw_fact.get('dispatch_id') not in roster:
                raise ValueError('custody recovery contains a fact outside the protection roster')
            _install_fact(conn, raw_fact)
        if checkpoint is not None:
            checkpoint('before_restore_commit')
        conn.execute('UPDATE protection_state SET ready = 1 WHERE singleton = 1')


def _fact_payload(entry: DeliveryEntry, first_request_id: str, request_ids: Sequence[str]) -> dict:
    binding = entry.binding
    return {
        'dispatch_id': binding.dispatch_id,
        'report_id': binding.report_id,
        'report_revision': binding.report_revision,
        'destination': binding.destination,
        'fingerprint': binding.fingerprint,
        'material': entry.material,
        'first_request_id': first_request_id,
        'request_ids': tuple(request_ids),
    }


def _protected_request_ids(conn: Connection, dispatch_id: str) -> list[str]:
    return [
        row[0] for row in conn.execute(
            'SELECT request_id FROM protected_requests WHERE dispatch_id = ? ORDER BY ordinal',
            (dispatch_id,),
        )
    ]


def claim_protected(
    conn: Connection,
    call_custody: Callable[[str, dict], object],
    dispatch_id: str,
    request_id: str,
) -> ClaimResult:
    """Settle a roster dispatch only after the independent custody side confirms it."""
    if not isinstance(dispatch_id, str) or not dispatch_id.strip():
        raise ValueError('dispatch_id must be nonempty text')
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('claim request_id must be nonempty text')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if not _roster_ids(conn):
            raise ValueError('protection roster must be configured before a protected claim')
        if not _protection_ready(conn):
            raise ValueError('protected claim entry is not open; restore custody facts first')
        if not _is_protected(conn, dispatch_id):
            raise ValueError(f'{dispatch_id} is not on the protection roster')
        _bind_request(conn, request_id, dispatch_id)
        entry = read_admission(conn, dispatch_id)
        if entry is None:
            raise ValueError(f'{dispatch_id} has no confirmed protected delivery')
        if entry.status == 'withdrawn':
            raise ValueError(f'{dispatch_id} was withdrawn and cannot be protected-claimed')
        if entry.status not in ('admitted', 'claimed'):
            raise ValueError(f'{dispatch_id} is not ready for a protected claim')

        if entry.status == 'admitted':
            payload = _fact_payload(entry, request_id, (request_id,))
            fact = call_custody('confirm', payload)
            checked = _validate_custody_fact(conn, fact)
            if checked['binding'] != entry.binding or checked['material'] != entry.material:
                raise ValueError(f'custody confirmation conflicts with the delivery {dispatch_id}')
            if checked['first_request_id'] != request_id:
                raise ValueError(
                    f'custody already settled {dispatch_id} under a different first request'
                )
            _install_fact(conn, fact)
        else:
            receipt = read_receipt(conn, dispatch_id)
            posting = read_posting(conn, dispatch_id)
            if receipt is None or posting is None or not receipt.matches(entry.binding):
                raise ValueError('formal posting and first receipt are incomplete')
            bound = _protected_request_ids(conn, dispatch_id)
            if not bound or bound[0] != receipt.request_id:
                raise ValueError(f'protected bindings for {dispatch_id} lost the first request')
            request_ids = bound if request_id in bound else (*bound, request_id)
            payload = _fact_payload(entry, receipt.request_id, request_ids)
            fact = call_custody('confirm', payload)
            checked = _validate_custody_fact(conn, fact)
            if checked['binding'] != entry.binding or checked['material'] != entry.material:
                raise ValueError(f'custody confirmation conflicts with the delivery {dispatch_id}')
            if checked['first_request_id'] != receipt.request_id:
                raise ValueError(f'custody changed the first request of {dispatch_id}')
            if request_id not in checked['request_ids']:
                raise ValueError(f'custody did not bind request {request_id} to {dispatch_id}')
            if request_id not in bound:
                _bind_protected_request(conn, dispatch_id, request_id, len(bound) + 1)
        settled = read_admission(conn, dispatch_id)
        return _replay(conn, settled)
