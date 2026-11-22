from daily.body import ReportBody, body_fingerprint, decode_report
from daily.totals import calculate_totals


def validate_report_material(
    material: str, expected_ref: tuple[str, int], expected_fingerprint: str
) -> ReportBody:
    if not isinstance(material, str) or body_fingerprint(material) != expected_fingerprint:
        raise ValueError('received material differs from the frozen binding')
    report = decode_report(material)
    if report.ref != expected_ref:
        raise ValueError('received report identity differs from the frozen binding')
    last_index = -1
    next_position = 1
    for line in report.lines:
        index = line.selection_index
        if type(index) is not int or not last_index <= index < len(report.parents) or index < 0:
            raise ValueError('received rows are outside the ordered parent selections')
        if index != last_index:
            next_position = 1
        if (line.parent != report.parents[index] or line.position != next_position
                or type(line.position) is not int):
            raise ValueError('received row has an invalid parent or original position')
        if not isinstance(line.source, str) or not line.source.strip():
            raise ValueError('received row lacks its source')
        if line.liters is None:
            if not isinstance(line.missing, str) or not line.missing.strip():
                raise ValueError('received missing row lacks its reason')
        elif type(line.liters) is not int or line.missing is not None:
            raise ValueError('received measurement is invalid')
        last_index = index
        next_position += 1
    if calculate_totals(report.lines) != report.totals:
        raise ValueError('received totals do not agree with the received rows')
    return report
