import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.record import initialize_reviews
from daily.freeze import freeze
from delivery.register import initialize_delivery
from dispatch.catalog import dispatch, initialize_source
from intake.batches import record


DESTINATION = 'estuary'
ORIGINAL = (
    {'liters': 7, 'source': 'north-log/1'},
    {'liters': 11, 'source': 'north-log/2'},
    {'missing': 'sensor stopped', 'source': 'north-shift/3'},
)
CORRECTED = (
    {'liters': 7, 'source': 'north-log/1'},
    {'liters': 13, 'source': 'north-correction/2'},
    {'missing': 'sensor stopped', 'source': 'north-shift/3'},
)


class Interrupted(RuntimeError):
    pass


def interrupt_at(stage):
    def checkpoint(reached):
        if reached == stage:
            raise Interrupted(reached)
    return checkpoint


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='daily-report-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.receiver = self.reviews = None
        self.addCleanup(self.close_all)
        self.open_source()
        self.open_receiver()
        self.open_reviews()

    def open_source(self):
        self.source = sqlite3.connect(self.root / 'source.sqlite')
        initialize_source(self.source)

    def open_receiver(self):
        self.receiver = sqlite3.connect(self.root / 'receiver.sqlite')
        initialize_delivery(self.receiver, DESTINATION)

    def open_reviews(self):
        self.reviews = sqlite3.connect(self.root / 'audit.sqlite')
        initialize_reviews(self.reviews)

    def close_source(self):
        if self.source is not None:
            self.source.close()
            self.source = None

    def close_receiver(self):
        if self.receiver is not None:
            self.receiver.close()
            self.receiver = None

    def close_reviews(self):
        if self.reviews is not None:
            self.reviews.close()
            self.reviews = None

    def close_all(self):
        self.close_source()
        self.close_receiver()
        self.close_reviews()

    def reopen_supply(self):
        self.close_source()
        self.close_receiver()
        self.open_source()
        self.open_receiver()

    def make_report(self):
        revision = record(self.source, 'north-am', 'north', ORIGINAL)
        return revision, freeze(self.source, 'day', [revision.ref])

    def arrange(self, dispatch_id='D'):
        revision, report = self.make_report()
        dispatch(self.source, dispatch_id, report.ref, DESTINATION)
        return revision, report

    def formal_counts(self):
        postings = self.receiver.execute('SELECT COUNT(*) FROM postings').fetchone()[0]
        receipts = self.receiver.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        return postings, receipts
