"""Project overview cards use only fictional projects in Django's isolated test DB."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Assignment, AuditEvent, Membership, Round, Unit
from .tests import TEST_SETTINGS, fixture
from .workspace_views import _add_collaboration_cards, _memberships


@override_settings(**TEST_SETTINGS)
class ProjectCollaborationCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.client.force_login(self.owner)

    def card(self, user=None):
        self.client.force_login(user or self.owner)
        response = self.client.get(reverse('projects'))
        membership = next(item for item in response.context['memberships'] if item.project_id == self.project.pk)
        return response, membership

    def new_round(self, kind='pilot', users=None, name='虚构新轮次'):
        r = Round.objects.create(project=self.project, book=self.book, name=name, kind=kind,
                                 started_by=self.owner if kind == 'quick' else None)
        Assignment.objects.bulk_create([Assignment(round=r, coder=coder, unit=unit)
                                        for coder in (users or [self.owner, self.coder]) for unit in self.units])
        return r

    def event(self, actor, action, when):
        event = AuditEvent.objects.create(project=self.project, actor=actor, action=action,
                                           object_type='Assignment', object_id='1')
        AuditEvent.objects.filter(pk=event.pk).update(created_at=when)

    def test_owner_sees_current_round_team_not_only_own_progress(self):
        self.r.assignments.filter(coder=self.coder).update(status='submitted', primary=self.first)
        first = self.r.assignments.filter(coder=self.owner).first()
        Assignment.objects.filter(pk=first.pk).update(status='submitted', primary=self.first)
        response, membership = self.card()
        self.assertEqual(membership.card_progress['label'], '团队进度')
        self.assertEqual((membership.card_progress['submitted'], membership.card_progress['total']), (4, 6))
        self.assertEqual((membership.member_count, membership.assignment_coder_count), (3, 2))
        self.assertContains(response, '多人协作')
        self.assertContains(response, '本轮有任务 2 人')
        self.assertContains(response, '4/6')
        self.assertContains(response, '我负责的')
        self.assertFalse(membership.card_progress['complete'])

    def test_member_sees_only_own_progress_and_joined_identity(self):
        self.r.assignments.filter(coder=self.owner).update(status='submitted', primary=self.first)
        response, membership = self.card(self.coder)
        self.assertEqual(membership.card_progress['label'], '我的任务进度')
        self.assertEqual((membership.card_progress['submitted'], membership.card_progress['total']), (0, 3))
        self.assertContains(response, '我加入的 · 编码员')
        self.assertNotContains(response, '团队进度')
        self.assertNotContains(response, '3/6')
        for sensitive in (self.units[0].text, self.first.definition, self.coder.username):
            self.assertNotIn(sensitive, response.content.decode().split('<section class="workspace-projects">')[1])

    def test_owner_personal_completion_does_not_mark_team_complete(self):
        self.r.assignments.filter(coder=self.owner).update(status='submitted', primary=self.first)
        response, membership = self.card()
        self.assertFalse(membership.card_progress['complete'])
        self.assertContains(response, '3/6')
        self.assertNotContains(response, 'workspace-coding-count is-complete')

    def test_team_completion_has_green_count(self):
        self.r.assignments.update(status='submitted', primary=self.first)
        response, membership = self.card()
        self.assertTrue(membership.card_progress['complete'])
        self.assertContains(response, '6/6<strong>已完成</strong>')
        self.assertContains(response, 'workspace-coding-count is-complete')
        self.assertContains(response, '查看小组任务')

    def test_new_personal_quick_batch_does_not_hide_unfinished_team_task(self):
        quick = self.new_round(kind='quick', users=[self.owner], name='虚构个人快速批次')
        response, membership = self.card()
        self.assertEqual(membership.card_progress['name'], self.r.name)
        self.assertEqual(membership.team_task_url, reverse('workbench', args=[self.r.pk]))
        self.assertEqual(membership.personal_task_url, reverse('workbench', args=[quick.pk]))
        self.assertContains(response, '继续小组任务')
        self.assertContains(response, '继续个人编码')

    def test_owner_without_personal_tasks_sees_team_and_no_fabricated_assignment(self):
        self.r.assignments.filter(coder=self.owner).delete()
        before = Assignment.objects.count()
        response, membership = self.card()
        self.assertEqual(membership.card_progress['label'], '团队进度')
        self.assertEqual(membership.card_progress['total'], 3)
        self.assertEqual(membership.assignment_coder_count, 1)
        self.assertEqual(membership.collaboration_label, '单人编码')
        self.assertEqual(membership.team_task_url, '')
        self.assertContains(response, '暂无分配给我的小组任务')
        self.assertEqual(Assignment.objects.count(), before)

    def test_multiple_active_team_tasks_go_to_existing_round_selection(self):
        self.new_round()
        response, membership = self.card()
        self.assertEqual(membership.team_task_url, reverse('project', args=[self.project.pk]) + '#project-rounds')
        self.assertContains(response, '继续小组任务')

    def test_prefers_my_unfinished_team_round_to_newer_team_round_without_my_tasks(self):
        latest = self.new_round(users=[self.coder])
        _, membership = self.card()
        self.assertNotEqual(membership.card_progress['name'], latest.name)
        self.assertEqual(membership.card_progress['name'], self.r.name)
        self.assertEqual(membership.card_progress['total'], 6)

    def test_single_assigned_coder_does_not_mean_multiplayer_even_with_multiple_members(self):
        self.r.assignments.filter(coder=self.coder).delete()
        _, membership = self.card()
        self.assertEqual(membership.member_count, 3)
        self.assertEqual(membership.assignment_coder_count, 1)
        self.assertEqual(membership.collaboration_label, '单人编码')

    def test_inactive_read_only_and_removed_members_do_not_count_as_coders(self):
        self.coder.is_active = False
        self.coder.save(update_fields=['is_active'])
        Assignment.objects.bulk_create([Assignment(round=self.r, coder=person, unit=self.units[0])
                                        for person in [self.viewer, self.outsider]])
        _, membership = self.card()
        self.assertEqual((membership.member_count, membership.assignment_coder_count), (2, 1))
        self.assertEqual(membership.card_progress['total'], 3)
        self.assertEqual(membership.collaboration_label, '单人编码')

    def test_unassigned_member_waits_without_peer_progress_or_task_creation(self):
        Membership.objects.create(project=self.project, user=self.outsider, role='coder')
        response, membership = self.card(self.outsider)
        self.assertIsNone(membership.card_progress)
        self.assertEqual(membership.team_task_url, '')
        self.assertContains(response, '暂无分配给我的小组任务')
        self.assertNotContains(response, '继续小组任务')
        self.assertFalse(self.r.assignments.filter(coder=self.outsider).exists())

    def test_read_only_member_does_not_get_coding_actions(self):
        response, membership = self.card(self.viewer)
        self.assertIsNone(membership.card_progress)
        self.assertNotContains(response, '开始个人编码')
        self.assertNotContains(response, '继续小组任务')
        self.assertNotContains(response, '暂无分配给我的小组任务')

    def test_creation_and_invitation_updates_are_not_coding_activity(self):
        when = timezone.now() + timedelta(days=1)
        self.event(self.owner, 'invitation.issue', when)
        _, membership = self.card()
        self.assertIsNone(membership.last_coded_at)

    def test_owner_recent_activity_uses_latest_real_save_or_submission(self):
        saved = timezone.now() - timedelta(hours=2)
        submitted = saved + timedelta(hours=1)
        self.event(self.owner, 'annotation.save', saved)
        self.event(self.coder, 'annotation.submit', submitted)
        self.event(self.owner, 'membership.set', submitted + timedelta(hours=1))
        _, membership = self.card()
        self.assertEqual(membership.last_coded_at, submitted)
        self.assertEqual(membership.activity_label, '团队最近编码')

    def test_member_recent_activity_is_private_to_their_own_saves(self):
        saved = timezone.now() - timedelta(hours=2)
        self.event(self.coder, 'annotation.save', saved)
        self.event(self.owner, 'annotation.submit', saved + timedelta(hours=1))
        _, membership = self.card(self.coder)
        self.assertEqual(membership.last_coded_at, saved)
        self.assertEqual(membership.activity_label, '我上次编码')

    def test_split_children_count_but_superseded_parent_does_not(self):
        parent = self.r.assignments.filter(coder=self.owner).first()
        parent.superseded = True
        parent.save(update_fields=['superseded'])
        children = [Unit.objects.create(record=parent.unit.record, start=left, end=right,
                                        text=parent.unit.text[left:right], active=False)
                    for left, right in [(0, 2), (2, len(parent.unit.text))]]
        Assignment.objects.bulk_create([Assignment(round=self.r, coder=self.owner, unit=unit, parent=parent)
                                        for unit in children])
        _, membership = self.card()
        self.assertEqual(membership.card_progress['total'], 7)

    def test_closed_history_is_not_added_to_current_progress(self):
        closed = self.new_round(name='虚构已归档轮次')
        closed.assignments.update(status='submitted', primary=self.first)
        closed.state = 'closed'
        closed.save(update_fields=['state'])
        _, membership = self.card()
        self.assertEqual(membership.card_progress['total'], 6)
        self.assertEqual(membership.card_progress['name'], self.r.name)
        self.r.assignments.update(status='submitted', primary=self.first)
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        response, membership = self.card()
        self.assertEqual(membership.card_progress['name'], closed.name)
        self.assertEqual(membership.card_progress['total'], 6)
        self.assertEqual(membership.card_progress['state'], 'closed')
        self.assertTrue(membership.card_progress['complete'])
        self.assertContains(response, '6/6<strong>已完成</strong>')
        self.assertContains(response, '已结束')
        self.assertNotContains(response, '暂无进行中的任务')
        self.assertEqual(membership.team_task_label, '查看小组任务')
        self.assertEqual(membership.team_task_url, reverse('workbench', args=[closed.pk]))
        self.assertFalse(membership.waiting_for_assignment)

    def test_review_round_remains_visible_completed_and_view_only(self):
        self.r.assignments.update(status='submitted', primary=self.first)
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        response, membership = self.card(self.coder)
        self.assertEqual(membership.card_progress['state'], 'review')
        self.assertEqual(membership.card_progress['label'], '我的任务进度')
        self.assertEqual((membership.card_progress['submitted'], membership.card_progress['total']), (3, 3))
        self.assertTrue(membership.card_progress['complete'])
        self.assertContains(response, '协商中')
        self.assertNotContains(response, '继续小组任务')
        self.assertEqual(membership.team_task_label, '查看小组任务')
        self.assertFalse(membership.waiting_for_assignment)

    def test_active_personal_batch_precedes_historical_team_round(self):
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        quick = self.new_round(kind='quick', users=[self.owner])
        for user in (self.owner,):
            _, membership = self.card(user)
            self.assertEqual(membership.card_progress['name'], quick.name)
            self.assertEqual(membership.card_progress['state'], 'active')
            self.assertEqual(membership.card_progress['total'], 3)

    def test_latest_single_history_wins_across_personal_and_team_batches(self):
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        quick = self.new_round(kind='quick', users=[self.owner], name='虚构已结束个人批次')
        quick.assignments.update(status='submitted', primary=self.first)
        quick.state = 'closed'
        quick.save(update_fields=['state'])
        response, membership = self.card()
        self.assertEqual(membership.card_progress['name'], quick.name)
        self.assertEqual(membership.card_progress['total'], 3)
        self.assertTrue(membership.card_progress['complete'])
        self.assertEqual(membership.personal_task_label, '查看个人编码')
        self.assertEqual(membership.personal_task_url, reverse('workbench', args=[quick.pk]))
        self.assertNotContains(response, '继续个人编码')
        self.assertFalse(membership.waiting_for_assignment)

    def test_personal_batch_still_shows_own_progress_without_active_team_round(self):
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        quick = self.new_round(kind='quick', users=[self.owner])
        _, membership = self.card()
        self.assertEqual(membership.card_progress['total'], 3)
        self.assertEqual(membership.card_progress['label'], '我的任务进度')
        self.assertEqual(membership.personal_task_url, reverse('workbench', args=[quick.pk]))

    def test_bulk_enrichment_has_constant_query_count_and_empty_list_does_not_query(self):
        Membership.objects.create(project=self.other, user=self.owner, role='coder')
        memberships = list(_memberships(self.owner))
        with self.assertNumQueries(3):
            _add_collaboration_cards(memberships, self.owner)
        with self.assertNumQueries(0):
            _add_collaboration_cards([], self.owner)

    def test_join_action_is_available_next_to_create_and_in_empty_state(self):
        response, _ = self.card()
        content = response.content.decode().split('<section class="workspace-projects">')[1]
        self.assertIn('href="/groups/join/"', content)
        self.assertIn('href="/projects/new/"', content)
        Membership.objects.filter(user=self.outsider).delete()
        self.client.force_login(self.outsider)
        empty = self.client.get(reverse('projects'))
        self.assertContains(empty, 'workspace-create')
        self.assertContains(empty, 'href="/groups/join/"')

    def test_page_is_read_only_and_does_not_expose_other_project(self):
        before = (Assignment.objects.count(), Round.objects.count(), AuditEvent.objects.count())
        response, _ = self.card()
        self.assertEqual((Assignment.objects.count(), Round.objects.count(), AuditEvent.objects.count()), before)
        self.assertNotContains(response, self.other.name)
        self.assertContains(response, '<aside class="sidebar">')
        self.assertContains(response, 'id="page-back"')
