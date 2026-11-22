from dataclasses import dataclass
from sqlite3 import Connection

from intake.observations import Observation


@dataclass(frozen=True)
class BatchRevision:
    batch_id: str
    revision: int
    station_id: str
    parent_revision: int | None
    observations: tuple[Observation, ...]

    @property
    def ref(self) -> tuple[str, int]:
        return self.batch_id, self.revision


def append_revision(
    conn: Connection,
    batch_id: str,
    station_id: str,
    parent_revision: int | None,
    observations: tuple[Observation, ...],
) -> BatchRevision:
    """Append a validated revision within the caller's write transaction."""
    revision = conn.execute(
        'SELECT COALESCE(MAX(revision), 0) + 1 FROM batch_revisions WHERE batch_id = ?',
        (batch_id,),
    ).fetchone()[0]
    conn.execute(
        'INSERT INTO batch_revisions(batch_id, revision, parent_revision) VALUES (?, ?, ?)',
        (batch_id, revision, parent_revision),
    )
    conn.executemany(
        'INSERT INTO observations(batch_id, revision, position, liters, missing, source) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (
            (batch_id, revision, item.position,
             None if item.liters is None else str(item.liters), item.missing, item.source)
            for item in observations
        ),
    )
    return BatchRevision(batch_id, revision, station_id, parent_revision, observations)


def read_revision(conn: Connection, ref: tuple[str, int]) -> BatchRevision:
    if (not isinstance(ref, (tuple, list)) or len(ref) != 2
            or not isinstance(ref[0], str) or not ref[0].strip()
            or type(ref[1]) is not int or ref[1] < 1):
        raise ValueError('a batch reference requires a batch id and positive revision')
    batch_id, revision = ref
    head = conn.execute(
        'SELECT batches.station_id, batch_revisions.parent_revision '
        'FROM batch_revisions JOIN batches USING (batch_id) '
        'WHERE batch_id = ? AND revision = ?',
        (batch_id, revision),
    ).fetchone()
    if head is None:
        raise KeyError((batch_id, revision))
    rows = conn.execute(
        'SELECT position, liters, missing, source FROM observations '
        'WHERE batch_id = ? AND revision = ? ORDER BY position',
        (batch_id, revision),
    )
    observations = tuple(
        Observation(position, None if liters is None else int(liters), missing, source)
        for position, liters, missing, source in rows
    )
    return BatchRevision(batch_id, revision, head[0], head[1], observations)
