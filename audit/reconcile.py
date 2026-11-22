from dataclasses import dataclass

from audit.evidence import AuditEvidence
from daily.totals import DailyTotals, calculate_totals


@dataclass(frozen=True)
class BalanceFinding:
    station_id: str | None
    frozen: tuple[int, int] | None
    booked: tuple[int, int] | None
    recomputed: tuple[int, int] | None
    matches: bool


def _by_station(totals: DailyTotals) -> dict[str, tuple[int, int]]:
    return {station.station_id: (station.liters, station.missing) for station in totals.stations}


def reconcile_balances(evidence: AuditEvidence) -> tuple[BalanceFinding, ...]:
    if evidence.posting is None:
        return ()
    frozen = evidence.frozen.totals
    booked = evidence.posting.body.totals
    recomputed = calculate_totals(evidence.posting.body.lines)
    frozen_total = frozen.liters, frozen.missing
    booked_total = booked.liters, booked.missing
    recomputed_total = recomputed.liters, recomputed.missing
    findings = [BalanceFinding(None, frozen_total, booked_total, recomputed_total,
                               frozen_total == booked_total == recomputed_total)]
    frozen_stations = _by_station(frozen)
    booked_stations = _by_station(booked)
    recomputed_stations = _by_station(recomputed)
    station_ids = dict.fromkeys((*frozen_stations, *booked_stations, *recomputed_stations))
    for station_id in station_ids:
        expected = frozen_stations.get(station_id)
        actual = booked_stations.get(station_id)
        calculated = recomputed_stations.get(station_id)
        findings.append(BalanceFinding(station_id, expected, actual, calculated,
                                       expected == actual == calculated))
    return tuple(findings)
