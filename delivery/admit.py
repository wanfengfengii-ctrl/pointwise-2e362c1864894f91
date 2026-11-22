from collections.abc import Callable
from sqlite3 import Connection

from daily.checks import validate_report_material
from delivery.register import DeliveryEntry, record_admission, store_material
from dispatch.catalog import DispatchBinding
from dispatch.handoff import Authority


def admit(
    conn: Connection,
    binding: DispatchBinding,
    material: str,
    *,
    authority: Authority | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> DeliveryEntry:
    """Validate a received body and apply a source decision when one is present."""
    validate_report_material(material, binding.report_ref, binding.fingerprint)
    if authority is not None and authority.binding != binding:
        raise ValueError('handoff decision belongs to different material')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        entry = store_material(conn, binding, material)
        if authority is None:
            return entry
        result = record_admission(conn, entry, authority.kind)
        if checkpoint is not None:
            checkpoint('before_admission_commit')
        return result
