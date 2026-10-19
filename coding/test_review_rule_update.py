"""Review forms never treat an older cycle's decision as the current conclusion."""
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from .book_updates import apply_book_update, begin_update
from .models import AuditEvent, Decision, Round
from .services import annotation_data, save_decision
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class ReviewRuleUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.book.refresh_from_db()
        self.r.refresh_from_db()
        self.book.policy = 'multi'
        self.book.save(update_fields=['policy'])
        self.client.force_login(self.owner)

    def review(self):
        return self.client.get(reverse('review', args=[self.r.pk]), {'unit': self.units[0].pk})

    def decision(self, **changes):
        Round.objects.filter(pk=self.r.pk).update(state='review')
        return save_decision(self.r, self.units[0], self.owner, {
            'revision': 0, 'primary': self.first.pk, 'secondary': [self.second.pk],
            'uncertain': True, 'no_code': False, 'note': '仅属于旧结论的虚构备注', **changes})

    def update_rules(self):
        Round.objects.filter(pk=self.r.pk).update(state='active')
        book = begin_update(self.project, self.owner, self.book, self.book.revision)
        apply_book_update(self.project, self.owner, book, book.revision,
                          {self.r.pk: self.r.revision})
        Round.objects.filter(pk=self.r.pk).update(state='review')
        self.r.refresh_from_db()
        return book

    def assert_empty_form(self, response, revision):
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['decision'])
        self.assertEqual(response.context['decision_data'], {
            'revision': revision, 'primary': None, 'secondary': [],
            'uncertain': False, 'no_code': False, 'note': ''})
        self.assertContains(response, '尚未定稿')

    def test_missing_decision_starts_empty_at_revision_zero(self):
        Round.objects.filter(pk=self.r.pk).update(state='review')
        self.assert_empty_form(self.review(), 0)
        self.assertFalse(self.r.decisions.exists())

    def test_current_cycle_preloads_all_existing_labels_flags_and_note(self):
        decision = self.decision()
        response = self.review()
        self.assertEqual(response.context['decision'], decision)
        self.assertEqual(response.context['decision_data'], annotation_data(decision))
        self.assertNotContains(response, '尚未定稿')

    def test_current_cycle_no_code_is_not_cleared(self):
        decision = self.decision(primary=None, secondary=[], no_code=True, uncertain=False)
        response = self.review()
        self.assertEqual(response.context['decision'], decision)
        self.assertTrue(response.context['decision_data']['no_code'])
        self.assertEqual(response.context['decision_data']['note'], decision.note)

    def test_rules_update_clears_old_labels_flags_and_note_but_retains_lock_revision(self):
        decision = self.decision()
        before = annotation_data(decision)
        self.update_rules()
        response = self.review()
        self.assert_empty_form(response, decision.revision)
        self.assertNotContains(response, decision.note)
        decision.refresh_from_db()
        self.assertEqual(annotation_data(decision), before)
        self.assertEqual(Decision.objects.filter(round=self.r).count(), 1)
        self.assertEqual(self.r.cycle, 2)

    def test_old_no_code_conclusion_is_not_selected_in_new_cycle(self):
        decision = self.decision(primary=None, secondary=[], no_code=True, uncertain=False)
        self.update_rules()
        self.assert_empty_form(self.review(), decision.revision)
        decision.refresh_from_db()
        self.assertTrue(decision.no_code)

    def test_old_cycle_is_not_displayed_as_current_even_in_read_only_closed_view(self):
        decision = self.decision()
        self.update_rules()
        Round.objects.filter(pk=self.r.pk).update(state='closed')
        response = self.review()
        self.assert_empty_form(response, decision.revision)
        self.assertContains(response, 'data-locked="true"')
        decision.refresh_from_db()
        self.assertEqual(decision.cycle, 1)

    def test_new_cycle_save_uses_existing_revision_and_preserves_both_audit_snapshots(self):
        decision = self.decision()
        before = annotation_data(decision)
        old_event = AuditEvent.objects.get(action='decision.save', object_id=str(decision.pk))
        book = self.update_rules()
        form = self.review().context['decision_data']
        mapped = book.codes.get(key=self.second.key)
        response = self.client.post(reverse('decision_save', args=[self.r.pk, self.units[0].pk]),
            json.dumps({**form, 'book_id': book.pk, 'primary': mapped.pk,
                        'note': '新规则下重新核对的虚构结论'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        decision.refresh_from_db()
        self.assertEqual((decision.revision, decision.cycle, decision.primary_id),
                         (before['revision'] + 1, self.r.cycle, mapped.pk))
        self.assertEqual(Decision.objects.filter(round=self.r).count(), 1)
        old_event.refresh_from_db()
        self.assertEqual(old_event.after, before)
        latest = AuditEvent.objects.filter(action='decision.save', object_id=str(decision.pk)).latest('id')
        self.assertEqual(latest.before, before)
        self.assertEqual(latest.after, annotation_data(decision))
        self.assertEqual(self.review().context['decision'], decision)

    def test_new_cycle_cannot_overwrite_the_old_row_with_revision_zero(self):
        decision = self.decision()
        before = annotation_data(decision)
        book = self.update_rules()
        response = self.client.post(reverse('decision_save', args=[self.r.pk, self.units[0].pk]),
            json.dumps({'revision': 0, 'book_id': book.pk, 'primary': None, 'secondary': [],
                        'no_code': True, 'uncertain': False, 'note': '虚构过期页面'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 409)
        decision.refresh_from_db()
        self.assertEqual(annotation_data(decision), before)
        self.assertEqual(AuditEvent.objects.filter(action='decision.save', object_id=str(decision.pk)).count(), 1)
