import hashlib
import json
import io
import zipfile
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import Assignment, AuditEvent, Code, Codebook, Membership, Project, Round, SourceDocument, SourceRecord, Unit
from .services import create_round, save_assignment
from .tests import TEST_SETTINGS


@override_settings(**TEST_SETTINGS)
class OpenEntryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('open_entry_owner', password='FictionalPass2026')
        self.project = Project.objects.create(name='虚构开放编码', owner=self.owner)
        Membership.objects.create(project=self.project, user=self.owner, role='owner')
        self.book = Codebook.objects.create(project=self.project, version=1)
        raw = b'text\nfictional\n'
        self.doc = SourceDocument.objects.create(project=self.project, name='fiction.csv', raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(), text_column='text')
        self.record = SourceRecord.objects.create(document=self.doc, position=1, text='先核对证据，再讨论结论。')
        self.unit = Unit.objects.create(record=self.record, text=self.record.text, end=len(self.record.text))
        self.client.force_login(self.owner)

    def start(self):
        response = self.client.post(f'/p/{self.project.pk}/quick-start/',
            {'book_id': self.book.pk, 'token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        return Round.objects.get(project=self.project)

    def test_empty_book_can_start_with_existing_units_and_add_first_code(self):
        response = self.client.get(f'/p/{self.project.pk}/code/')
        self.assertRedirects(response, f'/p/{self.project.pk}/quick-start/')
        r = self.start()
        self.assertTrue(r.sampling['open_coding'])
        self.book.refresh_from_db()
        self.assertFalse(self.book.frozen)
        a = Assignment.objects.get(round=r)
        response = self.client.get(f'/r/{r.pk}/')
        self.assertContains(response, 'workspace-code-create')
        response = self.client.post(f'/api/source-rounds/{r.pk}/codes/', json.dumps({
            'name': '核对依据', 'definition': '', 'book_id': self.book.pk, 'revision': self.book.revision}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        code = response.json()['code']
        self.assertEqual(Code.objects.get(pk=code['id']).name, '核对依据')
        saved = save_assignment(a.pk, self.owner, {'revision': 0, 'token': uuid.uuid4().hex,
            'book_id': self.book.pk, 'primary': code['id'], 'secondary': [], 'no_code': False,
            'uncertain': False, 'note': '', 'action': 'submit'})
        self.assertEqual(saved.status, 'submitted')
        event = AuditEvent.objects.get(action='annotation.submit', object_id=str(a.pk))
        self.assertIn('selected_code_snapshot', event.after)

    def test_fixed_research_round_still_requires_rules(self):
        with self.assertRaisesMessage(ValueError, '至少一个代码'):
            create_round(self.project, self.owner, self.book.pk, '统一任务', 'formal', [self.owner.pk], 0, 1, 'all', 0)
        self.assertFalse(Round.objects.exists())

    def test_document_does_not_require_manual_presegmentation(self):
        self.unit.active = False
        self.unit.save(update_fields=['active'])
        self.doc.units_confirmed = False
        self.doc.normalized = {'type': 'document', 'presentation': 'document', 'text': self.record.text,
            'blocks': [{'id': '1', 'text': self.record.text, 'start': 0, 'end': len(self.record.text), 'page': 1}]}
        self.doc.save(update_fields=['normalized', 'units_confirmed'])
        r = self.start()
        self.assertTrue(r.sampling['source_coding'])
        self.assertEqual(r.source_tasks.count(), 1)
        self.assertEqual(r.assignments.count(), 0)

    def test_existing_ready_book_keeps_normal_task_path(self):
        Code.objects.create(book=self.book, key='CHECK', name='核对', definition='核验依据')
        r = self.start()
        self.assertFalse(r.sampling.get('source_coding', False))
        self.assertFalse(r.sampling.get('open_coding', False))
        self.assertEqual(r.assignments.count(), 1)
        self.book.refresh_from_db()
        self.assertTrue(self.book.frozen)

    def test_changing_policy_after_a_saved_answer_keeps_old_round_rules(self):
        Code.objects.create(book=self.book, key='CHECK', name='核对', definition='核验依据')
        r = self.start()
        self.book.refresh_from_db()
        response = self.client.post(f'/p/{self.project.pk}/books/{self.book.pk}/policy/',
            {'revision': str(self.book.revision), 'policy': 'parallel'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project.codebooks.first().policy, 'parallel')
        r.refresh_from_db()
        self.assertEqual(r.book_id, self.book.pk)

    def test_source_results_are_in_archive_exports_history_and_project_progress(self):
        from .source_coding import save_source_annotation, start_source_coding
        from .services import project_archive
        from .source_models import SourceAnnotation
        r, _ = start_source_coding(self.project, self.owner, self.book.pk)
        task = r.source_tasks.get()
        code = Code.objects.create(book=self.book, key='CHECK', name='核对', definition='核验依据')
        for start, end in [(0, 5), (2, 8)]:
            save_source_annotation(task.pk, self.owner, {'start': start, 'end': end,
                'text': self.record.text[start:end], 'primary': code.pk, 'secondary': [],
                'note': '虚构保留依据', 'no_code': False, 'uncertain': False, 'revision': 0,
                'book_id': self.book.pk, 'token': uuid.uuid4().hex})
        with zipfile.ZipFile(io.BytesIO(project_archive(self.project))) as archive:
            names = archive.namelist()
            path = next(path for path in names if path.endswith('_source_annotations.json'))
            self.assertEqual(len(json.loads(archive.read(path))), 2)
            self.assertIn('book_snapshot', json.loads(archive.read(path))[0])
        for name in ['exports', 'archive', 'history', 'progress']:
            response = self.client.get(f'/p/{self.project.pk}/{name}/')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '原文选段')
        response = self.client.get('/projects/')
        member = response.context['memberships'][0]
        self.assertEqual(member.card_progress['total'], 1)
        self.assertEqual(member.card_progress['label'], '我的材料进度')
        self.assertEqual(member.personal_task_url, f'/r/{r.pk}/')
        self.assertEqual(SourceAnnotation.objects.count(), 2)

    def test_source_work_keeps_old_tasks_answers_and_history_reachable(self):
        from .source_coding import save_source_annotation, start_source_coding
        code = Code.objects.create(book=self.book, key='CHECK', name='核对', definition='核验依据')
        old_round = self.start()
        entry = old_round.assignments.get()
        save_assignment(entry.pk, self.owner, {'primary': code.pk, 'secondary': [], 'uncertain': False,
            'no_code': False, 'note': '虚构旧编码记录应保留', 'action': 'save', 'revision': 0,
            'token': uuid.uuid4().hex})
        snapshot = Assignment.objects.filter(pk=entry.pk).values().get()
        source_round, _ = start_source_coding(self.project, self.owner, self.book.pk)
        source_code = source_round.book.codes.get(key=code.key)
        save_source_annotation(source_round.source_tasks.get().pk, self.owner, {
            'start': 0, 'end': 5, 'text': self.record.text[:5], 'primary': source_code.pk,
            'secondary': [], 'uncertain': False, 'no_code': False, 'note': '虚构新片段',
            'revision': 0, 'book_id': source_round.book_id, 'token': uuid.uuid4().hex})
        page = self.client.get(f'/r/{source_round.pk}/')
        self.assertContains(page, f'href="/p/{self.project.pk}/#project-rounds">全部编码任务')
        self.assertContains(page, f'href="/p/{self.project.pk}/history/"')
        page = self.client.get(f'/p/{self.project.pk}/')
        self.assertEqual(set(page.context['rounds'].values_list('pk', flat=True)), {old_round.pk, source_round.pk})
        self.assertContains(page, '逐条编码')
        self.assertContains(page, '原文选段')
        self.assertContains(self.client.get(f'/p/{self.project.pk}/history/'), '虚构旧编码记录应保留')
        self.assertEqual(self.client.get(f'/r/{old_round.pk}/').context['annotation']['note'], '虚构旧编码记录应保留')
        self.assertEqual(Assignment.objects.filter(pk=entry.pk).values().get(), snapshot)
        old_round.refresh_from_db()
        self.assertEqual(old_round.book_id, self.book.pk)
