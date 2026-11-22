from dataclasses import replace

from daily.body import encode_report
from daily.freeze import freeze
from delivery.admit import admit
from delivery.claim import claim
from delivery.ledger import read_posting
from delivery.receipts import read_receipt
from delivery.register import read_admission
from dispatch.catalog import dispatch, read_binding, read_dispatch_material
from dispatch.handoff import read_handoff_state
from dispatch.requests import read_withdraw_request
from dispatch.resume import resume_handoff
from dispatch.withdraw import withdraw
from intake.corrections import correct
from tests.fixtures import CORRECTED, DESTINATION, Interrupted, WarehouseCase, interrupt_at


class SupplyTests(WarehouseCase):
    def test_claim_without_source(self):
        revision, report = self.arrange()
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        self.close_source()
        result = claim(self.receiver, 'D', 'first')
        self.assertEqual(result.status, 'claimed')
        self.assertEqual(result.report, report)
        self.assertEqual(result.report.totals.liters, 18)
        self.assertEqual([line.liters for line in result.report.lines], [7, 11, None])
        self.assertEqual([line.parent for line in result.report.lines], [revision.ref] * 3)
        self.assertEqual([line.source for line in result.report.lines],
                         ['north-log/1', 'north-log/2', 'north-shift/3'])
        self.assertEqual(result.receipt.receipt_id, 'D/first')
        self.assertEqual(result.receipt.request_id, 'first')
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_material_binding(self):
        _, report = self.arrange()
        binding, original = read_dispatch_material(self.source, 'D')
        tampered = replace(report, lines=(*report.lines[:2],
                           replace(report.lines[2], source='other-source/3')))
        with self.assertRaisesRegex(ValueError, 'frozen binding'):
            admit(self.receiver, binding, encode_report(tampered))
        self.assertIsNone(read_admission(self.receiver, 'D'))
        self.assertEqual(self.formal_counts(), (0, 0))
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D', material=original), 'admitted')
        self.close_source()
        result = claim(self.receiver, 'D', 'original')
        self.assertEqual(result.report.lines[2].source, 'north-shift/3')
        self.assertTrue(result.receipt.matches(binding))
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_atomic_receipt(self):
        self.arrange()
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        self.close_source()
        with self.assertRaisesRegex(Interrupted, 'after_posting'):
            claim(self.receiver, 'D', 'retry', checkpoint=interrupt_at('after_posting'))
        self.close_receiver()
        self.open_receiver()
        self.assertEqual(read_admission(self.receiver, 'D').status, 'admitted')
        self.assertIsNone(read_posting(self.receiver, 'D'))
        self.assertIsNone(read_receipt(self.receiver, 'D'))
        self.assertIsNone(self.receiver.execute(
            'SELECT 1 FROM claim_requests WHERE request_id = ?', ('retry',)
        ).fetchone())
        first = claim(self.receiver, 'D', 'retry')
        self.assertEqual(first.report.totals.liters, 18)
        self.assertEqual(first.receipt.request_id, 'retry')
        self.assertEqual(claim(self.receiver, 'D', 'retry'), first)
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_withdraw_before_handoff(self):
        self.arrange()
        self.close_receiver()
        self.assertEqual(withdraw(self.source, None, 'D', 'cancel'), 'withdrawn')
        self.close_source()
        self.open_source()
        self.assertEqual(withdraw(self.source, None, 'D', 'cancel'), 'withdrawn')
        self.assertEqual(read_handoff_state(self.source, 'D').phase, 'registered')
        self.assertEqual(read_withdraw_request(self.source, 'cancel').result, 'withdrawn')

    def test_late_admission_after_withdraw(self):
        self.arrange()
        _, delayed_material = read_dispatch_material(self.source, 'D')
        self.assertEqual(withdraw(self.source, None, 'D', 'cancel'), 'withdrawn')
        self.reopen_supply()
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D', material=delayed_material),
                         'withdrawn')
        self.assertEqual(read_admission(self.receiver, 'D').status, 'withdrawn')
        self.assertEqual(claim(self.receiver, 'D', 'late').status, 'withdrawn')
        self.assertEqual(self.formal_counts(), (0, 0))

    def test_material_only(self):
        self.arrange()
        with self.assertRaisesRegex(Interrupted, 'after_material_commit'):
            resume_handoff(self.source, self.receiver, 'D',
                           checkpoint=interrupt_at('after_material_commit'))
        self.reopen_supply()
        entry = read_admission(self.receiver, 'D')
        self.assertEqual(entry.status, 'pending')
        self.assertEqual(entry.material, read_dispatch_material(self.source, 'D')[1])
        self.assertEqual(claim(self.receiver, 'D', 'late').status, 'pending')
        self.assertEqual(self.formal_counts(), (0, 0))
        self.close_receiver()
        self.assertEqual(withdraw(self.source, None, 'D', 'cancel'), 'withdrawn')
        self.open_receiver()
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'withdrawn')
        self.assertEqual(claim(self.receiver, 'D', 'late').status, 'withdrawn')
        self.assertEqual(self.formal_counts(), (0, 0))

    def test_resume_handoff(self):
        _, report = self.make_report()
        cases = [('before_admission_commit', 'pending'), ('after_admission_commit', 'admitted')]
        for halt, receiver_status in cases:
            with self.subTest(halt=halt):
                dispatch(self.source, halt, report.ref, DESTINATION)
                with self.assertRaisesRegex(Interrupted, halt):
                    resume_handoff(self.source, self.receiver, halt, checkpoint=interrupt_at(halt))
                self.reopen_supply()
                self.assertEqual(read_handoff_state(self.source, halt).phase, 'granted')
                self.assertEqual(read_admission(self.receiver, halt).status, receiver_status)
                request_id = f'claim-{halt}'
                if receiver_status == 'pending':
                    self.assertEqual(claim(self.receiver, halt, request_id).status, 'pending')
                    self.assertIsNone(read_posting(self.receiver, halt))
                self.assertEqual(resume_handoff(self.source, self.receiver, halt), 'admitted')
                self.assertEqual(resume_handoff(self.source, self.receiver, halt), 'admitted')
                result = claim(self.receiver, halt, request_id)
                self.assertEqual(result.report, report)
                self.assertEqual(result.receipt.request_id, request_id)
                self.assertEqual(self.receiver.execute(
                    'SELECT COUNT(*) FROM inbox WHERE dispatch_id = ?', (halt,)
                ).fetchone()[0], 1)
        self.assertEqual(self.formal_counts(), (2, 2))

    def test_claim_then_withdraw(self):
        _, report = self.arrange()
        with self.assertRaises(Interrupted):
            resume_handoff(self.source, self.receiver, 'D',
                           checkpoint=interrupt_at('after_admission_commit'))
        self.close_source()
        with self.assertRaises(Interrupted):
            claim(self.receiver, 'D', 'night', checkpoint=interrupt_at('after_claim_commit'))
        self.close_receiver()
        self.open_source()
        self.assertEqual(withdraw(self.source, None, 'D', 'cancel'), 'pending')
        self.assertIsNone(read_handoff_state(self.source, 'D').terminal)
        self.assertIsNone(read_withdraw_request(self.source, 'cancel').result)
        self.open_receiver()
        original = read_receipt(self.receiver, 'D')
        replay = claim(self.receiver, 'D', 'night')
        self.assertEqual((replay.report, replay.receipt), (report, original))
        self.assertEqual(replay.report.totals.liters, 18)
        self.assertEqual(original.request_id, 'night')
        self.assertEqual(withdraw(self.source, self.receiver, 'D', 'cancel'), 'claimed')
        self.close_receiver()
        self.assertEqual(withdraw(self.source, None, 'D', 'cancel'), 'claimed')
        self.open_receiver()
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_withdraw_wins_at_receiver(self):
        self.arrange()
        with self.assertRaises(Interrupted):
            resume_handoff(self.source, self.receiver, 'D',
                           checkpoint=interrupt_at('after_admission_commit'))
        self.reopen_supply()
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        with self.assertRaisesRegex(Interrupted, 'after_remote_withdraw_commit'):
            withdraw(self.source, self.receiver, 'D', 'cancel',
                     checkpoint=interrupt_at('after_remote_withdraw_commit'))
        self.assertIsNone(read_handoff_state(self.source, 'D').terminal)
        self.reopen_supply()
        self.assertEqual(withdraw(self.source, self.receiver, 'D', 'cancel'), 'withdrawn')
        self.assertEqual(claim(self.receiver, 'D', 'late').status, 'withdrawn')
        self.assertEqual(self.formal_counts(), (0, 0))

    def test_action_request_conflict(self):
        _, report = self.arrange()
        dispatch(self.source, 'E', report.ref, DESTINATION)
        self.assertEqual(claim(self.receiver, 'D', 'get').status, 'pending')
        with self.assertRaisesRegex(ValueError, 'claim request parameters conflict'):
            claim(self.receiver, 'E', 'get')
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        first = claim(self.receiver, 'D', 'get')
        self.assertEqual(first.receipt.request_id, 'get')
        self.assertEqual(claim(self.receiver, 'D', 'get'), first)
        self.assertEqual(claim(self.receiver, 'D', 'another-get'), first)
        self.assertEqual(withdraw(self.source, self.receiver, 'D', 'cancel'), 'claimed')
        with self.assertRaisesRegex(ValueError, 'withdraw request parameters conflict'):
            withdraw(self.source, self.receiver, 'E', 'cancel')
        self.assertIsNone(read_handoff_state(self.source, 'E').terminal)
        self.assertEqual(withdraw(self.source, self.receiver, 'D', 'cancel'), 'claimed')
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_dispatch_id_conflict(self):
        original, report = self.arrange()
        changed = correct(self.source, original.ref, CORRECTED)
        newer = freeze(self.source, 'day', [changed.ref])
        original_binding = read_binding(self.source, 'D')
        self.assertEqual(dispatch(self.source, 'D', report.ref, DESTINATION), original_binding)
        with self.assertRaisesRegex(ValueError, 'dispatch parameters conflict'):
            dispatch(self.source, 'D', newer.ref, DESTINATION)
        self.assertEqual(read_binding(self.source, 'D'), original_binding)
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        self.assertEqual(claim(self.receiver, 'D', 'get').report.totals.liters, 18)

    def test_destination_conflict(self):
        _, report = self.arrange()
        with self.assertRaisesRegex(ValueError, 'dispatch parameters conflict'):
            dispatch(self.source, 'D', report.ref, 'hill-site')
        self.assertEqual(read_binding(self.source, 'D').destination, DESTINATION)
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        self.close_source()
        self.assertEqual(claim(self.receiver, 'D', 'get').receipt.destination, DESTINATION)
