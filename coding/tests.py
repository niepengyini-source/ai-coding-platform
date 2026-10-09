"""Regression tests use only synthetic data, never the research folder's sources."""
import csv
import io
import json
import sqlite3
import tempfile
import uuid
import zipfile
import subprocess
import sys
from pathlib import Path
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, Client, override_settings
from .models import (Project, Membership, Profile, Codebook, Code, SourceDocument, SourceRecord, Unit,
                     Round, Assignment, Decision, Comment, AuditEvent, UploadDraft)
from .services import (create_round, clone_book, annotation_data, csv_bytes, project_archive)
from .importing import parse_upload
from .reliability import kappa, alpha, report_for
from .reviewing import disagreement_rows
from .round_forms import RoundForm

TEST_SETTINGS = {
    'PASSWORD_HASHERS': ['django.contrib.auth.hashers.MD5PasswordHasher'],
    'STORAGES': {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}


def fixture():
    User = get_user_model()
    owner = User.objects.create_user('test_owner', password='test-only-password')
    coder = User.objects.create_user('test_coder', password='test-only-password')
    outsider = User.objects.create_user('test_outsider', password='test-only-password')
    viewer = User.objects.create_user('test_viewer', password='test-only-password')
    project = Project.objects.create(name='通用访谈研究', goal='观察材料中的行为', owner=owner)
    for user, role in [(owner, 'owner'), (coder, 'coder'), (viewer, 'viewer')]:
        Membership.objects.create(project=project, user=user, role=role)
    book = Codebook.objects.create(project=project, version=1, policy='single')
    first = Code.objects.create(book=book, key='PLAN', name='计划', definition='确定目标和分工')
    second = Code.objects.create(book=book, key='CHECK', name='检查', definition='检查进度或结果')
    doc = SourceDocument.objects.create(project=project, name='synthetic.csv', raw=b'text\nhello\n', sha256='a' * 64, text_column='text')
    units = []
    for pos, (text, group) in enumerate([('我们先确定目标再分工', 'A'), ('检查进度然后调整', 'A'), ('=SYNTHETIC_FORMULA()', 'B')], 1):
        record = SourceRecord.objects.create(document=doc, position=pos, text=text, metadata={'interview': group, 'speaker': '虚构参与者'}, context_key=group)
        units.append(Unit.objects.create(record=record, text=text, end=len(text)))
    r = create_round(project, owner, book.id, '第一轮试编码', 'pilot', [owner.id, coder.id], 0, 2026, 'all', 25)
    other = Project.objects.create(name='另一个研究', owner=outsider)
    Membership.objects.create(project=other, user=outsider, role='owner')
    other_book = Codebook.objects.create(project=other, version=1)
    foreign_code = Code.objects.create(book=other_book, key='FOREIGN', name='外部代码', definition='不应进入其他项目')
    return dict(owner=owner, coder=coder, outsider=outsider, viewer=viewer, project=project, book=book,
                first=first, second=second, doc=doc, units=units, r=r, other=other, foreign_code=foreign_code)


class Helpers:
    def own_assignment(self, user=None, unit=None):
        return Assignment.objects.get(round=self.r, coder=user or self.owner, unit=unit or self.units[0])

    def save(self, a, user=None, **kwargs):
        self.client.force_login(user or a.coder)
        data = {'primary': self.first.id, 'secondary': [], 'uncertain': False, 'no_code': False,
                'note': '依据原话判断', 'revision': a.revision, 'action': 'save', 'token': uuid.uuid4().hex}
        data.update(kwargs)
        return self.client.post(f'/api/assignments/{a.id}/save/', json.dumps(data), content_type='application/json')

    def submit_all(self):
        for a in self.r.assignments.all():
            response = self.save(a, action='submit')
            assert response.status_code == 200, response.content

    def lock(self):
        self.submit_all()
        self.client.force_login(self.owner)
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': self.r.revision, 'action': 'lock'})
        self.r.refresh_from_db()

    def decide(self, unit=None, **kwargs):
        self.client.force_login(self.owner)
        data = {'revision': 0, 'primary': self.first.id, 'secondary': [], 'uncertain': False,
                'no_code': False, 'note': '两人核对原文和规则后确定'}
        data.update(kwargs)
        return self.client.post(f'/api/rounds/{self.r.id}/units/{(unit or self.units[0]).id}/decision/',
                                json.dumps(data), content_type='application/json')


@override_settings(**TEST_SETTINGS)
class PlatformTests(Helpers, TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        cache.clear()

    def test_anonymous_must_login(self):
        for url in ['/', f'/p/{self.project.id}/', f'/r/{self.r.id}/']:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_cross_project_access_denied(self):
        self.client.force_login(self.outsider)
        for url in [f'/p/{self.project.id}/', f'/p/{self.project.id}/units/',
                    f'/p/{self.project.id}/books/{self.book.id}/', f'/r/{self.r.id}/review/',
                    f'/r/{self.r.id}/report/', f'/r/{self.r.id}/export/all/', f'/p/{self.project.id}/archive/']:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_blind_phase_hides_other_answers_from_owner(self):
        other = self.own_assignment(self.coder)
        self.save(other, note='SECRET_OTHER_ANSWER')
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(f'/r/{self.r.id}/'), 'SECRET_OTHER_ANSWER')
        for suffix in ['review/', 'report/', 'export/all/']:
            self.assertEqual(self.client.get(f'/r/{self.r.id}/{suffix}').status_code, 403)
        archive = self.client.get(f'/p/{self.project.id}/archive/')
        self.assertEqual(archive.status_code, 302)
        self.assertNotIn(b'SECRET_OTHER_ANSWER', archive.content)

    def test_cannot_open_or_save_other_assignment(self):
        a = self.own_assignment(self.coder)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f'/r/{self.r.id}/?a={a.id}').status_code, 404)
        self.assertEqual(self.save(a, user=self.owner).status_code, 404)

    def test_own_save_and_separate_coder_records(self):
        first, second = self.own_assignment(), self.own_assignment(self.coder)
        self.assertEqual(self.save(first, primary=self.first.id).status_code, 200)
        self.assertEqual(self.save(second, primary=self.second.id).status_code, 200)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.primary_id, second.primary_id), (self.first.id, self.second.id))

    def test_stale_save_returns_conflict_without_overwrite(self):
        a = self.own_assignment()
        self.assertEqual(self.save(a, note='新记录').status_code, 200)
        self.assertEqual(self.save(a, note='旧页面覆盖').status_code, 409)
        a.refresh_from_db()
        self.assertEqual(a.note, '新记录')

    def test_replayed_request_is_idempotent(self):
        a = self.own_assignment()
        token = uuid.uuid4().hex
        self.assertEqual(self.save(a, token=token).status_code, 200)
        self.assertEqual(self.save(a, token=token).status_code, 200)
        a.refresh_from_db()
        self.assertEqual(a.revision, 1)
        self.assertEqual(AuditEvent.objects.filter(object_id=str(a.id), action='annotation.save').count(), 1)

    def test_submission_locks_answer(self):
        a = self.own_assignment()
        self.assertEqual(self.save(a, action='submit').status_code, 200)
        a.refresh_from_db()
        self.assertEqual(self.save(a).status_code, 409)

    def test_cross_book_codes_rejected(self):
        self.assertEqual(self.save(self.own_assignment(), primary=self.foreign_code.id).status_code, 400)

    def test_no_code_cannot_coexist_with_labels(self):
        self.assertEqual(self.save(self.own_assignment(), no_code=True).status_code, 400)

    def test_explicit_no_code_submission(self):
        a = self.own_assignment()
        self.assertEqual(self.save(a, primary=None, no_code=True, action='submit').status_code, 200)

    def test_empty_submission_rejected(self):
        self.assertEqual(self.save(self.own_assignment(), primary=None, action='submit').status_code, 400)

    def test_uncertain_requires_note(self):
        self.assertEqual(self.save(self.own_assignment(), primary=None, uncertain=True, note='', action='submit').status_code, 400)
        self.assertEqual(self.save(self.own_assignment(), primary=None, uncertain=True, note='含义不明确', action='submit').status_code, 200)

    def test_single_label_rejects_secondary(self):
        self.assertEqual(self.save(self.own_assignment(), secondary=[self.second.id]).status_code, 400)

    def test_duplicate_secondary_rejected(self):
        Codebook.objects.filter(id=self.book.id).update(policy='multi')
        self.assertEqual(self.save(self.own_assignment(), secondary=[self.second.id, self.second.id]).status_code, 400)

    def test_json_validation_and_post_only(self):
        a = self.own_assignment()
        self.client.force_login(self.owner)
        url = f'/api/assignments/{a.id}/save/'
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, '[]', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(url, '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.save(a, uncertain='false').status_code, 400)

    def test_csrf_enforced_for_mutation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        a = self.own_assignment()
        self.assertEqual(client.post(f'/api/assignments/{a.id}/save/', '{}', content_type='application/json').status_code, 403)

    def test_viewer_cannot_edit_or_see_active_review(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(f'/p/{self.project.id}/members/').status_code, 403)
        self.assertEqual(self.client.get(f'/r/{self.r.id}/review/').status_code, 403)
        self.assertEqual(self.client.get(f'/p/{self.project.id}/materials/').status_code, 403)

    def test_all_submissions_required_before_review(self):
        self.client.force_login(self.owner)
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': 0, 'action': 'lock'})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')
        self.lock()
        self.assertEqual(self.r.state, 'review')
        self.assertEqual(self.client.get(f'/r/{self.r.id}/review/').status_code, 200)

    def test_training_public_comparison_not_final_decision(self):
        Round.objects.filter(pk=self.r.id).update(kind='training')
        self.client.force_login(self.coder)
        self.assertEqual(self.client.get(f'/r/{self.r.id}/review/').status_code, 200)
        self.assertEqual(self.decide().status_code, 409)

    def test_decision_separate_and_conflict_safe(self):
        self.lock()
        self.assertEqual(self.decide(primary=self.second.id).status_code, 200)
        self.assertEqual(self.decide(primary=self.first.id).status_code, 409)
        self.assertTrue(self.r.assignments.filter(primary=self.first).exists())
        self.assertEqual(Decision.objects.get(round=self.r, unit=self.units[0]).primary_id, self.second.id)

    def test_decision_requires_reason_and_role(self):
        self.lock()
        self.assertEqual(self.decide(note='').status_code, 400)
        self.client.force_login(self.coder)
        url = f'/api/rounds/{self.r.id}/units/{self.units[0].id}/decision/'
        self.assertEqual(self.client.post(url, '{}', content_type='application/json').status_code, 403)

    def test_comments_only_when_visible(self):
        self.client.force_login(self.owner)
        url = f'/r/{self.r.id}/units/{self.units[0].id}/comments/'
        self.assertEqual(self.client.post(url, {'body': '禁止泄露'}).status_code, 403)
        self.lock()
        self.assertEqual(self.client.post(url, {'body': '规则边界讨论'}).status_code, 302)
        self.assertEqual(Comment.objects.count(), 1)

    def test_close_requires_every_valid_decision(self):
        self.lock()
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': self.r.revision, 'action': 'close'})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'review')
        for u in self.units:
            self.assertEqual(self.decide(unit=u).status_code, 200)
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': self.r.revision, 'action': 'close'})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'closed')
        self.assertEqual(self.decide(revision=1).status_code, 409)

    def test_reopen_keeps_history_and_marks_nonblind(self):
        self.lock()
        self.decide()
        before = AuditEvent.objects.count()
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': self.r.revision, 'action': 'reopen', 'reason': '练习修订'})
        self.r.refresh_from_db()
        self.assertEqual((self.r.state, self.r.cycle), ('active', 2))
        self.assertEqual(Decision.objects.count(), 1)
        self.assertGreater(AuditEvent.objects.count(), before)
        self.assertContains(self.client.get(f'/r/{self.r.id}/'), '不能再声称盲法独立')

    def test_reports_use_independent_snapshot(self):
        self.lock()
        self.decide(primary=self.second.id)
        self.client.post(f'/r/{self.r.id}/report/')
        report = self.r.reports.get()
        self.assertEqual(report.data['pairs'][0]['agreement'], 1)
        self.assertTrue(all(a['primary'] == self.first.id for a in report.data['snapshot']))
        self.assertEqual(self.client.get(f'/r/{self.r.id}/report/').status_code, 200)

    def test_frozen_book_immutable_and_clone_independent(self):
        self.client.force_login(self.owner)
        before = self.first.definition
        response = self.client.post(f'/p/{self.project.id}/books/{self.book.id}/', {
            'code_id': self.first.id, 'key': 'PLAN', 'name': '计划', 'definition': '偷偷改规则', 'revision': 1}, follow=True)
        self.assertContains(response, '已经冻结')
        self.first.refresh_from_db()
        self.assertEqual(self.first.definition, before)
        book = clone_book(self.project, self.owner, self.book, '修订版', 'multi', '补充边界案例')
        self.assertEqual(book.version, 2)
        self.assertEqual(book.codes.count(), self.book.codes.count())
        self.assertFalse(book.frozen)
        self.assertNotEqual(book.codes.first().id, self.book.codes.first().id)

    def test_split_preserves_existing_assignments(self):
        self.client.force_login(self.owner)
        u = self.units[0]
        old_text = u.text
        self.client.post(f'/p/{self.project.id}/units/{u.id}/', {'revision': 0, 'action': 'split', 'offset': 4, 'note': '分成两个行为'})
        u.refresh_from_db()
        self.assertFalse(u.active)
        self.assertEqual(self.own_assignment(unit=u).unit.text, old_text)
        children = Unit.objects.filter(record=u.record, active=True).order_by('start')
        self.assertEqual(''.join(c.text for c in children), old_text)

    def test_merge_and_exclude_keep_original(self):
        u = self.units[0]
        self.client.force_login(self.owner)
        self.client.post(f'/p/{self.project.id}/units/{u.id}/', {'revision': 0, 'action': 'split', 'offset': 4, 'note': '测试切分'})
        children = list(Unit.objects.filter(record=u.record, active=True).order_by('start'))
        self.client.post(f'/p/{self.project.id}/units/{children[0].id}/', {'revision': 0, 'action': 'merge', 'note': '恢复完整意义'})
        merged = Unit.objects.get(record=u.record, active=True)
        self.assertEqual(merged.text, u.text)
        self.client.post(f'/p/{self.project.id}/units/{merged.id}/', {'revision': 0, 'action': 'exclude', 'note': '不在纳入范围'})
        self.assertFalse(Unit.objects.filter(record=u.record, active=True).exists())
        self.assertEqual(SourceRecord.objects.get(pk=u.record_id).text, u.text)

    def test_import_preview_mapping_duplicate_protection(self):
        self.client.force_login(self.owner)
        raw = 'id,custom_group,body\n1,G1,示例文字\n2,G1,\n'.encode('utf-8')
        url = f'/p/{self.project.id}/materials/'
        result = self.client.post(url, {'file': SimpleUploadedFile('custom.csv', raw)})
        self.assertContains(result, '确认材料字段')
        draft = UploadDraft.objects.get()
        self.client.post(url, {'draft_id': str(draft.id), 'text_column': 'body', 'context_column': 'custom_group'})
        doc = SourceDocument.objects.get(name='custom.csv')
        self.assertEqual(bytes(doc.raw), raw)
        self.assertEqual(doc.records.count(), 2)
        self.assertEqual(Unit.objects.filter(record__document=doc).count(), 1)
        result = self.client.post(url, {'draft_id': str(draft.id), 'text_column': 'body', 'context_column': 'custom_group'})
        self.assertContains(result, '已导入')
        self.assertEqual(SourceDocument.objects.filter(name='custom.csv').count(), 1)

    def test_context_excludes_other_interview(self):
        self.client.force_login(self.owner)
        response = self.client.get(f'/r/{self.r.id}/?a={self.own_assignment().id}')
        context = response.context['context']
        self.assertTrue(all(r.context_key == 'A' for r in context))
        self.assertEqual(len(context), 2)

    def test_html_escapes_research_content(self):
        self.client.force_login(self.owner)
        Unit.objects.filter(pk=self.units[0].id).update(text='<script>INJECTED()</script>')
        response = self.client.get(f'/r/{self.r.id}/?a={self.own_assignment().id}')
        self.assertNotContains(response, '<script>INJECTED()</script>')
        self.assertContains(response, '&lt;script&gt;INJECTED()&lt;/script&gt;')

    def test_export_and_archive_scope(self):
        self.lock()
        for u in self.units:
            self.decide(unit=u)
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': self.r.revision, 'action': 'close'})
        self.r.refresh_from_db()
        response = self.client.get(f'/r/{self.r.id}/export/final/')
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 3)
        self.assertTrue(next(r for r in rows if 'SYNTHETIC' in r['unit_text'])['unit_text'].startswith("'="))
        data = self.client.get(f'/p/{self.project.id}/archive/').content
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('manifest.json', z.namelist())
            self.assertIn('99_logs/audit.json', z.namelist())
            self.assertFalse(any('password' in name or 'secret.key' in name for name in z.namelist()))
            self.assertEqual(z.read(f'00_raw/document_{self.doc.id}.csv'), bytes(self.doc.raw))
            self.assertNotIn('另一个研究', z.read('project.json').decode())

    def test_sample_seed_and_stratification(self):
        args = (self.project, self.owner, self.book.id, '再次抽样', 'pilot', [self.owner.id, self.coder.id], 2, 14, 'all', 25)
        one, two = create_round(*args, stratify='interview'), create_round(*args, stratify='interview')
        self.assertEqual(one.sampling['unit_ids'], two.sampling['unit_ids'])
        chosen = Unit.objects.filter(id__in=one.sampling['unit_ids'])
        self.assertEqual({u.record.metadata['interview'] for u in chosen}, {'A', 'B'})

    def test_mixed_tasks_and_single_coder_report(self):
        mixed = create_round(self.project, self.owner, self.book.id, '混合', 'formal', [self.owner.id, self.coder.id], 0, 20, 'mixed', 50)
        self.assertEqual(mixed.assignments.count(), 5)
        single = create_round(self.project, self.owner, self.book.id, '单人', 'formal', [self.owner.id], 0, 20, 'distributed', 0)
        data = report_for(single)
        self.assertEqual(data['pairs'], [])
        self.assertIsNone(data['nominal_alpha'])

    def test_login_throttle_and_security_headers(self):
        for _ in range(10):
            self.client.post('/login/', {'username': 'bad', 'password': 'bad'})
        self.assertEqual(self.client.post('/login/', {'username': 'bad', 'password': 'bad'}).status_code, 429)
        response = self.client.get('/login/')
        self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_forced_first_password_change(self):
        Profile.objects.create(user=self.owner, must_change_password=True)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/').url, '/accounts/password/')
        self.assertEqual(self.client.get('/accounts/password/').status_code, 200)

    def test_primary_pages_render(self):
        self.client.force_login(self.owner)
        for url in ['/', '/guide/', '/projects/new/', f'/p/{self.project.id}/', f'/p/{self.project.id}/members/',
                    f'/p/{self.project.id}/units/', f'/p/{self.project.id}/materials/',
                    f'/p/{self.project.id}/books/{self.book.id}/', f'/p/{self.project.id}/rounds/new/', f'/r/{self.r.id}/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_manual_freeze_and_delete_draft_code(self):
        book = clone_book(self.project, self.owner, self.book, '草稿修改', 'single', '删除无关分类')
        removed = book.codes.first()
        self.client.force_login(self.owner)
        self.client.post(f'/p/{self.project.id}/codes/{removed.id}/remove/', {'revision': 0})
        self.assertFalse(Code.objects.filter(pk=removed.pk).exists())
        book.refresh_from_db()
        self.client.post(f'/p/{self.project.id}/books/{book.id}/freeze/', {'revision': book.revision})
        book.refresh_from_db()
        self.assertTrue(book.frozen)
        remaining = book.codes.first()
        self.client.post(f'/p/{self.project.id}/codes/{remaining.id}/remove/', {'revision': book.revision})
        self.assertTrue(Code.objects.filter(pk=remaining.id).exists())

    def test_reopen_single_task_preserves_answer_and_revision(self):
        a = self.own_assignment(self.coder)
        self.save(a, action='submit', note='误提交但保留这个判断')
        a.refresh_from_db()
        old_revision = a.revision
        self.client.force_login(self.owner)
        response = self.client.get(f'/r/{self.r.id}/')
        self.assertNotContains(response, '误提交但保留这个判断')
        self.client.post(f'/assignments/{a.id}/reopen/', {'revision': old_revision, 'reason': '本人要求补充依据'})
        a.refresh_from_db()
        self.assertEqual(a.status, 'draft')
        self.assertEqual(a.revision, old_revision + 1)
        self.assertEqual(a.note, '误提交但保留这个判断')
        self.assertEqual(self.save(a, revision=old_revision).status_code, 409)

    def test_progress_api_contains_no_labels_or_notes(self):
        self.save(self.own_assignment(self.coder), note='SECRET_PROGRESS')
        self.client.force_login(self.owner)
        response = self.client.get(f'/api/rounds/{self.r.id}/progress/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'state', 'my_total', 'my_done', 'total', 'done'})
        self.assertNotContains(response, 'SECRET_PROGRESS')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/api/rounds/{self.r.id}/progress/').status_code, 404)

    def test_primary_secondary_swap_not_hidden_by_set_agreement(self):
        Codebook.objects.filter(pk=self.book.id).update(policy='multi')
        for unit in self.units:
            self.save(self.own_assignment(unit=unit), primary=self.first.id, secondary=[self.second.id], action='submit')
            self.save(self.own_assignment(self.coder, unit=unit), primary=self.second.id, secondary=[self.first.id], action='submit')
        self.r.refresh_from_db()
        data = report_for(self.r)
        self.assertEqual(data['pairs'][0]['agreement'], 1)
        self.assertEqual(data['pairs'][0]['primary_agreement'], 0)

    def test_code_layer_cycle_rejected(self):
        book = clone_book(self.project, self.owner, self.book, '草稿', 'single', '调整层级')
        parent, child = list(book.codes.all())
        child.parent = parent
        child.save()
        self.client.force_login(self.owner)
        response = self.client.post(f'/p/{self.project.id}/books/{book.id}/', {'code_id': parent.id, 'key': parent.key,
            'name': parent.name, 'definition': parent.definition, 'parent': child.id, 'revision': 0})
        self.assertContains(response, '不能形成循环')

    def test_project_description_conflict_preserves_first_change(self):
        self.client.force_login(self.owner)
        url = f'/p/{self.project.id}/'
        self.client.post(url, {'name': '新名称', 'goal': '新目标', 'description': '范围', 'revision': 0})
        response = self.client.post(url, {'name': '旧窗口', 'goal': '旧目标', 'description': '', 'revision': 0})
        self.assertContains(response, '输入保留')
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, '新名称')

    def test_malformed_object_ids_are_not_server_errors(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f'/r/{self.r.id}/?a=bad-id').status_code, 404)
        self.assertEqual(self.client.post(f'/p/{self.project.id}/materials/', {'draft_id': 'not-uuid'}).status_code, 404)
        self.assertEqual(self.client.post(f'/p/{self.project.id}/books/new/', {'source_id': 'bad-id'}).status_code, 404)

    def test_export_has_flat_custom_fields_and_source_span(self):
        from .services import export_annotations
        result = export_annotations(self.r, self.r.assignments.all())
        rows = list(csv.DictReader(io.StringIO(result.decode('utf-8-sig'))))
        self.assertIn('meta_interview', rows[0])
        self.assertIn('meta_speaker', rows[0])
        self.assertIn('span_start', rows[0])
        self.assertEqual(rows[0]['meta_speaker'], '虚构参与者')

    def test_disagreement_queue_and_csv_blind_permissions(self):
        self.save(self.own_assignment(self.coder), note='PRIVATE_BEFORE_LOCK')
        for user, status in [(self.owner, 403), (self.coder, 403), (self.viewer, 403), (self.outsider, 404)]:
            self.client.force_login(user)
            for suffix in ['disagreements/', 'export/disagreements/']:
                response = self.client.get(f'/r/{self.r.id}/{suffix}')
                self.assertEqual(response.status_code, status)
                self.assertNotIn(b'PRIVATE_BEFORE_LOCK', response.content)

    def test_disagreement_queue_swap_and_uncertainty_are_visible_after_lock(self):
        Codebook.objects.filter(pk=self.book.id).update(policy='multi')
        self.lock()
        other = self.own_assignment(self.coder)
        Assignment.objects.filter(pk=self.own_assignment().id).update(secondary=[self.second.id])
        Assignment.objects.filter(pk=other.id).update(primary=self.second, secondary=[self.first.id], note='交换主次代码')
        Assignment.objects.filter(pk=self.own_assignment(unit=self.units[1]).id).update(uncertain=True, note='上下文不足')
        response = self.client.get(f'/r/{self.r.id}/disagreements/')
        self.assertContains(response, '主次代码互换')
        self.assertContains(response, '有人标记不确定')
        self.assertEqual(response.context['counts']['attention'], 2)
        exported = self.client.get(f'/r/{self.r.id}/export/disagreements/')
        rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 2)
        self.assertIn('primary_secondary_swap', rows[0]['disagreement_types'])
        self.assertIn('交换主次代码', rows[0]['independent_labels_json'])

    def test_queue_final_status_never_rewrites_independent_answers(self):
        self.lock()
        self.decide()
        response = self.client.get(f'/r/{self.r.id}/disagreements/?filter=undecided')
        self.assertEqual(response.context['counts']['undecided'], 2)
        self.assertEqual(self.r.assignments.filter(primary=self.first).count(), 6)
        self.client.post(f'/r/{self.r.id}/manage/', {'revision': self.r.revision, 'action': 'reopen', 'reason': '重新学习规则'})
        self.r.refresh_from_db()
        self.assertEqual(self.r.decisions.count(), 1)
        self.assertTrue(all(not row['resolved'] for row in disagreement_rows(self.r)))

    def test_queue_training_and_single_coder_do_not_claim_agreement(self):
        training = create_round(self.project, self.owner, self.book.id, '共同学习', 'training', [self.owner.id], 0, 23, 'all', 0)
        self.client.force_login(self.owner)
        response = self.client.get(f'/r/{training.id}/disagreements/')
        self.assertContains(response, '尚未提交（培训练习）')
        self.assertEqual(self.client.get(f'/r/{training.id}/export/disagreements/').status_code, 200)
        training.assignments.update(primary=self.first, status='submitted')
        response = self.client.get(f'/r/{training.id}/disagreements/?filter=all')
        self.assertContains(response, '单人编码，无法比较')
        self.assertEqual(response.context['counts']['attention'], 0)

    def test_review_opens_exact_queued_unit_and_rejects_foreign_id(self):
        self.lock()
        response = self.client.get(f'/r/{self.r.id}/review/?unit={self.units[2].id}')
        self.assertEqual(response.context['unit'].id, self.units[2].id)
        for value in ['bad', '0', '99999999']:
            self.assertEqual(self.client.get(f'/r/{self.r.id}/review/?unit={value}').status_code, 404)

    def test_hierarchical_report_uses_explicit_top_level_and_preserves_labels(self):
        top = Code.objects.create(book=self.book, key='REGULATION', name='调节', definition='显式一级类别')
        Code.objects.filter(id__in=[self.first.id, self.second.id]).update(parent=top)
        self.lock()
        Assignment.objects.filter(pk=self.own_assignment(self.coder).id).update(primary=self.second)
        self.r.refresh_from_db()
        data = report_for(self.r)
        self.assertEqual(data['pairs'][0]['primary_agreement'], .666667)
        self.assertEqual(data['pairs'][0]['dimension_agreement'], 1)
        self.assertIsNone(data['pairs'][0]['dimension_kappa'])  # Only one top-level category.
        self.assertIsNone(data['dimension_alpha'])
        self.assertEqual(len(data['hierarchy_snapshot']), 3)
        self.assertEqual(self.own_assignment(self.coder).primary_id, self.second.id)
        self.client.post(f'/r/{self.r.id}/report/')
        self.assertContains(self.client.get(f'/r/{self.r.id}/report/'), '一级维度：主代码所属的最顶层类别')

    def test_flat_code_names_never_imply_a_hierarchy(self):
        Code.objects.filter(pk=self.first.pk).update(key='A1')
        Code.objects.filter(pk=self.second.pk).update(key='A2')
        self.lock()
        data = report_for(self.r)
        self.assertFalse(data['has_hierarchy'])
        self.assertIsNone(data['dimension_alpha'])
        self.assertIsNone(data['pairs'][0]['dimension_agreement'])

    def test_report_secondary_and_uncertain_denominators(self):
        Codebook.objects.filter(pk=self.book.id).update(policy='multi')
        self.lock()
        Assignment.objects.filter(pk=self.own_assignment(self.coder).id).update(secondary=[self.second.id])
        Assignment.objects.filter(pk=self.own_assignment(unit=self.units[1]).id).update(uncertain=True, note='需复核')
        self.r.refresh_from_db()
        data = report_for(self.r)
        self.assertEqual(data['pairs'][0]['n'], 2)
        self.assertEqual(data['pairs'][0]['secondary_agreement'], .5)
        self.assertEqual(data['uncertain_rate'], .166667)
        self.assertEqual(data['assignment_count'], 6)

    def test_specified_sample_pool_is_recorded_and_repeatable(self):
        args = (self.project, self.owner, self.book.id, '针对分歧再次试编码', 'pilot', [self.owner.id, self.coder.id], 0, 45, 'all', 0)
        ids = [self.units[0].id, self.units[2].id]
        one = create_round(*args, unit_ids=ids)
        two = create_round(*args, unit_ids=ids)
        self.assertEqual(one.sampling['unit_ids'], two.sampling['unit_ids'])
        self.assertEqual(one.sampling['specified_pool_ids'], sorted(ids))
        self.assertEqual(set(one.assignments.values_list('unit_id', flat=True)), set(ids))
        self.assertEqual(one.assignments.count(), 4)

    def test_new_round_remembers_previously_visible_units(self):
        self.lock()
        repeated = create_round(self.project, self.owner, self.book.id, '旧样本复测', 'pilot',
                                [self.owner.id, self.coder.id], 0, 45, 'all', 0, unit_ids=[self.units[0].id])
        self.assertEqual(repeated.sampling['previously_visible_unit_ids'], [self.units[0].id])
        repeated.assignments.update(primary=self.first, status='submitted')
        repeated.state = 'review'; repeated.save()
        data = report_for(repeated)
        self.assertEqual(data['previously_visible_unit_count'], 1)
        self.assertTrue(data['sample_history_recorded'])
        self.client.force_login(self.owner)
        self.client.post(f'/r/{repeated.id}/report/')
        self.assertContains(self.client.get(f'/r/{repeated.id}/report/'), '不能抹掉先前记忆')

    def test_specified_sample_rejects_foreign_inactive_and_oversized_pool(self):
        args = (self.project, self.owner, self.book.id, '错误抽样', 'pilot', [self.owner.id], 0, 45, 'all', 0)
        Unit.objects.filter(pk=self.units[1].id).update(active=False)
        count = self.project.rounds.count()
        for ids in [[self.units[1].id], [99999999]]:
            with self.assertRaisesMessage(ValueError, '当前有效'):
                create_round(*args, unit_ids=ids)
        with self.assertRaisesMessage(ValueError, '超过'):
            create_round(*args[:6], 3, *args[7:], unit_ids=[self.units[0].id])
        self.assertEqual(self.project.rounds.count(), count)

    def test_round_form_parses_custom_unit_ids_and_reports_bad_input(self):
        data = {'name': '再次试编码', 'kind': 'pilot', 'book_id': self.book.id, 'coder_ids': [self.owner.id],
                'count': 0, 'seed': 10, 'stratify': '', 'distribution': 'all', 'double_percent': 0,
                'unit_ids': f'{self.units[0].id}， {self.units[2].id}'}
        form = RoundForm(data, project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['unit_ids'], [self.units[0].id, self.units[2].id])
        for value in ['1,1', '-1', 'abc', '1' * 5000]:
            data['unit_ids'] = value
            form = RoundForm(data, project=self.project)
            self.assertFalse(form.is_valid())
            self.assertIn('unit_ids', form.errors)

    def test_workbench_navigation_stays_available_without_side_task_list(self):
        self.client.force_login(self.owner)
        tasks = list(self.r.assignments.filter(coder=self.owner).order_by('id'))
        response = self.client.get(f'/r/{self.r.id}/?a={tasks[1].id}')
        self.assertContains(response, '切换我的编码任务')
        self.assertEqual(response.context['previous_task'].id, tasks[0].id)
        self.assertEqual(response.context['next_task'].id, tasks[2].id)
        self.assertNotEqual(response.context['next_draft'].id, tasks[1].id)

    def test_workbench_page_two_opens_a_task_from_that_page(self):
        for position in range(4, 57):
            record = SourceRecord.objects.create(document=self.doc, position=position, text='虚构分页单元')
            unit = Unit.objects.create(record=record, text=record.text, end=len(record.text))
            Assignment.objects.create(round=self.r, unit=unit, coder=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get(f'/r/{self.r.id}/?page=2')
        self.assertEqual(response.context['tasks'].number, 2)
        self.assertIn(response.context['assignment'], list(response.context['tasks']))
        last = self.r.assignments.filter(coder=self.owner).order_by('id').last()
        response = self.client.get(f'/r/{self.r.id}/?a={last.id}')
        self.assertEqual(response.context['tasks'].number, 2)
        self.assertEqual(response.context['assignment'].id, last.id)


@override_settings(**TEST_SETTINGS)
class UtilityTests(TestCase):
    def test_service_lock_rejects_duplicate_process_and_releases(self):
        from service_lock import ServiceLock
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.lock'
            script = 'import sys; from service_lock import ServiceLock; lock=ServiceLock(sys.argv[1]); lock.__enter__(); lock.close()'
            with ServiceLock(path):
                blocked = subprocess.run([sys.executable, '-c', script, str(path)], cwd=settings.BASE_DIR,
                                         capture_output=True, timeout=10)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(b'AlreadyRunning', blocked.stderr)
            allowed = subprocess.run([sys.executable, '-c', script, str(path)], cwd=settings.BASE_DIR,
                                     capture_output=True, timeout=10)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_service_lock_is_released_when_process_exits_abruptly(self):
        from service_lock import ServiceLock
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.lock'
            script = 'import os,sys; from service_lock import ServiceLock; lock=ServiceLock(sys.argv[1]); lock.__enter__(); os._exit(0)'
            exited = subprocess.run([sys.executable, '-c', script, str(path)], cwd=settings.BASE_DIR,
                                    capture_output=True, timeout=10)
            self.assertEqual(exited.returncode, 0, exited.stderr)
            with ServiceLock(path):
                pass

    def test_service_lock_keeps_separate_ports_independent(self):
        from service_lock import ServiceLock
        with tempfile.TemporaryDirectory() as directory:
            with ServiceLock(Path(directory) / '8000.lock'), ServiceLock(Path(directory) / '8001.lock'):
                pass

    def test_known_kappa_and_alpha(self):
        left = [1] * 5 + [2] * 5
        right = [1] * 4 + [2, 1] + [2] * 4
        self.assertEqual(kappa(left, right), .6)
        self.assertEqual(alpha([left, right]), .62)

    def test_degenerate_metrics_undefined(self):
        self.assertIsNone(kappa([], []))
        self.assertIsNone(kappa([1, 1], [1, 1]))
        self.assertIsNone(alpha([[1, 1], [1, 1]]))
        self.assertIsNone(alpha([[1, float('nan')], [float('nan'), 2]]))

    def test_csv_utf8_and_legacy_chinese(self):
        for encoding in ['utf-8-sig', 'gb18030']:
            headers, rows = parse_upload('sample.csv', '编号,文字\n1,中文示例\n'.encode(encoding))
            self.assertEqual(headers, ['编号', '文字'])
            self.assertEqual(rows[0]['文字'], '中文示例')

    def test_invalid_imports(self):
        for filename, raw in [('bad.csv', b'a,a\n1,2'), ('bad.csv', b'a,b\n1,2,3'), ('bad.xlsx', b'broken'), ('old.xls', b'legacy')]:
            with self.assertRaises(ValueError):
                parse_upload(filename, raw)

    def test_xlsx_roundtrip(self):
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(['custom_id', 'body']); ws.append([1, '虚构文本'])
        stream = io.BytesIO(); wb.save(stream)
        headers, rows = parse_upload('one.xlsx', stream.getvalue())
        self.assertEqual(rows[0]['body'], '虚构文本')

    def test_xlsx_rejects_malformed_xml_and_dtd_before_loading(self):
        from openpyxl import Workbook
        wb = Workbook(); wb.active.append(['text']); wb.active.append(['纯虚构测试'])
        original = io.BytesIO(); wb.save(original)
        for replacement in [b'<broken', b'<!DOCTYPE book [<!ENTITY sample "test">]><book>&sample;</book>']:
            altered = io.BytesIO()
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(altered, 'w', zipfile.ZIP_DEFLATED) as target:
                for entry in source.infolist():
                    target.writestr(entry.filename, replacement if entry.filename == 'xl/workbook.xml' else source.read(entry))
            with self.assertRaisesMessage(ValueError, 'Excel文件损坏'):
                parse_upload('damaged.xlsx', altered.getvalue())

    def test_csv_rejects_malformed_quotes_and_oversized_header_safely(self):
        for raw in [b'text\n"unclosed', ('x' * 200001 + '\nvalue').encode()]:
            with self.assertRaisesMessage(ValueError, 'CSV格式错误'):
                parse_upload('damaged.csv', raw)

    def test_formula_export_escaped(self):
        result = csv_bytes(['text'], [['=1+1'], ['  @SUM(A1)'], ['ordinary']]).decode('utf-8-sig')
        self.assertIn("'=1+1", result)
        self.assertIn("'  @SUM", result)

    def test_online_backup_restores_independent_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            conn = sqlite3.connect(source)
            conn.execute('CREATE TABLE synthetic(id INTEGER PRIMARY KEY, text TEXT)')
            conn.execute('INSERT INTO synthetic(text) VALUES (?)', ('仅测试数据',))
            conn.commit()
            with patch.dict(settings.DATABASES, {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': source}}):
                call_command('backup', output_dir=directory, stdout=io.StringIO())
            saved = next(Path(directory).glob('platform_*.sqlite3'))
            restored = sqlite3.connect(saved)
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(restored.execute('SELECT text FROM synthetic').fetchone()[0], '仅测试数据')
            restored.close(); conn.close()


@override_settings(**TEST_SETTINGS)
class ConcurrentTests(Helpers, TransactionTestCase):
    def setUp(self):
        for name, value in fixture().items():
            setattr(self, name, value)

    def concurrent_saves(self, assignments):
        clients = []
        payloads = []
        for i, a in enumerate(assignments):
            client = Client(); client.force_login(a.coder)
            clients.append(client)
            payloads.append({'revision': 0, 'primary': self.first.id if i == 0 else self.second.id,
                'secondary': [], 'note': f'并发输入{i}', 'uncertain': False, 'no_code': False,
                'action': 'save', 'token': uuid.uuid4().hex})
        barrier = Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return clients[index].post(f'/api/assignments/{assignments[index].id}/save/',
                    json.dumps(payloads[index]), content_type='application/json').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, [0, 1]))

    def test_two_windows_only_one_stale_revision_wins(self):
        a = self.own_assignment()
        results = self.concurrent_saves([a, a])
        self.assertEqual(sorted(results), [200, 409])
        a.refresh_from_db()
        self.assertEqual(a.revision, 1)

    def test_two_coders_same_unit_both_saved(self):
        results = self.concurrent_saves([self.own_assignment(), self.own_assignment(self.coder)])
        self.assertEqual(results, [200, 200])
        self.assertEqual(set(self.r.assignments.filter(unit=self.units[0]).values_list('primary_id', flat=True)), {self.first.id, self.second.id})

    def test_concurrent_decision_one_revision_wins(self):
        self.lock()
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        clients = [Client(), Client()]
        clients[0].force_login(self.owner); clients[1].force_login(self.coder)
        barrier = Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                payload = {'revision': 0, 'primary': self.first.id if index == 0 else self.second.id,
                           'secondary': [], 'no_code': False, 'uncertain': False, 'note': f'协商依据{index}'}
                return clients[index].post(f'/api/rounds/{self.r.id}/units/{self.units[0].id}/decision/',
                                          json.dumps(payload), content_type='application/json').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [0, 1]))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(Decision.objects.count(), 1)
