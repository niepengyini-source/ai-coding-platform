"""Incremental material import uses fictional rows and never replaces coding work."""
import csv
import io
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (Assignment, AuditEvent, Code, Codebook, Membership, Project,
                     Round, SourceDocument, SourceRecord, Unit)
from .services import import_records


class MaterialDedupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('material_dedup_owner')
        cls.project = Project.objects.create(name='虚构增量材料', owner=cls.owner)
        Membership.objects.create(project=cls.project, user=cls.owner, role='owner')

    def import_rows(self, rows, *, project=None, filename='fiction.csv',
                    text_column='text', context_column='group', raw=None, sheet_name=''):
        if raw is None:
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [text_column])
            writer.writeheader()
            writer.writerows(rows)
            raw = stream.getvalue().encode('utf-8')
        return import_records(project or self.project, self.owner,
                              SimpleNamespace(raw=raw, filename=filename), rows,
                              text_column, context_column, sheet_name)

    def row(self, **changes):
        return {'text': '我们先讨论目标。', 'id': '1', 'group': 'A',
                'speaker': '虚构参与者', 'time': '2026-01-01 09:00', **changes}

    def test_repeated_file_returns_existing_and_reports_skip_without_new_records(self):
        rows = [self.row()]
        first = self.import_rows(rows)
        second = self.import_rows(rows, filename='renamed.csv')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.import_counts, {'records': 0, 'units': 0, 'skipped': 1})
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.assertEqual(SourceRecord.objects.count(), 1)
        event = AuditEvent.objects.get(action='import.skipped')
        self.assertEqual(event.after, second.import_counts)

    def test_partial_overlap_adds_only_new_rows_and_keeps_original_positions(self):
        first = self.import_rows([self.row()])
        second = self.import_rows([self.row(), self.row(id='2')])
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.import_counts, {'records': 1, 'units': 1, 'skipped': 1})
        self.assertEqual(second.records.get().position, 2)
        self.assertEqual(SourceRecord.objects.count(), 2)
        self.assertEqual(Unit.objects.count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action='import').latest('id').after['skipped'], 1)

    def test_identical_rows_within_one_file_are_skipped(self):
        doc = self.import_rows([self.row(), self.row(), self.row(id='2'), self.row(id='2')])
        self.assertEqual(doc.import_counts, {'records': 2, 'units': 2, 'skipped': 2})
        self.assertEqual(list(doc.records.values_list('position', flat=True)), [1, 3])

    def test_every_metadata_value_is_part_of_identity(self):
        baseline = self.row()
        rows = [baseline]
        for field in ('id', 'group', 'speaker', 'time'):
            rows.append({**baseline, field: baseline[field] + '_different'})
        doc = self.import_rows(rows)
        self.assertEqual(doc.import_counts, {'records': 5, 'units': 5, 'skipped': 0})

    def test_text_difference_is_not_duplicate(self):
        doc = self.import_rows([self.row(), self.row(text='然后核对结果。')])
        self.assertEqual(doc.records.count(), 2)

    def test_metadata_column_order_does_not_create_duplicate(self):
        self.import_rows([self.row()])
        reversed_row = dict(reversed(list(self.row().items())))
        doc = self.import_rows([reversed_row])
        self.assertEqual(doc.import_counts, {'records': 0, 'units': 0, 'skipped': 1})
        self.assertEqual(SourceRecord.objects.count(), 1)
        self.assertEqual(bytes(doc.raw).decode('utf-8').splitlines()[0], 'time,speaker,group,id,text')

    def test_extra_even_empty_original_field_is_not_silently_discarded(self):
        self.import_rows([self.row()])
        doc = self.import_rows([self.row(extra='')])
        self.assertEqual(doc.import_counts['records'], 1)
        self.assertIn('extra', doc.records.get().metadata)

    def test_different_column_name_is_different_information(self):
        self.import_rows([self.row()])
        row = self.row()
        row['participant'] = row.pop('speaker')
        doc = self.import_rows([row])
        self.assertEqual(doc.import_counts['records'], 1)

    def test_whitespace_and_case_are_not_normalized_into_duplicates(self):
        self.import_rows([self.row()])
        doc = self.import_rows([self.row(text=' 我们先讨论目标。'), self.row(group='a')])
        self.assertEqual(doc.import_counts['records'], 2)

    def test_generated_missing_context_and_row_order_do_not_create_duplicate(self):
        row = self.row(group='')
        first = self.import_rows([row])
        second = self.import_rows([self.row(id='2', group=''), row])
        self.assertEqual(first.records.get().context_key, 'missing:1')
        self.assertEqual(second.import_counts, {'records': 1, 'units': 1, 'skipped': 1})
        self.assertEqual(SourceRecord.objects.count(), 2)

    def test_selected_context_mapping_is_not_an_original_field(self):
        rows = [self.row()]
        first = self.import_rows(rows)
        doc = self.import_rows(rows, raw=b'fictional differently serialized upload', context_column='speaker')
        self.assertEqual(doc.import_counts['records'], 0)
        self.assertEqual(first.records.get().context_key, 'value:A')

    def test_other_project_records_do_not_block_import(self):
        self.import_rows([self.row()])
        other = Project.objects.create(name='另一个虚构项目', owner=self.owner)
        doc = self.import_rows([self.row()], project=other)
        self.assertEqual(doc.import_counts, {'records': 1, 'units': 1, 'skipped': 0})

    def test_duplicate_reimport_keeps_split_units_and_saved_answers(self):
        doc = self.import_rows([self.row()])
        original = Unit.objects.get(record__document=doc)
        original.active = False
        original.save(update_fields=['active'])
        children = [Unit.objects.create(record=original.record, text=original.text[:4], start=0, end=4),
                    Unit.objects.create(record=original.record, text=original.text[4:], start=4, end=len(original.text))]
        book = Codebook.objects.create(project=self.project, version=1)
        code = Code.objects.create(book=book, key='P', name='计划', definition='虚构定义')
        batch = Round.objects.create(project=self.project, book=book, name='虚构任务', kind='quick')
        assignment = Assignment.objects.create(round=batch, unit=children[0], coder=self.owner,
                                               primary=code, status='submitted', note='已经核对', revision=7)
        imported = self.import_rows([self.row(), self.row(id='2')])
        assignment.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(imported.import_counts['skipped'], 1)
        self.assertFalse(original.active)
        self.assertEqual((assignment.primary_id, assignment.note, assignment.status, assignment.revision),
                         (code.pk, '已经核对', 'submitted', 7))
        self.assertEqual(batch.assignments.count(), 1)
        self.assertEqual(Unit.objects.filter(record=original.record).count(), 3)

    def test_blank_text_duplicate_is_skipped_without_creating_units(self):
        doc = self.import_rows([self.row(text=''), self.row(text=''), self.row(id='2')])
        self.assertEqual(doc.import_counts, {'records': 2, 'units': 1, 'skipped': 1})

    def test_invalid_context_rolls_back_new_document_and_partial_rows(self):
        self.import_rows([self.row()])
        before = (SourceDocument.objects.count(), SourceRecord.objects.count(), AuditEvent.objects.count())
        with self.assertRaisesMessage(ValueError, '最长450'):
            self.import_rows([self.row(id='2'), self.row(group='x' * 451)])
        self.assertEqual((SourceDocument.objects.count(), SourceRecord.objects.count(), AuditEvent.objects.count()), before)

    def test_sheet_and_filename_are_not_record_identity(self):
        self.import_rows([self.row()], sheet_name='第一批')
        doc = self.import_rows([self.row()], filename='other.xlsx', sheet_name='第二批')
        self.assertEqual(doc.import_counts, {'records': 0, 'units': 0, 'skipped': 1})
        self.assertEqual(SourceDocument.objects.count(), 2)
        self.assertEqual(SourceRecord.objects.count(), 1)
