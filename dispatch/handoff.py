from dataclasses import dataclass
from sqlite3 import Connection
from typing import TYPE_CHECKING, Literal

from dispatch.catalog import DispatchBinding, read_binding

if TYPE_CHECKING:
    from delivery.register import DeliveryEntry


@dataclass(frozen=True)
class HandoffState:
    phase: str
    terminal: str | None

    @property
    def status(self) -> str:
        if self.terminal is not None:
            return self.terminal
        return 'admitted' if self.phase == 'admitted' else 'pending'

    @property
    def source_can_decide(self) -> bool:
        return self.phase in ('registered', 'staging')


@dataclass(frozen=True)
class Authority:
    binding: DispatchBinding
    kind: Literal['grant', 'withdrawn']


def read_handoff_state(conn: Connection, dispatch_id: str) -> HandoffState:
    row = conn.execute(
        'SELECT phase, terminal FROM dispatches WHERE dispatch_id = ?', (dispatch_id,)
    ).fetchone()
    if row is None:
        raise KeyError(dispatch_id)
    return HandoffState(*row)


def start_handoff(conn: Connection, dispatch_id: str) -> HandoffState:
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        conn.execute(
            "UPDATE dispatches SET phase = 'staging' "
            "WHERE dispatch_id = ? AND phase = 'registered' AND terminal IS NULL",
            (dispatch_id,),
        )
        return read_handoff_state(conn, dispatch_id)


def issue_authority(conn: Connection, staged: 'DeliveryEntry') -> Authority:
    """Commit the source's decision before returning a destination-bound message."""
    dispatch_id = staged.binding.dispatch_id
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        binding = read_binding(conn, dispatch_id)
        if binding != staged.binding:
            raise ValueError('staged material belongs to a different dispatch binding')
        state = read_handoff_state(conn, dispatch_id)
        if state.source_can_decide:
            if state.terminal == 'withdrawn':
                return Authority(binding, 'withdrawn')
            if state.phase != 'staging' or staged.status != 'pending':
                raise ValueError('an active handoff with pending material is required')
            conn.execute("UPDATE dispatches SET phase = 'granted' WHERE dispatch_id = ?",
                         (dispatch_id,))
        return Authority(binding, 'grant')


def record_admission_result(conn: Connection, entry: 'DeliveryEntry') -> HandoffState:
    dispatch_id = entry.binding.dispatch_id
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if read_binding(conn, dispatch_id) != entry.binding:
            raise ValueError('admission result belongs to a different dispatch binding')
        state = read_handoff_state(conn, dispatch_id)
        if entry.status == 'pending':
            return state
        if state.source_can_decide:
            if state.terminal == 'withdrawn' and entry.status == 'withdrawn':
                return state
            raise ValueError('receiver disposition has no corresponding source grant')
        if state.terminal is not None:
            if entry.status in ('claimed', 'withdrawn') and entry.status != state.terminal:
                raise ValueError('receiver terminal result conflicts with the recorded result')
            return state
        terminal = entry.status if entry.status in ('claimed', 'withdrawn') else None
        conn.execute(
            "UPDATE dispatches SET phase = 'admitted', terminal = ? WHERE dispatch_id = ?",
            (terminal, dispatch_id),
        )
        return read_handoff_state(conn, dispatch_id)
