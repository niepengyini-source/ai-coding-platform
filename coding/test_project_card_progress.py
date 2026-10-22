"""Project cards show one private batch, not peers or duplicated historical rounds."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Assignment, Membership, Round, Unit
from .tests import TEST_SETTINGS, fixture
from .workspace_views import _add_coding_progress, _memberships


@override_settings(**TEST_SETTINGS)
class ProjectCardProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.client.force_login(self.owner)
        self.mine = self.r.assignments.filter(coder=self.owner)

    def card(self):
        response = self.client.get('/projects/')
        membership = next(item for item in response.context['memberships']
                          if item.project_id == self.project.pk)
        return response, membership.coding_progress

    def new_round(self, name, state='active'):
        round_ = Round.objects.create(project=self.project, book=self.book, name=name,
                                      kind='quick', state=state, started_by=self.owner)
        Assignment.objects.bulk_create([Assignment(round=round_, coder=self.owner, unit=unit)
                                        for unit in self.units])
        return round_

    def test_counts_only_own_submitted_not_saved_drafts_or_peers(self):
        entry = self.mine.first()
        self.mine.update(revision=2)
        self.mine.filter(pk=entry.pk).update(status='submitted', primary=self.first)
        self.r.assignments.exclude(coder=self.owner).update(status='submitted', primary=self.first)
        response, progress = self.card()
        self.assertEqual((progress['submitted'], progress['total']), (1, 3))
        self.assertContains(response, '4/6')
        self.assertContains(response, '团队进度')
        self.assertContains(response, self.r.name)
        self.assertNotContains(response, 'workspace-coding-count is-complete')

    def test_all_confirmed_shows_green_completed_for_this_batch(self):
        self.r.assignments.update(status='submitted', primary=self.first)
        response, progress = self.card()
        self.assertTrue(progress['complete'])
        self.assertContains(response, 'workspace-coding-count is-complete')
        self.assertContains(response, '6/6<strong>已完成</strong>')

    def test_project_without_personal_tasks_is_not_completed(self):
        self.client.force_login(self.viewer)
        response, progress = self.card()
        self.assertIsNone(progress)
        self.assertContains(response, '暂无进行中的任务')
        self.assertNotContains(response, 'workspace-coding-count is-complete')

    def test_multiple_rounds_are_not_added_together(self):
        latest = self.new_round('虚构新的个人批次')
        response, progress = self.card()
        self.assertEqual(progress['round_id'], latest.pk)
        self.assertEqual(progress['total'], 3)
        self.assertContains(response, '0/6')
        self.assertNotContains(response, '0/9')

    def test_active_unfinished_round_precedes_newer_completed_round(self):
        latest = self.new_round('虚构已完成批次')
        latest.assignments.update(status='submitted', primary=self.first)
        _, progress = self.card()
        self.assertEqual(progress['round_id'], self.r.pk)
        self.assertFalse(progress['complete'])

    def test_most_recent_draft_activity_selects_the_resumable_round(self):
        self.new_round('虚构后建批次')
        self.mine.filter(pk=self.mine.first().pk).update(updated_at=timezone.now() + timedelta(minutes=1))
        _, progress = self.card()
        self.assertEqual(progress['round_id'], self.r.pk)

    def test_closed_completed_round_remains_visible_when_no_active_work(self):
        self.mine.update(status='submitted', primary=self.first)
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        _, progress = self.card()
        self.assertEqual(progress['round_id'], self.r.pk)
        self.assertTrue(progress['complete'])

    def test_split_children_count_even_when_outside_shared_material_pool(self):
        parent = self.mine.first()
        parent.superseded = True
        parent.save(update_fields=['superseded'])
        children = [Unit.objects.create(record=parent.unit.record, start=left, end=right,
                    text=parent.unit.text[left:right], active=False)
                    for left, right in [(0, 2), (2, len(parent.unit.text))]]
        Assignment.objects.bulk_create([Assignment(round=self.r, coder=self.owner, unit=unit,
            parent=parent, status='submitted', primary=self.first) for unit in children])
        _, progress = self.card()
        self.assertEqual((progress['submitted'], progress['total']), (2, 4))

    def test_progress_fetch_is_one_bulk_query_for_multiple_project_cards(self):
        Membership.objects.create(project=self.other, user=self.owner, role='coder')
        memberships = list(_memberships(self.owner))
        self.assertEqual(len(memberships), 2)
        with self.assertNumQueries(1):
            _add_coding_progress(memberships, self.owner)
        mine = next(item for item in memberships if item.project_id == self.project.pk)
        other = next(item for item in memberships if item.project_id == self.other.pk)
        self.assertEqual(mine.coding_progress['total'], 3)
        self.assertIsNone(other.coding_progress)
