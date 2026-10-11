"""Simplified manual coding uses fictional records, never the research data."""
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from openpyxl import Workbook

from .importing import parse_upload
from .models import Assignment, AuditEvent, Round, SourceDocument, Unit, UploadDraft
from .tests import TEST_SETTINGS, fixture, Helpers
from .services import Conflict, save_assignment, project_archive
from .splitting import split_personal
from .workflow import start_quick_coding, close_quick_coding


def excel_bytes():
    workbook = Workbook()
    workbook.active.title = '说明'
    sheet = workbook.create_sheet('访谈')
    sheet.append(['Message Content', 'conversation_id', 'speaker'])
    sheet.append(['虚构的访谈内容', 'A', '参与者'])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@override_settings(**TEST_SETTINGS)
class SimpleImportTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.owner)
        self.url = f'/p/{self.project.pk}/materials/'

    def test_common_columns_are_selected_but_upload_does_not_import(self):
        upload = SimpleUploadedFile('fiction.csv', b'Message Content,conversation_id,speaker\nhello,A,one\n')
        response = self.client.post(self.url, {'file': upload})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['default_text'], 'Message Content')
        self.assertEqual(response.context['default_context'], 'conversation_id')
        self.assertEqual(SourceDocument.objects.filter(project=self.project).count(), 1)

    def test_ambiguous_text_columns_require_a_choice(self):
        response = self.client.post(self.url, {'file': SimpleUploadedFile('fiction.csv', b'text,content\nhello,world\n')})
        self.assertEqual(response.context['default_text'], '')
        draft = response.context['draft']
        response = self.client.post(self.url, {'draft_id': draft.pk, 'text_column': ''})
        self.assertContains(response, '请选择')
        self.assertEqual(response.context['draft'].pk, draft.pk)
        self.assertEqual(SourceDocument.objects.filter(project=self.project).count(), 1)

    def test_choose_excel_sheet_with_empty_cover_then_confirm(self):
        raw = excel_bytes()
        response = self.client.post(self.url, {'file': SimpleUploadedFile('fiction.xlsx', raw)})
        self.assertEqual(response.context['sheet_names'], ['说明', '访谈'])
        draft = response.context['draft']
        response = self.client.post(self.url, {'draft_id': draft.pk, 'sheet_name': '访谈', 'action': 'preview'})
        self.assertEqual(response.context['default_text'], 'Message Content')
        self.assertEqual(response.context['row_count'], 1)
        self.assertEqual(SourceDocument.objects.filter(project=self.project).count(), 1)
        response = self.client.post(self.url, {'draft_id': draft.pk, 'sheet_name': '访谈',
                                             'text_column': 'Message Content', 'context_column': 'conversation_id'})
        self.assertEqual(response.status_code, 302)
        doc = SourceDocument.objects.get(project=self.project, name='fiction.xlsx')
        self.assertEqual(doc.sheet_name, '访谈')
        self.assertEqual(bytes(doc.raw), raw)
        self.assertEqual(doc.records.get().metadata, {'conversation_id': 'A', 'speaker': '参与者'})

    def test_sheet_selection_cannot_name_a_missing_sheet(self):
        with self.assertRaisesMessage(ValueError, '工作表'):
            parse_upload('fiction.xlsx', excel_bytes(), sheet_name='不存在')

    def test_draft_is_private(self):
        draft = UploadDraft.objects.create(project=self.project, user=self.owner, filename='fiction.csv', raw=b'text\nhello\n')
        self.client.force_login(self.coder)
        self.assertEqual(self.client.post(self.url, {'draft_id': draft.pk, 'text_column': 'text'}).status_code, 403)


@override_settings(**TEST_SETTINGS)
class CodingEntryTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.coder)
        self.url = f'/p/{self.project.pk}/code/'

    def test_existing_task_opens_without_creating_a_round(self):
        count = Round.objects.count()
        self.assertRedirects(self.client.get(self.url), f'/r/{self.r.pk}/')
        self.assertEqual(Round.objects.count(), count)
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(response, '继续编码')
        self.assertContains(response, '<summary>项目管理</summary>', html=True)

    def test_no_active_task_goes_to_first_start_confirmation(self):
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        self.assertRedirects(self.client.get(self.url), f'/p/{self.project.pk}/quick-start/')

    def test_viewer_cannot_start_and_outsider_cannot_open(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)


@override_settings(**TEST_SETTINGS)
class WorkpadTests(Helpers, TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.coder)

    def test_picker_contains_real_definitions_and_examples_without_empty_fields(self):
        self.first.positive_example = '虚构的计划例子'
        self.first.negative_example = '虚构的反例'
        self.first.save()
        response = self.client.get(f'/r/{self.r.pk}/')
        self.assertContains(response, 'data-code-choice=')
        self.assertContains(response, self.first.definition)
        self.assertContains(response, '虚构的计划例子')
        self.assertContains(response, '虚构的反例')
        self.assertContains(response, '确认并下一条')
        self.assertNotContains(response, '暂无')

    def test_submit_response_has_next_own_draft_and_then_progress(self):
        mine = list(self.r.assignments.filter(coder=self.coder).order_by('id'))
        response = self.save(mine[0], action='submit')
        self.assertEqual(response.json()['next_url'], f'/r/{self.r.pk}/?a={mine[1].pk}')
        self.save(mine[1], action='submit')
        response = self.save(mine[2], action='submit')
        self.assertEqual(response.json()['next_url'], f'/p/{self.project.pk}/progress/')


@override_settings(**TEST_SETTINGS)
class WorkpadSplitTests(Helpers, TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.coder)
        self.personal, _ = start_quick_coding(self.project, self.coder, self.book.pk, str(uuid.uuid4()))
        self.a = self.personal.assignments.get(unit=self.units[0])
        self.url = f'/api/assignments/{self.a.pk}/split/'
        self.payload = {'offset': 5, 'revision': 0, 'token': str(uuid.uuid4())}

    def split(self, **changes):
        return self.client.post(self.url, json.dumps({**self.payload, **changes}), content_type='application/json')

    def test_split_preserves_global_material_and_other_coders_and_blank_children(self):
        old = list(self.r.assignments.values())
        source = self.a.unit.record.text
        response = self.split()
        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertTrue(self.a.superseded)
        segments = list(self.a.segments.order_by('unit__start'))
        self.assertEqual(len(segments), 2)
        self.assertEqual(''.join(s.unit.text for s in segments), source)
        self.assertEqual([(s.unit.start, s.unit.end) for s in segments], [(0, 5), (5, len(source))])
        self.assertTrue(all(not s.unit.active and not s.primary_id and s.revision == 0 for s in segments))
        self.units[0].refresh_from_db()
        self.assertTrue(self.units[0].active)
        self.assertEqual(Unit.objects.filter(record__document__project=self.project, active=True).count(), 3)
        self.assertEqual(old, list(self.r.assignments.values()))
        self.assertEqual(self.a.unit.record.text, source)

    def test_split_replay_is_idempotent_but_different_position_is_conflict(self):
        self.assertEqual(self.split().status_code, 200)
        self.assertEqual(self.split().status_code, 200)
        self.assertEqual(self.a.segments.count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action='annotation.split').count(), 1)
        self.assertEqual(self.split(offset=4).status_code, 409)

    def test_live_counts_and_exports_exclude_original_but_archive_retains_provenance(self):
        self.split()
        response = self.client.get(f'/api/rounds/{self.personal.pk}/progress/')
        self.assertEqual(response.json()['my_total'], 4)
        response = self.client.get(f'/p/{self.project.pk}/progress/')
        personal_row = next(row for row in response.context['rows'] if row['round_id'] == self.personal.pk)
        self.assertEqual(personal_row['total'], 4)
        response = self.client.get(f'/p/{self.project.pk}/exports/?target=mine&round={self.personal.pk}')
        table = response.context['table']
        self.assertEqual(len(table.rows), 4)
        self.assertIn('parent_assignment_id', table.headers)
        response = self.client.get(f'/p/{self.project.pk}/history/')
        self.assertEqual(response.context['page'].paginator.count, 0)
        self.assertEqual(self.client.get(f'/r/{self.personal.pk}/?a={self.a.pk}').status_code, 302)
        from .exporting import unit_table
        self.assertEqual(len(unit_table(self.project, include_inactive=True).rows), 3)

    def test_original_cannot_be_saved_with_split_token_or_reopened(self):
        self.split()
        response = self.save(self.a, token=self.payload['token'], action='save')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(f'/p/{self.project.pk}/history/{self.a.pk}/').status_code, 404)

    def test_saved_or_submitted_personal_answer_cannot_be_split(self):
        self.save(self.a)
        self.assertEqual(self.split(revision=1).status_code, 409)
        self.assertFalse(self.a.segments.exists())

    def test_invalid_boundary_revision_and_foreign_user_do_not_split(self):
        for offset in [0, len(self.a.unit.text), -1, True, '5', 1.5]:
            self.assertEqual(self.split(offset=offset).status_code, 400)
        self.assertEqual(self.split(revision=1).status_code, 409)
        self.client.force_login(self.owner)
        self.assertEqual(self.split().status_code, 404)
        self.assertFalse(self.a.segments.exists())

    def test_emoji_boundaries_nested_splits_and_personal_archive(self):
        record = self.a.unit.record
        record.text = '先😊讨论，后检查'
        record.save()
        self.a.unit.text, self.a.unit.end = record.text, len(record.text)
        self.a.unit.save()
        self.assertEqual(self.split(offset=2).status_code, 200)
        second = self.a.segments.order_by('unit__start').last()
        self.url = f'/api/assignments/{second.pk}/split/'
        self.assertEqual(self.split(offset=2, token=str(uuid.uuid4())).status_code, 200)
        live = self.personal.assignments.filter(superseded=False)
        parts = list(live.filter(unit__record=record).order_by('unit__start'))
        self.assertEqual(''.join(p.unit.text for p in parts), record.text)
        for entry in live:
            save_assignment(entry.pk, self.coder, {'revision': 0, 'primary': self.first.pk, 'secondary': [],
                'uncertain': False, 'no_code': False, 'note': '', 'action': 'submit', 'token': str(uuid.uuid4())})
        self.personal.refresh_from_db()
        close_quick_coding(self.personal.pk, self.coder, self.personal.revision)
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.state, 'closed')
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        with zipfile.ZipFile(io.BytesIO(project_archive(self.project))) as archive:
            data = archive.read(f'03_personal_coding/round_{self.personal.pk}_independent.csv').decode('utf-8-sig')
            self.assertIn('parent_assignment_id', data)
            self.assertIn('True', data)
            self.assertIn('annotation.split', archive.read('99_logs/audit.json').decode('utf-8'))

    def test_collaborative_split_is_a_suggestion_without_changing_any_tasks(self):
        a = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        self.url = f'/api/assignments/{a.pk}/split/'
        old = list(self.r.assignments.values())
        self.assertEqual(self.split().status_code, 403)
        self.url = f'/api/assignments/{a.pk}/split-suggestion/'
        self.assertEqual(self.split(reason='一句包含两个行为').status_code, 200)
        self.assertEqual(self.split(reason='一句包含两个行为').status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action='unit.split_suggest').count(), 1)
        self.assertEqual(old, list(self.r.assignments.values()))
        self.client.force_login(self.owner)
        response = self.client.get(f'/p/{self.project.pk}/units/')
        self.assertContains(response, '一句包含两个行为')
        response = self.client.post(f'/p/{self.project.pk}/units/{self.units[0].pk}/',
            {'action': 'split', 'offset': 5, 'note': '采纳建议，供后续任务使用', 'revision': 0})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(old, list(self.r.assignments.values()))

    def test_suggestion_requires_reason_and_own_active_collaborative_task(self):
        a = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        self.url = f'/api/assignments/{a.pk}/split-suggestion/'
        self.assertEqual(self.split().status_code, 400)
        self.r.state = 'closed'
        self.r.save()
        self.assertEqual(self.split(reason='按行为分开').status_code, 409)
        self.client.force_login(self.viewer)
        self.assertEqual(self.split(reason='按行为分开').status_code, 404)


@override_settings(**TEST_SETTINGS)
class SplitRaceTests(TransactionTestCase):
    def setUp(self):
        self.data = fixture()
        self.r, _ = start_quick_coding(self.data['project'], self.data['coder'], self.data['book'].pk, str(uuid.uuid4()))
        self.a = self.r.assignments.order_by('id').first()

    def race(self, actions):
        barrier = Barrier(len(actions))
        def worker(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return action()
                except Conflict:
                    return 'conflict'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(worker, actions))

    def test_same_split_replay_creates_only_two_fragments(self):
        payload = {'offset': 5, 'revision': 0, 'token': str(uuid.uuid4())}
        action = lambda: split_personal(self.a.pk, self.data['coder'], payload).pk
        self.assertEqual(self.race([action, action]), [self.a.pk, self.a.pk])
        self.assertEqual(self.a.segments.count(), 2)

    def test_simultaneous_different_splits_do_not_create_four_fragments(self):
        def action(offset):
            return lambda: split_personal(self.a.pk, self.data['coder'],
                {'offset': offset, 'revision': 0, 'token': str(uuid.uuid4())}).pk
        results = self.race([action(4), action(5)])
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(self.a.segments.count(), 2)

    def test_saving_and_splitting_cannot_both_change_the_same_original(self):
        split = lambda: split_personal(self.a.pk, self.data['coder'],
            {'offset': 5, 'revision': 0, 'token': str(uuid.uuid4())}).pk
        save = lambda: save_assignment(self.a.pk, self.data['coder'],
            {'revision': 0, 'action': 'save', 'token': str(uuid.uuid4()), 'primary': self.data['first'].pk,
             'secondary': [], 'uncertain': False, 'no_code': False, 'note': '虚构保存'}).pk
        results = self.race([split, save])
        self.assertEqual(results.count('conflict'), 1)
        self.a.refresh_from_db()
        self.assertFalse(self.a.superseded and self.a.primary_id)
        self.assertEqual(self.a.segments.count(), 2 if self.a.superseded else 0)
