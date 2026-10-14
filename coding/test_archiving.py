"""Archive checks use fictional data and never write to the research database."""
import csv
import io
import json
import zipfile
from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import path
from django.utils import timezone

from config.urls import urlpatterns as existing_urls
from .archiving import archive
from .models import Assignment, Decision, Membership, Project, Round, Unit
from .tests import TEST_SETTINGS, fixture

urlpatterns = [path('p/<int:project_id>/archive/', archive, name='archive'), *existing_urls]


@override_settings(ROOT_URLCONF='coding.test_archiving', **TEST_SETTINGS)
class ArchiveTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.url = f'/p/{self.project.pk}/archive/'
        self.client.force_login(self.coder)
        self.draft = Assignment.objects.get(round=self.r, coder=self.coder, unit=self.units[0])
        self.draft.revision, self.draft.note = 1, '没有主代码的已保存草稿'
        self.draft.save()
        self.submitted = Assignment.objects.get(round=self.r, coder=self.coder, unit=self.units[1])
        self.submitted.status, self.submitted.revision, self.submitted.primary = 'submitted', 2, self.first
        self.submitted.save()
        self.other_answer = Assignment.objects.get(round=self.r, coder=self.owner, unit=self.units[0])
        self.other_answer.revision, self.other_answer.note, self.other_answer.primary = 1, '负责人答案', self.second
        self.other_answer.save()

    def export(self, **values):
        return self.client.post(self.url, {'action': 'custom', 'mode': 'quick', 'scope': 'mine',
                                          'selection': 'all', 'format': 'json', 'page': 1, **values})

    def rows(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json; charset=utf-8')
        return json.loads(response.content)['rows']

    def test_get_shows_search_preview_without_changing_state(self):
        response = self.client.get(self.url)
        self.assertContains(response, '自定义归档导出')
        self.assertContains(response, '本页勾选')
        self.assertNotContains(response, '下载项目快照 ZIP')
        self.assertEqual(response.context['page'].paginator.count, 2)
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_saved_draft_without_code_exports_and_metadata_is_unchanged(self):
        rows = self.rows(self.export())
        draft = next(row for row in rows if row['assignment_id'] == self.draft.pk)
        self.assertEqual(draft['status'], 'draft')
        self.assertEqual(draft['primary_code'], '')
        self.assertEqual(draft['round_state'], 'active')
        self.assertEqual(draft['meta_interview'], 'A')
        self.assertEqual(json.loads(draft['metadata_json']), self.units[0].record.metadata)
        self.assertIn('+08:00', draft['updated_at'])
        self.assertEqual(draft['document_name'], 'synthetic.csv')

    def test_coder_cannot_select_all_coders_or_full_project(self):
        result = self.export(scope='all')
        self.assertNotEqual(result.get('Content-Type'), 'application/json; charset=utf-8')
        self.assertTrue(result.context['search_form'].errors)
        self.assertEqual(self.client.post(self.url, {'action': 'full'}).status_code, 403)

    def test_owner_can_choose_all_coders_and_default_is_still_mine(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url), '<option value="all">全部编码员</option>', html=True)
        self.assertEqual(len(self.rows(self.export())), 1)
        self.assertEqual(len(self.rows(self.export(scope='all'))), 3)

    def test_reviewer_can_export_all_and_viewer_only_their_own(self):
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        self.assertEqual(len(self.rows(self.export(scope='all'))), 3)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.context['page'].paginator.count, 0)
        self.assertEqual(self.client.post(self.url, {'action': 'full'}).status_code, 403)

    def test_outsider_and_logged_out_user_cannot_read_project(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_full_snapshot_allows_active_round_and_keeps_draft_state(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'action': 'full'})
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive_file:
            manifest = json.loads(archive_file.read('manifest.json'))
            self.assertFalse(manifest['changes_project_state'])
            self.assertEqual(manifest['rounds'][0]['state'], 'active')
            self.assertFalse(manifest['rounds'][0]['final_is_concluded'])
            entries = json.loads(archive_file.read(f'03_pilot/round_{self.r.pk}_assignments.json'))
            self.assertEqual(len(entries), 6)
            draft = next(row for row in entries if row['id'] == self.draft.pk)
            self.assertEqual(draft['note'], self.draft.note)
            self.assertEqual(draft['status'], 'draft')
            self.assertEqual(datetime.fromisoformat(draft['updated_at']), self.draft.updated_at)
            self.assertNotIn(f'06_final/round_{self.r.pk}.csv', archive_file.namelist())
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_empty_project_can_be_snapshotted(self):
        project = Project.objects.create(name='空项目', owner=self.owner)
        Membership.objects.create(project=project, user=self.owner, role='owner')
        self.client.force_login(self.owner)
        response = self.client.post(f'/p/{project.pk}/archive/', {'action': 'full'})
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive_file:
            self.assertEqual(json.loads(archive_file.read('manifest.json'))['rounds'], [])
            self.assertEqual(json.loads(archive_file.read('01_units/records.json')), [])

    def test_closed_quick_round_does_not_create_consensus_export(self):
        quick = Round.objects.create(project=self.project, book=self.book, kind='quick', state='closed',
                                     name='个人已完成', started_by=self.coder)
        Assignment.objects.create(round=quick, unit=self.units[0], coder=self.coder, status='submitted',
                                  primary=self.first, revision=1)
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'action': 'full'})
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive_file:
            self.assertNotIn(f'06_final/round_{quick.pk}.csv', archive_file.namelist())
            self.assertIn(f'03_personal_coding/round_{quick.pk}_independent.csv', archive_file.namelist())

    def test_provisional_decision_export_is_explicitly_not_concluded(self):
        self.r.state = 'review'
        self.r.save()
        Decision.objects.create(round=self.r, unit=self.units[1], primary=self.first, author=self.owner, note='待核对')
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'action': 'full'})
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive_file:
            self.assertNotIn(f'06_final/round_{self.r.pk}.csv', archive_file.namelist())
            self.assertIn(f'05_adjudication/round_{self.r.pk}_current_decisions.csv', archive_file.namelist())
            self.assertFalse(json.loads(archive_file.read('manifest.json'))['rounds'][0]['final_is_concluded'])

    def test_limit_exports_first_n_across_all_results(self):
        rows = self.rows(self.export(selection='first', limit=1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['assignment_id'], self.submitted.pk)

    def test_limit_requires_valid_positive_bounded_number(self):
        for limit in ('', 0, -1, 50001, '不是数字'):
            with self.subTest(limit=limit):
                result = self.export(selection='first', limit=limit)
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.context['selection_form'].errors)

    def test_selected_records_cannot_include_other_coders_or_fake_ids(self):
        for selected in [self.other_answer.pk, 999999]:
            with self.subTest(selected=selected):
                result = self.export(selection='selected', selected=[selected])
                self.assertTrue(result.context['selection_form'].errors)
        rows = self.rows(self.export(selection='selected', selected=[self.draft.pk]))
        self.assertEqual([row['assignment_id'] for row in rows], [self.draft.pk])

    def test_selected_records_must_still_match_filters(self):
        result = self.export(selection='selected', selected=[self.draft.pk], status='submitted')
        self.assertTrue(result.context['selection_form'].errors)

    def test_all_results_include_other_pages_and_selection_is_current_page_only(self):
        for index in range(28):
            unit = Unit.objects.create(record=self.units[0].record, text=f'分页虚构材料 {index}', end=1)
            Assignment.objects.create(round=self.r, unit=unit, coder=self.coder, revision=1)
        self.assertEqual(len(self.rows(self.export())), 30)
        self.assertEqual(len(self.rows(self.export(selection='first', limit=27))), 27)
        result = self.export(selection='selected', selected=[self.draft.pk], page=1)
        self.assertTrue(result.context['selection_form'].errors)
        self.assertEqual([row['assignment_id'] for row in self.rows(
            self.export(selection='selected', selected=[self.draft.pk], page=2))], [self.draft.pk])

    def test_preview_from_second_page_resets_hidden_page_and_selection_to_first(self):
        for index in range(28):
            unit = Unit.objects.create(record=self.units[0].record, text=f'预览分页材料 {index}', end=1)
            Assignment.objects.create(round=self.r, unit=unit, coder=self.coder, revision=1)
        result = self.export(action='preview', page=2)
        self.assertEqual(result.context['page'].number, 1)
        self.assertEqual(result.context['selection_form']['page'].value(), '1')
        shown = result.context['preview'][0]
        self.assertEqual([row['assignment_id'] for row in self.rows(
            self.export(selection='selected', selected=[shown.pk], page=1))], [shown.pk])
        result = self.export(selection='selected', selected=[self.draft.pk], page=999)
        self.assertEqual([row['assignment_id'] for row in self.rows(result)], [self.draft.pk])

    def test_database_busy_is_readable_for_full_and_custom_exports(self):
        self.client.force_login(self.owner)
        with patch('coding.archiving.project_archive', side_effect=OperationalError('database locked')):
            response = self.client.post(self.url, {'action': 'full'})
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, '原始编码没有改变', status_code=503)
        self.client.force_login(self.coder)
        with patch('coding.archiving.archive_annotation_table', side_effect=OperationalError('database locked')):
            response = self.export()
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, '原始编码没有改变', status_code=503)

    def test_foreign_project_and_round_filters_are_rejected(self):
        other_round = Round.objects.create(project=self.other, book=self.foreign_code.book, name='外部批次', kind='pilot')
        result = self.export(round_id=other_round.pk)
        self.assertTrue(result.context['search_form'].errors)
        result = self.export(document=999999)
        self.assertTrue(result.context['search_form'].errors)

    def test_keyword_exact_and_contains_search(self):
        self.assertEqual(len(self.rows(self.export(q='检查'))), 1)
        result = self.export(q='检查', mode='exact')
        self.assertContains(result, '没有匹配的编码记录')
        self.assertEqual(len(self.rows(self.export(q=self.units[1].text, mode='exact'))), 1)

    def test_metadata_filters_apply_exact_or_contains_and_keep_original_values(self):
        self.assertEqual(len(self.rows(self.export(metadata_field='interview', metadata_value='A', mode='exact'))), 2)
        self.assertEqual(len(self.rows(self.export(metadata_field='speaker', metadata_value='虚构', mode='quick'))), 2)
        result = self.export(metadata_field='interview', metadata_value='不存在', mode='exact')
        self.assertContains(result, '没有匹配的编码记录')
        result = self.export(metadata_value='A')
        self.assertTrue(result.context['search_form'].errors)

    def test_metadata_heading_is_literal_including_numbers_dots_and_quotes(self):
        record = self.units[0].record
        record.metadata.update({'2026': '甲组', 'source.group': '甲组', 'group"label': '甲组',
                                "group' OR 1=1 --": '甲组'})
        record.save(update_fields=['metadata'])
        for heading in ['2026', 'source.group', 'group"label', "group' OR 1=1 --"]:
            with self.subTest(heading=heading):
                rows = self.rows(self.export(metadata_field=heading, metadata_value='甲组', mode='exact'))
                self.assertEqual([row['assignment_id'] for row in rows], [self.draft.pk])

    def test_code_filter_searches_primary_and_secondary(self):
        self.assertEqual([row['assignment_id'] for row in self.rows(self.export(code='PLAN', mode='exact'))], [self.submitted.pk])
        self.draft.secondary = [self.second.pk]
        self.draft.save()
        rows = self.rows(self.export(code='CHECK', mode='exact'))
        self.assertEqual([row['assignment_id'] for row in rows], [self.draft.pk])

    def test_updated_time_range_is_inclusive_of_end_minute(self):
        old = timezone.now() - timedelta(days=3)
        Assignment.objects.filter(pk=self.draft.pk).update(updated_at=old)
        start = timezone.localtime(timezone.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.assertEqual(len(self.rows(self.export(start=start))), 1)
        finish = timezone.localtime(self.submitted.updated_at).strftime('%Y-%m-%dT%H:%M')
        self.assertEqual(len(self.rows(self.export(start=start, end=finish))), 1)
        result = self.export(start='2026-10-14T10:00', end='2026-10-13T10:00')
        self.assertTrue(result.context['search_form'].errors)

    def test_maximum_end_minute_is_rejected_without_overflow(self):
        result = self.export(end='9999-12-31T23:59')
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, '结束时间超出支持范围，请选择较早的时间')
        self.assertIn('end', result.context['search_form'].errors)

    def test_superseded_parent_is_optional_and_ancestry_is_preserved(self):
        self.draft.superseded = True
        self.draft.save()
        self.submitted.parent = self.draft
        self.submitted.save()
        rows = self.rows(self.export())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['parent_assignment_id'], self.draft.pk)
        rows = self.rows(self.export(include_superseded='on'))
        self.assertEqual(len(rows), 2)

    def test_more_than_max_rows_does_not_silently_truncate(self):
        with patch('coding.archiving.MAX_EXPORT_ROWS', 1):
            result = self.export()
            self.assertContains(result, '一次最多导出50000条')

    def test_formats_csv_tsv_xlsx_json(self):
        for extension, content_type in [('csv', 'text/csv'), ('tsv', 'text/tab-separated-values'),
                                         ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                                         ('json', 'application/json')]:
            with self.subTest(extension=extension):
                response = self.export(format=extension)
                self.assertTrue(response['Content-Type'].startswith(content_type))
                self.assertIn('.' + extension, response['Content-Disposition'])
                if extension == 'csv':
                    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(rows[0]['meta_interview'], 'A')

    def test_invalid_format_and_action_are_user_readable(self):
        result = self.export(format='html')
        self.assertTrue(result.context['selection_form'].errors)
        self.assertContains(self.export(action='erase'), '请选择完整项目快照或自定义导出')

    def test_preview_is_read_only_and_shows_applied_filters(self):
        result = self.export(action='preview', status='submitted')
        self.assertEqual(result.context['page'].paginator.count, 1)
        self.assertContains(result, '更多筛选条件')
        self.submitted.refresh_from_db()
        self.assertEqual(self.submitted.revision, 2)

    def test_new_preview_does_not_validate_old_selection_or_missing_export_count(self):
        result = self.export(action='preview', selection='selected', selected=[self.draft.pk], status='submitted')
        self.assertEqual(result.context['page'].paginator.count, 1)
        self.assertFalse(result.context['selection_form'].errors)
        result = self.export(action='preview', selection='first', limit='')
        self.assertEqual(result.context['page'].paginator.count, 2)
        self.assertFalse(result.context['selection_form'].errors)

    def test_empty_selection_cannot_export(self):
        result = self.export(selection='selected')
        self.assertContains(result, '请先勾选本页要导出的记录')
