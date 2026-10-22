"""Visible correction controls preserve submitted answers and fictional histories."""
import json
import uuid

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Assignment, AuditEvent, Round
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class CorrectionEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.task = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        self.task.primary = self.first
        self.task.note = '虚构旧判断依据'
        self.task.status = 'submitted'
        self.task.revision = 1
        self.task.save()

    def page(self, task=None):
        task = task or self.task
        return self.client.get(reverse('workbench', args=[task.round_id]), {'a': task.pk})

    def test_owner_can_find_correction_without_searching_history(self):
        before = AuditEvent.objects.count()
        response = self.page()
        self.assertContains(response, 'correction-entry')
        self.assertContains(response, '已提交 · 退回并修改')
        self.assertContains(response, reverse('history_reopen', args=[self.project.pk, self.task.pk]))
        self.assertContains(response, '旧编码保留在历史中')
        self.assertEqual(AuditEvent.objects.count(), before)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'submitted')

    def test_team_coder_cannot_reopen_submitted_answer_without_reviewer(self):
        self.client.force_login(self.coder)
        task = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        task.status = 'submitted'
        task.primary = self.first
        task.revision = 1
        task.save()
        response = self.page(task)
        self.assertNotContains(response, 'correction-entry')
        self.assertContains(response, '请负责人或复核者退回后修改')
        denied = self.client.post(reverse('history_reopen', args=[self.project.pk, task.pk]),
                                  {'revision': task.revision, 'reason': '虚构误提交'})
        self.assertEqual(denied.status_code, 403)

    def test_personal_coder_can_find_own_correction_entry(self):
        quick = Round.objects.create(project=self.project, book=self.book, name='虚构个人任务',
                                     kind='quick', started_by=self.coder)
        task = Assignment.objects.create(round=quick, coder=self.coder, unit=self.units[0],
                                         status='submitted', primary=self.first, revision=1)
        self.client.force_login(self.coder)
        self.assertContains(self.page(task), 'correction-entry')

    def test_review_or_closed_round_has_no_correction_entry(self):
        for state, message in [('review', '本轮已进入协商'), ('closed', '本批已结束')]:
            with self.subTest(state=state):
                self.r.state = state
                self.r.save(update_fields=['state'])
                response = self.page()
                self.assertNotContains(response, 'correction-entry')
                self.assertContains(response, message)

    def test_draft_has_clear_controls_not_reopen_controls(self):
        self.task.status = 'draft'
        self.task.save()
        response = self.page()
        self.assertContains(response, '清除主代码')
        self.assertNotContains(response, 'correction-entry')

    def test_reopen_then_clear_preserves_original_choice_and_reason_in_history(self):
        url = reverse('history_reopen', args=[self.project.pk, self.task.pk])
        response = self.client.post(url, {'revision': self.task.revision, 'reason': '虚构误选，退回核对'})
        self.assertRedirects(response, reverse('workbench', args=[self.r.pk]) + f'?a={self.task.pk}')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'draft')
        self.assertEqual(self.task.primary_id, self.first.pk)
        event = AuditEvent.objects.get(action='annotation.reopen', object_id=str(self.task.pk))
        self.assertEqual(event.before['primary'], self.first.pk)
        self.assertEqual(event.reason, '虚构误选，退回核对')
        response = self.client.post(reverse('assignment_save', args=[self.task.pk]),
            data=json.dumps({'action': 'save', 'revision': self.task.revision, 'token': str(uuid.uuid4()),
                             'book_id': self.book.pk, 'primary': None, 'secondary': [],
                             'no_code': False, 'uncertain': False, 'note': self.task.note}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.primary_id)
        self.assertEqual(self.task.note, '虚构旧判断依据')
        event = AuditEvent.objects.get(action='annotation.save', object_id=str(self.task.pk))
        self.assertEqual(event.before['primary'], self.first.pk)
        self.assertIsNone(event.after['primary'])

    def test_invalid_reason_or_old_revision_does_not_reopen(self):
        url = reverse('history_reopen', args=[self.project.pk, self.task.pk])
        for values in ({'revision': 1, 'reason': ''}, {'revision': 0, 'reason': '虚构旧版本'}):
            with self.subTest(values=values):
                self.client.post(url, values)
                self.task.refresh_from_db()
                self.assertEqual(self.task.status, 'submitted')
                self.assertEqual(self.task.revision, 1)

    def test_project_invitation_is_visible_in_header_only_to_owner(self):
        response = self.client.get(reverse('project', args=[self.project.pk]))
        self.assertContains(response, '>邀请成员</a>', count=1)
        self.assertContains(response, reverse('team_setup', args=[self.project.pk]))
        self.client.force_login(self.coder)
        self.assertNotContains(self.client.get(reverse('project', args=[self.project.pk])), '>邀请成员</a>')
