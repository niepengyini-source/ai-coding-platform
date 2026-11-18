"""Guidance uses fictional projects and never advances research state on GET."""
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from .guidance import PHASES, project_guidance
from .models import Assignment, Code, Codebook, Decision, Membership, Project, Round, Unit
from .services import create_round
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class ProjectGuidanceTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)

    def guide(self, user=None, role='owner'):
        return project_guidance(self.project, user or self.owner, role)

    def team_round(self, kind='formal', coders=None):
        return create_round(self.project, self.owner, self.book.pk, '虚构新轮次', kind,
                            coders or [self.coder.pk], 0, 2026, 'all', 0)

    def test_new_project_starts_with_materials_not_old_project_progress(self):
        new = Project.objects.create(name='新的虚构研究', owner=self.owner)
        Membership.objects.create(project=new, user=self.owner, role='owner')
        Codebook.objects.create(project=new, version=1)
        result = project_guidance(new, self.owner, 'owner')
        self.assertEqual(result['phase'], 1)
        self.assertEqual(result['missing'], 'materials')
        self.assertEqual(result['next_url'], reverse('materials', args=[new.pk]))
        self.assertIsNone(result['round_id'])
        self.assertEqual([p['label'] for p in result['phases']], list(PHASES))
        self.assertEqual(sum(p['current'] for p in result['phases']), 1)

    def test_current_empty_codebook_is_flagged_without_mutating_old_round(self):
        snapshot = (self.r.book_id, self.r.state, self.r.revision, self.r.assignments.count())
        newer = Codebook.objects.create(project=self.project, version=2)
        result = self.guide()
        self.assertEqual(result['phase'], 3)
        self.assertEqual(result['next_url'], reverse('coding_entry', args=[self.project.pk]))
        self.assertTrue(result['ready'])
        self.assertFalse(result['has_codes'])
        self.r.refresh_from_db()
        self.assertEqual(snapshot, (self.r.book_id, self.r.state, self.r.revision, self.r.assignments.count()))

    def test_ready_project_without_round_does_not_create_or_freeze_anything(self):
        self.r.assignments.all().delete()
        self.r.delete()
        self.book.frozen = False
        self.book.save(update_fields=['frozen'])
        self.book.refresh_from_db()
        before = (Round.objects.count(), Assignment.objects.count(), self.book.revision)
        result = self.guide()
        self.assertTrue(result['ready'])
        self.assertEqual(result['next_url'], reverse('coding_entry', args=[self.project.pk]))
        self.book.refresh_from_db()
        self.assertFalse(self.book.frozen)
        self.assertEqual(before, (Round.objects.count(), Assignment.objects.count(), self.book.revision))

    def test_personal_unfinished_work_has_priority_and_supplement_does_not_reset_it(self):
        latest = self.team_round()
        a = self.r.assignments.filter(coder=self.owner).first()
        a.revision = 2
        a.note = '虚构已保存记录'
        a.save()
        Unit.objects.create(record=self.units[0].record, start=0, end=1, text='补充材料')
        result = self.guide()
        self.assertEqual(result['round_id'], self.r.pk)
        self.assertNotEqual(result['round_id'], latest.pk)
        self.assertEqual(result['progress']['total'], 3)
        a.refresh_from_db()
        self.assertEqual(a.note, '虚构已保存记录')
        self.assertEqual(a.revision, 2)

    def test_manager_without_personal_tasks_gets_team_progress(self):
        self.r.assignments.filter(coder=self.owner).update(status='submitted')
        latest = self.team_round()
        result = self.guide()
        self.assertEqual(result['round_id'], latest.pk)
        self.assertEqual(result['phase'], 4)
        self.assertEqual(result['progress']['scope'], 'team')
        self.assertEqual(result['next_label'], '查看团队进度')
        self.assertEqual(result['next_url'], reverse('progress', args=[self.project.pk]))

    def test_submitted_member_waits_instead_of_receiving_review_action(self):
        self.r.assignments.filter(coder=self.coder).update(status='submitted')
        result = self.guide(self.coder, 'coder')
        self.assertEqual(result['title'], '我的任务已提交')
        self.assertEqual(result['progress']['scope'], 'my')
        self.assertEqual(result['progress']['total'], 3)
        self.assertEqual(result['next_url'], reverse('progress', args=[self.project.pk]))

    def test_new_team_work_does_not_hide_members_own_submitted_active_round(self):
        self.r.assignments.filter(coder=self.coder).update(status='submitted')
        later = self.team_round(coders=[self.owner.pk])
        result = self.guide(self.coder, 'coder')
        self.assertEqual(result['round_id'], self.r.pk)
        self.assertNotEqual(result['round_id'], later.pk)
        self.assertEqual(result['title'], '我的任务已提交')
        self.assertEqual(result['progress']['total'], 3)

    def test_new_team_work_does_not_hide_members_own_review_round(self):
        self.r.assignments.update(status='submitted')
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        self.team_round(coders=[self.owner.pk])
        result = self.guide(self.coder, 'coder')
        self.assertEqual(result['round_id'], self.r.pk)
        self.assertEqual(result['title'], '本轮正在协商复核')

    def test_training_submissions_are_not_called_independent_coding(self):
        current = self.team_round(kind='training')
        current.assignments.update(status='submitted')
        result = project_guidance(self.project, self.owner, 'owner', round_=current)
        self.assertEqual(result['phase'], 3)
        self.assertEqual(result['title'], '本轮培训练习已全部提交')
        self.assertNotIn('独立编码', result['title'])
        current.state = 'closed'
        current.save(update_fields=['state'])
        result = project_guidance(self.project, self.owner, 'owner', round_=current)
        self.assertEqual(result['title'], '本轮培训练习已结束')
        self.assertIn('培训练习不代替独立试编码', result['detail'])

    def test_reopened_round_submission_is_not_presented_as_fresh_independent_work(self):
        self.r.assignments.update(status='submitted')
        self.r.cycle = 2
        self.r.save(update_fields=['cycle'])
        result = self.guide()
        self.assertEqual(result['title'], '本轮编码已全部提交')

    def test_all_submitted_only_links_to_explicit_management_controls(self):
        self.r.assignments.update(status='submitted')
        result = self.guide()
        self.assertTrue(result['next_url'].endswith('#round-actions'))
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')
        self.assertFalse(self.r.decisions.exists())

    def test_trial_review_and_closed_stay_in_improvement_phase(self):
        self.r.assignments.update(status='submitted')
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        result = self.guide()
        self.assertEqual(result['phase'], 3)
        self.assertEqual(result['next_url'], reverse('review', args=[self.r.pk]))
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        result = self.guide()
        self.assertEqual(result['phase'], 3)
        self.assertIn('不代表规则已经合格', result['detail'])

    def test_formal_review_requires_current_non_uncertain_decisions(self):
        self.r.assignments.update(status='submitted')
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        current = self.team_round()
        current.assignments.update(status='submitted')
        current.state = 'review'
        current.cycle = 2
        current.save(update_fields=['state', 'cycle'])
        for index, unit in enumerate(self.units):
            Decision.objects.create(round=current, unit=unit, author=self.owner,
                                    primary=self.first, note='虚构判断',
                                    cycle=1 if index == 0 else 2, uncertain=index == 1)
        result = self.guide()
        self.assertEqual(result['phase'], 5)
        self.assertEqual(result['progress'], {'submitted': 1, 'total': 3, 'remaining': 2, 'scope': 'final'})
        current.decisions.update(cycle=2, uncertain=False)
        result = self.guide()
        self.assertEqual(result['next_label'], '检查并结束本轮')
        current.refresh_from_db()
        self.assertEqual(current.state, 'review')

    def test_formal_closed_offers_export_without_restarting_project(self):
        self.r.assignments.update(status='submitted')
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        current = self.team_round()
        current.state = 'closed'
        current.save(update_fields=['state'])
        result = self.guide()
        self.assertEqual(result['phase'], 5)
        self.assertIn(reverse('exports', args=[self.project.pk]), result['next_url'])
        self.assertIn('target=final', result['next_url'])
        self.assertEqual(Round.objects.filter(project=self.project).count(), 2)

    def test_own_quick_round_can_finish_and_exports_personal_not_consensus_data(self):
        current = self.team_round(kind='quick', coders=[self.owner.pk])
        current.assignments.update(status='submitted')
        result = project_guidance(self.project, self.owner, 'owner', round_=current)
        self.assertEqual(result['next_label'], '检查并结束本批')
        current.state = 'closed'
        current.save(update_fields=['state'])
        result = project_guidance(self.project, self.owner, 'owner', round_=current)
        self.assertEqual(result['phase'], 5)
        self.assertIn('target=mine', result['next_url'])

    def test_manager_viewing_another_persons_quick_round_cannot_be_guided_to_finish_or_empty_mine_export(self):
        personal = create_round(self.project, self.coder, self.book.pk, '虚构他人个人编码',
                                'quick', [self.coder.pk], 0, 2026, 'all', 0)
        personal.assignments.update(status='submitted')
        Membership.objects.create(project=self.project, user=self.outsider, role='reviewer')
        for state in ('active', 'closed'):
            personal.state = state
            personal.save(update_fields=['state'])
            for user, role in ((self.owner, 'owner'), (self.outsider, 'reviewer')):
                result = project_guidance(self.project, user, role, round_=personal)
                self.assertEqual(result['round_id'], personal.pk)
                self.assertEqual(result['next_label'], '查看项目进度')
                self.assertEqual(result['next_url'], reverse('progress', args=[self.project.pk]))
                self.assertNotIn('#round-actions', result['next_url'])
                self.assertNotIn('target=mine', result['next_url'])
                self.assertEqual(result['phase'], 5 if state == 'closed' else 4)

    def test_superseded_assignments_do_not_block_ready_for_review(self):
        self.r.assignments.update(status='submitted')
        old = self.r.assignments.filter(coder=self.owner).first()
        old.status, old.superseded = 'draft', True
        old.save(update_fields=['status', 'superseded'])
        result = self.guide()
        self.assertEqual(result['next_label'], '检查并进入复核')
        self.assertEqual(result['progress']['remaining'], 0)

    def test_quick_batches_of_other_people_are_not_exposed_by_suggestions(self):
        own = self.team_round(kind='quick', coders=[self.owner.pk])
        self.assertEqual(own.started_by_id, self.owner.pk)
        result = self.guide(self.coder, 'coder')
        self.assertEqual(result['round_id'], self.r.pk)
        self.assertNotIn(str(own.pk), result['next_url'])
        self.assertNotEqual(result['round_name'], own.name)

    def test_viewer_never_gets_start_or_manage_or_review_write_action(self):
        for state in ('active', 'review', 'closed'):
            self.r.state = state
            self.r.save(update_fields=['state'])
            result = self.guide(self.viewer, 'viewer')
            self.assertFalse(result['can_manage'])
            self.assertNotIn('#round-actions', result['next_url'])
            self.assertNotEqual(result['next_url'], reverse('coding_entry', args=[self.project.pk]))
            self.assertNotEqual(result['next_url'], reverse('review', args=[self.r.pk]))

    def test_outsider_and_forged_role_are_denied(self):
        with self.assertRaises(PermissionDenied):
            self.guide(self.outsider, 'owner')
        with self.assertRaises(PermissionDenied):
            self.guide(self.coder, 'owner')

    def test_explicit_round_context_does_not_get_replaced_by_other_pending_work(self):
        current = self.team_round()
        result = project_guidance(self.project, self.owner, 'owner', round_=current)
        self.assertEqual(result['round_id'], current.pk)
        self.assertEqual(result['phase'], 4)
        self.assertEqual(result['next_label'], '查看团队进度')

    def test_explicit_round_still_checks_project_and_private_batch_visibility(self):
        foreign = Round.objects.create(project=self.other, book=self.foreign_code.book,
                                       name='外部任务', kind='formal')
        with self.assertRaises(PermissionDenied):
            project_guidance(self.project, self.owner, 'owner', round_=foreign)
        private = self.team_round(kind='quick', coders=[self.owner.pk])
        with self.assertRaises(PermissionDenied):
            project_guidance(self.project, self.coder, 'coder', round_=private)

    def test_viewing_old_round_uses_its_rules_not_new_empty_draft(self):
        Codebook.objects.create(project=self.project, version=2)
        result = project_guidance(self.project, self.owner, 'owner', round_=self.r)
        self.assertEqual(result['round_id'], self.r.pk)
        self.assertTrue(result['ready'])

    def test_explicit_round_uses_existing_assigned_units_after_pool_is_deactivated(self):
        Unit.objects.filter(record__document__project=self.project).update(active=False)
        for user, role in ((self.owner, 'owner'), (self.coder, 'coder')):
            result = project_guidance(self.project, user, role, round_=self.r)
            self.assertEqual(result['round_id'], self.r.pk)
            self.assertEqual(result['next_label'], '继续我的编码')
            self.assertTrue(result['ready'])
        overview = self.guide()
        self.assertEqual(overview['missing'], 'materials')
        self.assertIn('可从原轮次继续', overview['detail'])

    def test_only_superseded_assignments_do_not_satisfy_round_readiness(self):
        Unit.objects.filter(record__document__project=self.project).update(active=False)
        self.r.assignments.filter(coder=self.coder).update(superseded=True)
        result = project_guidance(self.project, self.coder, 'coder', round_=self.r)
        self.assertEqual(result['missing'], 'materials')
        self.assertFalse(result['ready'])

    def test_manager_with_missing_material_and_member_missing_rules_get_safe_links(self):
        Unit.objects.filter(record__document__project=self.project).update(active=False)
        result = self.guide(self.viewer, 'viewer')
        self.assertEqual(result['next_url'], reverse('units', args=[self.project.pk]))
        Unit.objects.filter(record__document__project=self.project).update(active=True)
        new = Codebook.objects.create(project=self.project, version=2)
        result = self.guide(self.coder, 'coder')
        self.assertEqual(result['missing'], '')
        self.assertEqual(result['next_url'], reverse('coding_entry', args=[self.project.pk]))
        Code.objects.create(book=new, key='NEW', name='新代码', definition='虚构规则')
        self.assertTrue(self.guide(self.coder, 'coder')['ready'])
