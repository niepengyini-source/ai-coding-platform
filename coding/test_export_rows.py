"""Custom export regressions use synthetic fixtures and the isolated test database."""
import csv
import io
import json
import uuid

from django.test import Client, TestCase, override_settings
from openpyxl import load_workbook

from .exporting import annotation_table, disagreement_table, final_table, unit_table
from .models import Assignment, AuditEvent, Decision, Round, SourceRecord, Unit
from .splitting import split_personal
from .tests import Helpers, TEST_SETTINGS, fixture
from .workflow import start_quick_coding


@override_settings(**TEST_SETTINGS)
class ExportRowsTests(Helpers, TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = f'/p/{self.project.pk}/exports/'

    def table(self):
        return annotation_table(self.r, self.r.assignments.filter(coder=self.owner, superseded=False))

    def data(self, table=None, **changes):
        table = table or self.table()
        data = {'target': 'mine', 'round': self.r.pk, 'format': 'json', 'filename': '虚构选中记录',
                'encoding': 'utf-8-sig', 'delimiter': ',', 'fields': table.headers,
                'row_match': 'quick', 'selection': 'all', 'action': 'download'}
        for index, key in enumerate(table.headers):
            data[f'label_{index}'] = key
            data[f'order_{index}'] = index + 1
        data.update(changes)
        return data

    def rows(self, response, file_format='json'):
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response.get('Content-Disposition', ''))
        if file_format == 'json':
            return response.json()['rows']
        if file_format in ('csv', 'tsv'):
            return list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')),
                                       delimiter='\t' if file_format == 'tsv' else ','))
        workbook = load_workbook(io.BytesIO(response.content), read_only=True)
        try:
            values = list(workbook.active.values)
            return [dict(zip(values[0], row)) for row in values[1:]]
        finally:
            workbook.close()

    def assert_no_download(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Content-Disposition', response)

    def many_rows(self):
        for position in range(4, 31):
            text = f'虚构第{position}条材料'
            record = SourceRecord.objects.create(document=self.doc, position=position, text=text,
                                                  metadata={'group_id': f'G{position:03}'})
            unit = Unit.objects.create(record=record, text=text, end=len(text))
            Assignment.objects.create(round=self.r, unit=unit, coder=self.owner, primary=self.first, revision=1)

    def test_get_previews_rows_without_downloading_or_selecting_any(self):
        response = self.client.get(self.url, {'target': 'mine', 'round': self.r.pk})
        self.assert_no_download(response)
        self.assertEqual(response.context['row_count'], 3)
        self.assertEqual(len(response.context['row_preview']), 3)
        self.assertEqual(response.context['selected_count'], 0)
        self.assertFalse(response.context['row_form'].errors)

    def test_old_download_clients_without_new_fields_still_export_all(self):
        data = self.data()
        for key in ('row_match', 'selection', 'action'):
            del data[key]
        rows = self.rows(self.client.post(self.url, data))
        self.assertEqual(len(rows), 3)
        self.assertEqual([row['unit_text'] for row in rows], [unit.text for unit in self.units])

    def test_select_one_record_keeps_its_text_code_and_original_metadata(self):
        a = self.own_assignment(unit=self.units[1])
        Assignment.objects.filter(pk=a.pk).update(primary=self.second, revision=1, note='纯虚构备注')
        rows = self.rows(self.client.post(self.url, self.data(selection='selected', selected=[str(a.pk)])))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['assignment_id'], a.pk)
        self.assertEqual(rows[0]['unit_text'], self.units[1].text)
        self.assertEqual(rows[0]['primary_code'], 'CHECK')
        self.assertEqual(rows[0]['coder_note'], '纯虚构备注')
        self.assertEqual(json.loads(rows[0]['metadata_json']), self.units[1].record.metadata)

    def test_first_n_uses_source_order_and_caps_at_filtered_count(self):
        for limit, expected in [(1, 1), (2, 2), (30, 3)]:
            with self.subTest(limit=limit):
                rows = self.rows(self.client.post(self.url, self.data(selection='first', limit=limit)))
                self.assertEqual(len(rows), expected)
                self.assertEqual([row['unit_id'] for row in rows], [u.pk for u in self.units[:expected]])

    def test_invalid_or_missing_limit_never_downloads(self):
        for limit in ['', '0', '-1', '1.5', 'abc', '50001', '9' * 200]:
            with self.subTest(limit=limit):
                response = self.client.post(self.url, self.data(selection='first', limit=limit))
                self.assert_no_download(response)
                self.assertIn('limit', response.context['row_form'].errors)

    def test_selected_mode_without_checks_never_falls_back_to_all(self):
        response = self.client.post(self.url, self.data(selection='selected'))
        self.assert_no_download(response)
        self.assertIn('请先勾选', str(response.context['row_form'].errors))

    def test_quick_and_exact_search_match_values_without_changing_them(self):
        record = self.units[0].record
        record.metadata = {'group_id': 'Group-A'}
        record.save(update_fields=['metadata'])
        other = self.units[1].record
        other.metadata = {'group_id': 'Group-AB'}
        other.save(update_fields=['metadata'])
        for mode, expected in [('quick', 2), ('exact', 1)]:
            with self.subTest(mode=mode):
                rows = self.rows(self.client.post(self.url, self.data(row_query='group-a', row_match=mode)))
                self.assertEqual(len(rows), expected)
                self.assertEqual(rows[0]['meta_group_id'], 'Group-A')

    def test_metadata_filter_treats_dotted_quoted_and_numeric_keys_literally(self):
        record = self.units[0].record
        record.metadata = {'a.b': 'literal.dot', 'a': {'b': 'nested'}, '0': 7, 'quote"key': 'G001'}
        record.save(update_fields=['metadata'])
        for key, value in [('a.b', 'literal.dot'), ('0', '7'), ('quote"key', 'G001')]:
            with self.subTest(key=key):
                response = self.client.post(self.url, self.data(row_metadata_field=key,
                    row_metadata_value=value, row_match='exact'))
                rows = self.rows(response)
                self.assertEqual([row['unit_id'] for row in rows], [self.units[0].pk])
                self.assertEqual(json.loads(rows[0]['metadata_json']), record.metadata)

    def test_status_filters_distinguish_unsaved_draft_and_submitted(self):
        assignments = [self.own_assignment(unit=unit) for unit in self.units]
        Assignment.objects.filter(pk=assignments[1].pk).update(primary=self.first, revision=1)
        Assignment.objects.filter(pk=assignments[2].pk).update(primary=self.first, revision=1, status='submitted')
        for status, a in zip(('unsaved', 'draft', 'submitted'), assignments):
            with self.subTest(status=status):
                rows = self.rows(self.client.post(self.url, self.data(row_status=status)))
                self.assertEqual([row['assignment_id'] for row in rows], [a.pk])

    def test_no_search_matches_export_zero_rows_not_the_original_table(self):
        response = self.client.post(self.url, self.data(row_query='NO_SUCH_SYNTHETIC_RECORD'))
        self.assertEqual(self.rows(response), [])

    def test_invalid_filters_never_download(self):
        for changes, field in [({'row_metadata_field': 'interview'}, 'row_metadata_value'),
                               ({'row_metadata_value': 'A'}, 'row_metadata_value'),
                               ({'row_metadata_field': 'unknown', 'row_metadata_value': 'A'}, 'row_metadata_field'),
                               ({'row_match': 'bad'}, 'row_match'),
                               ({'row_status': 'bad'}, 'row_status')]:
            with self.subTest(changes=changes):
                response = self.client.post(self.url, self.data(**changes))
                self.assert_no_download(response)
                self.assertIn(field, response.context['row_form'].errors)

    def test_cross_page_selection_persists_and_unchecking_removes_only_current_page(self):
        self.many_rows()
        first = self.client.get(self.url, {'target': 'mine', 'round': self.r.pk})
        first_ids = first.context['visible_ids']
        chosen_first = first.context['row_preview'][0]['key']
        page_two = self.client.post(self.url, self.data(action='preview', selection='selected',
            row_page='2', preview_page='1', visible_ids=first_ids, selected=[chosen_first]))
        self.assert_no_download(page_two)
        self.assertEqual(page_two.context['row_page'].number, 2)
        self.assertEqual(page_two.context['selected_count'], 1)
        chosen_second = page_two.context['row_preview'][0]['key']
        page_one = self.client.post(self.url, self.data(action='preview', selection='selected', row_page='1',
            preview_page='2', retained_selected=page_two.context['retained_selected'],
            visible_ids=page_two.context['visible_ids'], selected=[chosen_second]))
        self.assertEqual(page_one.context['selected_count'], 2)
        self.assertTrue(page_one.context['row_preview'][0]['checked'])
        rows = self.rows(self.client.post(self.url, self.data(selection='selected', preview_page='1',
            retained_selected=page_one.context['retained_selected'], visible_ids=page_one.context['visible_ids'])))
        self.assertEqual([str(row['assignment_id']) for row in rows], [chosen_second])

    def test_paging_preserves_custom_columns_and_format_before_download(self):
        self.many_rows()
        table = self.table()
        text_index, code_index = table.headers.index('unit_text'), table.headers.index('primary_code')
        data = self.data(table, action='preview', row_page='2', format='csv', fields=['unit_text', 'primary_code'])
        data.update({f'label_{text_index}': '语句', f'label_{code_index}': '类别',
                     f'order_{text_index}': 2, f'order_{code_index}': 1})
        response = self.client.post(self.url, data)
        self.assert_no_download(response)
        self.assertEqual(response.context['export_form']['format'].value(), 'csv')
        self.assertEqual(response.context['export_form'][f'label_{text_index}'].value(), '语句')
        self.assertEqual(response.context['export_form'][f'order_{code_index}'].value(), '1')
        del data['row_page']
        data.update(action='download', selection='first', limit='1')
        rows = self.rows(self.client.post(self.url, data), 'csv')
        self.assertEqual(list(rows[0]), ['类别', '语句'])
        self.assertEqual(rows[0]['语句'], self.units[0].text)

    def test_filter_action_clears_old_checks_and_returns_to_first_page(self):
        selected = str(self.own_assignment(unit=self.units[2]).pk)
        response = self.client.post(self.url, self.data(action='filter', selection='selected',
            row_page='2', row_query=self.units[0].text, retained_selected=selected, selected=[selected]))
        self.assert_no_download(response)
        self.assertEqual(response.context['row_page'].number, 1)
        self.assertEqual(response.context['selected_count'], 0)
        self.assertEqual(response.context['row_count'], 1)
        self.assertFalse(response.context['row_form'].errors)

    def test_posted_foreign_or_other_coder_ids_never_escape_source_scope(self):
        other = self.own_assignment(self.coder)
        Assignment.objects.filter(pk=other.pk).update(note='SECRET_OTHER_CODER')
        quick, _ = start_quick_coding(self.project, self.owner, self.book.pk, uuid.uuid4())
        other_round = quick.assignments.first()
        for bad_id in (str(other.pk), str(other_round.pk), '999999999'):
            for field in ('retained_selected', 'visible_ids', 'selected'):
                with self.subTest(id=bad_id, field=field):
                    response = self.client.post(self.url, self.data(selection='selected',
                        **{field: [bad_id] if field == 'selected' else bad_id}))
                    self.assert_no_download(response)
                    self.assertTrue(response.context['row_form'].non_field_errors())
                    self.assertNotContains(response, 'SECRET_OTHER_CODER')

    def test_malformed_retained_identifiers_are_rejected(self):
        for value in ('-1', 'true', '1,,2', '1.5', '１', '9' * 21):
            with self.subTest(value=value):
                response = self.client.post(self.url, self.data(selection='selected', retained_selected=value))
                self.assert_no_download(response)
                self.assertTrue(response.context['row_form'].non_field_errors())

    def test_active_blind_round_export_gates_survive_row_selection(self):
        for target in ('all', 'disagreements', 'final'):
            for action in ('preview', 'download'):
                with self.subTest(target=target, action=action):
                    response = self.client.post(self.url, self.data(target=target, action=action,
                        selection='selected', selected=[str(self.own_assignment().pk)]))
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn('Content-Disposition', response)

    def test_other_members_private_quick_round_is_not_accessible(self):
        quick, _ = start_quick_coding(self.project, self.coder, self.book.pk, uuid.uuid4())
        selected = str(quick.assignments.first().pk)
        self.client.force_login(self.viewer)
        response = self.client.post(self.url, self.data(round=quick.pk, selection='selected', selected=[selected]))
        self.assertEqual(response.status_code, 404)

    def test_all_export_selects_one_coder_not_both_answers_for_same_unit(self):
        Round.objects.filter(pk=self.r.pk).update(state='review')
        a = self.own_assignment(self.coder)
        Assignment.objects.filter(pk=a.pk).update(primary=self.second, revision=1, note='虚构成员答案')
        table = annotation_table(self.r, self.r.assignments.filter(superseded=False))
        rows = self.rows(self.client.post(self.url, self.data(table, target='all', selection='selected',
                                                             selected=[str(a.pk)])))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['coder'], self.coder.username)
        self.assertEqual(rows[0]['primary_code'], 'CHECK')

    def test_material_rows_use_unit_identifiers_without_coding_status(self):
        table = unit_table(self.project)
        response = self.client.get(self.url, {'target': 'units'})
        self.assertNotIn('row_status', response.context['row_form'].fields)
        rows = self.rows(self.client.post(self.url, self.data(table, target='units', selection='selected',
            selected=[str(self.units[1].pk)])))
        self.assertEqual([row['unit_id'] for row in rows], [self.units[1].pk])
        self.assertNotIn('primary_code', rows[0])

    def test_final_rows_use_current_decision_identifiers(self):
        current = Decision.objects.create(round=self.r, unit=self.units[0], primary=self.second,
            author=self.owner, note='虚构定稿', cycle=self.r.cycle)
        Decision.objects.create(round=self.r, unit=self.units[1], primary=self.first,
            author=self.owner, note='旧周期', cycle=self.r.cycle + 1)
        Round.objects.filter(pk=self.r.pk).update(state='closed')
        rows = self.rows(self.client.post(self.url, self.data(final_table(self.r), target='final',
            selection='selected', selected=[str(current.pk)])))
        self.assertEqual([row['decision_id'] for row in rows], [current.pk])
        self.assertEqual(rows[0]['primary_code'], 'CHECK')
        self.assertEqual(rows[0]['cycle'], self.r.cycle)

    def test_disagreement_rows_use_unit_ids_and_keep_independent_judgments(self):
        Round.objects.filter(pk=self.r.pk).update(state='review')
        self.r.assignments.update(status='submitted', primary=self.first, revision=1)
        self.r.assignments.filter(coder=self.coder, unit=self.units[1]).update(primary=self.second)
        table = disagreement_table(self.r)
        rows = self.rows(self.client.post(self.url, self.data(table, target='disagreements',
            selection='selected', selected=[str(self.units[1].pk)])))
        self.assertEqual([row['unit_id'] for row in rows], [self.units[1].pk])
        self.assertEqual({item['primary_code'] for item in json.loads(rows[0]['independent_labels_json'])},
                         {'PLAN', 'CHECK'})

    def test_split_children_export_selected_text_codes_and_unchanged_metadata_in_all_formats(self):
        a = self.own_assignment()
        record = a.unit.record
        record.metadata = {'group_id': 'G007', 'created_at': '2026-10-14T11:05:06+08:00',
                           'message_id': 'M0008', 'seq_num': '0008'}
        record.save(update_fields=['metadata'])
        original = SourceRecord.objects.values().get(pk=record.pk)
        split_personal(a.pk, self.owner, {'cuts': [2, 4, 6], 'revision': 0, 'token': str(uuid.uuid4())})
        children = list(a.segments.select_related('unit').order_by('unit__start'))
        for child, code in zip(children, (self.first, self.second, self.first, self.second)):
            Assignment.objects.filter(pk=child.pk).update(primary=code, revision=1, status='submitted')
        chosen = [children[0], children[3]]
        for file_format in ('json', 'csv', 'tsv', 'xlsx'):
            with self.subTest(format=file_format):
                rows = self.rows(self.client.post(self.url, self.data(format=file_format, selection='selected',
                    selected=[str(child.pk) for child in chosen])), file_format)
                self.assertEqual([row['unit_text'] for row in rows], [child.unit.text for child in chosen])
                self.assertEqual([row['primary_code'] for row in rows], ['PLAN', 'CHECK'])
                self.assertEqual([int(row['span_start']) for row in rows], [0, 6])
                self.assertNotIn(str(a.pk), [str(row['assignment_id']) for row in rows])
                for row in rows:
                    self.assertEqual(json.loads(row['metadata_json']), record.metadata)
                    self.assertEqual(row['meta_group_id'], 'G007')
                    self.assertEqual(row['meta_created_at'], record.metadata['created_at'])
                    self.assertEqual(int(row['parent_assignment_id']), a.pk)
        self.assertEqual(SourceRecord.objects.values().get(pk=record.pk), original)
        response = self.client.post(self.url, self.data(selection='selected', retained_selected=str(a.pk)))
        self.assert_no_download(response)
        self.assertTrue(response.context['row_form'].non_field_errors())

    def test_previews_and_downloads_do_not_change_research_records(self):
        assignments = list(Assignment.objects.order_by('pk').values())
        records = list(SourceRecord.objects.order_by('pk').values())
        units = list(Unit.objects.order_by('pk').values())
        audit_count, round_count = AuditEvent.objects.count(), Round.objects.count()
        self.client.get(self.url, {'target': 'mine', 'round': self.r.pk})
        self.client.post(self.url, self.data(action='filter', row_query='确定'))
        self.rows(self.client.post(self.url, self.data(selection='first', limit='1')))
        self.assertEqual(list(Assignment.objects.order_by('pk').values()), assignments)
        self.assertEqual(list(SourceRecord.objects.order_by('pk').values()), records)
        self.assertEqual(list(Unit.objects.order_by('pk').values()), units)
        self.assertEqual((AuditEvent.objects.count(), Round.objects.count()), (audit_count, round_count))

    def test_row_preview_and_download_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for action in ('filter', 'preview', 'download'):
            with self.subTest(action=action):
                self.assertEqual(client.post(self.url, self.data(action=action)).status_code, 403)
