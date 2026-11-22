from daily.freeze import freeze, read_frozen
from intake.batches import record
from intake.corrections import correct
from intake.revisions import read_revision
from tests.fixtures import CORRECTED, ORIGINAL, WarehouseCase


class DailyTests(WarehouseCase):
    def test_frozen_totals(self):
        revision, report = self.make_report()
        self.assertEqual(report.parents, (revision.ref,))
        self.assertEqual([line.liters for line in report.lines], [7, 11, None])
        self.assertEqual([line.source for line in report.lines],
                         ['north-log/1', 'north-log/2', 'north-shift/3'])
        self.assertEqual((report.totals.liters, report.totals.missing), (18, 1))
        self.assertEqual([(s.station_id, s.liters, s.missing) for s in report.totals.stations],
                         [('north', 18, 1)])
        self.assertEqual(read_frozen(self.source, report.ref), report)
        empty = freeze(self.source, 'empty-day', [])
        self.assertEqual(empty.lines, ())
        self.assertEqual(empty.parents, ())
        self.assertEqual(empty.totals.stations, ())
        self.assertEqual((empty.totals.liters, empty.totals.missing), (0, 0))

    def test_zero_and_missing(self):
        rows = (*ORIGINAL[:2], {'liters': 0, 'source': 'north-log/zero'}, ORIGINAL[2])
        batch = record(self.source, 'zeros', 'north', rows)
        report = freeze(self.source, 'day', [batch.ref])
        self.assertEqual([line.liters for line in report.lines], [7, 11, 0, None])
        self.assertEqual([line.position for line in report.lines], [1, 2, 3, 4])
        self.assertIsNone(report.lines[2].missing)
        self.assertEqual(report.lines[3].missing, 'sensor stopped')
        self.assertEqual((report.totals.liters, report.totals.missing), (18, 1))
        self.assertEqual(report.totals.stations[0].missing, 1)

    def test_explicit_revision(self):
        first = record(self.source, 'north-am', 'north', ORIGINAL)
        corrected = correct(self.source, first.ref, CORRECTED)
        old = freeze(self.source, 'day', [first.ref])
        new = freeze(self.source, 'day', [corrected.ref])
        self.assertEqual(corrected.parent_revision, first.revision)
        self.assertEqual(read_revision(self.source, first.ref), first)
        self.assertEqual((old.parents, new.parents), ((first.ref,), (corrected.ref,)))
        self.assertEqual((old.totals.liters, new.totals.liters), (18, 20))
        self.assertEqual((old.lines[1].source, new.lines[1].source),
                         ('north-log/2', 'north-correction/2'))
        self.assertEqual((old.lines[1].parent, new.lines[1].parent), (first.ref, corrected.ref))

    def test_invalid_batch(self):
        with self.assertRaises(ValueError):
            record(self.source, 'bad', 'north', [
                {'liters': 7, 'missing': 'also missing', 'source': 'invalid-log/1'},
            ])
        with self.assertRaises(KeyError):
            read_revision(self.source, ('bad', 1))
        with self.assertRaises(KeyError):
            freeze(self.source, 'bad-day', [('bad', 1)])

    def test_atomic_rejection(self):
        valid = record(self.source, 'valid', 'north', ORIGINAL)
        with self.assertRaises(ValueError):
            record(self.source, 'mixed', 'south', [
                {'liters': 3, 'source': 'south-log/1'},
                {'liters': 8, 'missing': 'broken', 'source': 'south-log/2'},
            ])
        self.assertIsNone(self.source.execute(
            'SELECT 1 FROM batches WHERE batch_id = ?', ('mixed',)
        ).fetchone())
        self.assertIsNone(self.source.execute(
            'SELECT 1 FROM stations WHERE station_id = ?', ('south',)
        ).fetchone())
        with self.assertRaises(KeyError):
            freeze(self.source, 'mixed-day', [valid.ref, ('mixed', 1)])
        with self.assertRaises(KeyError):
            read_frozen(self.source, ('mixed-day', 1))
        self.assertEqual(freeze(self.source, 'mixed-day', [valid.ref]).ref, ('mixed-day', 1))

    def test_exact_large_integer_and_selection_order(self):
        north = record(self.source, 'north', 'north', [{'liters': 2**63 + 7, 'source': 'N/1'}])
        south = record(self.source, 'south', 'south', [{'liters': 11, 'source': 'S/1'}])
        report = freeze(self.source, 'ordered', [south.ref, north.ref, south.ref])
        self.assertEqual([line.source for line in report.lines], ['S/1', 'N/1', 'S/1'])
        self.assertEqual([line.selection_index for line in report.lines], [0, 1, 2])
        self.assertEqual(report.totals.liters, 2**63 + 29)
        self.assertEqual([(s.station_id, s.liters) for s in report.totals.stations],
                         [('south', 22), ('north', 2**63 + 7)])

    def test_missing_parent_and_boolean_value(self):
        first = record(self.source, 'north-am', 'north', ORIGINAL)
        with self.assertRaises(KeyError):
            correct(self.source, ('north-am', 9), CORRECTED)
        with self.assertRaises(ValueError):
            correct(self.source, first.ref, [{'liters': True, 'source': 'north-log/error'}])
        self.assertEqual(read_revision(self.source, first.ref), first)
        self.assertEqual(correct(self.source, first.ref, CORRECTED).revision, 2)
