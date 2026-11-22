from dataclasses import replace

from audit.history import review
from audit.record import initialize_reviews, read_reviews
from daily.body import encode_report
from daily.freeze import freeze
from delivery.claim import claim
from delivery.ledger import read_posting
from delivery.receipts import read_receipt
from delivery.register import initialize_delivery
from dispatch.catalog import dispatch, initialize_source
from dispatch.resume import resume_handoff
from dispatch.withdraw import withdraw
from intake.batches import record
from intake.corrections import correct
from intake.revisions import read_revision
from tests.fixtures import CORRECTED, DESTINATION, Interrupted, WarehouseCase, interrupt_at


class AuditTests(WarehouseCase):
    def test_reply_loss_and_correction(self):
        revision, original = self.arrange()
        self.assertEqual(resume_handoff(self.source, self.receiver, 'D'), 'admitted')
        self.close_source()
        with self.assertRaisesRegex(Interrupted, 'after_claim_commit'):
            claim(self.receiver, 'D', 'night', checkpoint=interrupt_at('after_claim_commit'))
        self.reopen_supply()
        original_receipt = read_receipt(self.receiver, 'D')
        corrected = correct(self.source, revision.ref, CORRECTED)
        new = freeze(self.source, 'day', [corrected.ref])
        self.assertEqual(new.totals.liters, 20)
        replay = claim(self.receiver, 'D', 'night')
        self.assertEqual(replay.report, original)
        self.assertEqual(replay.receipt, original_receipt)
        self.assertEqual(replay.report.totals.liters, 18)
        self.assertEqual(replay.report.lines[1].source, 'north-log/2')
        self.assertEqual(withdraw(self.source, self.receiver, 'D', 'cancel'), 'claimed')
        record = review(self.source, self.receiver, self.reviews, 'D')
        self.assertEqual(record.status, 'claimed')
        self.assertEqual(record.receipt['receipt_id'], 'D/first')
        self.assertEqual(record.report['parents'], [['north-am', 1]])
        self.assertEqual(record.balances[0]['recomputed'], [18, 1])
        self.assertEqual(record.differences, [])
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_row_lineage(self):
        parent, _ = self.arrange()
        resume_handoff(self.source, self.receiver, 'D')
        first = claim(self.receiver, 'D', 'night')
        correction = correct(self.source, parent.ref, CORRECTED)
        freeze(self.source, 'day', [correction.ref])
        self.reopen_supply()
        checked = review(self.source, self.receiver, self.reviews, 'D')
        self.assertEqual(checked.receipt['receipt_id'], first.receipt.receipt_id)
        self.assertEqual(checked.posted_lines, 3)
        self.assertEqual(
            [(row['origin']['position'], row['origin']['batch_revision'], row['origin']['source'])
             for row in checked.lineage],
            [(1, 1, 'north-log/1'), (2, 1, 'north-log/2'), (3, 1, 'north-shift/3')],
        )
        for row in checked.lineage:
            self.assertTrue(row['matches'])
            self.assertEqual(row['posted'], row['origin'])
            self.assertEqual(row['frozen'], row['origin'])
        self.assertEqual(checked.differences, [])

    def test_station_reconcile(self):
        north = record(self.source, 'north-am', 'north', [{'liters': 7, 'source': 'N/1'}])
        south = record(self.source, 'south-am', 'south', [{'liters': 11, 'source': 'S/1'}])
        report = freeze(self.source, 'two-station-day', [north.ref, south.ref])
        dispatch(self.source, 'D', report.ref, DESTINATION)
        resume_handoff(self.source, self.receiver, 'D')
        first = claim(self.receiver, 'D', 'night')
        healthy = review(self.source, self.receiver, self.reviews, 'D')
        self.assertEqual([(item['station_id'], item['recomputed']) for item in healthy.balances],
                         [(None, [18, 0]), ('north', [7, 0]), ('south', [11, 0])])
        self.assertEqual(healthy.differences, [])

        posting = read_posting(self.receiver, 'D')
        north_total, south_total = posting.body.totals.stations
        wrong_totals = replace(posting.body.totals, stations=(
            replace(north_total, liters=south_total.liters),
            replace(south_total, liters=north_total.liters),
        ))
        bad_body = replace(posting.body, totals=wrong_totals)
        with self.receiver:
            self.receiver.execute('UPDATE postings SET body = ? WHERE dispatch_id = ?',
                                  (encode_report(bad_body), 'D'))
        checked = review(self.source, self.receiver, self.reviews, 'D')
        self.assertEqual(checked.status, 'claimed')
        self.assertEqual(checked.receipt['receipt_id'], first.receipt.receipt_id)
        self.assertEqual(checked.balances[0]['booked'], [18, 0])
        self.assertTrue(checked.balances[0]['matches'])
        self.assertEqual(checked.balances[1]['booked'], [11, 0])
        self.assertEqual(checked.balances[2]['booked'], [7, 0])
        self.assertFalse(checked.balances[1]['matches'])
        self.assertFalse(checked.balances[2]['matches'])
        self.assertIn('balance:station:north', checked.differences)
        self.assertIn('balance:station:south', checked.differences)
        self.assertEqual(read_reviews(self.reviews, 'D'), (healthy, checked))

    def test_reinitialize_keeps_records(self):
        _, original = self.arrange()
        resume_handoff(self.source, self.receiver, 'D')
        first = claim(self.receiver, 'D', 'night')
        old_review = review(self.source, self.receiver, self.reviews, 'D')
        initialize_source(self.source)
        initialize_delivery(self.receiver, DESTINATION)
        initialize_reviews(self.reviews)
        self.reopen_supply()
        self.close_reviews()
        self.open_reviews()
        self.assertEqual(claim(self.receiver, 'D', 'night'), first)
        self.assertEqual(read_posting(self.receiver, 'D').body, original)
        self.assertEqual(read_reviews(self.reviews, 'D'), (old_review,))
        new_review = review(self.source, self.receiver, self.reviews, 'D')
        self.assertEqual(new_review.receipt, old_review.receipt)
        self.assertEqual(new_review.report, old_review.report)
        self.assertEqual(new_review.lineage, old_review.lineage)
        self.assertGreater(new_review.review_id, old_review.review_id)

    def test_review_statuses(self):
        _, report = self.arrange()
        dispatch(self.source, 'cancelled', report.ref, DESTINATION)
        dispatch(self.source, 'waiting', report.ref, DESTINATION)
        resume_handoff(self.source, self.receiver, 'D')
        claim(self.receiver, 'D', 'night')
        self.assertEqual(withdraw(self.source, None, 'cancelled', 'cancel'), 'withdrawn')
        with self.assertRaises(Interrupted):
            resume_handoff(self.source, self.receiver, 'waiting',
                           checkpoint=interrupt_at('after_material_commit'))
        self.reopen_supply()
        initialize_reviews(self.reviews)
        outcomes = [review(self.source, self.receiver, self.reviews, dispatch_id)
                    for dispatch_id in ('D', 'cancelled', 'waiting')]
        self.assertEqual([result.status for result in outcomes], ['claimed', 'withdrawn', 'pending'])
        self.assertEqual([result.posted_lines for result in outcomes], [3, 0, 0])
        self.assertEqual([result.receipt for result in outcomes[1:]], [None, None])
        self.assertEqual(self.formal_counts(), (1, 1))

    def test_init_preserves_changes(self):
        revision, report = self.arrange()
        resume_handoff(self.source, self.receiver, 'D')
        first = claim(self.receiver, 'D', 'first')
        corrected = correct(self.source, revision.ref, CORRECTED)
        newer = freeze(self.source, 'day', [corrected.ref])
        before = review(self.source, self.receiver, self.reviews, 'D')
        for _ in range(2):
            initialize_source(self.source)
            initialize_delivery(self.receiver, DESTINATION)
            initialize_reviews(self.reviews)
        self.reopen_supply()
        self.close_reviews()
        self.open_reviews()
        self.assertEqual(read_revision(self.source, corrected.ref), corrected)
        self.assertEqual(claim(self.receiver, 'D', 'first'), first)
        self.assertEqual(first.report, report)
        self.assertEqual(first.report.totals.liters, 18)
        self.assertEqual(newer.totals.liters, 20)
        self.assertEqual(read_reviews(self.reviews, 'D'), (before,))
        after = review(self.source, self.receiver, self.reviews, 'D')
        self.assertEqual(after.report, before.report)
        self.assertEqual(after.receipt, before.receipt)
        self.assertEqual(after.differences, [])
