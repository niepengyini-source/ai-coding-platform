"""Project-list navigation uses fictional accounts and Django's temporary test DB."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from .models import Membership, Project, ProjectJoinRequest
from .tests import TEST_SETTINGS


@override_settings(**TEST_SETTINGS, PLATFORM_ALLOW_REGISTRATION=True)
class ProjectsSidebarTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.member = users.objects.create_user('sidebar_member')
        self.peer = users.objects.create_user('sidebar_peer')
        self.private = Project.objects.create(name='虚构未加入项目', goal='不应泄漏的虚构目标',
                                              owner=self.peer)
        Membership.objects.create(project=self.private, user=self.peer, role='owner')
        self.client.force_login(self.member)

    def assert_project_navigation(self, response):
        self.assertContains(response, '<aside class="sidebar">')
        self.assertContains(response, 'aria-label="主要导航"')
        self.assertContains(response, 'data-sidebar-toggle')
        self.assertContains(response, 'href="/projects/" class="nav-item" aria-current="page"')
        self.assertNotContains(response, 'workspace-layout')
        self.assertNotContains(response, '<div class="nav-label">当前项目</div>')
        self.assertNotContains(response, 'id="page-back"')
        for path, label in [('/groups/join/', '用邀请码加入'), ('/guide/', '使用帮助')]:
            with self.subTest(path=path):
                self.assertContains(response, f'href="{path}"')
                self.assertContains(response, label)

    def test_project_list_keeps_sidebar_without_project_items(self):
        owned = Project.objects.create(name='虚构自建项目', owner=self.member)
        Membership.objects.create(project=owned, user=self.member, role='owner')
        response = self.client.get('/projects/')
        self.assertContains(response, '<aside class="sidebar">')
        self.assertContains(response, '我的项目')
        self.assertNotContains(response, 'id="project-navigation"')

    def test_empty_projects_keeps_create_join_and_help_actions(self):
        response = self.client.get('/projects/')
        self.assert_project_navigation(response)
        self.assertContains(response, '还没有项目')
        self.assertContains(response, 'workspace-create')
        self.assertContains(response, 'href="/projects/new/"', count=1)

    def test_owned_and_joined_project_cards_keep_details_and_timestamps(self):
        owned = Project.objects.create(name='虚构自建项目', owner=self.member)
        Membership.objects.create(project=owned, user=self.member, role='owner')
        Membership.objects.create(project=self.private, user=self.member, role='coder')
        response = self.client.get('/projects/')
        self.assert_project_navigation(response)
        self.assertContains(response, owned.name)
        self.assertContains(response, self.private.name)
        self.assertContains(response, '创建时间', count=2)
        self.assertContains(response, '最后更新', count=2)
        self.assertContains(response, '<details class="workspace-project-details">', count=2)

    def test_project_list_does_not_expose_unjoined_or_pending_projects(self):
        ProjectJoinRequest.objects.create(project=self.private, user=self.member)
        response = self.client.get('/projects/')
        self.assert_project_navigation(response)
        self.assertNotContains(response, self.private.name)
        self.assertNotContains(response, self.private.goal)
        self.assertNotContains(response, f'href="/p/{self.private.id}/"')
        self.assertEqual(self.client.get(f'/p/{self.private.id}/').status_code, 404)

    def test_home_shows_same_project_list_with_sidebar(self):
        response = self.client.get('/')
        self.assert_project_navigation(response)
        self.assertTemplateUsed(response, 'coding/projects.html')

    def test_public_auth_pages_remain_without_sidebar(self):
        self.client.logout()
        for path in ['/login/', '/accounts/register/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotContains(response, '<aside class="sidebar">')
                self.assertContains(response, 'workspace-layout')
                self.assertNotContains(response, 'id="page-back"')
