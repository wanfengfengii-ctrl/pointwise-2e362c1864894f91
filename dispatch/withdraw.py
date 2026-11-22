from collections.abc import Callable
from sqlite3 import Connection

from delivery.register import withdraw_admitted
from dispatch.catalog import read_binding
from dispatch.handoff import read_handoff_state, record_admission_result
from dispatch.requests import bind_withdraw_request, save_withdraw_result
from dispatch.resume import resume_handoff


def withdraw(
    source: Connection,
    receiver: Connection | None,
    dispatch_id: str,
    request_id: str,
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> str:
    source.execute('BEGIN IMMEDIATE')
    with source:
        binding = read_binding(source, dispatch_id)
        request = bind_withdraw_request(source, request_id, dispatch_id)
        if request.result is not None:
            return request.result
        state = read_handoff_state(source, dispatch_id)
        if state.terminal is not None:
            return save_withdraw_result(source, request_id, dispatch_id, state.terminal)
        if state.source_can_decide:
            source.execute(
                "UPDATE dispatches SET terminal = 'withdrawn' WHERE dispatch_id = ?", (dispatch_id,)
            )
            return save_withdraw_result(source, request_id, dispatch_id, 'withdrawn')

    if receiver is None:
        return 'pending'
    status = resume_handoff(source, receiver, dispatch_id)
    if status == 'admitted':
        entry = withdraw_admitted(receiver, binding)
        if entry is None:
            return 'pending'
        if checkpoint is not None:
            checkpoint('after_remote_withdraw_commit')
        status = record_admission_result(source, entry).status
    if status == 'pending':
        return status
    source.execute('BEGIN IMMEDIATE')
    with source:
        return save_withdraw_result(source, request_id, dispatch_id, status)
