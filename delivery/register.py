from dataclasses import dataclass, replace
from pathlib import Path
from sqlite3 import Connection

from delivery.receipts import read_receipt
from dispatch.catalog import DispatchBinding


@dataclass(frozen=True)
class DeliveryEntry:
    binding: DispatchBinding
    material: str
    status: str


def initialize_delivery(conn: Connection, destination: str) -> None:
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError('destination must be nonempty text')
    if conn.in_transaction:
        raise ValueError('delivery initialization requires an idle connection')
    schema = Path(__file__).resolve().parents[1] / 'schema' / 'delivery.sql'
    conn.execute('PRAGMA foreign_keys = ON')
    with conn:
        conn.executescript('BEGIN IMMEDIATE;\n' + schema.read_text(encoding='utf-8') + '\nCOMMIT;')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        conn.execute(
            'INSERT INTO receiver_site(singleton, destination) VALUES (1, ?) '
            'ON CONFLICT(singleton) DO NOTHING', (destination,)
        )
        _require_destination(conn, destination)


def _require_destination(conn: Connection, destination: str) -> None:
    row = conn.execute('SELECT destination FROM receiver_site WHERE singleton = 1').fetchone()
    if row is None or row[0] != destination:
        raise ValueError('material is addressed to a different delivery site')


def read_admission(conn: Connection, dispatch_id: str) -> DeliveryEntry | None:
    row = conn.execute(
        'SELECT dispatch_id, report_id, report_revision, destination, fingerprint, material, status '
        'FROM inbox WHERE dispatch_id = ?', (dispatch_id,)
    ).fetchone()
    if row is None:
        return None
    return DeliveryEntry(DispatchBinding(*row[:5]), row[5], row[6])


def store_material(conn: Connection, binding: DispatchBinding, material: str) -> DeliveryEntry:
    """Store validated material in the caller's transaction without granting authority."""
    _require_destination(conn, binding.destination)
    existing = read_admission(conn, binding.dispatch_id)
    if existing is not None:
        if existing.binding != binding or existing.material != material:
            raise ValueError('received material conflicts with the original delivery')
        return existing
    conn.execute(
        'INSERT INTO inbox(dispatch_id, report_id, report_revision, destination, fingerprint, '
        "material, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (binding.dispatch_id, binding.report_id, binding.report_revision,
         binding.destination, binding.fingerprint, material),
    )
    return DeliveryEntry(binding, material, 'pending')


def record_admission(conn: Connection, entry: DeliveryEntry, decision: str) -> DeliveryEntry:
    if decision == 'grant':
        if entry.status != 'pending':
            return entry
        status = 'admitted'
    elif decision == 'withdrawn':
        if entry.status == 'withdrawn':
            return entry
        if entry.status != 'pending':
            raise ValueError('source withdrawal conflicts with an active receiver disposition')
        status = 'withdrawn'
    else:
        raise ValueError('unsupported handoff decision')
    changed = conn.execute(
        "UPDATE inbox SET status = ? WHERE dispatch_id = ? AND status = 'pending'",
        (status, entry.binding.dispatch_id),
    )
    if changed.rowcount != 1:
        raise ValueError('admission no longer has pending material')
    return replace(entry, status=status)


def record_disposition(conn: Connection, entry: DeliveryEntry, status: str) -> DeliveryEntry:
    if entry.status != 'admitted' or status not in ('claimed', 'withdrawn'):
        raise ValueError('terminal disposition requires an admitted delivery')
    changed = conn.execute(
        "UPDATE inbox SET status = ? WHERE dispatch_id = ? AND status = 'admitted'",
        (status, entry.binding.dispatch_id),
    )
    if changed.rowcount != 1:
        raise ValueError('delivery already has a disposition')
    return replace(entry, status=status)


def withdraw_admitted(conn: Connection, binding: DispatchBinding) -> DeliveryEntry | None:
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        _require_destination(conn, binding.destination)
        entry = read_admission(conn, binding.dispatch_id)
        if entry is None:
            return None
        if entry.binding != binding:
            raise ValueError('withdrawal belongs to a different delivery binding')
        if entry.status == 'pending':
            return None
        if entry.status == 'admitted':
            return record_disposition(conn, entry, 'withdrawn')
        if entry.status == 'claimed':
            receipt = read_receipt(conn, binding.dispatch_id)
            if receipt is None or not receipt.matches(binding):
                raise ValueError('claimed delivery has no matching first receipt')
        return entry
