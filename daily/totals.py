from collections.abc import Iterable
from dataclasses import dataclass

from daily.select import ReportLine


@dataclass(frozen=True)
class StationTotal:
    station_id: str
    liters: int
    missing: int


@dataclass(frozen=True)
class DailyTotals:
    liters: int
    missing: int
    stations: tuple[StationTotal, ...]


def calculate_totals(lines: Iterable[ReportLine]) -> DailyTotals:
    """Accumulate integer liters and count missing rows in first-seen station order."""
    stations: dict[str, tuple[int, int]] = {}
    for line in lines:
        liters, missing = stations.get(line.station_id, (0, 0))
        if line.liters is None:
            missing += 1
        else:
            liters += line.liters
        stations[line.station_id] = liters, missing
    subtotals = tuple(
        StationTotal(station_id, liters, missing)
        for station_id, (liters, missing) in stations.items()
    )
    return DailyTotals(
        sum(station.liters for station in subtotals),
        sum(station.missing for station in subtotals),
        subtotals,
    )
