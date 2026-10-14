"""Multi-segment regressions use synthetic fixtures and a separate test database."""
import csv
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from openpyxl import load_workbook

from .exporting import annotation_table, final_table
from .models import Assignment, AuditEvent, Code, Decision, Membership, SourceDocument, SourceRecord, Unit
from .services import Conflict, annotation_data, save_assignment
from .splitting import split_personal
from .tests import TEST_SETTINGS, fixture
from .workflow import start_quick_coding


@override_settings(**TEST_SETTINGS)
class MultiSplitTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.codes = [self.first, self.second,
            Code.objects.create(book=self.book, key='ADJUST', name='调整', definition='改变做法'),
            Code.objects.create(book=self.book, key='SUMMARIZE', name='总结', definition='整理结论')]
        self.personal, _ = start_quick_coding(self.project, self.coder, self.book.pk, uuid.uuid4())
        self.a = self.personal.assignments.get(unit=self.units[0])
        self.client.force_login(self.coder)

    def split(self, a=None, **changes):
        a = a or self.a
        data = {'cuts': [2], 'revision': a.revision, 'token': str(uuid.uuid4()), **changes}
        response = self.client.post(f'/api/assignments/{a.pk}/split/', json.dumps(data),
                                    content_type='application/json')
        return response, data

    def set_text(self, text):
        record = self.a.unit.record
        record.text = text
        record.save(update_fields=['text'])
        self.a.unit.text, self.a.unit.end = text, len(text)
        self.a.unit.save(update_fields=['text', 'end'])

    def children(self, a=None):
        return list((a or self.a).segments.select_related('unit__record').order_by('unit__start'))

    def save(self, a, code=None, **changes):
        data = {'revision': a.revision, 'token': str(uuid.uuid4()), 'action': 'save',
                'primary': (code or self.first).pk, 'secondary': [], 'uncertain': False,
                'no_code': False, 'note': '纯虚构判断', **changes}
        save_assignment(a.pk, a.coder, data)
        a.refresh_from_db()

    def unchanged_counts(self):
        return (Unit.objects.count(), Assignment.objects.count(), AuditEvent.objects.count(),
                SourceRecord.objects.count(), SourceDocument.objects.count())

    def assert_rejected_without_writes(self, expected=400, **changes):
        counts = self.unchanged_counts()
        before = annotation_data(self.a)
        self.personal.refresh_from_db()
        round_revision = self.personal.revision
        response, _ = self.split(**changes)
        self.assertEqual(response.status_code, expected, response.content)
        self.assertEqual(self.unchanged_counts(), counts)
        self.a.refresh_from_db()
        self.personal.refresh_from_db()
        self.assertEqual(annotation_data(self.a), before)
        self.assertFalse(self.a.superseded)
        self.assertEqual(self.personal.revision, round_revision)

    def test_one_cut_creates_exactly_two_units_and_opens_first(self):
        response, _ = self.split()
        self.assertEqual(response.status_code, 200)
        children = self.children()
        self.assertEqual(len(children), 2)
        self.assertEqual([c.unit.text for c in children], [self.a.unit.text[:2], self.a.unit.text[2:]])
        self.assertEqual(response.json()['next_url'], f'/r/{self.personal.pk}/?a={children[0].pk}')

    def test_three_cuts_create_four_lossless_independent_units(self):
        response, _ = self.split(cuts=[2, 4, 6])
        self.assertEqual(response.status_code, 200)
        children = self.children()
        self.assertEqual([(c.unit.start, c.unit.end) for c in children],
                         [(0, 2), (2, 4), (4, 6), (6, len(self.a.unit.text))])
        self.assertEqual(''.join(c.unit.text for c in children), self.a.unit.text)
        self.assertTrue(all(c.unit.record_id == self.a.unit.record_id and not c.unit.active for c in children))
        self.assertTrue(all(c.status == 'draft' and not c.primary_id and not c.secondary for c in children))
        event = AuditEvent.objects.get(action='annotation.split', object_id=str(self.a.pk))
        self.assertEqual(event.after['cuts'], [2, 4, 6])

    def test_five_cuts_create_six_units(self):
        response, _ = self.split(cuts=[1, 2, 3, 4, 5])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.children()), 6)

    def test_fifty_units_are_allowed(self):
        self.set_text('甲' * 100)
        response, _ = self.split(cuts=list(range(1, 50)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.children()), 50)
        self.assertEqual(''.join(c.unit.text for c in self.children()), self.a.unit.text)

    def test_fifty_one_units_are_rejected_without_writes(self):
        self.set_text('甲' * 100)
        self.assert_rejected_without_writes(cuts=list(range(1, 51)))

    def test_bad_cut_types_empty_duplicates_unsorted_and_endpoints_do_not_write(self):
        invalid = [None, {}, '2', 2, [], [True], [2.0], ['2'], [None], [[2]],
                   [0], [-1], [len(self.a.unit.text)], [999], [2, 2], [4, 2]]
        for cuts in invalid:
            with self.subTest(cuts=cuts):
                self.assert_rejected_without_writes(cuts=cuts)

    def test_whitespace_only_units_are_rejected_without_writes(self):
        self.set_text('甲  \r\n乙')
        for cuts in [[1, 5], [1, 2], [2, 5]]:
            with self.subTest(cuts=cuts):
                self.assert_rejected_without_writes(cuts=cuts)

    def test_whitespace_and_punctuation_are_kept_exactly(self):
        self.set_text('  甲。\r\n  乙！  ')
        response, _ = self.split(cuts=[6])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c.unit.text for c in self.children()], ['  甲。\r\n', '  乙！  '])

    def test_invalid_focus_positions_do_not_write(self):
        for start in [None, True, -1, len(self.a.unit.text), 999, '1', 1.0, [], {}]:
            with self.subTest(start=start):
                self.assert_rejected_without_writes(start=start)

    def test_focus_can_open_any_selected_fragment(self):
        response, _ = self.split(cuts=[2, 4, 6], start=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_url'], f'/r/{self.personal.pk}/?a={self.children()[2].pk}')

    def test_invalid_tokens_do_not_write(self):
        for token in ['', None, {}, 'bad-token']:
            with self.subTest(token=token):
                self.assert_rejected_without_writes(token=token)

    def test_stale_and_noninteger_revisions_do_not_write(self):
        for revision in [999, True, None, '0']:
            with self.subTest(revision=revision):
                self.assert_rejected_without_writes(expected=409, revision=revision)

    def test_repeated_identical_request_is_idempotent(self):
        response, data = self.split(cuts=[2, 4, 6], start=4)
        self.assertEqual(response.status_code, 200)
        counts = self.unchanged_counts()
        replay, _ = self.split(**data)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), response.json())
        self.assertEqual(self.unchanged_counts(), counts)
        self.assertEqual(AuditEvent.objects.filter(action='annotation.split', object_id=str(self.a.pk)).count(), 1)

    def test_same_token_with_different_cuts_conflicts_without_writes(self):
        _, data = self.split(cuts=[2, 4, 6])
        counts = self.unchanged_counts()
        response, _ = self.split(**{**data, 'cuts': [2, 5, 6]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unchanged_counts(), counts)

    def test_new_token_cannot_resplit_superseded_parent(self):
        self.split(cuts=[2, 4, 6])
        counts = self.unchanged_counts()
        response, _ = self.split(cuts=[2, 4, 6])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unchanged_counts(), counts)

    def test_submitted_parent_keeps_original_judgment_and_history(self):
        self.save(self.a, action='submit', uncertain=True)
        old = annotation_data(self.a)
        response, _ = self.split(cuts=[2, 4, 6])
        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertTrue(self.a.superseded)
        for field in ['primary', 'secondary', 'uncertain', 'no_code', 'note', 'status']:
            self.assertEqual(annotation_data(self.a)[field], old[field])
        self.assertContains(self.client.get(f'/p/{self.project.pk}/history/{self.a.pk}/'), '原记录已拆分')
        self.assertTrue(all(not c.primary_id and not c.note and not c.uncertain for c in self.children()))

    def test_unicode_crlf_and_nested_splits_keep_exact_original_spans(self):
        original = '甲😊\r\n乙𠮷\r\n丙丁'
        self.set_text(original)
        self.assertEqual(self.split(cuts=[4, 8])[0].status_code, 200)
        child = self.children()[0]
        self.save(child)
        self.assertEqual(self.split(child, cuts=[1])[0].status_code, 200)
        live = list(self.personal.assignments.filter(superseded=False, unit__record=self.a.unit.record)
                    .select_related('unit').order_by('unit__start'))
        self.assertEqual([a.unit.text for a in live], ['甲', '😊\r\n', '乙𠮷\r\n', '丙丁'])
        self.assertEqual(''.join(a.unit.text for a in live), original)
        self.assertTrue(all(a.unit.text == original[a.unit.start:a.unit.end] for a in live))

    def test_shared_matching_boundaries_reuse_units_without_changing_other_coder(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        original = self.r.assignments.values().get(pk=other.pk)
        self.assertEqual(self.split(mine, cuts=[2, 4, 6])[0].status_code, 200)
        self.assertEqual(self.r.assignments.values().get(pk=other.pk), original)
        first_ids = [a.unit_id for a in self.children(mine)]
        self.client.force_login(self.owner)
        self.assertEqual(self.split(other, cuts=[2, 4, 6])[0].status_code, 200)
        self.assertEqual([a.unit_id for a in self.children(other)], first_ids)
        self.assertEqual(Unit.objects.filter(record=self.a.unit.record, active=False).count(), 4)

    def test_foreign_assignment_and_viewer_cannot_split(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.split()[0].status_code, 404)
        self.client.force_login(self.coder)
        Membership.objects.filter(project=self.project, user=self.coder).update(role='viewer')
        self.assert_rejected_without_writes(expected=403)

    def test_locked_round_and_bad_original_mapping_do_not_write(self):
        self.personal.state = 'closed'
        self.personal.save(update_fields=['state'])
        self.assert_rejected_without_writes(expected=409)
        self.personal.state = 'active'
        self.personal.save(update_fields=['state'])
        self.a.unit.text = self.a.unit.text[:-1] + '错'
        self.a.unit.save(update_fields=['text'])
        self.assert_rejected_without_writes(expected=409)

    def coded_parts_with_metadata(self):
        metadata = {'created_at': '2026-10-14T11:05:06.123+08:00', 'condition': 'multi_agent',
                    'group_id': 'G007', 'sender_id': 'S042', 'message_id': 'M0008', 'seq_num': '0008'}
        record = self.a.unit.record
        record.metadata = metadata
        record.save(update_fields=['metadata'])
        original_record = SourceRecord.objects.values().get(pk=record.pk)
        original_doc = SourceDocument.objects.values().get(pk=record.document_id)
        self.assertEqual(self.split(cuts=[2, 4, 6])[0].status_code, 200)
        children = self.children()
        for child, code in zip(children, self.codes):
            self.save(child, code, action='submit')
        self.assertEqual(SourceRecord.objects.values().get(pk=record.pk), original_record)
        self.assertEqual(SourceDocument.objects.values().get(pk=record.document_id), original_doc)
        return children, metadata

    def assert_export_metadata(self, file_format):
        children, metadata = self.coded_parts_with_metadata()
        response = self.client.get(f'/r/{self.personal.pk}/export/mine/?format={file_format}')
        self.assertEqual(response.status_code, 200)
        if file_format == 'csv':
            rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'), newline='')))
        elif file_format == 'json':
            rows = response.json()['rows']
        else:
            workbook = load_workbook(io.BytesIO(response.content), read_only=True)
            try:
                values = list(workbook.active.values)
                rows = [dict(zip(values[0], row)) for row in values[1:]]
            finally:
                workbook.close()
        fragment_rows = [row for row in rows if str(row['record_position']) == '1']
        self.assertEqual(len(fragment_rows), 4)
        self.assertEqual([row['primary_code'] for row in fragment_rows], [code.key for code in self.codes])
        self.assertEqual([row['unit_text'] for row in fragment_rows], [child.unit.text for child in children])
        self.assertEqual([str(row['span_start']) for row in fragment_rows], ['0', '2', '4', '6'])
        self.assertEqual([str(row['record_position']) for row in rows], ['1', '1', '1', '1', '2', '3'])
        self.assertNotIn(str(self.a.pk), [str(row['assignment_id']) for row in rows])
        for row in fragment_rows:
            self.assertEqual(json.loads(row['metadata_json']), metadata)
            self.assertEqual(str(row['parent_assignment_id']), str(self.a.pk))
            for key, value in metadata.items():
                self.assertEqual(row['meta_' + key], value)

    def test_csv_exports_different_codes_and_unchanged_source_metadata(self):
        self.assert_export_metadata('csv')

    def test_xlsx_exports_different_codes_and_unchanged_source_metadata(self):
        self.assert_export_metadata('xlsx')

    def test_json_exports_different_codes_and_unchanged_source_metadata(self):
        self.assert_export_metadata('json')

    def test_annotation_export_keeps_fragments_at_original_record_position(self):
        self.coded_parts_with_metadata()
        table = annotation_table(self.personal, self.personal.assignments.filter(superseded=False))
        index = table.headers.index('record_position')
        self.assertEqual([row[index] for row in table.rows], [1, 1, 1, 1, 2, 3])

    def test_final_export_keeps_fragments_before_later_source_records(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        self.assertEqual(self.split(mine, cuts=[2, 4, 6])[0].status_code, 200)
        ordered_units = [child.unit for child in self.children(mine)] + self.units[1:]
        for unit in ordered_units:
            Decision.objects.create(round=self.r, unit=unit, author=self.owner, primary=self.first, cycle=self.r.cycle)
        table = final_table(self.r)
        self.assertEqual([row[table.headers.index('unit_id')] for row in table.rows], [u.pk for u in ordered_units])


@override_settings(**TEST_SETTINGS)
class ConcurrentMultiSplitTests(TransactionTestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.a = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        self.other = self.r.assignments.get(coder=self.owner, unit=self.units[0])

    def split_together(self, tokens):
        gate = Barrier(2)
        other_before = Assignment.objects.values().get(pk=self.other.pk)
        record_before = SourceRecord.objects.values().get(pk=self.a.unit.record_id)

        def worker(token):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                try:
                    split_personal(self.a.pk, self.coder,
                                   {'cuts': [2, 4, 6], 'revision': 0, 'token': token})
                    return 'ok'
                except Conflict:
                    return 'conflict'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, tokens))
        children = list(self.a.segments.select_related('unit').order_by('unit__start'))
        self.a.refresh_from_db()
        self.assertTrue(self.a.superseded)
        self.assertEqual(self.a.revision, 1)
        self.assertEqual(len(children), 4)
        self.assertEqual(''.join(child.unit.text for child in children), self.a.unit.text)
        self.assertEqual(AuditEvent.objects.filter(action='annotation.split', object_id=str(self.a.pk)).count(), 1)
        self.assertEqual(Assignment.objects.values().get(pk=self.other.pk), other_before)
        self.assertEqual(SourceRecord.objects.values().get(pk=self.a.unit.record_id), record_before)
        return results

    def test_simultaneous_same_token_is_idempotent(self):
        token = str(uuid.uuid4())
        self.assertEqual(self.split_together([token, token]), ['ok', 'ok'])

    def test_simultaneous_different_tokens_create_only_one_set_of_units(self):
        self.assertEqual(sorted(self.split_together([str(uuid.uuid4()), str(uuid.uuid4())])), ['conflict', 'ok'])
