import hashlib
import json
from dataclasses import asdict, dataclass

from daily.select import ReportLine
from daily.totals import DailyTotals, StationTotal


@dataclass(frozen=True)
class ReportBody:
    report_id: str
    revision: int
    parents: tuple[tuple[str, int], ...]
    lines: tuple[ReportLine, ...]
    totals: DailyTotals

    @property
    def ref(self) -> tuple[str, int]:
        return self.report_id, self.revision


def encode_report(report: ReportBody) -> str:
    return json.dumps(asdict(report), ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def decode_report(material: str) -> ReportBody:
    """Read the canonical report shape. Admission checks the business invariants separately."""
    if not isinstance(material, str):
        raise ValueError('report material must be text')
    try:
        data = json.loads(material)
        totals = data['totals']
        report = ReportBody(
            data['report_id'], data['revision'],
            tuple(tuple(ref) for ref in data['parents']),
            tuple(ReportLine(**line) for line in data['lines']),
            DailyTotals(totals['liters'], totals['missing'],
                        tuple(StationTotal(**item) for item in totals['stations'])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid report material') from exc
    if encode_report(report) != material:
        raise ValueError('report material is not canonical')
    return report


def body_fingerprint(material: str) -> str:
    return hashlib.sha256(material.encode('utf-8')).hexdigest()
