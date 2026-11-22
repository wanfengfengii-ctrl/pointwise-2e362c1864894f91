from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class Station:
    station_id: str


def resolve_station(conn: Connection, station_id: str, *, register: bool = False) -> Station:
    """Resolve a station, optionally registering it in the caller's intake transaction."""
    if not isinstance(station_id, str) or not station_id.strip():
        raise ValueError('station_id must be nonempty text')
    if register:
        conn.execute(
            'INSERT INTO stations(station_id) VALUES (?) ON CONFLICT(station_id) DO NOTHING',
            (station_id,),
        )
    row = conn.execute(
        'SELECT station_id FROM stations WHERE station_id = ?', (station_id,)
    ).fetchone()
    if row is None:
        raise KeyError(station_id)
    return Station(row[0])
