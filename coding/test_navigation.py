from django.core.cache import cache
from django.test import TestCase, override_settings
from .models import Assignment, Profile, Round
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class NavigationTests(TestCase):
    def setUp(self):
        cache.clear()
        for name, value in fixture().items():
            setattr(self, name, value)

    def test_public_login_and_registration_are_focused_without_sidebar_or_return(self):
        for path in ['/login/', '/accounts/register/']:
            response = self.client.get(path)
            self.assertNotContains(response, 'id="page-back"')
            self.assertNotContains(response, '<aside class="sidebar">')
            self.assertContains(response, 'data-navigation-user="anonymous"')

    def test_workspace_and_join_pages_include_return_and_identity(self):
        self.client.force_login(self.owner)
        for path in ['/projects/new/', '/groups/join/', '/guide/', '/accounts/password/']:
            response = self.client.get(path)
            self.assertContains(response, 'id="page-back"')
            self.assertContains(response, f'data-navigation-user="{self.owner.id}"')

    def test_workspace_entries_keep_navigation_identity(self):
        self.client.force_login(self.owner)
        for path in ['/', '/projects/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, f'data-navigation-user="{self.owner.id}"')
                if path == '/':
                    self.assertNotContains(response, 'id="page-back"')
                else:
                    self.assertContains(response, 'id="page-back"', count=1)
                    self.assertContains(response, 'href="/">← 返回上一页')

    def test_workspace_pages_fallback_to_function_selection(self):
        self.client.force_login(self.owner)
        for path in ['/groups/join/', '/guide/', '/accounts/password/',
                     '/accounts/password/done/']:
            with self.subTest(path=path):
                self.assertContains(self.client.get(path), 'href="/">← 返回上一页')

    def test_project_creation_falls_back_to_project_list(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/projects/new/'),
                            'href="/projects/">← 返回上一页')

    def test_project_child_pages_fallback_to_project(self):
        self.client.force_login(self.owner)
        for path in [f'/p/{self.project.id}/members/', f'/p/{self.project.id}/materials/',
            f'/p/{self.project.id}/units/', f'/p/{self.project.id}/books/{self.book.id}/',
            f'/p/{self.project.id}/rounds/new/', f'/p/{self.project.id}/progress/',
            f'/p/{self.project.id}/history/', f'/p/{self.project.id}/exports/',
            f'/p/{self.project.id}/archive/',
            f'/p/{self.project.id}/books/{self.book.id}/import/']:
            response = self.client.get(path)
            self.assertContains(response, f'href="/p/{self.project.id}/">← 返回上一页')

    def test_workbench_returns_to_project_and_review_returns_to_workbench(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(f'/r/{self.r.id}/'), f'href="/p/{self.project.id}/">← 返回上一页')
        Round.objects.filter(pk=self.r.id).update(state='review')
        for path in [f'/r/{self.r.id}/review/', f'/r/{self.r.id}/disagreements/', f'/r/{self.r.id}/report/']:
            self.assertContains(self.client.get(path), f'href="/r/{self.r.id}/">← 返回上一页')

    def test_project_home_falls_back_to_workspace(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(f'/p/{self.project.id}/'), 'href="/projects/">← 返回上一页')

    def test_history_detail_falls_back_to_history_list(self):
        assignment = Assignment.objects.get(round=self.r, coder=self.owner, unit=self.units[0])
        assignment.revision = 1
        assignment.save(update_fields=['revision'])
        self.client.force_login(self.owner)
        response = self.client.get(f'/p/{self.project.id}/history/{assignment.id}/')
        self.assertContains(response,
                            f'href="/p/{self.project.id}/history/">← 返回上一页')

    def test_invalid_history_correction_keeps_safe_history_return(self):
        assignment = Assignment.objects.get(round=self.r, coder=self.owner, unit=self.units[0])
        assignment.revision = 1
        assignment.save(update_fields=['revision'])
        self.client.force_login(self.owner)
        response = self.client.post(
            f'/p/{self.project.id}/history/{assignment.id}/reopen/',
            {'revision': assignment.revision, 'reason': ''})
        self.assertContains(response,
                            f'href="/p/{self.project.id}/history/">← 返回上一页')
        assignment.refresh_from_db()
        self.assertEqual(assignment.revision, 1)

    def test_first_password_setup_cannot_be_bypassed_by_return(self):
        Profile.objects.create(user=self.coder, must_change_password=True)
        self.client.force_login(self.coder)
        response = self.client.get('/accounts/password/')
        self.assertContains(response, '请先完成首次密码设置')
        self.assertNotContains(response, 'id="page-back"')
        self.assertEqual(self.client.get('/').url, '/accounts/password/')

    def test_permission_error_still_has_safe_return_without_private_data(self):
        self.client.force_login(self.coder)
        response = self.client.get(f'/p/{self.project.id}/members/')
        self.assertContains(response, 'id="page-back"', status_code=403)
        self.assertContains(response, 'href="/">← 返回上一页', status_code=403)
        self.assertNotContains(response, self.project.goal, status_code=403)

    def test_missing_page_has_return_without_exposing_other_projects(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f'/p/{self.project.id}/')
        self.assertContains(response, 'id="page-back"', status_code=404)
        self.assertContains(response, 'href="/">← 返回上一页', status_code=404)
        self.assertNotContains(response, self.project.name, status_code=404)
