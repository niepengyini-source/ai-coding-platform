"""Last-task checks use only the current member's effective assignments."""
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Assignment, AuditEvent, Unit
from .tests import TEST_SETTINGS, fixture
from .workflow import coding_assignments


@override_settings(**TEST_SETTINGS)
class TaskCompletionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.entries = list(coding_assignments(self.r, self.owner))
        self.api = reverse('round_progress', args=[self.r.pk])

    def url(self, entry):
        return reverse('workbench', args=[self.r.pk]) + '?a=' + str(entry.pk)

    def test_last_task_keeps_next_button_and_save_task_dialog(self):
        response = self.client.get(self.url(self.entries[-1]))
        self.assertContains(response, 'data-coding-last disabled>下一个 →</button>')
        self.assertContains(response, '已经是最后一个了')
        self.assertContains(response, 'data-task-save>保存任务</button>')
        self.assertContains(response, 'data-projects-url="/projects/"')
        self.assertContains(response, 'task_navigation.js')
        self.assertNotContains(self.client.get(self.url(self.entries[0])), 'data-task-completion aria')

    def test_submitted_last_task_can_still_show_completion_choices(self):
        Assignment.objects.filter(pk=self.entries[-1].pk).update(status='submitted', primary=self.first)
        self.assertContains(self.client.get(self.url(self.entries[-1])), 'data-coding-last disabled')

    def test_remaining_count_includes_drafts_and_prefers_another_unfinished_unit(self):
        first, second, last = self.entries
        Assignment.objects.filter(pk=first.pk).update(primary=self.first, revision=1)
        Assignment.objects.filter(pk=second.pk).update(status='submitted', primary=self.first)
        data = self.client.get(self.api, {'current': last.pk}).json()
        self.assertEqual((data['my_remaining'], data['my_done'], data['my_total']), (2, 1, 3))
        self.assertEqual(data['next_unfinished_url'], self.url(first))

    def test_only_current_draft_returns_current_not_someone_elses_tasks(self):
        last = self.entries[-1]
        Assignment.objects.filter(round=self.r, coder=self.owner).exclude(pk=last.pk).update(status='submitted', primary=self.first)
        data = self.client.get(self.api, {'current': last.pk}).json()
        self.assertEqual(data['my_remaining'], 1)
        self.assertEqual(data['next_unfinished_url'], self.url(last))

    def test_completed_count_ignores_other_members_and_superseded_tasks(self):
        Assignment.objects.filter(round=self.r, coder=self.owner).update(status='submitted', primary=self.first)
        unit = Unit.objects.create(record=self.doc.records.first(), start=0, end=1, text='虚')
        Assignment.objects.create(round=self.r, coder=self.owner, unit=unit, superseded=True)
        response = self.client.get(self.api, {'current': self.entries[-1].pk})
        self.assertEqual(response.json()['my_remaining'], 0)
        self.assertEqual(response.json()['next_unfinished_url'], '')
        self.assertEqual(response.json()['my_total'], 3)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_empty_viewer_has_no_next_private_task(self):
        self.client.force_login(self.viewer)
        data = self.client.get(self.api).json()
        self.assertEqual((data['my_remaining'], data['next_unfinished_url']), (0, ''))

    def test_foreign_superseded_and_invalid_current_ids_are_rejected(self):
        peer = self.r.assignments.filter(coder=self.coder).first()
        for current in (peer.pk, 'invalid', '0'):
            self.assertEqual(self.client.get(self.api, {'current': current}).status_code, 404)
        Assignment.objects.filter(pk=self.entries[0].pk).update(superseded=True)
        self.assertEqual(self.client.get(self.api, {'current': self.entries[0].pk}).status_code, 404)

    def test_progress_check_does_not_save_submit_or_add_history(self):
        before = list(Assignment.objects.order_by('pk').values())
        audit = AuditEvent.objects.count()
        self.client.get(self.api, {'current': self.entries[-1].pk})
        self.assertEqual(before, list(Assignment.objects.order_by('pk').values()))
        self.assertEqual(audit, AuditEvent.objects.count())

    def test_private_round_and_outsider_require_access(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.api).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.api).status_code, 302)
