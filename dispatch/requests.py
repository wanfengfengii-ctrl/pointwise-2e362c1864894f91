from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class WithdrawRequest:
    dispatch_id: str
    result: str | None


def read_withdraw_request(conn: Connection, request_id: str) -> WithdrawRequest | None:
    row = conn.execute(
        'SELECT dispatch_id, result FROM withdraw_requests WHERE request_id = ?', (request_id,)
    ).fetchone()
    return None if row is None else WithdrawRequest(*row)


def bind_withdraw_request(conn: Connection, request_id: str, dispatch_id: str) -> WithdrawRequest:
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('withdraw request_id must be nonempty text')
    request = read_withdraw_request(conn, request_id)
    if request is not None:
        if request.dispatch_id != dispatch_id:
            raise ValueError(f'withdraw request parameters conflict for {request_id}')
        return request
    conn.execute(
        'INSERT INTO withdraw_requests(request_id, dispatch_id) VALUES (?, ?)',
        (request_id, dispatch_id),
    )
    return WithdrawRequest(dispatch_id, None)


def save_withdraw_result(conn: Connection, request_id: str, dispatch_id: str, result: str) -> str:
    if result not in ('claimed', 'withdrawn'):
        raise ValueError('cannot cache a nonterminal withdrawal result')
    updated = conn.execute(
        'UPDATE withdraw_requests SET result = ? WHERE request_id = ? AND dispatch_id = ? '
        'AND (result IS NULL OR result = ?)', (result, request_id, dispatch_id, result)
    )
    if updated.rowcount != 1:
        raise ValueError('withdrawal result conflicts with its request binding')
    return result
