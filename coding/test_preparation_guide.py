"""A first-use preparation journey, with synthetic uploads and no research data."""
from html.parser import HTMLParser

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (Assignment, AuditEvent, Code, Codebook, CodebookImportDraft,
                     Membership, Project, Round, Unit, UploadDraft)
from .services import create_round
from .tests import TEST_SETTINGS, fixture


class PreparationMarkup(HTMLParser):
    void_tags = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parents': self.stack.copy(), 'text': ''}
        self.nodes.append(node)
        if tag not in self.void_tags:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data

    def find(self, tag=None, class_name=None, parent=None):
        return [node for node in self.nodes
                if (tag is None or node['tag'] == tag)
                and (class_name is None or class_name in node['attrs'].get('class', '').split())
                and (parent is None or any(ancestor is parent for ancestor in node['parents']))]


@override_settings(**TEST_SETTINGS)
class PreparationGuideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.existing = fixture()

    def setUp(self):
        cache.clear()
        self.owner, self.coder, self.viewer = [self.existing[key] for key in ('owner', 'coder', 'viewer')]
        self.project = Project.objects.create(name='虚构的新用户准备流程', owner=self.owner,
                                              goal='核对虚构材料中的计划与检查')
        for user, role in ((self.owner, 'owner'), (self.coder, 'coder'), (self.viewer, 'viewer')):
            Membership.objects.create(project=self.project, user=user, role=role)
        self.book = Codebook.objects.create(project=self.project, version=1)
        self.client.force_login(self.owner)

    def url(self, name, book=None):
        args = [self.project.pk]
        if name in ('codebook', 'book_import'):
            args.append((book or self.book).pk)
        return reverse(name, args=args)

    def guide(self, response, stage, *, complete=(), next_url=None, next_text=None):
        self.assertEqual(response.status_code, 200)
        markup = PreparationMarkup(response.content.decode())
        guides = markup.find('section', 'setup-guide')
        self.assertEqual(len(guides), 1)
        guide = guides[0]
        self.assertEqual(guide['attrs'].get('aria-label'), '当前工作指引')
        self.assertEqual(guide['attrs'].get('data-setup-stage'), str(stage))
        lists = markup.find('ol', parent=guide)
        self.assertEqual(len(lists), 1)
        self.assertEqual(lists[0]['attrs'].get('aria-label'), '编码准备步骤')
        steps = markup.find('li', parent=lists[0])
        self.assertEqual(len(steps), 3)
        self.assertEqual([i for i, item in enumerate(steps, 1)
                          if item['attrs'].get('aria-current') == 'step'], [stage])
        self.assertEqual([i for i, item in enumerate(steps, 1)
                          if 'is-complete' in item['attrs'].get('class', '').split()], list(complete))
        links = markup.find('a', 'setup-next', guide)
        if next_url is None:
            self.assertEqual(links, [])
        else:
            self.assertEqual([link['attrs'].get('href') for link in links], [next_url])
            if next_text:
                self.assertIn(next_text, links[0]['text'])
        return guide

    def preview_material(self, raw=None):
        raw = raw if raw is not None else (
            'message_id,content,group_id\n'
            'fictional-1,我们先确定目标,G1\n'
            'fictional-2,检查当前进度,G1\n').encode()
        response = self.client.post(self.url('materials'), {
            'file': SimpleUploadedFile('preparation.csv', raw, content_type='text/csv')})
        return response, UploadDraft.objects.filter(project=self.project, user=self.owner).latest('created_at')

    def confirm_material(self, draft, **changes):
        return self.client.post(self.url('materials'), {
            'draft_id': str(draft.pk), 'text_column': 'content', 'context_column': 'group_id', **changes}, follow=True)

    def import_material(self):
        _, draft = self.preview_material()
        return self.confirm_material(draft)

    def preview_book(self, raw=None):
        raw = raw if raw is not None else (
            'Code,Definition of behavior,Example\n'
            'PLAN,确定下一步行动,我们先确定目标\n'
            'CHECK,核对当前进度,还有什么没完成\n').encode()
        response = self.client.post(self.url('book_import'), {
            'action': 'upload', 'auto_preview': 'yes',
            'file': SimpleUploadedFile('fictional-rules.csv', raw, content_type='text/csv')}, follow=True)
        return response, CodebookImportDraft.objects.filter(book=self.book, user=self.owner).latest('created_at')

    def add_code(self):
        return Code.objects.create(book=self.book, key='PLAN', name='计划', definition='虚构行动安排')

    def snapshot(self):
        return {
            'rounds': list(Round.objects.values()),
            'assignments': list(Assignment.objects.values()),
            'books': list(Codebook.objects.values()),
            'codes': list(Code.objects.values()),
            'units': list(Unit.objects.values()),
            'events': list(AuditEvent.objects.values()),
        }

    def test_empty_material_page_keeps_upload_as_the_next_action(self):
        response = self.client.get(self.url('materials'))
        self.guide(response, 1)
        self.assertContains(response, '上传并预览')
        self.assertFalse(self.project.documents.exists())
        self.assertFalse(self.project.rounds.exists())

    def test_real_uploads_connect_previews_confirmations_and_coding_start(self):
        response, material = self.preview_material()
        self.guide(response, 1)
        self.assertFalse(self.project.documents.exists())
        self.assertContains(response, '确认导入')

        response = self.confirm_material(material)
        self.guide(response, 2, complete=(1,), next_url=self.url('codebook'), next_text='下一步：准备编码本')
        self.assertEqual(self.project.documents.count(), 1)
        self.assertEqual(Unit.objects.filter(record__document__project=self.project, active=True).count(), 2)
        self.assertFalse(self.project.rounds.exists())

        response = self.client.get(self.url('codebook'))
        self.guide(response, 2, complete=(1,), next_url=self.url('book_import'), next_text='下一步：导入编码本')
        response = self.client.get(self.url('book_import'))
        self.guide(response, 2, complete=(1,))
        response, draft = self.preview_book()
        self.guide(response, 2, complete=(1,))
        self.assertTrue(draft.preview)
        self.assertFalse(self.book.codes.exists())
        self.assertContains(response, '确认导入并使用')

        response = self.client.post(self.url('book_import'), {
            'action': 'confirm', 'draft_id': str(draft.pk), 'confirmed': 'yes'}, follow=True)
        self.book = self.project.codebooks.first()
        self.guide(response, 3, complete=(1, 2), next_url=self.url('coding_entry'), next_text='下一步：确认并开始')
        self.assertEqual(self.book.codes.count(), 2)
        self.assertFalse(self.project.rounds.exists())
        self.assertFalse(self.book.frozen)

        response = self.client.get(self.url('coding_entry'), follow=True)
        self.guide(response, 3, complete=(1, 2))
        self.assertContains(response, '确认编码准备')
        self.assertFalse(self.project.rounds.exists())
        response = self.client.post(self.url('quick_start'), response.context['form'].initial, follow=True)
        work = self.project.rounds.get(kind='quick', started_by=self.owner)
        self.assertRedirects(response, reverse('workbench', args=[work.pk]))
        self.assertEqual(work.assignments.count(), 2)
        self.assertNotContains(response, 'class="setup-guide"')

    def test_manual_code_save_also_reveals_confirmation_step(self):
        self.import_material()
        response = self.client.post(self.url('codebook'), {
            'revision': self.book.revision, 'key': 'PLAN', 'name': '计划', 'definition': '虚构行动安排'}, follow=True)
        self.guide(response, 3, complete=(1, 2), next_url=self.url('coding_entry'))
        self.assertEqual(self.book.codes.count(), 1)
        self.assertFalse(self.project.rounds.exists())

    def test_bad_material_column_stays_at_unconfirmed_material_step(self):
        _, draft = self.preview_material()
        response = self.confirm_material(draft, text_column='missing-column')
        self.guide(response, 1)
        self.assertContains(response, '请选择有效且不同的文本列')
        self.assertFalse(self.project.documents.exists())
        self.assertFalse(self.project.rounds.exists())

    def test_blank_material_does_not_complete_preparation(self):
        response = self.client.post(self.url('materials'), {
            'file': SimpleUploadedFile('blank.csv', b'content\n\n', content_type='text/csv')})
        self.guide(response, 1)
        self.assertFalse(Unit.objects.filter(record__document__project=self.project, active=True).exists())
        self.assertFalse(self.project.rounds.exists())

    def test_unconfirmed_rule_preview_is_not_a_ready_codebook(self):
        self.import_material()
        response, draft = self.preview_book()
        self.guide(response, 2, complete=(1,))
        self.assertContains(response, 'href="#book-import-confirm"')
        self.assertContains(response, 'id="book-import-confirm"', count=1)
        self.assertFalse(self.book.codes.exists())
        self.assertIsNone(draft.applied_book_id)
        response = self.client.get(self.url('units'))
        self.guide(response, 2, complete=(1,), next_url=self.url('codebook'))
        response = self.client.get(self.url('coding_entry'), follow=True)
        self.assertTemplateUsed(response, 'coding/quick_start.html')
        self.assertContains(response, '新增代码')
        self.assertFalse(self.book.codes.exists())
        self.assertFalse(self.project.rounds.exists())

    def test_failed_rule_confirmation_keeps_preview_and_no_next_link(self):
        self.import_material()
        _, draft = self.preview_book()
        response = self.client.post(self.url('book_import'), {
            'action': 'confirm', 'draft_id': str(draft.pk), 'confirmed': 'no'}, follow=True)
        self.guide(response, 2, complete=(1,))
        self.assertContains(response, '请勾选确认')
        self.assertFalse(self.book.codes.exists())
        self.assertFalse(self.project.rounds.exists())

    def test_unrecognized_rule_columns_keep_mapping_in_the_rule_step(self):
        self.import_material()
        response, draft = self.preview_book(b'Identifier,Meaning\nX,A fictional rule\n')
        self.guide(response, 2, complete=(1,))
        self.assertFalse(draft.preview)
        self.assertContains(response, '确认对应字段')
        self.assertFalse(self.book.codes.exists())

    def test_codebook_uploaded_first_still_points_to_missing_materials(self):
        self.add_code()
        response = self.client.get(self.url('codebook'))
        self.guide(response, 1, complete=(2,), next_url=self.url('materials'))
        self.assertFalse(self.project.rounds.exists())

    def test_excluded_materials_are_not_counted_as_ready(self):
        self.import_material()
        self.add_code()
        Unit.objects.filter(record__document__project=self.project).update(active=False)
        response = self.client.get(self.url('codebook'))
        self.guide(response, 1, complete=(2,), next_url=self.url('materials'))

    def test_coder_waits_for_missing_resources_without_management_links(self):
        self.client.force_login(self.coder)
        self.guide(self.client.get(self.url('units')), 1)
        self.assertEqual(self.client.get(self.url('materials')).status_code, 403)
        self.client.force_login(self.owner)
        self.import_material()
        self.client.force_login(self.coder)
        self.guide(self.client.get(self.url('codebook')), 2, complete=(1,))
        self.assertEqual(self.client.get(self.url('book_import')).status_code, 403)

    def test_coder_can_follow_ready_project_into_start_confirmation(self):
        self.import_material()
        self.add_code()
        self.client.force_login(self.coder)
        self.guide(self.client.get(self.url('units')), 3, complete=(1, 2), next_url=self.url('coding_entry'))
        self.guide(self.client.get(self.url('coding_entry'), follow=True), 3, complete=(1, 2))
        self.assertFalse(self.project.rounds.exists())

    def test_viewer_does_not_get_actionable_preparation_guide(self):
        self.client.force_login(self.viewer)
        for path in (self.url('units'), self.url('codebook')):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(PreparationMarkup(response.content.decode()).find('section', 'setup-guide'), [])

    def test_historical_rule_page_does_not_misrepresent_current_preparation(self):
        self.import_material()
        self.add_code()
        current = Codebook.objects.create(project=self.project, version=2)
        Code.objects.create(book=current, key='CURRENT', name='当前虚构规则', definition='当前定义')
        response = self.client.get(self.url('codebook'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PreparationMarkup(response.content.decode()).find('section', 'setup-guide'), [])
        self.assertContains(response, '历史规则')

    def test_get_refreshes_do_not_freeze_rules_create_work_or_touch_records(self):
        self.import_material()
        self.add_code()
        before = self.snapshot()
        paths = [self.url(name) for name in ('materials', 'units', 'codebook', 'book_import', 'quick_start')]
        for _ in range(2):
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.book.refresh_from_db()
        self.assertFalse(self.book.frozen)
        self.assertFalse(self.project.rounds.exists())

    def test_existing_round_keeps_original_workflow_hint_without_preparation_reset(self):
        self.import_material()
        self.add_code()
        current = create_round(self.project, self.owner, self.book.pk, '虚构进行中试编码',
                               'pilot', [self.owner.pk, self.coder.pk], 0, 2026, 'all', 25)
        before = self.snapshot()
        for name in ('materials', 'units', 'codebook'):
            with self.subTest(page=name):
                response = self.client.get(self.url(name))
                self.assertEqual(response.status_code, 200)
                markup = PreparationMarkup(response.content.decode())
                self.assertEqual(markup.find('section', 'setup-guide'), [])
                self.assertEqual(len(markup.find('section', 'workflow-hint')), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(current.assignments.count(), 4)
