from collections.abc import Iterable, Mapping
from sqlite3 import Connection

from intake.observations import validate_observations
from intake.revisions import BatchRevision, append_revision
from intake.stations import resolve_station


def record(
    conn: Connection, batch_id: str, station_id: str, rows: Iterable[Mapping[str, object]]
) -> BatchRevision:
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError('batch_id must be nonempty text')
    observations = validate_observations(rows)
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if conn.execute('SELECT 1 FROM batches WHERE batch_id = ?', (batch_id,)).fetchone():
            raise ValueError(f'batch already exists: {batch_id}')
        station = resolve_station(conn, station_id, register=True)
        conn.execute(
            'INSERT INTO batches(batch_id, station_id) VALUES (?, ?)',
            (batch_id, station.station_id),
        )
        return append_revision(conn, batch_id, station.station_id, None, observations)
