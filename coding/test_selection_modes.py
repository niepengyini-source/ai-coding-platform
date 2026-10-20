"""Task selection uses only synthetic projects and preserves the old sample-pool API."""
import random

from django.test import SimpleTestCase, TestCase, override_settings

from .models import Assignment, AuditEvent, Round, SourceDocument, SourceRecord, Unit
from .round_forms import RoundForm
from .selection import parse_positions, select_round_units
from .services import create_round
from .tests import TEST_SETTINGS, fixture


class MaterialPositionTests(SimpleTestCase):
    def test_mixed_positions_ranges_and_overlaps_are_sorted_and_deduplicated(self):
        self.assertEqual(parse_positions('8、3，5-7 6-9; 12 - 13', 20), [3, 5, 6, 7, 8, 9, 12, 13])

    def test_blank_input_is_an_empty_selection(self):
        self.assertEqual(parse_positions(' \n ', 0), [])

    def test_invalid_positions_ranges_and_large_expansions_are_rejected(self):
        for value in ['0', '-1', '3-1', '21', '2-21', 'a', '１', '1' * 5000]:
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                parse_positions(value, 20)
        with self.assertRaisesMessage(ValueError, '最多'):
            parse_positions('1-10001', 10001)

    def test_repeated_large_ranges_do_not_expand_before_deduplication(self):
        self.assertEqual(len(parse_positions(' '.join(['1-10000'] * 100), 10000)), 10000)


@override_settings(**TEST_SETTINGS)
class TaskSelectionTests(TestCase):
    def setUp(self):
        for name, value in fixture().items():
            setattr(self, name, value)
        for position in range(4, 10):
            record = SourceRecord.objects.create(document=self.doc, position=position,
                text=f'虚构材料{position}', metadata={'interview': 'B' if position % 2 else 'C'})
            self.units.append(Unit.objects.create(record=record, text=record.text, end=len(record.text)))

    def ids(self, entries):
        return [entry.pk for entry in entries]

    def selected(self, **changes):
        return select_round_units(self.project, **{'count': 0, 'seed': 17, **changes})

    def create(self, **changes):
        values = {'book_id': self.book.pk, 'name': '虚构选取方式', 'kind': 'pilot',
                  'coder_ids': [self.owner.pk, self.coder.pk], 'count': 0, 'seed': 17,
                  'distribution': 'all', 'double_percent': 25}
        return create_round(self.project, self.owner, **{**values, **changes})

    def test_sequential_uses_one_based_active_order_not_database_ids(self):
        Unit.objects.filter(pk=self.units[0].pk).update(active=False)
        result = self.selected(selection_mode='sequential', start_position=2, count=3)
        self.assertEqual(self.ids(result), self.ids(self.units[2:5]))

    def test_sequential_zero_means_every_remaining_unit_and_default_starts_at_one(self):
        self.assertEqual(self.ids(self.selected(selection_mode='sequential', start_position=4)), self.ids(self.units[3:]))
        self.assertEqual(self.ids(self.selected(selection_mode='sequential', count=2)), self.ids(self.units[:2]))

    def test_sequential_rejects_invalid_start_and_insufficient_remaining_units(self):
        for start, count in [(0, 1), (10, 0), (9, 2), (-1, 1)]:
            with self.subTest(start=start, count=count), self.assertRaises(ValueError):
                self.selected(selection_mode='sequential', start_position=start, count=count)
        with self.assertRaises(ValueError):
            self.selected(selection_mode='sequential', unit_ids=[self.units[0].pk])

    def test_manual_is_exact_in_source_order_and_does_not_resample(self):
        chosen = [self.units[7].pk, self.units[1].pk, self.units[5].pk, self.units[1].pk]
        expected = self.ids([self.units[1], self.units[5], self.units[7]])
        self.assertEqual(self.ids(self.selected(selection_mode='manual', unit_ids=chosen)), expected)
        self.assertEqual(self.ids(self.selected(selection_mode='manual', unit_ids=chosen, count=3, seed=999)), expected)
        with self.assertRaises(ValueError):
            self.selected(selection_mode='manual', unit_ids=chosen, count=2)
        with self.assertRaises(ValueError):
            self.selected(selection_mode='manual')

    def test_manual_rejects_foreign_and_inactive_units(self):
        foreign_record = SourceRecord.objects.create(document=SourceDocument.objects.create(
            project=self.other, name='fictional.csv', raw=b'x', sha256='b' * 64, text_column='text'),
            position=1, text='虚构外部材料')
        foreign = Unit.objects.create(record=foreign_record, text=foreign_record.text, end=len(foreign_record.text))
        Unit.objects.filter(pk=self.units[0].pk).update(active=False)
        for unit_id in [foreign.pk, self.units[0].pk, 999999999]:
            with self.subTest(unit_id=unit_id), self.assertRaisesMessage(ValueError, '当前有效'):
                self.selected(selection_mode='manual', unit_ids=[unit_id])

    def test_nonrandom_modes_cannot_silently_use_stratified_sampling(self):
        for mode in ['sequential', 'manual']:
            with self.subTest(mode=mode), self.assertRaisesMessage(ValueError, '分层'):
                self.selected(selection_mode=mode, stratify='interview', unit_ids=[self.units[0].pk] if mode == 'manual' else None)

    def test_random_and_legacy_specified_pool_match_previous_rng(self):
        self.assertEqual(self.ids(self.selected(count=4)), self.ids(random.Random(17).sample(self.units, 4)))
        pool = [self.units[1], self.units[4], self.units[6]]
        self.assertEqual(self.ids(self.selected(count=2, unit_ids=self.ids(pool))),
                         self.ids(random.Random(17).sample(pool, 2)))

    def test_read_only_selection_does_not_freeze_or_create_anything(self):
        self.book.refresh_from_db()
        before = (self.book.revision, self.book.frozen, Round.objects.count(), Assignment.objects.count(), AuditEvent.objects.count())
        self.selected(selection_mode='sequential', count=2)
        self.selected(selection_mode='manual', unit_ids=[self.units[0].pk])
        self.book.refresh_from_db()
        self.assertEqual(before, (self.book.revision, self.book.frozen, Round.objects.count(), Assignment.objects.count(), AuditEvent.objects.count()))

    def test_created_round_records_selection_and_preserves_distribution(self):
        sequential = self.create(selection_mode='sequential', start_position=3, count=4,
                                 distribution='distributed')
        self.assertEqual(sequential.sampling['unit_ids'], self.ids(self.units[2:6]))
        self.assertEqual(sequential.sampling['selection_mode'], 'sequential')
        self.assertEqual(sequential.sampling['start_position'], 3)
        self.assertIn('顺序', sequential.sampling['method'])
        self.assertEqual(sequential.assignments.count(), 4)
        for coder in [self.owner, self.coder]:
            self.assertEqual(sequential.assignments.filter(coder=coder).count(), 2)
        manual = self.create(selection_mode='manual', unit_ids=[self.units[0].pk, self.units[5].pk])
        self.assertEqual(manual.assignments.count(), 4)
        self.assertEqual(manual.sampling['count'], 2)
        self.assertEqual(manual.sampling['selection_mode'], 'manual')
        self.assertNotIn('随机', manual.sampling['method'])

    def test_failed_selection_is_atomic_and_leaves_rule_revision_unchanged(self):
        self.book.refresh_from_db()
        before = (Round.objects.count(), Assignment.objects.count(), self.book.revision)
        with self.assertRaises(ValueError):
            self.create(selection_mode='sequential', start_position=8, count=5)
        self.book.refresh_from_db()
        self.assertEqual(before, (Round.objects.count(), Assignment.objects.count(), self.book.revision))

    def test_quick_coding_remains_all_units_in_source_order(self):
        quick = self.create(kind='quick', coder_ids=[self.owner.pk], distribution='all')
        self.assertEqual(quick.sampling['unit_ids'], self.ids(self.units))
        self.assertEqual(quick.sampling['selection_mode'], 'all')
        for changes in [{'selection_mode': 'manual'}, {'selection_mode': 'sequential'}, {'start_position': 2}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(kind='quick', coder_ids=[self.owner.pk], **changes)

    def form(self, **changes):
        return RoundForm({'name': '虚构任务安排', 'kind': 'pilot', 'book_id': self.book.pk,
                          'coder_ids': [self.owner.pk], 'distribution': 'all', **changes}, project=self.project)

    def test_form_defaults_allow_legacy_posts(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['selection_mode'], 'random')
        self.assertEqual(form.cleaned_data['seed'], 2026)
        self.assertEqual(form.cleaned_data['count'], 0)
        self.assertNotIn('manual_positions', form.cleaned_data)

    def test_manual_form_merges_positions_ranges_and_checked_ids(self):
        Unit.objects.filter(pk=self.units[0].pk).update(active=False)
        form = self.form(selection_mode='manual', manual_positions='2、4-5、4',
                         unit_ids=f'{self.units[2].pk},{self.units[7].pk},{self.units[7].pk}', count=99)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['unit_ids'], self.ids([self.units[2], self.units[4], self.units[5], self.units[7]]))
        self.assertEqual(form.cleaned_data['count'], 4)
        self.assertNotIn('manual_positions', form.cleaned_data)

    def test_manual_form_rejects_invalid_range_or_empty_selection(self):
        for positions in ['100', '2-1', '-1']:
            form = self.form(selection_mode='manual', manual_positions=positions)
            self.assertFalse(form.is_valid())
            self.assertIn('manual_positions', form.errors)
        form = self.form(selection_mode='manual')
        self.assertFalse(form.is_valid())
        self.assertIn('unit_ids', form.errors)

    def test_form_sequential_clears_hidden_selection_and_random_settings(self):
        form = self.form(selection_mode='sequential', unit_ids=str(self.units[0].pk), stratify='interview')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['start_position'], 1)
        self.assertEqual(form.cleaned_data['unit_ids'], [])
        self.assertEqual(form.cleaned_data['stratify'], '')
