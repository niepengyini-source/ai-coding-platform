"""Short import flow, using synthetic rule files and an isolated test database."""
import json
from html import escape
from urllib.parse import parse_qs, urlparse

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .codebook_files import normalized_codes, parse_codebook, suggest_mapping
from .exporting import docx_bytes
from .models import AuditEvent, Code, Codebook, CodebookImportDraft
from .test_files import workbook_bytes, text_pdf, minimal_docx, WORD_NS
from .tests import TEST_SETTINGS, fixture


THREE_COLUMNS = ['Code', 'Definition of behavior', 'Example']
RULES = [['PLAN', '先确定目标和分工。', '我们先讨论各自负责什么。'],
         ['CHECK', '检查团队当前进度。', '还有哪个任务没完成？']]
RULE_CSV = ('Code,Definition of behavior,Example\n'
            'PLAN,先确定目标和分工。,我们先讨论各自负责什么。\n'
            'CHECK,检查团队当前进度。,还有哪个任务没完成？\n').encode('utf-8')


class SimpleRuleParserTests(TestCase):
    def parse(self, filename, raw):
        headers, rows, _, _, _ = parse_codebook(filename, raw)
        return normalized_codes(rows, suggest_mapping(headers))

    def test_three_column_paper_headers_are_matched_by_meaning(self):
        mapping = suggest_mapping(THREE_COLUMNS)
        self.assertEqual(mapping['key'], 'Code')
        self.assertEqual(mapping['name'], '')
        self.assertEqual(mapping['definition'], 'Definition of behavior')
        self.assertEqual(mapping['positive_example'], 'Example')
        codes = self.parse('rules.csv', RULE_CSV)
        self.assertEqual(codes[0]['key'], 'PLAN')
        self.assertEqual(codes[0]['name'], 'PLAN')
        self.assertEqual(codes[0]['definition'], RULES[0][1])
        self.assertEqual(codes[0]['positive_example'], RULES[0][2])

    def test_two_column_table_needs_no_name_or_example_column(self):
        code = self.parse('rules.csv', 'Code,行为定义\nA,说明原因\n'.encode())[0]
        self.assertEqual(code['name'], 'A')
        self.assertEqual(code['definition'], '说明原因')
        self.assertEqual(code['positive_example'], '')

    def test_explicit_name_is_kept_and_empty_selected_name_is_not_invented(self):
        code = self.parse('rules.csv', 'Code,Name,Definition of behavior\nA,解释原因,说明观点理由\n'.encode())[0]
        self.assertEqual(code['name'], '解释原因')
        with self.assertRaisesMessage(ValueError, '缺少代码名称'):
            self.parse('rules.csv', b'Code,Name,Definition of behavior\nA,,Give reasons\n')

    def test_example_is_never_used_as_definition(self):
        mapping = suggest_mapping(['Code', 'Example'])
        self.assertEqual(mapping['definition'], '')
        with self.assertRaisesMessage(ValueError, '缺少定义'):
            self.parse('rules.csv', b'Code,Example\nA,A sample utterance\n')

    def test_competing_definition_columns_are_not_chosen_by_position(self):
        for headers in (['Code', 'Definition', 'Description'], ['Description', 'Code', 'Definition']):
            self.assertEqual(suggest_mapping(headers)['definition'], '')

    def test_british_spelling_and_multiline_word_headers(self):
        grid = [['Code', 'Definition\nof behaviour', 'Examples']] + RULES
        xml = f'<w:document xmlns:w="{WORD_NS}"><w:body><w:tbl>'
        for row in grid:
            xml += '<w:tr>'
            for value in row:
                text = escape(value).replace('\n', '</w:t><w:br/><w:t>')
                xml += f'<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>'
            xml += '</w:tr>'
        xml += '</w:tbl></w:body></w:document>'
        raw = minimal_docx(xml)
        codes = self.parse('rules.docx', raw)
        self.assertEqual(codes[1]['name'], 'CHECK')
        self.assertEqual(codes[1]['definition'], RULES[1][1])
        self.assertEqual(codes[1]['positive_example'], RULES[1][2])

    def test_two_field_text_blocks_remain_separate_records(self):
        raw = ('Code: A\nDefinition of behavior: Give reasons\nExample: Because...\n\n'
               'Code: B\nDefinition of behavior: Check progress\nExample: Are we done?\n').encode()
        codes = self.parse('rules.txt', raw)
        self.assertEqual([code['key'] for code in codes], ['A', 'B'])
        self.assertEqual(codes[1]['positive_example'], 'Are we done?')

    def test_display_name_fallback_does_not_relax_duplicate_checks(self):
        with self.assertRaisesMessage(ValueError, '重复'):
            self.parse('rules.csv', b'Code,Definition of behavior\nA,One rule\nA,Another rule\n')
        with self.assertRaisesMessage(ValueError, '多个编码字段'):
            normalized_codes([{'Code': 'A', 'definition': 'Rule'}],
                             {'key': 'Code', 'name': 'Code', 'definition': 'definition'})


@override_settings(**TEST_SETTINGS)
class SimpleRuleImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = f'/p/{self.project.id}/books/{self.book.id}/import/'

    def auto_upload(self, raw=RULE_CSV, filename='rules.csv', book=None):
        url = f'/p/{self.project.id}/books/{(book or self.book).id}/import/'
        response = self.client.post(url, {'action': 'upload', 'auto_preview': 'yes',
                                        'file': SimpleUploadedFile(filename, raw)})
        self.assertEqual(response.status_code, 302, response.content)
        draft_id = parse_qs(urlparse(response.url).query)['draft'][0]
        return response, CodebookImportDraft.objects.get(pk=draft_id)

    def confirm(self, draft, confirmed='yes'):
        return self.client.post(f'/p/{self.project.id}/books/{draft.book_id}/import/',
                                {'action': 'confirm', 'draft_id': str(draft.id), 'confirmed': confirmed})

    def test_upload_page_has_short_flow_and_requests_automatic_preview(self):
        response = self.client.get(self.url)
        self.assertContains(response, '上传并查看结果')
        self.assertContains(response, 'name="auto_preview" value="yes"')
        self.assertNotContains(response, 'name="map_definition"')

    def test_upload_goes_straight_to_preview_but_does_not_import(self):
        count, book_count = Code.objects.count(), Codebook.objects.count()
        response, draft = self.auto_upload()
        self.assertEqual(parse_qs(urlparse(response.url).query)['stage'], ['preview'])
        self.assertTrue(draft.preview['automatic'])
        self.assertTrue(draft.preview['name_from_key'])
        self.assertEqual(draft.preview['mapping']['name'], '')
        self.assertEqual(draft.preview['codes'][0]['definition'], RULES[0][1])
        self.assertEqual(draft.preview['codes'][0]['positive_example'], RULES[0][2])
        self.assertIsNone(draft.applied_book_id)
        page = self.client.get(response.url)
        self.assertContains(page, '已自动整理，请核对')
        self.assertContains(page, RULES[0][1])
        self.assertContains(page, RULES[0][2])
        self.assertNotContains(page, '未填写')
        self.assertEqual(Code.objects.count(), count)
        self.assertEqual(Codebook.objects.count(), book_count)

    def test_confirmation_still_requires_checkbox_and_is_idempotent(self):
        _, draft = self.auto_upload()
        self.assertContains(self.confirm(draft, confirmed='no'), '请勾选')
        draft.refresh_from_db()
        self.assertIsNone(draft.applied_book_id)
        self.assertEqual(self.confirm(draft).status_code, 302)
        draft.refresh_from_db()
        code = draft.applied_book.codes.get(key='PLAN')
        self.assertEqual(code.name, 'PLAN')
        self.assertEqual(code.definition, RULES[0][1])
        self.assertEqual(code.positive_example, RULES[0][2])
        count = Code.objects.count()
        self.confirm(draft)
        self.assertEqual(Code.objects.count(), count)
        self.assertEqual(AuditEvent.objects.filter(action='codebook.import').count(), 1)
        self.assertEqual(self.book.codes.get(key='PLAN').name, '计划')

    def test_empty_draft_uses_current_book_and_preserves_policy(self):
        book = Codebook.objects.create(project=self.project, version=2, title='空草稿', policy='single')
        _, draft = self.auto_upload(book=book)
        self.assertEqual(draft.preview['settings']['mode'], 'append')
        self.assertEqual(book.codes.count(), 0)
        self.confirm(draft)
        draft.refresh_from_db()
        self.assertEqual(draft.applied_book_id, book.id)
        book.refresh_from_db()
        self.assertEqual(book.policy, 'single')
        self.assertEqual(book.codes.count(), 2)

    def test_nonempty_draft_defaults_to_new_version_without_overwrite(self):
        book = Codebook.objects.create(project=self.project, version=2, title='已有草稿', policy='single')
        original = Code.objects.create(book=book, key='PLAN', name='已有规则', definition='保留原定义')
        _, draft = self.auto_upload(book=book)
        self.assertEqual(draft.preview['settings']['mode'], 'new')
        self.confirm(draft)
        original.refresh_from_db()
        self.assertEqual(original.definition, '保留原定义')
        self.assertEqual(book.codes.count(), 1)

    def test_unrecognized_headers_fall_back_to_two_required_choices(self):
        response, draft = self.auto_upload(b'Identifier,Meaning,Example\nA,Rule,Example text\n')
        self.assertNotIn('stage=preview', response.url)
        self.assertEqual(draft.preview, {})
        page = self.client.get(response.url)
        self.assertContains(page, '哪列是编码编号或原代码')
        self.assertContains(page, '哪列说明这个编码的含义')
        self.assertContains(page, 'id="book-import-advanced"')
        self.assertNotContains(page, 'id="book-import-advanced" open')
        self.assertFalse(page.context['mapping_form'].fields['map_name'].required)
        self.assertTrue(page.context['mapping_form'].fields['map_definition'].required)

    def test_ambiguous_columns_require_review_and_do_not_guess(self):
        response, draft = self.auto_upload(b'Code,Definition,Description\nA,Actual rule,Other explanation\n')
        self.assertEqual(draft.preview, {})
        self.assertNotIn('stage=preview', response.url)
        page = self.client.get(response.url)
        self.assertContains(page, '有多个可能的来源')
        self.assertContains(page, 'id="book-import-advanced" open')
        self.assertEqual(page.context['mapping_form']['map_definition'].value(), '')

    def test_example_only_file_does_not_become_a_definition(self):
        response, draft = self.auto_upload(b'Code,Example\nA,A quoted example\n')
        self.assertEqual(draft.preview, {})
        self.assertNotIn('stage=preview', response.url)
        self.assertEqual(self.confirm(draft).status_code, 200)
        draft.refresh_from_db()
        self.assertIsNone(draft.applied_book_id)

    def test_duplicate_codes_do_not_produce_partial_preview(self):
        count = Code.objects.count()
        response, draft = self.auto_upload(b'Code,Definition of behavior\nA,One\nA,Two\n')
        self.assertEqual(draft.preview, {})
        self.assertContains(self.client.get(response.url), '在文件中重复')
        self.assertEqual(Code.objects.count(), count)

    def test_reviewing_mapping_does_not_rewrite_saved_preview(self):
        _, draft = self.auto_upload()
        snapshot = draft.preview
        page = self.client.get(self.url + '?draft=' + str(draft.id))
        self.assertContains(page, '确认对应字段')
        self.assertNotContains(page, 'id="book-import-advanced" open')
        draft.refresh_from_db()
        self.assertEqual(draft.preview, snapshot)

    def test_manual_mapping_can_reuse_code_as_name_without_duplicate_selection(self):
        _, draft = self.auto_upload(b'Identifier,Meaning\nA,Rule\n')
        data = {'action': 'preview', 'draft_id': str(draft.id), 'mode': 'new',
                'title': 'Synthetic rules', 'policy': 'single', 'change_note': 'Synthetic test',
                'map_key': 'Identifier', 'map_name': '', 'map_definition': 'Meaning'}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.preview['codes'][0]['name'], 'A')

    def test_return_to_adjust_preserves_explicitly_disabled_name_and_example(self):
        raw = b'Code,Name,Definition,Example\nA,A name,A rule,An example\n'
        _, draft = self.auto_upload(raw)
        data = {'action': 'preview', 'draft_id': str(draft.id), 'mode': 'new', 'title': 'Rules',
                'policy': 'single', 'change_note': 'Synthetic test', 'map_key': 'Code',
                'map_name': '', 'map_definition': 'Definition', 'map_positive_example': ''}
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        page = self.client.get(self.url + '?draft=' + str(draft.id))
        self.assertEqual(page.context['mapping_form']['map_name'].value(), '')
        self.assertEqual(page.context['mapping_form']['map_positive_example'].value(), '')

    def test_manual_definition_example_swap_is_flagged_in_preview(self):
        _, draft = self.auto_upload()
        data = {'action': 'preview', 'draft_id': str(draft.id), 'mode': 'new', 'title': 'Rules',
                'policy': 'single', 'change_note': 'Synthetic test', 'map_key': 'Code',
                'map_name': 'Definition of behavior', 'map_definition': 'Example'}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response.url), '这个表头通常表示')

    def test_auto_preview_supports_existing_file_types(self):
        formats = {
            'rules.docx': docx_bytes(THREE_COLUMNS, RULES, 'Synthetic rules'),
            'rules.xlsx': workbook_bytes([('Rules', [THREE_COLUMNS] + RULES)]),
            'rules.tsv': ('Code\tDefinition of behavior\tExample\nA\tRule\tAn example\n').encode(),
            'rules.json': json.dumps([dict(zip(THREE_COLUMNS, RULES[0]))]).encode(),
            'rules.md': b'|Code|Definition of behavior|Example|\n|---|---|---|\n|A|Rule|An example|\n',
            'rules.txt': b'Code: A\nDefinition of behavior: Rule\nExample: An example\n',
            'rules.pdf': text_pdf('Code: A\nDefinition of behavior: Rule\nExample: An example'),
        }
        for filename, raw in formats.items():
            with self.subTest(filename=filename):
                response, draft = self.auto_upload(raw, filename)
                self.assertIn('stage=preview', response.url)
                self.assertTrue(draft.preview['codes'][0]['definition'])
                self.assertTrue(draft.preview['codes'][0]['positive_example'])
                self.assertIsNone(draft.applied_book_id)

    def test_auto_upload_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        count = CodebookImportDraft.objects.count()
        response = client.post(self.url, {'action': 'upload', 'auto_preview': 'yes',
                               'file': SimpleUploadedFile('rules.csv', RULE_CSV)})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CodebookImportDraft.objects.count(), count)
