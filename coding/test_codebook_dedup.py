"""Repeated synthetic rules are skipped without changing their original version."""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .book_updates import begin_update
from .codebook_files import (FIELDS, apply_codebook_import, normalized_codes,
                             same_codebook_codes, stored_code_values)
from .file_forms import CodebookMappingForm
from .models import AuditEvent, Code, Codebook, CodebookImportDraft
from .services import Conflict
from .tests import TEST_SETTINGS, fixture


MAPPING = {field: field for field, _, _ in FIELDS}


@override_settings(**TEST_SETTINGS)
class CodebookDedupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.book.refresh_from_db()

    def rows(self, book=None):
        return [stored_code_values(code) for code in
                (book or self.book).codes.select_related('parent')]

    def draft(self, rows, mode='merge', book=None, policy=None):
        book = book or self.book
        stats = {}
        existing = list(book.codes.select_related('parent')) if mode in ('merge', 'append') else []
        codes = normalized_codes(rows, MAPPING, existing, stats)
        return CodebookImportDraft.objects.create(book=book, user=self.owner,
            filename='fictional-repeat.csv', raw=b'fictional rules only', preview={
                'revision': book.revision, 'mapping': MAPPING, 'codes': codes,
                'import_stats': stats, 'settings': {'mode': mode, 'title': 'Another filename',
                    'policy': policy or book.policy, 'change_note': 'Synthetic duplicate check'}})

    def apply(self, draft):
        return apply_codebook_import(draft.pk, self.owner, draft.book)

    def test_exact_file_duplicates_are_reduced_and_counted(self):
        row = {'key': 'NEW', 'name': '新规则', 'definition': '虚构定义'}
        stats = {}
        codes = normalized_codes([row, row, dict(row)], MAPPING, stats=stats)
        self.assertEqual(len(codes), 1)
        self.assertEqual(stats, {'duplicate_count': 2, 'duplicate_file_count': 2,
                                 'duplicate_existing_count': 0})

    def test_same_identifier_different_field_is_not_an_exact_file_duplicate(self):
        row = self.rows()[0]
        for field, _, _ in FIELDS:
            if field == 'key':
                continue
            replacement = '#112233' if field == 'color' else '不同内容'
            with self.subTest(field=field), self.assertRaisesMessage(ValueError, '规则内容不同'):
                normalized_codes([row, {**row, field: replacement}], MAPPING)

    def test_same_definition_with_different_identifier_is_retained(self):
        row = {'key': 'A', 'name': '虚构名称', 'definition': '相同定义'}
        self.assertEqual(len(normalized_codes([row, {**row, 'key': 'B'}], MAPPING)), 2)

    def test_whitespace_and_equivalent_hex_color_have_same_stored_meaning(self):
        code = self.first
        code.color = '#AABBCC'
        code.save(update_fields=['color'])
        row = stored_code_values(code)
        row['definition'] = ' ' + row['definition'] + '\n'
        row['color'] = '#aabbcc'
        stats = {}
        self.assertEqual(normalized_codes([row], MAPPING, [code], stats), [])
        self.assertEqual(stats['duplicate_existing_count'], 1)

    def test_exact_existing_duplicate_preserves_original_and_all_fields_are_compared(self):
        row = stored_code_values(self.first)
        stats = {}
        self.assertEqual(normalized_codes([row, row], MAPPING, [self.first], stats), [])
        self.assertEqual(stats['duplicate_count'], 2)
        for field, _, _ in FIELDS:
            if field == 'key':
                continue
            replacement = '#112233' if field == 'color' else '不同内容'
            with self.subTest(field=field), self.assertRaisesMessage(ValueError, '不会覆盖旧代码'):
                normalized_codes([{**row, field: replacement}], MAPPING, [self.first])

    def test_duplicate_parent_can_be_reused_by_new_child(self):
        row = stored_code_values(self.first)
        child = {'key': 'CHILD', 'name': '子代码', 'definition': '虚构子规则', 'parent_key': self.first.key}
        result = normalized_codes([row, child], MAPPING, self.book.codes.select_related('parent'))
        self.assertEqual([entry['key'] for entry in result], ['CHILD'])

    def test_merge_is_offered_for_frozen_and_editable_books(self):
        for frozen in (True, False):
            self.book.frozen = frozen
            form = CodebookMappingForm(book=self.book, headers=['key', 'definition'])
            choices = dict(form.fields['mode'].choices)
            self.assertIn('merge', choices)
            self.assertEqual('append' in choices, not frozen)

    def test_frozen_merge_all_duplicates_is_noop_and_replay_safe(self):
        original = list(self.book.codes.values())
        revision = self.book.revision
        before = self.project.codebooks.count()
        draft = self.draft(self.rows())
        target, changed = self.apply(draft)
        self.assertFalse(changed)
        self.assertEqual(target.pk, self.book.pk)
        self.assertEqual(self.project.codebooks.count(), before)
        self.book.refresh_from_db()
        self.assertEqual(self.book.revision, revision)
        self.assertEqual(list(self.book.codes.values()), original)
        draft.refresh_from_db()
        self.assertEqual(draft.applied_book_id, self.book.pk)
        self.assertFalse(self.apply(draft)[1])
        self.assertEqual(AuditEvent.objects.filter(action='codebook.import_duplicates').count(), 1)

    def test_frozen_merge_preserves_all_existing_rules_and_hierarchy(self):
        row = stored_code_values(self.first)
        child = {'key': 'CHILD', 'name': '子代码', 'definition': '虚构子规则', 'parent_key': self.first.key}
        before = list(self.book.codes.values())
        draft = self.draft([row, child, child])
        target, changed = self.apply(draft)
        self.assertTrue(changed)
        self.assertNotEqual(target.pk, self.book.pk)
        self.assertEqual(target.policy, self.book.policy)
        self.assertEqual(target.title, self.book.title)
        self.assertEqual(target.codes.count(), 3)
        self.assertEqual(list(self.book.codes.values()), before)
        parent = target.codes.get(key=self.first.key)
        self.assertEqual(target.codes.get(key='CHILD').parent_id, parent.pk)
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)
        event = AuditEvent.objects.get(action='codebook.new_version', object_id=str(target.pk))
        self.assertEqual(event.after['from'], self.book.pk)
        self.assertEqual(event.after['code_map'][str(self.first.pk)], parent.pk)
        imported = AuditEvent.objects.get(action='codebook.import', object_id=str(target.pk))
        self.assertEqual(imported.after['code_count'], 1)
        self.assertEqual(imported.after['duplicate_count'], 2)

    def test_editable_merge_adds_only_new_rules_and_retains_own_policy(self):
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        original = list(current.codes.values())
        rows = self.rows(current) + [{'key': 'NEW', 'name': '新规则', 'definition': '虚构新定义'}]
        draft = self.draft(rows, book=current, policy='multi')
        target, changed = self.apply(draft)
        self.assertTrue(changed)
        self.assertEqual(target.pk, current.pk)
        self.assertEqual(target.policy, 'single')
        self.assertEqual(target.codes.count(), 3)
        self.assertEqual(list(target.codes.exclude(key='NEW').values()), original)

    def test_editable_append_all_duplicates_is_noop(self):
        current = begin_update(self.project, self.owner, self.book, self.book.revision)
        revision = current.revision
        draft = self.draft(self.rows(current), 'append', current)
        self.assertFalse(self.apply(draft)[1])
        current.refresh_from_db()
        self.assertEqual(current.revision, revision)
        self.assertEqual(current.codes.count(), 2)

    def test_second_file_with_same_current_rules_does_not_create_another_version(self):
        draft = self.draft([{'key': 'NEW', 'name': '新规则', 'definition': '虚构定义'}])
        target, _ = self.apply(draft)
        before = self.project.codebooks.count()
        repeat = self.draft(self.rows(target), book=target)
        self.assertFalse(self.apply(repeat)[1])
        self.assertEqual(self.project.codebooks.count(), before)
        self.assertEqual(self.project.codebooks.first().pk, target.pk)

    def test_explicit_new_same_rule_set_is_noop_regardless_filename_order(self):
        rows = list(reversed(self.rows()))
        self.assertTrue(same_codebook_codes(rows, self.book.codes.select_related('parent')))
        before = self.project.codebooks.count()
        draft = self.draft(rows, mode='new')
        target, changed = self.apply(draft)
        self.assertFalse(changed)
        self.assertEqual(target.pk, self.book.pk)
        self.assertEqual(self.project.codebooks.count(), before)

    def test_new_duplicate_audit_counts_each_source_row_only_once(self):
        rows = self.rows()
        draft = self.draft(rows + [rows[0]], mode='new')
        # The current preview already includes the identical existing rules in
        # its displayed skipped count; confirmation must not count them twice.
        draft.preview['import_stats']['duplicate_count'] += len(rows)
        draft.preview['unchanged'] = True
        draft.save(update_fields=['preview'])
        self.assertFalse(self.apply(draft)[1])
        event = AuditEvent.objects.get(action='codebook.import_duplicates')
        self.assertEqual(event.after['duplicate_count'], 3)

    def test_explicit_new_different_policy_is_intentional_change_not_duplicate(self):
        draft = self.draft(self.rows(), mode='new', policy='multi')
        target, changed = self.apply(draft)
        self.assertTrue(changed)
        self.assertNotEqual(target.pk, self.book.pk)
        self.assertEqual(target.policy, 'multi')
        self.assertEqual(target.codes.count(), 2)

    def test_explicit_new_can_replace_rules_without_modifying_old_code(self):
        original = stored_code_values(self.first)
        draft = self.draft([{**original, 'definition': '显式替换后的虚构定义'}], mode='new')
        target, changed = self.apply(draft)
        self.assertTrue(changed)
        self.assertEqual(target.codes.count(), 1)
        self.assertEqual(target.codes.get().definition, '显式替换后的虚构定义')
        self.first.refresh_from_db()
        self.assertEqual(stored_code_values(self.first), original)

    def test_merge_rechecks_conflicting_preview_before_writing_any_rules(self):
        row = stored_code_values(self.first)
        draft = self.draft([{'key': 'NEW', 'name': '新规则', 'definition': '虚构定义'}])
        draft.preview['codes'].append({**row, 'definition': '不能覆盖旧定义'})
        draft.save(update_fields=['preview'])
        before = self.project.codebooks.count()
        with self.assertRaisesMessage(ValueError, '不会覆盖旧代码'):
            self.apply(draft)
        draft.refresh_from_db()
        self.assertIsNone(draft.applied_book_id)
        self.assertEqual(self.project.codebooks.count(), before)
        self.assertFalse(Code.objects.filter(book__project=self.project, key='NEW').exists())

    def test_stale_duplicate_preview_cannot_bypass_revision_check(self):
        draft = self.draft(self.rows())
        Codebook.objects.filter(pk=self.book.pk).update(revision=self.book.revision + 1)
        with self.assertRaisesMessage(Conflict, '预览后编码本已变化'):
            self.apply(draft)

    def upload(self, rows, filename='fictional-rules.json'):
        self.client.force_login(self.owner)
        url = reverse('book_import', args=[self.project.pk, self.book.pk])
        response = self.client.post(url, {'action': 'upload', 'auto_preview': 'yes',
            'file': SimpleUploadedFile(filename, json.dumps(rows).encode('utf-8'))})
        self.assertEqual(response.status_code, 302)
        draft = CodebookImportDraft.objects.filter(book=self.book).latest('created_at')
        return response, draft

    def test_auto_preview_all_repeated_rules_can_be_confirmed_without_new_version(self):
        before = self.project.codebooks.count()
        response, draft = self.upload(self.rows() * 2)
        self.assertIn('stage=preview', response.url)
        self.assertEqual(draft.preview['codes'], [])
        self.assertEqual(draft.preview['import_stats']['duplicate_count'], 4)
        page = self.client.get(response.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '4')
        confirmed = self.client.post(reverse('book_import', args=[self.project.pk, self.book.pk]), {
            'action': 'confirm', 'draft_id': str(draft.pk), 'confirmed': 'yes'}, follow=True)
        self.assertContains(confirmed, '保留原编码本')
        self.assertEqual(self.project.codebooks.count(), before)
        self.assertEqual(self.book.codes.count(), 2)

    def test_auto_merge_keeps_unmentioned_existing_rule_while_adding_new_rule(self):
        row = stored_code_values(self.first)
        response, draft = self.upload([row, {'key': 'NEW', 'name': '新增', 'definition': '虚构定义'}])
        self.assertIn('stage=preview', response.url)
        self.assertEqual(draft.preview['settings']['mode'], 'merge')
        self.assertEqual([row['key'] for row in draft.preview['codes']], ['NEW'])
        self.assertEqual(draft.preview['import_stats']['duplicate_count'], 1)
        response = self.client.post(reverse('book_import', args=[self.project.pk, self.book.pk]), {
            'action': 'confirm', 'draft_id': str(draft.pk), 'confirmed': 'yes'})
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(set(draft.applied_book.codes.values_list('key', flat=True)), {'PLAN', 'CHECK', 'NEW'})

    def test_auto_merge_changed_existing_definition_is_not_silently_overwritten(self):
        row = stored_code_values(self.first)
        response, draft = self.upload([{**row, 'definition': '改动后的不同定义'}])
        self.assertIn('advanced=1', response.url)
        self.assertEqual(draft.preview, {})
        self.assertContains(self.client.get(response.url), '不会覆盖旧代码')
        self.first.refresh_from_db()
        self.assertEqual(self.first.definition, row['definition'])
