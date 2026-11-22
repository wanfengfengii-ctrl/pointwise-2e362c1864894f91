import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.custody import Custody
from audit.history import review
from audit.record import initialize_reviews
from daily.freeze import freeze
from delivery.claim import (
    claim,
    claim_protected,
    configure_protection,
    restore_protected,
)
from delivery.ledger import read_posting
from delivery.receipts import read_receipt
from delivery.register import initialize_delivery, read_admission
from dispatch.catalog import dispatch, initialize_source
from dispatch.resume import resume_handoff
from dispatch.withdraw import withdraw
from intake.batches import record
from intake.corrections import correct
from rollback_lab import PROTECTED, replace_receiver, save_image, seed_dispatches
from tests.fixtures import DESTINATION


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


def run_protection() -> None:
    """断源换盘后凭独立保管端恢复 R1 首执，并裁决 R2 改绑。"""
    with TemporaryDirectory(prefix='watershed-protected-') as directory, ExitStack() as resources:
        root = Path(directory)
        live_path = root / 'receiver.sqlite'
        early_path = root / 'early-disk.sqlite'
        with closing(sqlite3.connect(root / 'source.sqlite')) as source:
            initialize_source(source)
            receiver = resources.enter_context(
                closing(sqlite3.connect(live_path))
            )
            initialize_delivery(receiver, DESTINATION)
            original, _ = seed_dispatches(source)

            # 早盘：E 与对照日报 V1 已接办，D 尚未接办；保护名单先固定再开放
            for dispatch_id in ('E', 'V1'):
                if resume_handoff(source, receiver, dispatch_id) != 'admitted':
                    raise RuntimeError(dispatch_id)
            configure_protection(receiver, PROTECTED)
            save_image(receiver, early_path)
            # 晚盘：名单内的 D 也已按原流程接办并过了撤发期
            if resume_handoff(source, receiver, 'D') != 'admitted':
                raise RuntimeError('D admission failed')

        # 源仓在首次保护领取前即可关闭；保管库独立于外站盘
        with Custody(root / 'custody.sqlite', DESTINATION) as custody:
            restore_protected(receiver, custody)
            first = claim_protected(receiver, custody, 'D', 'R1')
            repeated = claim_protected(receiver, custody, 'D', 'R2')
            if first.receipt.request_id != 'R1' or repeated.receipt.request_id != 'R1':
                raise RuntimeError('first executor was not preserved on repeated claims')

            # 复领号改领另一日报必须拒绝
            try:
                claim_protected(receiver, custody, 'E', 'R2')
            except ValueError as exc:
                rebind_decision = f'rejected: {exc}'
            else:
                raise RuntimeError('R2 rebinding to E was not blocked')

            # 换回早于接办的旧盘：盘上没有 D 的任何痕迹
            receiver = resources.enter_context(
                closing(replace_receiver(receiver, live_path, early_path))
            )
            if read_admission(receiver, 'D') is not None:
                raise RuntimeError('early disk already contains D')
            # 先恢复，再开放保护领取
            configure_protection(receiver, PROTECTED)
            restore_protected(receiver, custody)
            recovered = claim_protected(receiver, custody, 'D', 'R2')
            try:
                claim_protected(receiver, custody, 'E', 'R2')
            except ValueError as exc:
                rollback_rebind = f'rejected: {exc}'
            else:
                raise RuntimeError('R2 rebinding survived the disk rollback')

            # 名单外的 V1 仍走普通入口
            ordinary = claim(receiver, 'V1', 'ordinary-request')

        posting = read_posting(receiver, 'D')
        receipt = read_receipt(receiver, 'D')
        bound_requests = [
            row[0] for row in receiver.execute(
                'SELECT request_id FROM protected_requests WHERE dispatch_id = ? ORDER BY ordinal',
                ('D',),
            )
        ]
        expected = {
            'liters': 18,
            'report_ref': ['day', 1],
            'receipt_id': 'D/first',
            'first_executor': 'R1',
            'bound_requests': ['R1', 'R2'],
            'values': [7, 11, None],
            'sources': ['north-log/1', 'north-log/2', 'north-shift/3'],
            'ordinary_claim': 'claimed',
        }
        actual = {
            'liters': posting.body.totals.liters,
            'report_ref': list(posting.body.ref),
            'receipt_id': receipt.receipt_id,
            'first_executor': receipt.request_id,
            'bound_requests': bound_requests,
            'values': [line.liters for line in posting.body.lines],
            'sources': [line.source for line in posting.body.lines],
            'ordinary_claim': ordinary.status,
        }
        if actual != expected or recovered.report != original:
            raise RuntimeError(f'protected rollback outcome differs: {actual!r}')
        event = {
            'case_id': 'p1.custody_rollback',
            'expected': expected,
            'actual': actual,
            'rebind_before_rollback': rebind_decision,
            'rebind_after_rollback': rollback_rebind,
            'code_paths': ['delivery/claim.py', 'audit/custody.py', 'rollback_lab.py'],
            'oracle': '断源换盘后，D 仍为 18 L，回执 D/first 的首执固定为 R1；'
                      'R2 可复领 D，改领 E 一律拒绝；名单外 V1 按原流程领取。',
        }
        print('PROTECTED_ROLLBACK ' + json.dumps(event, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    run()
    run_protection()
