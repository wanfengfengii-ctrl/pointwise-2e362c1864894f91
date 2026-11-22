from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    position: int
    liters: int | None
    missing: str | None
    source: str


def validate_observations(rows: Iterable[Mapping[str, object]]) -> tuple[Observation, ...]:
    """Validate a complete batch and assign its original one-based positions."""
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes, Mapping)):
        raise ValueError('observations must be an ordered iterable of rows')
    observations = []
    allowed_keys = {'liters', 'missing', 'source'}
    for position, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or row.keys() - allowed_keys:
            raise ValueError(f'observation {position} has invalid fields')
        liters = row.get('liters')
        missing = row.get('missing')
        source = row.get('source')
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f'observation {position} requires source text')
        if (liters is None) == (missing is None):
            raise ValueError(f'observation {position} requires exactly one of liters or missing')
        if liters is not None and type(liters) is not int:
            raise ValueError(f'observation {position} requires integer liters')
        if missing is not None and (not isinstance(missing, str) or not missing.strip()):
            raise ValueError(f'observation {position} requires a nonempty missing reason')
        observations.append(Observation(position, liters, missing, source))
    return tuple(observations)
