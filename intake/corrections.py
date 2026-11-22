from collections.abc import Iterable, Mapping
from sqlite3 import Connection

from intake.observations import validate_observations
from intake.revisions import BatchRevision, append_revision, read_revision
from intake.stations import resolve_station


def correct(
    conn: Connection, parent: tuple[str, int], rows: Iterable[Mapping[str, object]]
) -> BatchRevision:
    observations = validate_observations(rows)
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        original = read_revision(conn, parent)
        station = resolve_station(conn, original.station_id)
        return append_revision(
            conn, original.batch_id, station.station_id, original.revision, observations
        )
