"""Synthetic inputs only: JSON normalization must not discard legacy rows or source bytes."""
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from .importing import parse_upload
from .material_normalization import document_payload, normalize_material
from .models import AuditEvent, Membership, Project, SourceDocument, SourceRecord, Unit
from .services import import_records
from .test_files import text_pdf
from .tests import TEST_SETTINGS

WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def material_docx(body, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>')
        if extra:
            archive.writestr('custom.xml', '<value>' + extra + '</value>')
    return stream.getvalue()


def mixed_pdf(with_text=True):
    writer = PdfWriter()
    if with_text:
        writer.append(PdfReader(io.BytesIO(text_pdf('A readable first policy page.'))))
    page = writer.add_blank_page(400, 500)
    content = DecodedStreamObject()
    content.set_data(b'0 0 200 200 re f')
    page[NameObject('/Contents')] = writer._add_object(content)
    if with_text:
        writer.append(PdfReader(io.BytesIO(text_pdf('Brief caption.'))))
        image = DecodedStreamObject()
        image.set_data(b'\xff\x00\x00')
        image.update({NameObject('/Subtype'): NameObject('/Image'),
                      NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1),
                      NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
        page = writer.pages[2]
        page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Im1'): writer._add_object(image)})
        content = DecodedStreamObject()
        content.set_data(page.get_contents().get_data() + b'\nq 100 0 0 100 30 400 cm /Im1 Do Q')
        page[NameObject('/Contents')] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class MaterialNormalizationParserTests(SimpleTestCase):
    def test_csv_preserves_all_fields_and_original_row_order(self):
        raw = 'id,text,role\n001,同一句话,学生\n002,同一句话,AI\n'.encode()
        normalized = normalize_material('聊天.csv', raw)
        self.assertEqual((normalized['schema_version'], normalized['type'], normalized['presentation']), (1, 'records', 'unit'))
        self.assertEqual(normalized['headers'], ['id', 'text', 'role'])
        self.assertEqual([block['metadata']['id'] for block in normalized['blocks']], ['001', '002'])
        self.assertEqual(normalized['rows'][1]['role'], 'AI')
        self.assertEqual(json.loads(json.dumps(normalized, ensure_ascii=False)), normalized)

    def test_txt_keeps_legacy_line_records_including_empty_lines(self):
        normalized = normalize_material('访谈.txt', '第一行\r\n\r\n第三行'.encode())
        self.assertEqual([block['text'] for block in normalized['blocks']], ['第一行', '', '第三行'])
        self.assertEqual(parse_upload('访谈.txt', b'one\ntwo')[1], [{'text': 'one'}, {'text': 'two'}])

    def test_prose_recommendation_uses_content_without_changing_record_semantics(self):
        prose = ('完整政策正文段落。' * 100 + '\n') * 3
        document = normalize_material('prose.txt', prose.encode())
        self.assertEqual((document['type'], document['presentation']), ('records', 'document'))
        self.assertEqual([block['text'] for block in document['blocks']], [*prose.splitlines(), ''])
        raw = ('text,speaker\n' + ',Participant A\n'.join(['A' * 900] * 3) + ',Participant A\n').encode()
        conversation = normalize_material('dialogue.csv', raw)
        self.assertEqual((conversation['type'], conversation['presentation']), ('records', 'unit'))

    def test_docx_body_order_headings_table_and_duplicates_are_retained(self):
        raw = material_docx('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Section one</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>A repeated policy paragraph.</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Policy</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Requirement</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '<w:p><w:r><w:t>A repeated policy paragraph.</w:t></w:r></w:p>')
        normalized = normalize_material('policy.docx', raw)
        self.assertEqual(normalized['type'], 'document')
        self.assertEqual([block['kind'] for block in normalized['blocks']], ['heading', 'paragraph', 'table', 'paragraph'])
        self.assertEqual(normalized['blocks'][0]['level'], 1)
        self.assertEqual(normalized['blocks'][2]['text'], 'Policy\tRequirement')
        self.assertEqual(normalized['text'].count('A repeated policy paragraph.'), 2)
        for block in normalized['blocks']:
            self.assertEqual(normalized['text'][block['start']:block['end']], block['text'])
        headers, rows = parse_upload('policy.docx', raw)
        self.assertEqual(headers, ['text', 'source_document_id'])
        self.assertEqual(rows[0]['text'], normalized['text'])
        self.assertEqual(rows[0]['source_document_id'], normalized['source']['sha256'])

    def test_docx_entity_expansion_and_unreadable_images_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_material('bad.docx', material_docx('<w:p><w:r><w:drawing/></w:r></w:p>'))
        with self.assertRaises(ValueError):
            normalize_material('bad.docx', b'not a zip')

    def test_docx_content_controls_are_not_silently_skipped(self):
        raw = material_docx('<w:sdt><w:sdtContent><w:p><w:r><w:t>Policy inside a content control.</w:t>'
                            '</w:r></w:p></w:sdtContent></w:sdt>')
        self.assertEqual(normalize_material('policy.docx', raw)['text'], 'Policy inside a content control.')

    def test_text_pdf_has_page_numbers_and_exact_character_offsets(self):
        normalized = normalize_material('policy.pdf', text_pdf('Synthetic policy.\n\nA second paragraph to encode.'))
        self.assertEqual(normalized['pages'], [{'page': 1, 'empty': False}])
        self.assertEqual(normalized['blocks'][0]['page'], 1)
        for block in normalized['blocks']:
            self.assertEqual(normalized['text'][block['start']:block['end']], block['text'])
        self.assertTrue(normalized['warnings'])

    def test_blank_pdf_page_is_kept_in_source_page_map(self):
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(text_pdf('A meaningful policy paragraph.'))))
        writer.add_blank_page(400, 500)
        stream = io.BytesIO()
        writer.write(stream)
        normalized = normalize_material('policy.pdf', stream.getvalue())
        self.assertEqual(normalized['pages'], [{'page': 1, 'empty': False}, {'page': 2, 'empty': True}])

    def test_mixed_pdf_preserves_page_numbers_and_readable_short_text(self):
        normalized = normalize_material('partly-scanned.pdf', mixed_pdf())
        self.assertEqual(normalized['pages'], [
            {'page': 1, 'empty': False}, {'page': 2, 'empty': True, 'review': 'missing_text'},
            {'page': 3, 'empty': False, 'review': 'sparse_text'}])
        self.assertEqual(normalized['pages_to_review'], [2, 3])
        self.assertEqual(normalized['text'], 'A readable first policy page.\n\nBrief caption.')
        self.assertEqual([block['page'] for block in normalized['blocks']], [1, 3])

    def test_pdf_without_any_readable_text_retains_page_diagnostics(self):
        normalized = normalize_material('scanned.pdf', mixed_pdf(with_text=False))
        self.assertEqual(normalized['text'], '')
        self.assertEqual(normalized['blocks'], [])
        self.assertEqual(normalized['pages_to_review'], [1])

    def test_encrypted_and_corrupt_pdf_fail_explicitly(self):
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(text_pdf('A protected policy paragraph.'))))
        writer.encrypt('synthetic-password')
        stream = io.BytesIO()
        writer.write(stream)
        with self.assertRaisesMessage(ValueError, '加密PDF'):
            normalize_material('protected.pdf', stream.getvalue())
        with self.assertRaises(ValueError):
            normalize_material('broken.pdf', b'%PDF-not-a-document')

    def test_pdf_timeout_fails_without_partial_import(self):
        import subprocess
        with patch('coding.material_normalization.subprocess.run', side_effect=subprocess.TimeoutExpired('pdf', 30)):
            with self.assertRaisesMessage(ValueError, '超时'):
                normalize_material('slow.pdf', b'%PDF-stub')

    def test_document_size_limit_is_separate_from_legacy_tables(self):
        with patch('coding.importing.MAX_BYTES', 4), patch('coding.importing.MAX_DOCUMENT_BYTES', 12):
            with self.assertRaisesMessage(ValueError, '最大80MB'):
                parse_upload('policy.pdf', b'x' * 13)
            with self.assertRaisesMessage(ValueError, '最大8MB'):
                parse_upload('table.csv', b'x' * 5)


@override_settings(**TEST_SETTINGS)
class MaterialNormalizationImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('synthetic_normalization_owner')
        cls.project = Project.objects.create(name='虚构统一材料项目', owner=cls.owner)

    def import_file(self, filename, raw):
        _, rows = parse_upload(filename, raw)
        return import_records(self.project, self.owner, SimpleNamespace(filename=filename, raw=raw), rows, 'text', '')

    def test_document_import_keeps_source_and_waits_for_unit_confirmation(self):
        raw = material_docx('<w:p><w:r><w:t>A policy paragraph.</w:t></w:r></w:p>')
        document = self.import_file('policy.docx', raw)
        self.assertEqual(bytes(document.raw), raw)
        self.assertFalse(document.units_confirmed)
        self.assertEqual(document.workbench_mode, 'auto')
        self.assertEqual(document.unit_revision, 0)
        self.assertEqual(document.records.count(), 1)
        self.assertEqual(Unit.objects.filter(record__document=document).count(), 0)
        self.assertEqual(document.records.get().text, document.normalized['text'])
        self.assertEqual(document.import_counts, {'records': 1, 'units': 0, 'skipped': 0})

    def test_mixed_pdf_upload_requires_acknowledgment_and_preserves_original(self):
        Membership.objects.create(project=self.project, user=self.owner, role='owner')
        self.client.force_login(self.owner)
        url = f'/p/{self.project.pk}/materials/'
        raw = mixed_pdf()
        preview = self.client.post(url, {'file': SimpleUploadedFile('mixed.pdf', raw, content_type='application/pdf')})
        self.assertContains(preview, '第 2、3 页的文字需要核对')
        self.assertFalse(SourceDocument.objects.exists())
        form = {'draft_id': preview.context['draft'].pk, 'text_column': 'text'}
        rejected = self.client.post(url, form)
        self.assertContains(rejected, '需要核对的页面后再导入')
        self.assertFalse(SourceDocument.objects.exists())
        accepted = self.client.post(url, {**form, 'acknowledge_pdf_pages': 'yes'})
        document = SourceDocument.objects.get()
        self.assertRedirects(accepted, f'/p/{self.project.pk}/source-coding/?document={document.pk}',
                             fetch_redirect_response=False)
        self.assertEqual(bytes(document.raw), raw)
        self.assertEqual(document.normalized['pages_to_review'], [2, 3])
        self.assertFalse(Unit.objects.filter(record__document=document).exists())

    def test_scanned_pdf_remains_available_for_preview_without_text_tasks(self):
        Membership.objects.create(project=self.project, user=self.owner, role='owner')
        self.client.force_login(self.owner)
        url = f'/p/{self.project.pk}/materials/'
        raw = mixed_pdf(with_text=False)
        preview = self.client.post(url, {'file': SimpleUploadedFile('scanned.pdf', raw, content_type='application/pdf')})
        accepted = self.client.post(url, {'draft_id': preview.context['draft'].pk, 'text_column': 'text',
                                          'acknowledge_pdf_pages': 'yes'})
        self.assertRedirects(accepted, url, fetch_redirect_response=False)
        document = SourceDocument.objects.get()
        self.assertEqual(bytes(document.raw), raw)
        self.assertEqual(document.records.get().text, '')
        listing = self.client.get(url)
        self.assertContains(listing, '查看原版 PDF')
        self.assertNotContains(listing, '编码全部已导入材料')
        self.assertNotContains(listing, f'?document={document.pk}')
        self.assertRedirects(self.client.get(f'/p/{self.project.pk}/code/'), url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(f'/p/{self.project.pk}/materials/{document.pk}/prepare/').status_code, 404)

    def test_legacy_rows_keep_existing_dedup_and_default_units(self):
        document = self.import_file('rows.csv', b'text,role\nsame,student\nsame,student\nsame,AI\n')
        self.assertTrue(document.units_confirmed)
        self.assertEqual(document.normalized['type'], 'records')
        self.assertEqual(len(document.normalized['rows']), 3)
        self.assertEqual(list(document.records.values_list('position', flat=True)), [1, 3])
        self.assertEqual(Unit.objects.filter(record__document=document).count(), 2)
        self.assertEqual(document.import_counts, {'records': 2, 'units': 2, 'skipped': 1})

    def test_identical_paragraphs_in_distinct_documents_are_retained(self):
        body = '<w:p><w:r><w:t>The same policy language.</w:t></w:r></w:p>'
        first = self.import_file('first.docx', material_docx(body))
        second = self.import_file('second.docx', material_docx(body, 'a distinct source version'))
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.import_counts['skipped'], 0)
        self.assertEqual(SourceRecord.objects.count(), 2)
        repeated = self.import_file('renamed.docx', bytes(first.raw))
        self.assertEqual(repeated.pk, first.pk)
        self.assertEqual(SourceRecord.objects.count(), 2)

    def test_tampered_document_preview_rolls_back(self):
        raw = material_docx('<w:p><w:r><w:t>A policy paragraph.</w:t></w:r></w:p>')
        _, rows = parse_upload('policy.docx', raw)
        rows[0]['text'] = 'altered text'
        with self.assertRaisesMessage(ValueError, '无法对应'):
            import_records(self.project, self.owner, SimpleNamespace(filename='policy.docx', raw=raw), rows, 'text', '')
        self.assertFalse(SourceDocument.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_old_document_gets_read_only_json_without_reparse_or_migration(self):
        document = SourceDocument.objects.create(project=self.project, name='legacy.csv', sha256='f' * 64,
                                                 raw=b'legacy bytes', text_column='text')
        SourceRecord.objects.create(document=document, position=3, text='Old material', metadata={'speaker': 'A'})
        normalized = document_payload(document)
        self.assertEqual(normalized['blocks'][0]['row'], 3)
        self.assertEqual(normalized['blocks'][0]['metadata'], {'speaker': 'A'})
        document.refresh_from_db()
        self.assertEqual(document.normalized, {})
        self.assertTrue(document.units_confirmed)
