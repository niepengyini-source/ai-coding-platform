"""Navigation guidance is presentation only; research state remains explicit."""
from html.parser import HTMLParser

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Assignment, Codebook, Decision, Membership, Project, Round, UploadDraft
from .tests import TEST_SETTINGS, fixture


class JourneyMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.current_steps = 0
        self.next_links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'li' and attrs.get('aria-current') == 'step':
            self.current_steps += 1
        if tag == 'a' and 'journey-primary' in attrs.get('class', '').split():
            self.next_links.append(attrs['href'])


@override_settings(**TEST_SETTINGS)
class JourneyInterfaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.owner)

    def test_project_has_one_recommended_action_and_no_automatic_completed_steps(self):
        response = self.client.get(f'/p/{self.project.pk}/')
        markup = JourneyMarkup(response.content.decode())
        self.assertEqual(markup.current_steps, 1)
        self.assertEqual(markup.next_links, [f'/r/{self.r.pk}/'])
        self.assertContains(response, '本轮我的提交')
        self.assertContains(response, '0 / 3')
        self.assertContains(response, '研究目标与材料范围')
        self.assertNotContains(response, 'class="eyebrow"')
        self.assertContains(response, 'id="project-material-file"')
        self.assertContains(response, '/static/journey.css')

    def test_new_project_is_independent_and_existing_project_is_not_reset(self):
        new = Project.objects.create(name='另一个全新虚构项目', owner=self.owner)
        Membership.objects.create(project=new, user=self.owner, role='owner')
        Codebook.objects.create(project=new, version=1)
        response = self.client.get(f'/p/{new.pk}/')
        self.assertEqual(JourneyMarkup(response.content.decode()).next_links, [f'/p/{new.pk}/materials/'])
        self.assertNotContains(response, self.r.name)
        self.assertEqual(self.r.assignments.count(), 6)
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertEqual(JourneyMarkup(response.content.decode()).next_links, [f'/r/{self.r.pk}/'])

    def test_material_import_result_keeps_preview_and_points_to_missing_rules(self):
        new = Project.objects.create(name='虚构导入流程', owner=self.owner)
        Membership.objects.create(project=new, user=self.owner, role='owner')
        book = Codebook.objects.create(project=new, version=1)
        path = f'/p/{new.pk}/materials/'
        response = self.client.post(path, {'file': SimpleUploadedFile('journey.csv', 'text,group\n虚构材料,G1\n'.encode())})
        self.assertContains(response, '确认导入')
        self.assertEqual(new.documents.count(), 0)
        draft = UploadDraft.objects.get(project=new, user=self.owner)
        response = self.client.post(path, {'draft_id': draft.pk, 'text_column': 'text', 'context_column': 'group'}, follow=True)
        self.assertContains(response, '材料已导入')
        self.assertContains(response, '准备编码本')
        self.assertContains(response, f'href="/p/{new.pk}/books/{book.pk}/"')
        self.assertEqual(new.documents.count(), 1)
        self.assertFalse(new.rounds.exists())

    def test_materials_rules_and_progress_keep_next_step_without_creating_round(self):
        self.book.refresh_from_db()
        before = (Round.objects.count(), Assignment.objects.count(), self.book.revision)
        for suffix in ['materials/', 'units/', f'books/{self.book.pk}/', 'progress/']:
            with self.subTest(suffix=suffix):
                response = self.client.get(f'/p/{self.project.pk}/{suffix}')
                self.assertContains(response, 'aria-label="当前工作指引"')
                self.assertContains(response, '继续我的编码')
                self.assertContains(response, 'id="page-back"')
        self.book.refresh_from_db()
        self.assertEqual(before, (Round.objects.count(), Assignment.objects.count(), self.book.revision))

    def test_current_workbench_does_not_have_duplicate_continue_button(self):
        response = self.client.get(f'/r/{self.r.pk}/')
        self.assertNotContains(response, 'aria-label="当前工作指引"')
        self.assertContains(response, '确认并下一条')
        self.assertContains(response, '保存草稿')

    def test_manager_without_personal_work_has_team_next_step_not_empty_workbench(self):
        self.r.assignments.filter(coder=self.owner).delete()
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertEqual(JourneyMarkup(response.content.decode()).next_links, [f'/p/{self.project.pk}/progress/'])
        self.assertContains(response, '本轮团队提交')
        response = self.client.get(f'/p/{self.project.pk}/progress/')
        hint = response.content.decode().split('aria-label="当前工作指引"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('<a ', hint)

    def test_finished_manager_sees_team_counts_not_personal_completion(self):
        self.r.assignments.filter(coder=self.owner).update(status='submitted')
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(response, '查看团队编码进度')
        self.assertContains(response, '本轮团队提交')
        self.assertContains(response, '3 / 6')
        self.assertNotContains(response, '本轮我的提交')

    def test_manager_with_pending_personal_work_keeps_personal_counts(self):
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(response, '本轮我的提交')
        self.assertContains(response, '0 / 3')
        self.assertNotContains(response, '本轮团队提交')

    def test_reopened_work_is_not_described_as_new_independent_coding(self):
        self.r.assignments.update(status='submitted')
        self.r.cycle = 2
        self.r.save(update_fields=['cycle'])
        response = self.client.get(f'/r/{self.r.pk}/')
        self.assertContains(response, '本轮编码已全部提交')
        self.assertNotContains(response, '独立编码已全部提交')
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_all_submitted_recommends_explicit_post_action_but_does_not_lock(self):
        self.r.assignments.update(status='submitted')
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertEqual(JourneyMarkup(response.content.decode()).next_links, [f'/r/{self.r.pk}/#round-actions'])
        response = self.client.get(f'/r/{self.r.pk}/')
        self.assertContains(response, 'id="round-actions"', count=1)
        self.assertContains(response, '进入协商')
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_review_report_and_disagreements_keep_current_round_guidance(self):
        self.r.assignments.update(status='submitted')
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        for suffix in ['review/', 'disagreements/', 'report/']:
            with self.subTest(suffix=suffix):
                response = self.client.get(f'/r/{self.r.pk}/{suffix}')
                self.assertContains(response, 'aria-label="当前工作指引"')
                self.assertContains(response, '继续协商定稿')
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'review')
        self.assertFalse(self.r.decisions.exists())

    def test_completed_review_offers_explicit_finish_without_changing_state(self):
        self.r.assignments.update(status='submitted')
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        for unit in self.units:
            Decision.objects.create(round=self.r, unit=unit, primary=self.first,
                                    note='虚构定稿记录', author=self.owner, cycle=self.r.cycle)
        response = self.client.get(f'/r/{self.r.pk}/review/')
        self.assertContains(response, '本轮结论已填写')
        self.assertContains(response, f'href="/r/{self.r.pk}/#round-actions"')
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'review')

    def test_member_finished_task_is_waiting_not_receiving_owner_actions(self):
        self.r.assignments.filter(coder=self.coder).update(status='submitted')
        self.client.force_login(self.coder)
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(response, '我的任务已提交')
        self.assertNotContains(response, '检查并进入复核')
        self.assertNotContains(response, '安排团队编码')

    def test_viewer_has_no_start_or_end_next_step(self):
        self.client.force_login(self.viewer)
        response = self.client.get(f'/p/{self.project.pk}/')
        links = JourneyMarkup(response.content.decode()).next_links
        self.assertEqual(links, [f'/p/{self.project.pk}/progress/'])
        self.assertNotContains(response, f'href="/p/{self.project.pk}/code/"')
        self.assertNotContains(response, 'id="project-material-file"')

    def test_workspace_and_login_do_not_get_project_guide_or_expanded_navigation(self):
        for path in ['/', '/projects/', '/groups/join/', '/guide/']:
            with self.subTest(path=path):
                self.assertNotContains(self.client.get(path), 'aria-label="研究工作阶段"')
        self.client.logout()
        response = self.client.get('/login/')
        self.assertNotContains(response, '<aside class="sidebar">')
        self.assertNotContains(response, '当前工作指引')

    def test_other_project_and_other_private_round_remain_inaccessible(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/p/{self.project.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/r/{self.r.pk}/').status_code, 404)

    def test_prepared_project_has_no_mandatory_repeated_wizard(self):
        self.r.assignments.all().delete()
        self.r.delete()
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(response, '材料与编码本已准备好')
        self.assertEqual(JourneyMarkup(response.content.decode()).next_links, [f'/p/{self.project.pk}/code/'])
        self.assertFalse(self.project.rounds.exists())

    def test_personal_quick_batch_is_not_labelled_as_validated_formal_research(self):
        from .services import create_round
        self.r.assignments.update(status='submitted')
        self.r.state = 'closed'
        self.r.save(update_fields=['state'])
        create_round(self.project, self.owner, self.book.pk, '虚构个人批次', 'quick',
                     [self.owner.pk], 0, 2026, 'all', 0)
        response = self.client.get(f'/p/{self.project.pk}/')
        stages = response.content.decode().split('aria-label="研究工作阶段"', 1)[1].split('</ol>', 1)[0]
        self.assertIn('个人编码', stages)
        self.assertIn('编码结果与备份', stages)
        self.assertNotIn('正式编码', stages)

    def test_persistent_stage_bar_respects_blind_coding_and_unlocks_in_review(self):
        response = self.client.get(f'/p/{self.project.pk}/units/')
        self.assertContains(response, 'aria-label="项目阶段导航"')
        self.assertNotContains(response, f'href="/r/{self.r.pk}/disagreements/"')
        self.assertContains(response, '4. 分歧与协商')
        self.assertNotContains(self.client.get(f'/r/{self.r.pk}/'), 'aria-label="项目阶段导航"')
        self.assertNotContains(self.client.get(f'/guide/?project={self.project.pk}'), 'aria-label="项目阶段导航"')
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        unlocked = self.client.get(f'/p/{self.project.pk}/units/')
        self.assertContains(unlocked, f'href="/r/{self.r.pk}/disagreements/"')

    def test_frozen_codebook_uses_readonly_grid_and_clean_form_labels(self):
        frozen_page = self.client.get(f'/p/{self.project.pk}/books/{self.book.pk}/')
        self.assertContains(frozen_page, 'class="code-grid-readonly"')
        self.assertNotContains(frozen_page, 'id="code-editor"')
        overview = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(overview, '研究目标：这次编码要回答什么？</label>')
        self.assertNotContains(overview, '？:')

    def test_reliability_report_displays_coder_usernames_directly(self):
        self.r.assignments.update(status='submitted', primary=self.first)
        self.r.state = 'review'
        self.r.save(update_fields=['state'])
        self.client.post(f'/r/{self.r.pk}/report/')
        report_page = self.client.get(f'/r/{self.r.pk}/report/')
        self.assertContains(report_page, '编码员配对')
        self.assertContains(report_page, f'{self.owner.username} / {self.coder.username}')
