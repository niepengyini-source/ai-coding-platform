import hashlib
import io
import csv

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .document_reading import reading_context
from .exporting import annotation_table
from .models import Assignment, SourceDocument, SourceRecord, Unit
from .selection import select_round_units
from .source_exports import source_export_response
from .test_material_normalization import material_docx
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class DocumentReadingTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        text = '原文标题\n\n需要核对😀的政策文字。\n\n另一段不在本次编码范围内。'
        raw = b'fictional original retained'
        digest = hashlib.sha256(raw).hexdigest()
        self.document = SourceDocument.objects.create(project=self.project, name='policy.pdf', raw=raw,
            sha256=digest, text_column='text', normalized={'schema_version': 1, 'type': 'document',
            'presentation': 'document', 'text': text, 'blocks': [
                {'id': '1', 'start': 0, 'end': 4, 'text': text[:4], 'page': 1, 'heading': '原文标题', 'level': 1},
                {'id': '2', 'start': 6, 'end': len(text), 'text': text[6:], 'page': 2, 'heading': '原文标题', 'level': 0}]})
        record = SourceRecord.objects.create(document=self.document, position=1, text=text,
            metadata={'source_document_id': digest})
        unit = Unit.objects.create(record=record, start=6, end=21, text=text[6:21])
        self.assignment = Assignment.objects.create(round=self.r, unit=unit, coder=self.owner)
        self.client.force_login(self.owner)

    def test_document_opens_full_reading_with_same_annotation_endpoint(self):
        response = self.client.get(f'/r/{self.r.pk}/?a={self.assignment.pk}')
        self.assertContains(response, 'data-default-tab="document-tab"')
        self.assertContains(response, '<mark>' + self.assignment.unit.text + '</mark>')
        self.assertContains(response, f'data-endpoint="/api/assignments/{self.assignment.pk}/save/"')
        self.assertContains(response, 'PDF 第 2 页')
        self.assertEqual(len(response.context['document_reading']['outline']), 1)
        self.assertContains(response, 'document-coding-entry')
        self.assertContains(response, f'href="/p/{self.project.pk}/source-coding/?document={self.document.pk}"')

    def test_old_material_can_enter_selection_without_new_project_or_reimport(self):
        from .source_models import SourceAnnotation
        before = (self.r.assignments.count(), Unit.objects.count(), SourceDocument.objects.count())
        start_url = f'/p/{self.project.pk}/source-coding/?document={self.doc.pk}'
        response = self.client.get(start_url)
        self.assertContains(response, '不需要重新上传或新建项目')
        self.assertFalse(self.project.rounds.filter(sampling__source_coding=True).exists())
        response = self.client.post(start_url)
        self.assertEqual(response.status_code, 302)
        source_round = self.project.rounds.get(sampling__source_coding=True)
        task = source_round.source_tasks.get()
        response = self.client.get(response.url)
        self.assertContains(response, 'data-source-annotation-form')
        code = source_round.book.codes.get(key=self.first.key)
        text = self.doc.records.order_by('position').first().text
        response = self.client.post(f'/api/source-tasks/{task.pk}/annotations/', {
            'start': 0, 'end': 5, 'text': text[:5], 'primary': code.pk, 'secondary': [],
            'book_id': source_round.book_id, 'note': '虚构旧材料划选验证', 'revision': 0,
            'no_code': False, 'uncertain': False, 'token': 'fictional-legacy-selection'}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SourceAnnotation.objects.get().text, text[:5])
        self.assertEqual(before, (self.r.assignments.count(), Unit.objects.count(), SourceDocument.objects.count()))

    def test_plain_records_stay_unit_mode_but_can_read_full_source(self):
        task = self.r.assignments.filter(coder=self.owner).exclude(pk=self.assignment.pk).first()
        result = reading_context(task, {})['document_reading']
        self.assertFalse(result['recommended'])
        self.assertEqual(len(result['blocks']), self.doc.records.count())
        self.assertTrue(reading_context(task, {'reader_page': '1'})['document_reading']['recommended'])

    def test_pending_revisions_do_not_rewrite_old_assigned_text(self):
        self.document.units_confirmed = False
        self.document.save(update_fields=['units_confirmed'])
        response = self.client.get(f'/r/{self.r.pk}/?a={self.assignment.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.assignment.unit_id, [u.pk for u in select_round_units(self.project, 0, 1)])

    def test_original_is_private_and_verifies_integrity(self):
        url = f'/p/{self.project.pk}/materials/{self.document.pk}/original/'
        response = self.client.get(url)
        self.assertEqual(response.content, bytes(self.document.raw))
        self.assertIn('attachment', response['Content-Disposition'])
        outsider = get_user_model().objects.create_user('reading_outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.owner)
        SourceDocument.objects.filter(pk=self.document.pk).update(sha256='0' * 64)
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_original_preview_accepts_only_known_media_and_requires_project_access(self):
        url = f'/p/{self.project.pk}/materials/{self.document.pk}/preview/'
        self.assertEqual(self.client.get(url).status_code, 404)
        raw = b'%PDF-1.7\n% fictional preview bytes'
        SourceDocument.objects.filter(pk=self.document.pk).update(
            raw=raw, sha256=hashlib.sha256(raw).hexdigest(),
        )
        response = self.client.get(url)
        self.assertEqual(response.content, raw)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].startswith('inline'))
        self.assertEqual(response['Cache-Control'], 'no-store')
        outsider = get_user_model().objects.create_user('preview_outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_source_export_has_fragment_location_without_repeating_entire_document(self):
        table = annotation_table(self.r, Assignment.objects.filter(pk=self.assignment.pk))
        response = source_export_response(self.project, table, 'csv', 'policy')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertIn('平台_PDF页码', rows[0])
        self.assertIn('policy.pdf', rows[1])
        self.assertIn('2', rows[1])
        self.assertNotIn(self.assignment.unit.record.text, rows[1])

    def test_docx_upload_preview_confirmation_and_prepare_redirect(self):
        raw = material_docx('<w:p><w:r><w:t>学校应先核对资料。</w:t></w:r></w:p><w:p><w:r><w:t>教师记录判断依据。</w:t></w:r></w:p>')
        url = f'/p/{self.project.pk}/materials/'
        response = self.client.post(url, {'file': SimpleUploadedFile('sample.docx', raw)})
        self.assertContains(response, '推荐使用全文阅读工作台')
        draft = response.context['draft']
        response = self.client.post(url, {'draft_id': str(draft.pk), 'text_column': 'text', 'context_column': ''})
        document = self.project.documents.get(name='sample.docx')
        self.assertRedirects(response, f'/p/{self.project.pk}/source-coding/?document={document.pk}')
        self.assertFalse(document.units_confirmed)
        self.assertFalse(Unit.objects.filter(record__document=document).exists())

    def test_mode_choice_cannot_change_task_or_ignore_revision(self):
        url = f'/p/{self.project.pk}/materials/{self.document.pk}/mode/'
        response = self.client.post(url, {'workbench_mode': 'unit', 'revision': '0'})
        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.workbench_mode, 'unit')
        self.client.post(url, {'workbench_mode': 'document', 'revision': '0'})
        self.document.refresh_from_db()
        self.assertEqual(self.document.workbench_mode, 'unit')
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.revision, 0)

    def test_independent_document_tasks_keep_shared_boundaries(self):
        self.r.kind = 'formal'
        self.r.save(update_fields=['kind'])
        url = f'/api/assignments/{self.assignment.pk}/split/'
        response = self.client.post(url, '{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('统一文档片段', response.json()['error'])
        self.assertFalse(self.assignment.segments.exists())
