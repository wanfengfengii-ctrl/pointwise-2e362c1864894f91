import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.history import review
from audit.record import initialize_reviews
from daily.freeze import freeze
from delivery.claim import claim
from delivery.register import initialize_delivery
from dispatch.catalog import dispatch, initialize_source
from dispatch.resume import resume_handoff
from dispatch.withdraw import withdraw
from intake.batches import record
from intake.corrections import correct


OBSERVATIONS = (
    {'liters': 7, 'source': '北站纸簿第 1 行'},
    {'liters': 11, 'source': '北站纸簿第 2 行'},
    {'liters': 0, 'source': '北站纸簿第 3 行'},
    {'missing': '探头停测', 'source': '北站交班簿第 4 行'},
)


def run() -> None:
    with TemporaryDirectory(prefix='watershed-demo-') as directory, ExitStack() as resources:
        root = Path(directory)
        with closing(sqlite3.connect(root / 'source.sqlite')) as source:
            initialize_source(source)
            receiver = resources.enter_context(closing(sqlite3.connect(root / 'receiver.sqlite')))
            initialize_delivery(receiver, '河口外站')
            revision = record(source, '北站早班', '北站', OBSERVATIONS)
            report = freeze(source, '北站日报', [revision.ref])
            dispatch(source, '夜班-1', report.ref, '河口外站')
            admission = resume_handoff(source, receiver, '夜班-1')
        if admission != 'admitted':
            raise RuntimeError(f'demo admission returned {admission}')
        claimed = claim(receiver, '夜班-1', '外站领取-1')
        with closing(sqlite3.connect(root / 'source.sqlite')) as source:
            initialize_source(source)
            updated_rows = list(OBSERVATIONS)
            updated_rows[1] = {'liters': 13, 'source': '北站勘误簿第 2 行'}
            corrected = correct(source, revision.ref, updated_rows)
            new_report = freeze(source, '北站日报', [corrected.ref])
            withdrawal = withdraw(source, receiver, '夜班-1', '夜班撤发-1')
            reviews = resources.enter_context(closing(sqlite3.connect(root / 'audit.sqlite')))
            initialize_reviews(reviews)
            reviewed = review(source, receiver, reviews, '夜班-1')

        expected = {'liters': 18, 'missing': 1, 'values': [7, 11, 0, None]}
        actual = {'liters': report.totals.liters, 'missing': report.totals.missing,
                  'values': [line.liters for line in report.lines]}
        downstream_expected = {
            'claim': 'claimed', 'withdraw': 'claimed', 'receipt': '夜班-1/first',
            'liters_booked': 18, 'liters_reconciled': 18, 'missing_reconciled': 1,
            'corrected_liters': 20, 'posted_lines': 4, 'differences': [],
        }
        downstream_actual = {
            'claim': claimed.status, 'withdraw': withdrawal, 'receipt': claimed.receipt.receipt_id,
            'liters_booked': claimed.report.totals.liters,
            'liters_reconciled': reviewed.balances[0]['recomputed'][0],
            'missing_reconciled': reviewed.balances[0]['recomputed'][1],
            'corrected_liters': new_report.totals.liters, 'posted_lines': reviewed.posted_lines,
            'differences': reviewed.differences,
        }
        if actual != expected or downstream_actual != downstream_expected:
            raise RuntimeError(f'demo outcome differs: {actual!r} {downstream_actual!r}')
        event = {
            'case_id': 'n1.zero_and_missing', 'input': OBSERVATIONS, 'expected': expected,
            'actual': actual, 'downstream_expected': downstream_expected,
            'downstream_actual': downstream_actual,
            'code_paths': ['intake/batches.py', 'daily/freeze.py', 'daily/totals.py',
                           'dispatch/resume.py', 'delivery/claim.py', 'audit/history.py'],
            'oracle': '手算 7 + 11 + 0 = 18，第四行是唯一缺测。更正后 7 + 13 + 0 = 20，原号仍复核 18 L。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(event, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    run()
