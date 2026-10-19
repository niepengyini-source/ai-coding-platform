"""Stale import tabs must not replace or modify historical codebooks."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .book_updates import begin_update
from .codebook_files import apply_codebook_import
from .models import AuditEvent, Code, Codebook, CodebookImportDraft
from .services import Conflict
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class CurrentBookImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.book.refresh_from_db()

    def url(self, book):
        return reverse('book_import', args=[self.project.pk, book.pk])

    def preview(self, book, mode='new'):
        response = self.client.post(self.url(book), {'action': 'upload',
            'file': SimpleUploadedFile('fictional.csv', b'key,name,definition\nIMPORTED,Imported,Fictional definition\n')})
        self.assertEqual(response.status_code, 302)
        draft = CodebookImportDraft.objects.filter(book=book).latest('created_at')
        response = self.client.post(self.url(book), {'action': 'preview', 'draft_id': str(draft.pk),
            'mode': mode, 'title': '虚构导入规则', 'policy': book.policy, 'change_note': '虚构导入测试',
            'map_key': 'key', 'map_name': 'name', 'map_definition': 'definition'})
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        return draft

    def confirm(self, book, draft):
        return self.client.post(self.url(book), {'action': 'confirm', 'draft_id': str(draft.pk),
                                                'confirmed': 'yes'})

    def assert_not_applied(self, draft, current, book_count, code_count):
        draft.refresh_from_db()
        self.assertIsNone(draft.applied_book_id)
        self.assertEqual(self.project.codebooks.first().pk, current.pk)
        self.assertEqual(self.project.codebooks.count(), book_count)
        self.assertEqual(Code.objects.filter(book__project=self.project).count(), code_count)
        self.assertFalse(AuditEvent.objects.filter(project=self.project, action='codebook.import').exists())

    def test_stale_new_preview_cannot_displace_updated_current_book(self):
        draft = self.preview(self.book)
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        Code.objects.create(book=current, key='KEEP', name='新规则', definition='不能被旧页面覆盖')
        counts = self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()
        with self.assertRaisesMessage(Conflict, '当前编码本已变化'):
            apply_codebook_import(draft.pk, self.owner, self.book)
        self.assert_not_applied(draft, current, *counts)
        self.assertTrue(current.codes.filter(key='KEEP').exists())

    def test_stale_append_preview_cannot_modify_historical_editable_book(self):
        old = begin_update(self.project, self.owner, self.book, self.book.revision)
        draft = self.preview(old, 'append')
        current = Codebook.objects.create(project=self.project, version=3, title='虚构当前编码本')
        counts = self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()
        before = list(old.codes.values())
        response = self.confirm(old, draft)
        self.assertContains(response, '当前编码本已变化')
        self.assertEqual(list(old.codes.values()), before)
        self.assert_not_applied(draft, current, *counts)

    def test_stale_new_confirmation_shows_normal_page_error(self):
        draft = self.preview(self.book)
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        counts = self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()
        response = self.confirm(self.book, draft)
        self.assertContains(response, '请打开当前编码本重新导入')
        self.assertContains(response, '历史规则保持不变')
        self.assert_not_applied(draft, current, *counts)

    def test_applied_new_import_replay_survives_later_current_book_change(self):
        draft = self.preview(self.book)
        target, created = apply_codebook_import(draft.pk, self.owner, self.book)
        self.assertTrue(created)
        current = Codebook.objects.create(project=self.project, version=3, title='后续的虚构当前规则')
        counts = self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()
        replay, created = apply_codebook_import(draft.pk, self.owner, self.book)
        self.assertEqual(replay.pk, target.pk)
        self.assertFalse(created)
        response = self.confirm(self.book, draft)
        self.assertRedirects(response, reverse('codebook', args=[self.project.pk, target.pk]))
        self.assertEqual(self.project.codebooks.first().pk, current.pk)
        self.assertEqual((self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()), counts)
        self.assertEqual(AuditEvent.objects.filter(project=self.project, action='codebook.import').count(), 1)

    def test_applied_append_replay_is_still_safe_when_source_becomes_historical(self):
        old = begin_update(self.project, self.owner, self.book, self.book.revision)
        draft = self.preview(old, 'append')
        target, created = apply_codebook_import(draft.pk, self.owner, old)
        self.assertTrue(created)
        self.assertEqual(target.pk, old.pk)
        current = Codebook.objects.create(project=self.project, version=3, title='后续的虚构当前规则')
        replay, created = apply_codebook_import(draft.pk, self.owner, old)
        self.assertEqual(replay.pk, old.pk)
        self.assertFalse(created)
        self.assertEqual(old.codes.filter(key='IMPORTED').count(), 1)
        self.assertEqual(self.project.codebooks.first().pk, current.pk)

    def test_current_append_import_remains_usable(self):
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        draft = self.preview(current, 'append')
        response = self.confirm(current, draft)
        self.assertRedirects(response, reverse('codebook', args=[self.project.pk, current.pk]))
        self.assertTrue(current.codes.filter(key='IMPORTED').exists())
        self.assertEqual(self.project.codebooks.first().pk, current.pk)

    def copy_request(self, source):
        return self.client.post(reverse('book_new', args=[self.project.pk]), {
            'source_id': source.pk, 'title': '虚构复制规则', 'policy': source.policy,
            'change_note': '兼容旧入口的虚构复制测试'}, follow=True)

    def test_legacy_copy_route_rejects_frozen_historical_source(self):
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        before = self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()
        response = self.copy_request(self.book)
        self.assertContains(response, '只能从当前编码本继续更新')
        self.assertEqual(self.project.codebooks.first().pk, current.pk)
        self.assertEqual((self.project.codebooks.count(), Code.objects.filter(book__project=self.project).count()), before)

    def test_legacy_copy_route_rejects_editable_historical_source(self):
        old = begin_update(self.project, self.owner, self.book, self.book.revision)
        current = Codebook.objects.create(project=self.project, version=3, title='虚构当前规则')
        response = self.copy_request(old)
        self.assertContains(response, '只能从当前编码本继续更新')
        self.assertEqual(self.project.codebooks.first().pk, current.pk)
        self.assertEqual(self.project.codebooks.count(), 3)

    def test_legacy_copy_route_still_allows_current_source(self):
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        response = self.copy_request(current)
        latest = self.project.codebooks.first()
        self.assertRedirects(response, reverse('codebook', args=[self.project.pk, latest.pk]))
        self.assertEqual(latest.version, 3)
        self.assertEqual(list(latest.codes.values_list('key', flat=True)),
                         list(current.codes.values_list('key', flat=True)))
        event = AuditEvent.objects.get(project=self.project, object_id=str(latest.pk), action='codebook.new_version')
        self.assertEqual(event.after['from'], current.pk)
