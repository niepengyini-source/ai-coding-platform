"""Preparation confirmation keeps existing coding and permission boundaries."""
import uuid

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuditEvent, Code, Codebook, Membership, Round
from .tests import TEST_SETTINGS, fixture
from .workflow_forms import QuickStartForm


@override_settings(**TEST_SETTINGS)
class StartConfirmationTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.coder)
        self.url = reverse('quick_start', args=[self.project.pk])

    def start_data(self, **changes):
        return {'book_id': self.book.pk, 'token': str(uuid.uuid4()), **changes}

    def test_current_rule_is_hidden_but_summary_is_visible(self):
        response = self.client.get(self.url)
        self.assertContains(response, '确认编码准备')
        self.assertContains(response, '3 个编码单元')
        self.assertContains(response, '初始编码本 · v1')
        self.assertContains(response, '2 个代码 · 单标签')
        self.assertContains(response, 'type="hidden" name="book_id"')
        self.assertNotContains(response, '<select name="book_id"')
        self.assertTrue(response.context['form'].fields['book_id'].widget.is_hidden)

    def test_get_and_refresh_do_not_start_or_freeze_again(self):
        rounds = Round.objects.count()
        self.book.refresh_from_db()
        revision = self.book.revision
        events = AuditEvent.objects.count()
        for _ in range(2):
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.book.refresh_from_db()
        self.assertEqual(Round.objects.count(), rounds)
        self.assertEqual(AuditEvent.objects.count(), events)
        self.assertEqual(self.book.revision, revision)

    def test_stale_book_is_rejected_and_current_summary_remains_visible(self):
        original = self.client.get(self.url).context['form'].initial
        current = Codebook.objects.create(project=self.project, version=2, title='新的当前规则')
        Code.objects.create(book=current, key='NEW', name='新规则', definition='虚构定义')
        response = self.client.post(self.url, original)
        self.assertContains(response, '当前编码本已更新或不可用')
        self.assertContains(response, '新的当前规则 · v2')
        self.assertEqual(response.context['selected_book'], current)
        self.assertFalse(Round.objects.filter(kind='quick').exists())

    def test_hidden_book_tampering_and_missing_book_are_rejected(self):
        for value in [self.foreign_code.book_id, '', 'not-a-book', 999999999999]:
            with self.subTest(book=value):
                response = self.client.post(self.url, self.start_data(book_id=value))
                self.assertEqual(response.status_code, 200)
                self.assertIn('book_id', response.context['form'].errors)
                self.assertFalse(Round.objects.filter(kind='quick').exists())

    def test_invalid_token_cannot_create_round_with_hidden_book(self):
        response = self.client.post(self.url, self.start_data(token='invalid'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.context['form'].errors)
        self.assertFalse(Round.objects.filter(kind='quick').exists())

    def test_resume_has_one_primary_action_and_does_not_create_on_get(self):
        data = self.start_data()
        self.client.post(self.url, data)
        personal = Round.objects.get(kind='quick')
        response = self.client.get(self.url)
        self.assertContains(response, '继续我的编码 →', count=1)
        self.assertNotContains(response, '<button class="button" type="submit">')
        self.assertNotContains(response, '开始个人编码 →')
        self.assertContains(response, reverse('workbench', args=[personal.pk]))
        self.assertEqual(Round.objects.filter(kind='quick').count(), 1)
        self.client.post(self.url, data)
        self.client.post(self.url, self.start_data())
        self.assertEqual(Round.objects.filter(kind='quick').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='quick.start').count(), 1)

    def test_team_arrangement_remains_available_to_manager_only(self):
        team_url = reverse('round_new', args=[self.project.pk])
        self.assertNotContains(self.client.get(self.url), team_url)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url), team_url)

    def test_form_still_accepts_only_current_project_book(self):
        form = QuickStartForm(self.start_data(), project=self.project)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['book_id'], self.book)


@override_settings(**TEST_SETTINGS)
class RoundCreationDestinationTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.url = reverse('round_new', args=[self.project.pk])

    def arrange(self, actor, coder_ids):
        self.client.force_login(actor)
        response = self.client.post(self.url, {
            'name': '虚构的准备确认测试轮次', 'kind': 'pilot', 'book_id': self.book.pk,
            'coder_ids': coder_ids, 'count': 0, 'seed': 2026, 'stratify': '',
            'distribution': 'all', 'double_percent': 25, 'unit_ids': '',
        })
        return response, Round.objects.filter(project=self.project).latest('pk')

    def test_owner_without_own_tasks_goes_to_team_progress(self):
        response, current = self.arrange(self.owner, [self.coder.pk])
        self.assertRedirects(response, reverse('progress', args=[self.project.pk]))
        self.assertEqual(current.assignments.count(), 3)
        self.assertFalse(current.assignments.filter(coder=self.owner).exists())

    def test_owner_with_own_tasks_goes_to_workbench(self):
        response, current = self.arrange(self.owner, [self.owner.pk, self.coder.pk])
        self.assertRedirects(response, reverse('workbench', args=[current.pk]))
        self.assertEqual(current.assignments.count(), 6)

    def test_reviewer_without_own_tasks_goes_to_team_progress(self):
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        response, current = self.arrange(self.coder, [self.owner.pk])
        self.assertRedirects(response, reverse('progress', args=[self.project.pk]))
        self.assertFalse(current.assignments.filter(coder=self.coder).exists())

    def test_reviewer_with_own_tasks_goes_to_workbench(self):
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        response, current = self.arrange(self.coder, [self.coder.pk])
        self.assertRedirects(response, reverse('workbench', args=[current.pk]))

    def test_regular_member_still_cannot_arrange_team_tasks(self):
        response, _ = self.arrange(self.coder, [self.coder.pk])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Round.objects.filter(project=self.project).count(), 1)
