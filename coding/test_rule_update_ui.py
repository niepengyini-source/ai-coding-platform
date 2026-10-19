"""Rule-update HTTP regressions use only the temporary test database."""
import json
import uuid

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .book_updates import apply_book_update, begin_update
from .models import Assignment, AuditEvent, Code, Codebook, Round
from .round_forms import RoundForm
from .services import save_assignment
from .tests import TEST_SETTINGS, fixture
from .workflow_forms import QuickStartForm


@override_settings(**TEST_SETTINGS)
class RuleUpdateUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.book.refresh_from_db()
        self.r.refresh_from_db()
        self.client.force_login(self.owner)
        self.entry = self.r.assignments.filter(coder=self.owner).order_by('pk').first()

    def url(self, name, book=None):
        return reverse(name, args=[self.project.pk, (book or self.book).pk])

    def draft(self):
        return begin_update(self.project, self.owner, self.book, self.book.revision)

    def code_data(self, book, **changes):
        return {'key': 'NEW', 'name': '虚构新增规则', 'definition': '虚构新增规则的定义',
                'revision': book.revision, **changes}

    def apply_data(self, book, **changes):
        return {'revision': book.revision, 'confirmed': 'yes', 'round_ids': [self.r.pk],
                f'round_revision_{self.r.pk}': self.r.revision, **changes}

    def apply(self, book):
        apply_book_update(self.project, self.owner, book, book.revision,
                          {self.r.pk: self.r.revision})
        self.r.refresh_from_db()
        self.entry.refresh_from_db()

    def save_data(self, **changes):
        return {'primary': self.first.pk, 'secondary': [], 'uncertain': False,
                'no_code': False, 'note': '虚构判断说明', 'action': 'save',
                'revision': self.entry.revision, 'token': uuid.uuid4().hex, **changes}

    def post_json(self, name, data):
        return self.client.post(reverse(name, args=[self.entry.pk]), json.dumps(data),
                                content_type='application/json')

    def test_update_requires_post_and_get_never_creates_a_version(self):
        before = (Codebook.objects.count(), AuditEvent.objects.count())
        self.assertEqual(self.client.get(self.url('book_update')).status_code, 405)
        self.assertEqual(self.client.get(self.url('book_apply')).status_code, 405)
        self.assertEqual((Codebook.objects.count(), AuditEvent.objects.count()), before)

    def test_update_clones_frozen_rules_once_and_reopens_the_same_editor(self):
        response = self.client.post(self.url('book_update'), {'revision': self.book.revision})
        book = self.project.codebooks.first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url('codebook', book) + '#code-editor')
        self.assertEqual(book.codes.count(), self.book.codes.count())
        self.assertFalse(book.frozen)
        self.assertEqual(book.version, 2)
        again = self.client.post(self.url('book_update'), {'revision': self.book.revision})
        self.assertEqual(again.url, response.url)
        self.assertEqual(self.project.codebooks.count(), 2)
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)

    def test_stale_update_request_does_not_create_a_version(self):
        response = self.client.post(self.url('book_update'),
                                    {'revision': self.book.revision + 1}, follow=True)
        self.assertContains(response, '编码本已变化')
        self.assertEqual(self.project.codebooks.count(), 1)

    def test_current_editor_adds_and_edits_without_reimporting(self):
        book = self.draft()
        response = self.client.post(self.url('codebook', book), self.code_data(book))
        self.assertRedirects(response, self.url('codebook', book))
        code = book.codes.get(key='NEW')
        book.refresh_from_db()
        response = self.client.post(self.url('codebook', book), self.code_data(book,
            code_id=code.pk, name='修订后的虚构名称', definition='修订后的虚构定义'))
        self.assertRedirects(response, self.url('codebook', book))
        code.refresh_from_db()
        self.assertEqual((code.name, code.definition), ('修订后的虚构名称', '修订后的虚构定义'))
        self.assertEqual(self.book.codes.count(), 2)
        self.assertEqual(book.codes.count(), 3)

    def test_duplicate_key_and_stale_editor_preserve_existing_rules(self):
        book = self.draft()
        response = self.client.post(self.url('codebook', book), self.code_data(book, key='PLAN'))
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(book.codes.count(), 2)
        self.client.post(self.url('codebook', book), self.code_data(book))
        response = self.client.post(self.url('codebook', book), self.code_data(book, key='STALE'))
        self.assertContains(response, '其他人已保存编码本变化')
        self.assertFalse(book.codes.filter(key='STALE').exists())

    def test_parent_cycle_and_foreign_parent_are_rejected(self):
        book = self.draft()
        first, second = book.codes.get(key='PLAN'), book.codes.get(key='CHECK')
        second.parent = first
        second.save()
        response = self.client.post(self.url('codebook', book), self.code_data(book,
            code_id=first.pk, key=first.key, name=first.name, parent=second.pk))
        self.assertTrue(response.context['form'].errors['parent'])
        first.refresh_from_db()
        self.assertIsNone(first.parent_id)
        response = self.client.post(self.url('codebook', book),
                                    self.code_data(book, parent=self.foreign_code.pk))
        self.assertTrue(response.context['form'].errors['parent'])
        self.assertFalse(book.codes.filter(key='NEW').exists())

    def test_older_editable_copy_is_read_only_after_current_book_changes(self):
        old = self.draft()
        current = Codebook.objects.create(project=self.project, version=3, title='当前虚构规则')
        response = self.client.post(self.url('codebook', old), self.code_data(old), follow=True)
        self.assertContains(response, '只能编辑当前编码本')
        self.assertEqual(response.context['book'].pk, current.pk)
        self.assertFalse(old.codes.filter(key='NEW').exists())
        page = self.client.get(self.url('codebook', old))
        self.assertNotContains(page, 'id="code-editor"')
        code = old.codes.first()
        response = self.client.post(reverse('code_remove', args=[self.project.pk, code.pk]),
                                    {'revision': old.revision}, follow=True)
        self.assertContains(response, '只能修改当前编码本')
        self.assertTrue(Code.objects.filter(pk=code.pk).exists())

    def test_project_and_task_forms_offer_one_current_book(self):
        book = self.draft()
        response = self.client.get(reverse('project', args=[self.project.pk]))
        html = response.content.decode()
        # Old round names may mention their historical rules, but the rules card has one link.
        rules = html.split('<h2>编码规则</h2>', 1)[1].split('</section>', 1)[0]
        self.assertIn(self.url('codebook', book), rules)
        self.assertNotIn(self.url('codebook'), rules)
        quick = QuickStartForm(project=self.project)
        self.assertEqual(list(quick.fields['book_id'].queryset), [book])
        planned = RoundForm(project=self.project)
        self.assertEqual([pk for pk, label in planned.fields['book_id'].choices], [book.pk])
        rejected = QuickStartForm({'book_id': self.book.pk, 'token': uuid.uuid4()}, project=self.project)
        self.assertFalse(rejected.is_valid())
        self.assertIn('book_id', rejected.errors)

    def test_apply_requires_explicit_confirmation(self):
        book = self.draft()
        data = self.apply_data(book, confirmed='')
        response = self.client.post(self.url('book_apply', book), data, follow=True)
        self.assertContains(response, '请确认所选批次')
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)
        self.assertFalse(AuditEvent.objects.filter(action='round.book_update').exists())

    def test_apply_updates_only_the_selected_round_and_preserves_answer(self):
        other = Round.objects.create(project=self.project, book=self.book, name='未选择的虚构批次', kind='pilot')
        save_assignment(self.entry.pk, self.owner, self.save_data(action='submit'))
        book = self.draft()
        response = self.client.post(self.url('book_apply', book), self.apply_data(book), follow=True)
        self.assertContains(response, '已更新 1 个进行中的批次')
        self.r.refresh_from_db()
        other.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.r.book_id, book.pk)
        self.assertEqual(other.book_id, self.book.pk)
        self.assertEqual(self.entry.primary_id, book.codes.get(key='PLAN').pk)
        self.assertEqual((self.entry.status, self.entry.revision), ('draft', 2))
        self.assertEqual(self.entry.unit.text, self.units[0].text)

    def test_stale_round_and_nonactive_round_cannot_be_applied(self):
        book = self.draft()
        response = self.client.post(self.url('book_apply', book),
            self.apply_data(book, **{f'round_revision_{self.r.pk}': self.r.revision + 1}), follow=True)
        self.assertContains(response, '编码批次已变化')
        for state in ['review', 'closed']:
            Round.objects.filter(pk=self.r.pk).update(state=state)
            response = self.client.post(self.url('book_apply', book), self.apply_data(book), follow=True)
            self.assertContains(response, '只能更新进行中的批次')
            self.r.refresh_from_db()
            self.assertEqual(self.r.book_id, self.book.pk)

    def test_apply_rejects_foreign_round_ids_without_partial_mutation(self):
        book = self.draft()
        foreign = Round.objects.create(project=self.other, book=self.foreign_code.book,
                                       name='其他项目的虚构批次', kind='pilot')
        response = self.client.post(self.url('book_apply', book), self.apply_data(book,
            round_ids=[self.r.pk, foreign.pk], **{f'round_revision_{foreign.pk}': foreign.revision}), follow=True)
        self.assertContains(response, '请选择本项目的进行中批次')
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)
        self.assertFalse(AuditEvent.objects.filter(action='round.book_update').exists())

    def test_only_managers_may_update_or_apply_and_foreign_book_is_hidden(self):
        book = self.draft()
        for actor, status in [(self.coder, 403), (self.viewer, 403), (self.outsider, 404)]:
            self.client.force_login(actor)
            for name, data in [('book_update', {'revision': book.revision}),
                               ('book_apply', self.apply_data(book))]:
                self.assertEqual(self.client.post(self.url(name, book), data).status_code, status)
        self.client.force_login(self.owner)
        for name in ['book_update', 'book_apply']:
            self.assertEqual(self.client.post(self.url(name, self.foreign_code.book),
                self.apply_data(book)).status_code, 404)
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)

    def test_unsaved_old_tabs_cannot_bypass_rules_guard_with_no_code_or_uncertain(self):
        book = self.draft()
        self.apply(book)
        self.assertEqual(self.entry.revision, 0)
        for selection in [{'primary': None, 'no_code': True},
                          {'primary': None, 'uncertain': True, 'note': '虚构待核对原因'}]:
            for book_field in [{}, {'book_id': self.book.pk}]:
                response = self.post_json('assignment_save', self.save_data(**selection, **book_field))
                self.assertEqual(response.status_code, 409)
                self.assertIn('编码规则已更新', response.json()['error'])
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.revision, self.entry.no_code, self.entry.uncertain), (0, False, False))

    def test_current_book_save_payload_works_and_workbench_emits_book_identity(self):
        book = self.draft()
        self.apply(book)
        page = self.client.get(reverse('workbench', args=[self.r.pk]), {'a': self.entry.pk})
        self.assertContains(page, f'data-book-id="{book.pk}"')
        response = self.post_json('assignment_save', self.save_data(
            book_id=book.pk, primary=book.codes.get(key='PLAN').pk))
        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.revision, self.entry.primary_id), (1, book.codes.get(key='PLAN').pk))

    def test_split_requires_current_book_and_keeps_original_metadata(self):
        book = self.draft()
        self.apply(book)
        before = Assignment.objects.count()
        data = {'cuts': [2, 4], 'revision': 0, 'token': str(uuid.uuid4())}
        for book_field in [{}, {'book_id': self.book.pk}]:
            response = self.post_json('assignment_split', {**data, **book_field})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(Assignment.objects.count(), before)
        response = self.post_json('assignment_split', {**data, 'book_id': book.pk})
        self.assertEqual(response.status_code, 200)
        children = list(self.entry.segments.select_related('unit__record').order_by('unit__start'))
        self.assertEqual(len(children), 3)
        self.assertEqual(''.join(a.unit.text for a in children), self.entry.unit.text)
        for child in children:
            self.assertEqual(child.unit.record.metadata, self.entry.unit.record.metadata)

    def test_history_retains_old_and_new_code_labels_and_records_rules_update(self):
        save_assignment(self.entry.pk, self.owner, self.save_data(action='submit'))
        book = self.draft()
        mapped = book.codes.get(key='PLAN')
        mapped.key, mapped.name = 'REVISED', '修订后的计划'
        mapped.save()
        self.apply(book)
        response = self.client.get(reverse('history_detail', args=[self.project.pk, self.entry.pk]))
        self.assertContains(response, 'PLAN · 计划')
        self.assertContains(response, 'REVISED · 修订后的计划')
        self.assertContains(response, '更新编码规则')
        event = AuditEvent.objects.get(action='annotation.rules_update', object_id=str(self.entry.pk))
        self.assertEqual((event.before['book_id'], event.after['book_id']), (self.book.pk, book.pk))
        self.assertEqual((event.before['primary'], event.after['primary']), (self.first.pk, mapped.pk))
        self.assertEqual((event.before['status'], event.after['status']), ('submitted', 'draft'))

    def test_update_and_apply_have_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for name in ['book_update', 'book_apply']:
            self.assertEqual(client.post(self.url(name), {'revision': self.book.revision}).status_code, 403)
        self.assertEqual(self.project.codebooks.count(), 1)
