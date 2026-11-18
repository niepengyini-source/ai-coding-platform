import json
import hashlib
import csv
import io
import uuid

from django.test import TestCase, override_settings

from .archiving import search_archive
from .book_updates import apply_book_update, begin_update
from .exporting import annotation_table, archive_annotation_table, final_table
from .models import Assignment, AuditEvent, Decision
from .reliability import report_for
from .reviewing import disagreement_rows
from .services import annotation_data, save_assignment, save_decision, validate_labels
from .source_exports import source_export_response
from .tests import TEST_SETTINGS, fixture
from .workflow import search_history


@override_settings(**TEST_SETTINGS)
class ParallelLabelTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.book.policy = 'parallel'
        self.book.save(update_fields=['policy'])
        self.r.book = self.book
        self.book.refresh_from_db()
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['text', 'interview', 'speaker'])
        for record in self.doc.records.order_by('position'):
            writer.writerow([record.text, record.metadata['interview'], record.metadata['speaker']])
        self.doc.raw = output.getvalue().encode('utf-8')
        self.doc.sha256 = hashlib.sha256(self.doc.raw).hexdigest()
        self.doc.save(update_fields=['raw', 'sha256'])
        self.entry = self.r.assignments.get(coder=self.owner, unit=self.units[0])

    def data(self, **values):
        return {'primary': None, 'secondary': [], 'labels': [self.second.pk, self.first.pk],
                'uncertain': False, 'no_code': False, 'note': '虚构并列判断', 'revision': 0,
                'action': 'save', 'token': uuid.uuid4().hex, **values}

    def test_parallel_labels_have_explicit_storage_and_stable_order(self):
        saved = save_assignment(self.entry.pk, self.owner, self.data())
        self.assertIsNone(saved.primary_id)
        self.assertEqual(saved.secondary, [])
        self.assertEqual(saved.labels, sorted([self.first.pk, self.second.pk]))
        self.assertEqual(annotation_data(saved)['labels'], saved.labels)

    def test_parallel_validation_rejects_foreign_duplicate_mixed_and_malformed_choices(self):
        cases = [{'labels': [self.foreign_code.pk]}, {'labels': [self.first.pk, self.first.pk]},
                 {'labels': [True]}, {'labels': ['1']}, {'labels': [self.first.pk], 'primary': self.second.pk},
                 {'labels': [self.first.pk], 'secondary': [self.second.pk]}, {'labels': [self.first.pk], 'no_code': True},
                 {'labels': [], 'uncertain': True, 'note': ''}, {'labels': [], 'primary': 0}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_labels(self.book, self.data(**values))

    def test_old_policies_keep_payload_shape_and_cardinality(self):
        self.book.policy = 'multi'
        self.book.save(update_fields=['policy'])
        self.entry.round.book = self.book
        self.assertNotIn('labels', annotation_data(self.entry))
        self.assertNotIn('labels', validate_labels(self.book, self.data(labels=[], primary=self.first.pk)))
        with self.assertRaises(ValueError):
            validate_labels(self.book, self.data(labels=[]))
        with self.assertRaises(ValueError):
            validate_labels(self.book, self.data(primary=self.first.pk))

    def test_order_does_not_create_disagreement_or_fake_primary_reliability(self):
        Assignment.objects.filter(round=self.r, unit=self.units[0]).update(
            labels=[self.first.pk, self.second.pk], status='submitted', revision=1)
        other = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other.labels = [self.second.pk, self.first.pk]
        other.save(update_fields=['labels'])
        result = report_for(self.r)
        self.assertIsNone(result['primary_alpha'])
        self.assertIsNone(result['pairs'][0]['primary_kappa'])
        self.assertEqual(result['pairs'][0]['agreement'], 1)
        row = next(row for row in disagreement_rows(self.r) if row['unit'].pk == self.units[0].pk)
        self.assertEqual(row['types'], [])
        other.labels = [self.first.pk]
        other.save(update_fields=['labels'])
        row = next(row for row in disagreement_rows(self.r) if row['unit'].pk == self.units[0].pk)
        self.assertEqual(row['types'], ['parallel_difference'])

    def test_independent_decision_and_exports_use_parallel_codes(self):
        save_assignment(self.entry.pk, self.owner, self.data())
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        decision = save_decision(self.r, self.units[0], self.owner, self.data())
        self.assertEqual(set(decision.labels), {self.first.pk, self.second.pk})
        table = annotation_table(self.r, Assignment.objects.filter(pk=self.entry.pk))
        self.assertIn('codes', table.headers)
        self.assertNotIn('primary_code', table.headers)
        self.assertIn(self.first.key, table.rows[0][table.headers.index('codes')])
        self.assertIn('codes', final_table(self.r).headers)
        response = source_export_response(self.project, table, 'csv', '并列结果')
        text = response.content.decode('utf-8-sig')
        self.assertIn('平台_并列代码', text)
        self.assertNotIn('平台_主代码', text)

    def test_parallel_book_update_maps_codes_and_rejects_policy_conversion(self):
        save_assignment(self.entry.pk, self.owner, self.data())
        draft = begin_update(self.project, self.owner, self.book, self.book.revision)
        apply_book_update(self.project, self.owner, draft, draft.revision, {self.r.pk: self.r.revision})
        self.entry.refresh_from_db()
        self.assertEqual(set(self.entry.labels), set(draft.codes.values_list('pk', flat=True)))
        self.r.refresh_from_db()
        draft.refresh_from_db()
        changed = begin_update(self.project, self.owner, draft, draft.revision)
        changed.policy = 'single'
        changed.save(update_fields=['policy'])
        with self.assertRaisesMessage(ValueError, '标签方式'):
            apply_book_update(self.project, self.owner, changed, changed.revision, {self.r.pk: self.r.revision})

    def test_source_round_cannot_be_silently_switched_to_new_book(self):
        self.r.sampling = {**self.r.sampling, 'source_coding': True}
        self.r.save(update_fields=['sampling'])
        draft = begin_update(self.project, self.owner, self.book, self.book.revision)
        with self.assertRaisesMessage(ValueError, '原文选段'):
            apply_book_update(self.project, self.owner, draft, draft.revision, {self.r.pk: self.r.revision})

    def test_parallel_names_are_searchable_without_exposing_other_coders(self):
        save_assignment(self.entry.pk, self.owner, self.data())
        found = search_history(self.project, self.owner, {'q': self.second.name, 'mode': 'quick'})
        self.assertEqual(list(found.values_list('pk', flat=True)), [self.entry.pk])
        found = search_archive(self.project, self.owner, 'owner', {'q': self.second.key, 'mode': 'quick'})
        self.assertEqual(list(found.values_list('pk', flat=True)), [self.entry.pk])
        self.client.force_login(self.owner)
        response = self.client.get(f'/p/{self.project.pk}/history/')
        self.assertContains(response, self.second.name)
        self.assertContains(response, '已确认')

    def test_open_coding_audit_captures_the_selected_definition(self):
        self.r.sampling = {**self.r.sampling, 'open_coding': True}
        self.r.save(update_fields=['sampling'])
        save_assignment(self.entry.pk, self.owner, self.data())
        snapshot = AuditEvent.objects.filter(action='annotation.save', object_id=str(self.entry.pk)).latest('pk').after['selected_code_snapshot']
        self.assertEqual(snapshot['policy'], 'parallel')
        self.assertEqual({item['definition'] for item in snapshot['codes']}, {self.first.definition, self.second.definition})
