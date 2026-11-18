"""Fictional sources only: optional excerpts do not alter preassigned Unit tasks."""
import hashlib
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Assignment, AuditEvent, Code, Codebook, Membership, Project, Round, SourceDocument, SourceRecord, Unit
from .source_coding import (add_workspace_code, canonical_source, finish_source_coding, save_source_annotation,
    set_source_completed, source_annotation_data, start_source_coding)
from .source_models import SourceAnnotation, SourceCodingTask
from .services import Conflict
from .tests import TEST_SETTINGS


@override_settings(**TEST_SETTINGS)
class SourceCodingTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user('source_open_owner', password='test-password')
        self.coder = users.objects.create_user('source_open_coder', password='test-password')
        self.outsider = users.objects.create_user('source_open_outsider', password='test-password')
        self.viewer = users.objects.create_user('source_open_viewer', password='test-password')
        self.project = Project.objects.create(name='虚构通用原文编码', owner=self.owner)
        for user, role in [(self.owner, 'owner'), (self.coder, 'coder'), (self.viewer, 'viewer')]:
            Membership.objects.create(project=self.project, user=user, role=role)
        self.book = Codebook.objects.create(project=self.project, version=1, policy='multi')
        raw = b'text,speaker\nfiction,fiction\n'
        self.document = SourceDocument.objects.create(project=self.project, name='fiction.csv', raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(), text_column='text')
        self.first = SourceRecord.objects.create(document=self.document, position=1,
            text='先核对😀资料，再记录依据。', metadata={'speaker': '甲'})
        self.second = SourceRecord.objects.create(document=self.document, position=3,
            text='然后讨论各自的判断。', metadata={'speaker': '乙'})
        self.unit = Unit.objects.create(record=self.first, text=self.first.text, end=len(self.first.text))
        self.round, _ = start_source_coding(self.project, self.owner, self.book.pk)
        self.task = self.round.source_tasks.get(coder=self.owner, document=self.document)
        self.client.force_login(self.owner)

    def add_code(self, name='核对资料'):
        self.book.refresh_from_db()
        result = add_workspace_code(self.round.pk, self.owner, {'name': name, 'definition': '',
            'book_id': self.book.pk, 'revision': self.book.revision})
        return Code.objects.get(pk=result['code']['id'])

    def payload(self, *, start=0, end=None, **extra):
        text, _ = canonical_source(self.document)
        code = self.book.codes.first() or self.add_code()
        end = len(text) if end is None else end
        return {'start': start, 'end': end, 'text': text[start:end], 'revision': 0,
                'book_id': self.book.pk, 'token': uuid.uuid4().hex,
                'primary': code.pk, 'secondary': [], 'labels': [], 'uncertain': False,
                'no_code': False, 'note': '虚构编码依据', **extra}

    def test_empty_codebook_starts_without_units_or_freezing_rules(self):
        self.book.refresh_from_db()
        self.assertFalse(self.book.frozen)
        self.assertFalse(self.book.codes.exists())
        self.assertEqual(self.round.source_tasks.count(), 1)
        self.assertEqual(Unit.objects.count(), 1)
        self.assertFalse(self.round.assignments.exists())
        self.assertTrue(self.round.sampling['source_coding'])

    def test_start_is_replay_safe_and_old_units_are_not_reassigned(self):
        second_round, created = start_source_coding(self.project, self.owner, self.book.pk)
        self.assertFalse(created)
        self.assertEqual(second_round.pk, self.round.pk)
        self.assertEqual(SourceCodingTask.objects.count(), 1)
        self.assertEqual(Assignment.objects.count(), 0)

    def test_empty_material_and_viewer_cannot_start(self):
        other = Project.objects.create(owner=self.owner, name='空材料项目')
        Membership.objects.create(project=other, user=self.owner, role='owner')
        book = Codebook.objects.create(project=other, version=1)
        with self.assertRaisesMessage(ValueError, '先导入'):
            start_source_coding(other, self.owner, book.pk)
        with self.assertRaises(PermissionDenied):
            start_source_coding(self.project, self.viewer, self.book.pk)

    def test_frozen_rules_are_copied_without_touching_old_round(self):
        code = self.add_code()
        self.round.state = 'closed'
        self.round.save(update_fields=['state'])
        fixed = Round.objects.create(project=self.project, book=self.book, kind='formal', name='固定单元')
        assignment = Assignment.objects.create(round=fixed, unit=self.unit, coder=self.owner, primary=code)
        self.book.frozen = True
        self.book.save(update_fields=['frozen'])
        new, created = start_source_coding(self.project, self.owner, self.book.pk)
        self.assertTrue(created)
        self.assertNotEqual(new.book_id, self.book.pk)
        self.assertFalse(new.book.frozen)
        assignment.refresh_from_db()
        self.assertEqual(assignment.primary_id, code.pk)
        self.assertEqual(assignment.round.book_id, self.book.pk)

    def test_coder_cannot_create_a_code_or_clone_frozen_project_rules(self):
        self.book.frozen = True
        self.book.save(update_fields=['frozen'])
        new, _ = start_source_coding(self.project, self.coder, self.book.pk)
        self.assertEqual(new.book_id, self.book.pk)
        with self.assertRaises(PermissionDenied):
            add_workspace_code(new.pk, self.coder, {'book_id': self.book.pk, 'revision': 0, 'name': '不允许'})
        self.assertEqual(self.project.codebooks.count(), 1)

    def test_name_only_code_creation_and_revision_checks(self):
        code = self.add_code()
        self.assertEqual(code.definition, '')
        self.assertEqual(code.key, 'C001')
        with self.assertRaises(Conflict):
            add_workspace_code(self.round.pk, self.owner,
                {'name': '过时页面', 'book_id': self.book.pk, 'revision': 0})
        self.assertEqual(self.book.codes.count(), 1)

    def test_canonical_text_preserves_real_row_numbers_and_cross_paragraph_emoji(self):
        text, blocks = canonical_source(self.document)
        self.assertEqual(text, self.first.text + '\n\n' + self.second.text)
        self.assertEqual([block['row'] for block in blocks], [1, 3])
        annotation = save_source_annotation(self.task.pk, self.owner, self.payload())
        self.assertIn('😀', annotation.text)
        self.assertIn('\n\n', annotation.text)
        self.first.refresh_from_db()
        self.assertEqual(self.first.text, '先核对😀资料，再记录依据。')
        self.assertEqual(Unit.objects.count(), 1)

    def test_cross_paragraph_form_crlf_does_not_change_source_offsets(self):
        payload = self.payload()
        payload['text'] = payload['text'].replace('\n', '\r\n')
        saved = save_source_annotation(self.task.pk, self.owner, payload)
        self.assertEqual(saved.end, len(canonical_source(self.document)[0]))

    def test_overlapping_quotes_are_separate_but_exact_range_is_unique(self):
        first = save_source_annotation(self.task.pk, self.owner, self.payload(end=8))
        second = save_source_annotation(self.task.pk, self.owner, self.payload(start=2, end=10))
        self.assertNotEqual(first.pk, second.pk)
        with self.assertRaises(Conflict):
            save_source_annotation(self.task.pk, self.owner, self.payload(end=8))
        self.assertEqual(self.task.annotations.count(), 2)

    def test_stale_save_is_rejected_and_same_token_retry_is_idempotent(self):
        payload = self.payload()
        first = save_source_annotation(self.task.pk, self.owner, payload)
        repeated = save_source_annotation(self.task.pk, self.owner, payload)
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(repeated.revision, 1)
        payload['token'] = uuid.uuid4().hex
        with self.assertRaises(Conflict):
            save_source_annotation(self.task.pk, self.owner, payload)
        self.assertEqual(AuditEvent.objects.filter(object_type='SourceAnnotation').count(), 1)

    def test_bad_source_offsets_and_foreign_codes_do_not_save(self):
        foreign_book = Codebook.objects.create(project=self.project, version=2)
        foreign = Code.objects.create(book=foreign_book, key='OTHER', name='别的规则', definition='测试')
        payload = self.payload(primary=foreign.pk)
        with self.assertRaisesMessage(ValueError, '本轮编码本'):
            save_source_annotation(self.task.pk, self.owner, payload)
        payload = self.payload(text='改写过的文字')
        with self.assertRaisesMessage(ValueError, '原文位置'):
            save_source_annotation(self.task.pk, self.owner, payload)
        with self.assertRaises(ValueError):
            save_source_annotation(self.task.pk, self.owner, self.payload(start=True))
        self.assertFalse(self.task.annotations.exists())

    def test_saved_quote_is_editable_and_retains_code_definition_snapshot(self):
        payload = self.payload()
        first = save_source_annotation(self.task.pk, self.owner, payload)
        before = source_annotation_data(first)
        code = self.book.codes.first()
        code.name = '之后改名'
        code.save(update_fields=['name'])
        self.assertEqual(source_annotation_data(first)['code_names'], ['核对资料'])
        payload.update(revision=1, note='修改后的解释', token=uuid.uuid4().hex)
        edited = save_source_annotation(self.task.pk, self.owner, payload)
        self.assertEqual(edited.pk, first.pk)
        event = AuditEvent.objects.filter(object_type='SourceAnnotation').last()
        self.assertEqual(event.before['note'], before['note'])
        self.assertEqual(event.after['note'], '修改后的解释')

    def test_manual_completion_can_reopen_before_round_closes(self):
        annotation = save_source_annotation(self.task.pk, self.owner, self.payload())
        task = set_source_completed(self.task.pk, self.owner, {'completed': True, 'revision': 0})
        self.assertTrue(task.completed)
        with self.assertRaises(Conflict):
            save_source_annotation(task.pk, self.owner, self.payload(revision=annotation.revision))
        task = set_source_completed(task.pk, self.owner, {'completed': False, 'revision': 1})
        self.assertFalse(task.completed)
        save_source_annotation(task.pk, self.owner, self.payload(revision=annotation.revision))

    def test_finish_requires_material_completion_and_then_locks_coding(self):
        with self.assertRaisesMessage(ValueError, '所有材料'):
            finish_source_coding(self.round.pk, self.owner, 0)
        set_source_completed(self.task.pk, self.owner, {'completed': True, 'revision': 0})
        closed = finish_source_coding(self.round.pk, self.owner, 0)
        self.assertEqual(closed.state, 'closed')
        with self.assertRaises(Conflict):
            set_source_completed(self.task.pk, self.owner, {'completed': False, 'revision': 1})

    def test_http_annotations_and_history_are_private(self):
        own = save_source_annotation(self.task.pk, self.owner, self.payload())
        other_task = SourceCodingTask.objects.create(round=self.round, document=self.document, coder=self.coder)
        peer = save_source_annotation(other_task.pk, self.coder, self.payload())
        response = self.client.get(f'/api/source-tasks/{self.task.pk}/annotations/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([a['id'] for a in response.json()['annotations']], [own.pk])
        self.assertEqual(self.client.get(f'/api/source-tasks/{other_task.pk}/annotations/').status_code, 404)
        self.assertEqual(self.client.get(reverse('source_history', args=[peer.pk])).status_code, 404)
        history = self.client.get(reverse('source_history', args=[own.pk]))
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()['events']), 1)

    def test_http_save_retains_csrf_and_missing_membership_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        response = csrf_client.post(f'/api/source-tasks/{self.task.pk}/annotations/',
            json.dumps(self.payload()), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/api/source-tasks/{self.task.pk}/annotations/').status_code, 404)

    def test_export_only_contains_own_annotations_with_source_row_locations(self):
        save_source_annotation(self.task.pk, self.owner, self.payload(note='自己可见'))
        other_task = SourceCodingTask.objects.create(round=self.round, document=self.document, coder=self.coder)
        save_source_annotation(other_task.pk, self.coder, self.payload(note='其他人的答案'))
        response = self.client.get(reverse('source_export', args=[self.round.pk]) + '?format=csv')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8-sig')
        self.assertIn('自己可见', content)
        self.assertNotIn('其他人的答案', content)
        self.assertIn('1,3', content)

    def test_word_export_summarizes_own_references_and_distinct_sources_by_code(self):
        first = self.add_code()
        first.definition = '复核书面证据'
        first.save(update_fields=['definition'])
        second = self.add_code('依据')
        save_source_annotation(self.task.pk, self.owner, self.payload(end=8, primary=first.pk, secondary=[second.pk]))
        save_source_annotation(self.task.pk, self.owner, self.payload(start=2, end=10, primary=first.pk))
        save_source_annotation(self.task.pk, self.owner, self.payload(start=4, end=9, primary=None, no_code=True))
        other = SourceDocument.objects.create(project=self.project, name='另一个材料', raw=b'fiction',
            sha256=hashlib.sha256(b'fiction').hexdigest(), text_column='text')
        SourceRecord.objects.create(document=other, position=1, text=self.first.text)
        other_task = SourceCodingTask.objects.create(round=self.round, document=other, coder=self.owner)
        save_source_annotation(other_task.pk, self.owner, self.payload(end=8, primary=first.pk))
        peer_task = SourceCodingTask.objects.create(round=self.round, document=self.document, coder=self.coder)
        save_source_annotation(peer_task.pk, self.coder, self.payload(end=8, primary=first.pk))

        response = self.client.get(reverse('source_export', args=[self.round.pk]) + '?format=docx')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].endswith('wordprocessingml.document'))
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            document = ET.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        rows = [[''.join(node.text or '' for node in cell.findall('.//w:t', ns))
                 for cell in row.findall('w:tc', ns)] for row in document.findall('.//w:tbl/w:tr', ns)]
        self.assertEqual(rows, [['名称', '描述', '来源数', '引用数'],
                                ['核对资料', '复核书面证据', '2', '3'], ['依据', '', '1', '1']])

    def test_detail_exports_image_region_instead_of_text_offsets(self):
        picture = SourceDocument.objects.create(project=self.project, name='fiction.png', raw=b'picture fixture',
            sha256=hashlib.sha256(b'picture fixture').hexdigest(), text_column='', normalized={'type': 'image'})
        task = SourceCodingTask.objects.create(round=self.round, document=picture, coder=self.owner)
        region = {'x': 0.125, 'y': 0.25, 'width': 0.5, 'height': 0.375}
        save_source_annotation(self.task.pk, self.owner, self.payload(end=8))
        save_source_annotation(task.pk, self.owner, self.payload(region=region))

        response = self.client.get(reverse('source_export', args=[self.round.pk]) + '?format=json')

        self.assertEqual(response.status_code, 200)
        rows = {row['原文件']: row for row in response.json()['rows']}
        self.assertEqual(rows['fiction.png']['图像区域'], region)
        self.assertEqual(rows['fiction.png']['阅读文本起点'], '')
        self.assertEqual(rows['fiction.csv']['阅读文本起点'], 0)
        self.assertIsNone(rows['fiction.csv']['图像区域'])

    def test_parallel_rule_uses_independent_label_set(self):
        self.book.policy = 'parallel'
        self.book.save(update_fields=['policy'])
        a, b = self.add_code('第一类'), self.add_code('第二类')
        payload = self.payload(primary=None, secondary=[], labels=[b.pk, a.pk])
        saved = save_source_annotation(self.task.pk, self.owner, payload)
        self.assertIsNone(saved.primary_id)
        self.assertEqual(saved.secondary, [])
        self.assertEqual(set(saved.labels), {a.pk, b.pk})
