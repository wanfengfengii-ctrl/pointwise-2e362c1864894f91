import sqlite3

from audit.custody import Custody
from daily.body import encode_report
from delivery.claim import (
    claim,
    claim_protected,
    configure_protection,
    restore_protected,
)
from delivery.ledger import read_posting
from delivery.receipts import read_receipt
from delivery.register import initialize_delivery, read_admission
from dispatch.catalog import read_dispatch_material
from dispatch.resume import resume_handoff
from rollback_lab import CutCustodyCall, replace_receiver, save_image, seed_dispatches
from tests.fixtures import DESTINATION, Interrupted, WarehouseCase, interrupt_at

ROSTER = ('D', 'E')


class ProtectionCase(WarehouseCase):
    def open_custody(self):
        self.custody_conn = sqlite3.connect(self.root / 'custody.sqlite')
        self.addCleanup(self.custody_conn.close)
        self.custody = Custody(self.root / 'custody.sqlite', DESTINATION)
        self.addCleanup(self.custody.close)

    def seed_roster(self, dispatch_ids=ROSTER, admit=ROSTER):
        seed_dispatches(self.source)
        for dispatch_id in admit:
            self.assertEqual(resume_handoff(self.source, self.receiver, dispatch_id), 'admitted')
        configure_protection(self.receiver, dispatch_ids)
        self.open_custody()

    def reopen_receiver(self):
        self.close_receiver()
        self.open_receiver()


class RosterTests(ProtectionCase):
    def test_roster_validation_and_fixed_list(self):
        seed_dispatches(self.source)
        with self.assertRaisesRegex(ValueError, 'nonempty sequence'):
            configure_protection(self.receiver, ())
        with self.assertRaisesRegex(ValueError, 'nonempty sequence'):
            configure_protection(self.receiver, ('D', ''))
        with self.assertRaisesRegex(ValueError, 'nonempty sequence'):
            configure_protection(self.receiver, ('D', 'D'))
        configure_protection(self.receiver, ROSTER)
        configure_protection(self.receiver, ('E', 'D'))
        with self.assertRaisesRegex(ValueError, 'protection roster.*fixed'):
            configure_protection(self.receiver, ('D',))
        with self.assertRaisesRegex(ValueError, 'protection roster.*fixed'):
            configure_protection(self.receiver, ('D', 'E', 'V1'))

    def test_normal_claim_rejected_without_occupying_request(self):
        self.seed_roster()
        with self.assertRaisesRegex(ValueError, 'claim_protected'):
            claim(self.receiver, 'D', 'R1')
        self.assertIsNone(self.receiver.execute(
            'SELECT 1 FROM claim_requests WHERE request_id = ?', ('R1',)
        ).fetchone())
        # the rejected request id is still free for a roster-free dispatch
        self.assertEqual(resume_handoff(self.source, self.receiver, 'V1'), 'admitted')
        ordinary = claim(self.receiver, 'V1', 'R1')
        self.assertEqual(ordinary.status, 'claimed')
        self.assertEqual(read_receipt(self.receiver, 'V1').request_id, 'R1')

    def test_protected_entry_closed_until_restore(self):
        self.seed_roster()
        with self.assertRaisesRegex(ValueError, 'not open'):
            claim_protected(self.receiver, self.custody, 'D', 'R1')
        self.assertIsNone(read_posting(self.receiver, 'D'))
        self.assertIsNone(read_receipt(self.receiver, 'D'))

    def test_protected_claim_rejects_dispatch_outside_roster(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        with self.assertRaisesRegex(ValueError, 'not on the protection roster'):
            claim_protected(self.receiver, self.custody, 'V1', 'R9')
        with self.assertRaisesRegex(ValueError, 'not on the protection roster'):
            claim_protected(self.receiver, self.custody, 'unknown', 'R9')


class ProtectedClaimTests(ProtectionCase):
    def test_first_claim_confirms_before_posting_and_persists_every_binding(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        first = claim_protected(self.receiver, self.custody, 'D', 'R1')
        self.assertEqual(first.status, 'claimed')
        self.assertEqual(first.report.totals.liters, 18)
        self.assertEqual([line.liters for line in first.report.lines], [7, 11, None])
        self.assertEqual([line.source for line in first.report.lines],
                         ['north-log/1', 'north-log/2', 'north-shift/3'])
        self.assertEqual(first.receipt.receipt_id, 'D/first')
        self.assertEqual(first.receipt.request_id, 'R1')
        fact = self.custody('recovery', {'destination': DESTINATION})
        d_fact = next(item for item in fact if item['dispatch_id'] == 'D')
        self.assertEqual(d_fact['first_request_id'], 'R1')
        self.assertEqual(d_fact['request_ids'], ('R1',))
        self.assertEqual(read_posting(self.receiver, 'D').body, first.report)
        # repeat with the same request and then a new request: the first executor stays R1
        replay = claim_protected(self.receiver, self.custody, 'D', 'R1')
        self.assertEqual(replay, first)
        repeated = claim_protected(self.receiver, self.custody, 'D', 'R2')
        self.assertEqual(repeated.report, first.report)
        self.assertEqual(repeated.receipt, first.receipt)
        self.assertEqual(read_receipt(self.receiver, 'D').request_id, 'R1')
        d_fact = next(item for item in self.custody('recovery', {'destination': DESTINATION})
                      if item['dispatch_id'] == 'D')
        self.assertEqual(d_fact['first_request_id'], 'R1')
        self.assertEqual(d_fact['request_ids'], ('R1', 'R2'))
        self.assertEqual(
            [row[0] for row in self.receiver.execute(
                'SELECT request_id FROM protected_requests WHERE dispatch_id = ? ORDER BY ordinal',
                ('D',))],
            ['R1', 'R2'],
        )
        self.assertEqual(
            [row[0] for row in self.receiver.execute(
                'SELECT dispatch_id FROM claim_requests ORDER BY request_id')],
            ['D', 'D'],
        )

    def test_rebound_request_to_another_report_is_refused(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        first = claim_protected(self.receiver, self.custody, 'D', 'R1')
        claim_protected(self.receiver, self.custody, 'D', 'R2')
        with self.assertRaisesRegex(ValueError, 'claim request parameters conflict'):
            claim_protected(self.receiver, self.custody, 'E', 'R2')
        # E remains untouched and R2 stays bound to D
        self.assertIsNone(read_posting(self.receiver, 'E'))
        self.assertEqual(read_receipt(self.receiver, 'D').request_id, 'R1')
        self.assertEqual(claim_protected(self.receiver, self.custody, 'D', 'R2'), first)
        self.assertEqual(read_admission(self.receiver, 'E').status, 'admitted')

    def test_custody_rejects_wrong_site_and_conflicting_facts(self):
        self.seed_roster()
        other = Custody(self.root / 'other-custody.sqlite', 'hill-site')
        self.addCleanup(other.close)
        restore_protected(self.receiver, self.custody)
        with self.assertRaisesRegex(ValueError, 'different receiver site'):
            claim_protected(self.receiver, other, 'D', 'R1')
        self.assertIsNone(read_posting(self.receiver, 'D'))

        class WrongMaterial:
            def __init__(self, custody):
                self.custody = custody

            def __call__(self, operation, payload):
                if operation != 'confirm':
                    return self.custody(operation, payload)
                tampered = dict(payload)
                tampered['material'] = payload['material'].replace('north-log/1', 'fake/1')
                return self.custody(operation, tampered)

        with self.assertRaisesRegex(ValueError, 'fingerprint does not match'):
            claim_protected(self.receiver, WrongMaterial(self.custody), 'D', 'R1')
        self.assertIsNone(read_posting(self.receiver, 'D'))
        self.assertIsNone(read_receipt(self.receiver, 'D'))

    def test_cut_before_confirm_leaves_nothing_and_restores_cleanly(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        cut = CutCustodyCall(self.custody, 'before_confirm')
        with self.assertRaisesRegex(Interrupted, 'before_confirm'):
            claim_protected(self.receiver, cut, 'D', 'R1')
        self.assertIsNone(read_posting(self.receiver, 'D'))
        self.assertIsNone(read_receipt(self.receiver, 'D'))
        self.assertIsNone(self.receiver.execute(
            'SELECT 1 FROM claim_requests WHERE request_id = ?', ('R1',)
        ).fetchone())
        self.assertEqual(self.custody('recovery', {'destination': DESTINATION}), [])
        # retry the same request once the call goes through
        first = claim_protected(self.receiver, self.custody, 'D', 'R1')
        self.assertEqual(first.receipt.request_id, 'R1')

    def test_cut_after_confirm_recovers_the_real_first_executor(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        cut = CutCustodyCall(self.custody, 'after_confirm')
        with self.assertRaisesRegex(Interrupted, 'after_confirm'):
            claim_protected(self.receiver, cut, 'D', 'R1')
        # the failed receiver transaction is discarded on a fresh connection
        self.reopen_receiver()
        # the custody side kept the confirmation; the receiver has no formal entry
        self.assertIsNone(read_posting(self.receiver, 'D'))
        self.assertIsNone(read_receipt(self.receiver, 'D'))
        facts = self.custody('recovery', {'destination': DESTINATION})
        self.assertEqual([fact['dispatch_id'] for fact in facts], ['D'])
        self.assertEqual(facts[0]['first_request_id'], 'R1')
        # the replacement disk keeps the fixed roster
        configure_protection(self.receiver, ROSTER)
        # starting a brand-new request cannot rewrite the settled first executor
        with self.assertRaisesRegex(ValueError, 'conflicts with the settled fact'):
            claim_protected(self.receiver, self.custody, 'D', 'R2')
        self.assertIsNone(read_posting(self.receiver, 'D'))
        # the operator re-runs recovery, then the original request replays through custody
        restore_protected(self.receiver, self.custody)
        recovered = claim_protected(self.receiver, self.custody, 'D', 'R1')
        self.assertEqual(recovered.report.totals.liters, 18)
        self.assertEqual(recovered.receipt.receipt_id, 'D/first')
        self.assertEqual(recovered.receipt.request_id, 'R1')
        # a later repeated request is bound without changing the first executor
        repeated = claim_protected(self.receiver, self.custody, 'D', 'R2')
        self.assertEqual(repeated.receipt, recovered.receipt)
        self.assertEqual(read_receipt(self.receiver, 'D').request_id, 'R1')
        self.assertEqual(
            [row[0] for row in self.receiver.execute(
                'SELECT request_id FROM protected_requests WHERE dispatch_id = ? ORDER BY ordinal',
                ('D',))],
            ['R1', 'R2'],
        )

    def test_restore_checkpoint_failure_keeps_partial_install_hidden(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        claim_protected(self.receiver, self.custody, 'D', 'R1')
        claim_protected(self.receiver, self.custody, 'E', 'E-first')
        # a replacement disk carries the fixed roster but none of the protected facts
        replacement_path = self.root / 'replacement.sqlite'
        self.close_receiver()
        replacement = sqlite3.connect(replacement_path)
        self.addCleanup(replacement.close)
        initialize_delivery(replacement, DESTINATION)
        configure_protection(replacement, ROSTER)
        with self.assertRaisesRegex(Interrupted, 'before_restore_commit'):
            restore_protected(
                replacement, self.custody,
                checkpoint=interrupt_at('before_restore_commit'),
            )
        self.assertIsNone(read_posting(replacement, 'D'))
        self.assertIsNone(read_posting(replacement, 'E'))
        self.assertEqual(replacement.execute(
            'SELECT COUNT(*) FROM protected_requests'
        ).fetchone()[0], 0)
        ready = replacement.execute(
            'SELECT ready FROM protection_state WHERE singleton = 1'
        ).fetchone()[0]
        self.assertEqual(ready, 0)
        with self.assertRaisesRegex(ValueError, 'not open'):
            claim_protected(replacement, self.custody, 'D', 'R2')
        # retry restores both facts together
        restore_protected(replacement, self.custody)
        d = claim_protected(replacement, self.custody, 'D', 'R2')
        e = claim_protected(replacement, self.custody, 'E', 'E-second')
        self.assertEqual(d.receipt.request_id, 'R1')
        self.assertEqual(e.receipt.request_id, 'E-first')
        self.assertEqual(read_posting(replacement, 'D').body.totals.liters, 18)

    def test_restore_is_repeatable_and_ignores_nothing_on_first_enable(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        restore_protected(self.receiver, self.custody)
        self.assertEqual(self.receiver.execute(
            'SELECT ready FROM protection_state WHERE singleton = 1'
        ).fetchone()[0], 1)
        self.assertEqual(self.receiver.execute('SELECT COUNT(*) FROM postings').fetchone()[0], 0)

    def test_restore_rejects_fact_outside_roster(self):
        # custody holds D and E, but the replacement disk only protects D
        seed_dispatches(self.source)
        for dispatch_id in ('D', 'E'):
            self.assertEqual(resume_handoff(self.source, self.receiver, dispatch_id), 'admitted')
        configure_protection(self.receiver, ROSTER)
        self.open_custody()
        restore_protected(self.receiver, self.custody)
        claim_protected(self.receiver, self.custody, 'D', 'R1')
        claim_protected(self.receiver, self.custody, 'E', 'E-first')
        # build a replacement receiver disk whose fixed roster contains only D
        narrow_path = self.root / 'narrow-receiver.sqlite'
        self.close_receiver()
        narrow = sqlite3.connect(narrow_path)
        self.addCleanup(narrow.close)
        initialize_delivery(narrow, DESTINATION)
        configure_protection(narrow, ('D',))
        with self.assertRaisesRegex(ValueError, 'outside the protection roster'):
            restore_protected(narrow, self.custody)
        self.assertEqual(narrow.execute(
            'SELECT ready FROM protection_state WHERE singleton = 1'
        ).fetchone()[0], 0)
        self.assertEqual(narrow.execute('SELECT COUNT(*) FROM postings').fetchone()[0], 0)

    def test_unconfirmed_candidate_has_no_formal_records(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        # E is admitted and restored-empty: no candidate may become visible
        self.assertIsNone(read_posting(self.receiver, 'E'))
        self.assertIsNone(read_receipt(self.receiver, 'E'))
        with self.assertRaisesRegex(ValueError, 'not on the protection roster'):
            claim_protected(self.receiver, self.custody, 'V1', 'R9')
        self.assertEqual(claim(self.receiver, 'V1', 'R9').status, 'pending')

    def test_restore_failure_keeps_protection_closed(self):
        self.seed_roster()

        class FailingCustody:
            def __call__(self, operation, payload):
                raise RuntimeError('custody offline')

        with self.assertRaisesRegex(RuntimeError, 'custody offline'):
            restore_protected(self.receiver, FailingCustody())
        self.assertEqual(self.receiver.execute(
            'SELECT ready FROM protection_state WHERE singleton = 1'
        ).fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, 'not open'):
            claim_protected(self.receiver, self.custody, 'D', 'R1')
        # retry once custody is reachable
        restore_protected(self.receiver, self.custody)
        first = claim_protected(self.receiver, self.custody, 'D', 'R1')
        self.assertEqual(first.receipt.request_id, 'R1')

    def test_request_namespace_is_shared_with_ordinary_claims(self):
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        self.assertEqual(resume_handoff(self.source, self.receiver, 'V1'), 'admitted')
        claim(self.receiver, 'V1', 'shared')
        with self.assertRaisesRegex(ValueError, 'claim request parameters conflict'):
            claim_protected(self.receiver, self.custody, 'D', 'shared')
        self.assertIsNone(read_posting(self.receiver, 'D'))

    def test_ordinary_flow_unchanged_outside_roster(self):
        self.seed_roster(dispatch_ids=('D',), admit=('D', 'V1'))
        restore_protected(self.receiver, self.custody)
        result = claim(self.receiver, 'V1', 'ordinary')
        self.assertEqual(result.status, 'claimed')
        self.assertEqual(result.receipt.receipt_id, 'V1/first')
        self.assertEqual(read_receipt(self.receiver, 'V1').request_id, 'ordinary')

    def test_audit_reconciles_a_restored_protected_claim(self):
        from audit.history import review
        self.seed_roster()
        restore_protected(self.receiver, self.custody)
        claim_protected(self.receiver, self.custody, 'D', 'R1')
        claim_protected(self.receiver, self.custody, 'D', 'R2')
        # replace the disk and recover solely from the independent custody store
        replacement_path = self.root / 'replacement.sqlite'
        self.close_receiver()
        replacement = sqlite3.connect(replacement_path)
        self.addCleanup(replacement.close)
        initialize_delivery(replacement, DESTINATION)
        configure_protection(replacement, ROSTER)
        restore_protected(replacement, self.custody)
        reviewed = review(self.source, replacement, self.reviews, 'D')
        self.assertEqual(reviewed.status, 'claimed')
        self.assertEqual(reviewed.receipt['receipt_id'], 'D/first')
        self.assertEqual(reviewed.receipt['request_id'], 'R1')
        self.assertEqual(reviewed.balances[0]['recomputed'], [18, 1])
        self.assertEqual(reviewed.differences, [])
        self.assertEqual(
            [row['origin']['source'] for row in reviewed.lineage],
            ['north-log/1', 'north-log/2', 'north-shift/3'],
        )


class RollbackLabTests(ProtectionCase):
    def test_disk_rollback_restores_r1_first_executor_and_blocks_rebind(self):
        live_path = self.root / 'receiver.sqlite'
        early = self.root / 'early.sqlite'
        late = self.root / 'late.sqlite'
        seed_dispatches(self.source)
        for dispatch_id in ('E', 'V1'):
            self.assertEqual(resume_handoff(self.source, self.receiver, dispatch_id), 'admitted')
        configure_protection(self.receiver, ROSTER)
        save_image(self.receiver, early)
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        save_image(self.receiver, late)
        # source warehouse may go offline before the first protected claim
        self.close_source()

        custody = Custody(self.root / 'custody.sqlite', DESTINATION)
        self.addCleanup(custody.close)
        restore_protected(self.receiver, custody)
        first = claim_protected(self.receiver, custody, 'D', 'R1')
        claim_protected(self.receiver, custody, 'D', 'R2')
        self.assertEqual(first.report.totals.liters, 18)

        self.receiver = replace_receiver(self.receiver, live_path, early)
        self.assertIsNone(read_admission(self.receiver, 'D'))
        configure_protection(self.receiver, ROSTER)
        restore_protected(self.receiver, custody)
        recovered = claim_protected(self.receiver, custody, 'D', 'R2')
        self.assertEqual(recovered.report, first.report)
        self.assertEqual(recovered.receipt.receipt_id, 'D/first')
        self.assertEqual(recovered.receipt.request_id, 'R1')
        self.assertEqual([line.source for line in recovered.report.lines],
                         ['north-log/1', 'north-log/2', 'north-shift/3'])
        with self.assertRaisesRegex(ValueError, 'claim request parameters conflict'):
            claim_protected(self.receiver, custody, 'E', 'R2')
        # an even later rollback replays to the same settled fact
        self.receiver = replace_receiver(self.receiver, live_path, late)
        configure_protection(self.receiver, ROSTER)
        restore_protected(self.receiver, custody)
        again = claim_protected(self.receiver, custody, 'D', 'R2')
        self.assertEqual(again.receipt.request_id, 'R1')
        self.assertEqual(again.report.totals.liters, 18)

    def test_second_issue_keeps_exact_identity_under_protection(self):
        seed_dispatches(self.source)
        _, material_d = read_dispatch_material(self.source, 'D')
        _, material_e = read_dispatch_material(self.source, 'E')
        self.assertNotEqual(material_d, material_e)
        for dispatch_id in ('D', 'E'):
            self.assertEqual(resume_handoff(self.source, self.receiver, dispatch_id), 'admitted')
        configure_protection(self.receiver, ROSTER)
        custody = Custody(self.root / 'custody.sqlite', DESTINATION)
        self.addCleanup(custody.close)
        restore_protected(self.receiver, custody)
        d = claim_protected(self.receiver, custody, 'D', 'R1')
        e = claim_protected(self.receiver, custody, 'E', 'R9')
        self.assertEqual(d.report.ref, ('day', 1))
        self.assertEqual(e.report.ref, ('day', 2))
        self.assertEqual(encode_report(d.report), material_d)
        self.assertEqual(encode_report(e.report), material_e)
        self.assertNotEqual(d.receipt.fingerprint, e.receipt.fingerprint)

    def test_cut_before_confirm_across_replacement_disk_recovers_without_that_claim(self):
        live_path = self.root / 'receiver.sqlite'
        image_path = self.root / 'clean-disk.sqlite'
        seed_dispatches(self.source)
        for dispatch_id in ('D', 'E'):
            self.assertEqual(resume_handoff(self.source, self.receiver, dispatch_id), 'admitted')
        configure_protection(self.receiver, ROSTER)
        custody = Custody(self.root / 'custody.sqlite', DESTINATION)
        self.addCleanup(custody.close)
        restore_protected(self.receiver, custody)
        save_image(self.receiver, image_path)
        cut = CutCustodyCall(custody, 'before_confirm')
        with self.assertRaisesRegex(Interrupted, 'before_confirm'):
            claim_protected(self.receiver, cut, 'D', 'R1')
        # swap to a clean replacement disk: custody has no confirmed fact for this lost call
        self.receiver = replace_receiver(self.receiver, live_path, image_path)
        configure_protection(self.receiver, ROSTER)
        self.assertEqual(custody('recovery', {'destination': DESTINATION}), [])
        restore_protected(self.receiver, custody)
        # nothing was booked; the same request can now start the claim cleanly
        self.assertIsNone(read_posting(self.receiver, 'D'))
        first = claim_protected(self.receiver, custody, 'D', 'R1')
        self.assertEqual(first.receipt.request_id, 'R1')
        self.assertEqual(first.report.totals.liters, 18)

    def test_restored_roster_keeps_ordinary_reports_claimable(self):
        live_path = self.root / 'receiver.sqlite'
        image_path = self.root / 'disk.sqlite'
        seed_dispatches(self.source)
        for dispatch_id in ('D', 'E', 'V1'):
            self.assertEqual(resume_handoff(self.source, self.receiver, dispatch_id), 'admitted')
        configure_protection(self.receiver, ROSTER)
        custody = Custody(self.root / 'custody.sqlite', DESTINATION)
        self.addCleanup(custody.close)
        restore_protected(self.receiver, custody)
        claim_protected(self.receiver, custody, 'D', 'R1')
        save_image(self.receiver, image_path)
        self.receiver = replace_receiver(self.receiver, live_path, image_path)
        configure_protection(self.receiver, ROSTER)
        restore_protected(self.receiver, custody)
        # ordinary entry cannot bypass protection, but roster-free V1 follows the old flow
        with self.assertRaisesRegex(ValueError, 'claim_protected'):
            claim(self.receiver, 'D', 'bypass')
        ordinary = claim(self.receiver, 'V1', 'ordinary')
        self.assertEqual(ordinary.status, 'claimed')
        self.assertEqual(read_receipt(self.receiver, 'V1').request_id, 'ordinary')
        self.assertEqual(read_receipt(self.receiver, 'D').request_id, 'R1')
