"""Keyboard skip targets navigate private drafts without altering annotations."""
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Assignment, AuditEvent, Unit
from .tests import TEST_SETTINGS, fixture
from .workflow import coding_assignments, next_coding_url


@override_settings(**TEST_SETTINGS)
class KeyboardSkipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.entries = list(coding_assignments(self.r, self.owner))

    def url(self, entry):
        return reverse('workbench', args=[self.r.pk]) + '?a=' + str(entry.pk)

    def progress_url(self):
        return reverse('progress', args=[self.project.pk])

    def test_skip_advances_in_source_order_without_bouncing_to_first_draft(self):
        first, second, third = self.entries
        response = self.client.get(self.url(first))
        self.assertEqual(response.context['skip_url'], self.url(second))
        response = self.client.get(response.context['skip_url'])
        self.assertEqual(response.context['assignment'].pk, second.pk)
        self.assertEqual(response.context['skip_url'], self.url(third))
        self.assertEqual(response.context['next_url'], self.url(third))

    def test_editable_workbench_has_skip_buttons_in_actions_and_quick_picker_without_a_code(self):
        response = self.client.get(self.url(self.entries[0]))
        self.assertIsNone(response.context['assignment'].primary_id)
        self.assertContains(response, 'data-shortcut-skip disabled', count=2)
        html = response.content.decode()
        self.assertLess(html.index('data-shortcut-skip disabled'), html.index('data-primary-fallback'))
        self.assertIn('data-shortcut-skip disabled', html.split('<dialog ', 1)[1])
        self.assertContains(response, '跳过当前条')
        self.assertContains(response, 'data-shortcut-hint="skip">Alt+Enter</kbd>')
        self.assertContains(response, 'data-coding-next>下一个 →</a>')

    def test_submitted_and_locked_workbenches_have_no_active_skip_button(self):
        current = self.entries[0]
        Assignment.objects.filter(pk=current.pk).update(status='submitted', primary=self.first)
        self.assertNotContains(self.client.get(self.url(current)), 'data-shortcut-skip disabled')
        self.assertNotContains(self.client.get(self.url(current)), 'data-coding-next')
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        self.assertNotContains(self.client.get(self.url(self.entries[1])), 'data-shortcut-skip disabled')

    def test_viewer_without_private_assignments_has_no_skip_button(self):
        self.client.force_login(self.viewer)
        self.assertNotContains(self.client.get(f'/r/{self.r.pk}/'), 'data-shortcut-skip disabled')

    def test_help_retains_customizable_skip_keyboard_binding(self):
        response = self.client.get('/guide/')
        self.assertContains(response, 'data-shortcut-record="skip"')
        self.assertContains(response, '设置跳过当前条的快捷键')

    def test_confirm_after_skip_continues_forward_before_revisiting_skipped_records(self):
        first, second, third = self.entries
        Assignment.objects.filter(pk=second.pk).update(status='submitted', primary=self.first)
        self.assertEqual(next_coding_url(second), self.url(third))
        Assignment.objects.filter(pk=third.pk).update(status='submitted', primary=self.first)
        self.assertEqual(next_coding_url(third), self.url(first))

    def test_last_draft_wraps_to_first_in_source_order(self):
        first, _, third = self.entries
        response = self.client.get(self.url(third))
        self.assertEqual(response.context['skip_url'], self.url(first))

    def test_skip_ignores_submitted_and_superseded_entries(self):
        first, second, third = self.entries
        Assignment.objects.filter(pk=second.pk).update(status='submitted', primary=self.first)
        hidden_unit = Unit.objects.create(record=first.unit.record, start=1, end=2,
                                          text=first.unit.text[1:2])
        Assignment.objects.create(round=self.r, unit=hidden_unit, coder=self.owner,
                                  superseded=True)
        response = self.client.get(self.url(first))
        self.assertEqual(response.context['skip_url'], self.url(third))

    def test_skipping_only_remaining_draft_goes_to_progress_not_another_coder(self):
        current = self.entries[1]
        Assignment.objects.filter(round=self.r, coder=self.owner).exclude(pk=current.pk).update(
            status='submitted', primary=self.first)
        response = self.client.get(self.url(current))
        self.assertEqual(response.context['skip_url'], self.progress_url())
        progress = self.client.get(response.context['skip_url'])
        self.assertEqual(progress.status_code, 200)
        current.refresh_from_db()
        self.assertEqual((current.status, current.revision), ('draft', 0))

    def test_skip_url_always_stays_with_the_signed_in_coder(self):
        self.client.force_login(self.coder)
        coder_entries = list(coding_assignments(self.r, self.coder))
        response = self.client.get(self.url(coder_entries[0]))
        self.assertEqual(response.context['skip_url'], self.url(coder_entries[1]))
        self.assertEqual(self.client.get(self.url(self.entries[0])).status_code, 404)

    def test_outsider_and_anonymous_users_cannot_open_skip_targets(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url(self.entries[0])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url(self.entries[0])).status_code, 302)

    def test_get_workbench_and_skip_target_never_saves_or_submits(self):
        first, second, _ = self.entries
        Assignment.objects.filter(pk=first.pk).update(primary=self.first, note='虚构草稿', revision=3)
        before = list(Assignment.objects.order_by('pk').values())
        audit_count = AuditEvent.objects.count()
        response = self.client.get(self.url(first))
        self.assertEqual(response.context['skip_url'], self.url(second))
        self.assertEqual(self.client.get(response.context['skip_url']).status_code, 200)
        self.assertEqual(list(Assignment.objects.order_by('pk').values()), before)
        self.assertEqual(AuditEvent.objects.count(), audit_count)

    def test_no_private_assignments_have_only_progress_as_skip_target(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('workbench', args=[self.r.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['assignment'])
        self.assertEqual(response.context['skip_url'], self.progress_url())
