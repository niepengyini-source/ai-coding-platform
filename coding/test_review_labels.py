"""Chinese review labels describe the judgment, not submission or finalization."""
import re

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuditEvent
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class ReviewLabelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.task = self.r.assignments.get(coder=self.owner, unit=self.units[0])

    def marker(self):
        before = AuditEvent.objects.count()
        response = self.client.get(reverse('workbench', args=[self.r.pk]), {'a': self.task.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.count(), before)
        match = re.search(r'data-review-marker[^>]*>([^<]+)</span>', response.content.decode())
        self.assertIsNotNone(match)
        self.assertContains(response, '仅表示当前判断是否需复核，不代表已提交或定稿')
        self.assertContains(response, 'name="uncertain">需复核')
        return match.group(1)

    def test_untouched_and_note_only_records_are_not_confirmed(self):
        self.assertEqual(self.marker(), '未判断')
        self.task.note = '虚构备注，但还没有作出判断'
        self.task.revision = 1
        self.task.save()
        self.assertEqual(self.marker(), '未判断')

    def test_chosen_primary_is_confirmed_without_claiming_submission(self):
        self.task.primary = self.first
        self.task.revision = 1
        self.task.save()
        self.assertEqual(self.marker(), '已确认')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'draft')
        self.assertFalse(self.task.uncertain)

    def test_uncertain_true_is_review_and_is_not_reversed(self):
        self.task.primary = self.first
        self.task.uncertain = True
        self.task.note = '虚构疑点，需进一步核对'
        self.task.revision = 1
        self.task.save()
        self.assertEqual(self.marker(), '需复核')
        self.task.refresh_from_db()
        self.assertTrue(self.task.uncertain)

    def test_explicit_no_code_is_still_a_judgment(self):
        self.task.no_code = True
        self.task.revision = 1
        self.task.save()
        self.assertEqual(self.marker(), '已确认')

    def test_submission_does_not_clear_review_flag(self):
        self.task.primary = self.first
        self.task.uncertain = True
        self.task.note = '虚构待复核依据'
        self.task.status = 'submitted'
        self.task.revision = 1
        self.task.save()
        self.assertEqual(self.marker(), '需复核')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'submitted')
        self.assertTrue(self.task.uncertain)
