from collections.abc import Callable
from sqlite3 import Connection

from delivery.admit import admit
from dispatch.catalog import read_dispatch_material
from dispatch.handoff import issue_authority, record_admission_result, start_handoff


def resume_handoff(
    source: Connection,
    receiver: Connection | None,
    dispatch_id: str,
    *,
    material: str | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> str:
    state = start_handoff(source, dispatch_id)
    if receiver is None:
        return state.status
    binding, frozen_material = read_dispatch_material(source, dispatch_id)
    received_material = frozen_material if material is None else material
    staged = admit(receiver, binding, received_material)
    if checkpoint is not None:
        checkpoint('after_material_commit')
    authority = issue_authority(source, staged)
    entry = admit(receiver, binding, received_material, authority=authority, checkpoint=checkpoint)
    if checkpoint is not None:
        checkpoint('after_admission_commit')
    return record_admission_result(source, entry).status
