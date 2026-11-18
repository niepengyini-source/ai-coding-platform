"""Project help follows explicit links, never a session-wide last-project value."""
from django.core.cache import cache
from django.test import TestCase, override_settings

from .models import Codebook, Membership, Project, Round
from .tests import TEST_SETTINGS, fixture
from .workflow import start_quick_coding


@override_settings(**TEST_SETTINGS)
class ProjectHelpTests(TestCase):
    def setUp(self):
        cache.clear()
        for name, value in fixture().items():
            setattr(self, name, value)
        self.client.force_login(self.owner)
        self.help = f'/guide/?project={self.project.pk}'
        self.round_help = self.help + f'&round={self.r.pk}'

    def sidebar(self, response):
        self.assertEqual(response.status_code, 200)
        return response.content.decode().split('<nav aria-label="主要导航">', 1)[1].split('</nav>', 1)[0]

    def test_global_help_keeps_only_three_global_navigation_links(self):
        response = self.client.get('/guide/')
        sidebar = self.sidebar(response)
        self.assertEqual(sidebar.count('class="nav-item"'), 3)
        self.assertIn('href="/projects/"', sidebar)
        self.assertIn('href="/groups/join/"', sidebar)
        self.assertIn('href="/guide/"', sidebar)
        self.assertNotIn(self.project.name, sidebar)
        self.assertNotIn('project', response.context)

    def test_project_help_preserves_full_project_sidebar_and_project_return(self):
        response = self.client.get(self.help)
        sidebar = self.sidebar(response)
        self.assertEqual(response.context['project'], self.project)
        self.assertEqual(response.context['role'], 'owner')
        self.assertIn(self.project.name, sidebar)
        for suffix in ['', 'units/', f'books/{self.book.pk}/', 'code/', 'history/', 'exports/', 'archive/',
                       'progress/', 'members/', 'rounds/new/']:
            self.assertIn(f'href="/p/{self.project.pk}/{suffix}"', sidebar)
        self.assertContains(response, f'href="/p/{self.project.pk}/">← 返回上一页')
        self.assertIn(f'href="{self.help}"', sidebar)

    def test_workbench_help_preserves_round_and_return_route(self):
        response = self.client.get(self.round_help)
        self.assertEqual(response.context['round'], self.r)
        self.assertIn(f'href="/r/{self.r.pk}/"', self.sidebar(response))
        self.assertContains(response, f'href="/r/{self.r.pk}/">← 返回上一页')
        self.assertContains(response, f'href="{self.help}&amp;round={self.r.pk}" class="nav-item"')

    def test_project_pages_link_to_scoped_help(self):
        for suffix in ['', 'units/', 'materials/', f'books/{self.book.pk}/', 'history/', 'exports/', 'archive/']:
            with self.subTest(suffix=suffix):
                response = self.client.get(f'/p/{self.project.pk}/{suffix}')
                self.assertContains(response, f'href="{self.help}" class="nav-item"')
                self.assertContains(response, '<span class="nav-text">使用帮助</span>', html=True)

    def test_workbench_help_and_settings_share_project_and_round_context(self):
        response = self.client.get(f'/r/{self.r.pk}/')
        self.assertContains(response, f'href="{self.help}&amp;round={self.r.pk}" class="nav-item"')
        self.assertContains(response, '<span class="nav-text">使用帮助</span>', html=True)
        self.assertContains(response, f'href="{self.help}&amp;round={self.r.pk}#shortcuts">设置快捷键')

    def test_my_projects_and_join_leave_project_context(self):
        response = self.client.get(self.round_help)
        sidebar = self.sidebar(response)
        self.assertIn('href="/projects/"', sidebar)
        self.assertIn('href="/groups/join/"', sidebar)
        for path in ['/projects/', '/groups/join/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                sidebar = self.sidebar(response)
                self.assertEqual(sidebar.count('class="nav-item"'), 3)
                self.assertNotIn('当前项目', sidebar)
                self.assertIn('href="/guide/"', sidebar)

    def test_context_does_not_leak_between_project_tabs_or_global_help(self):
        second = Project.objects.create(name='第二个虚构标签页', owner=self.owner)
        Membership.objects.create(project=second, user=self.owner, role='owner')
        Codebook.objects.create(project=second, version=1)
        paths = [(self.help, self.project), (f'/guide/?project={second.pk}', second),
                 (self.round_help, self.project), ('/guide/', None)]
        before = dict(self.client.session)
        for path, project in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                if project:
                    self.assertEqual(response.context['project'], project)
                else:
                    self.assertNotIn('project', response.context)
        self.assertEqual(dict(self.client.session), before)

    def test_anonymous_help_redirects_to_login_including_context(self):
        self.client.logout()
        for path in ['/guide/', self.help, self.round_help]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith('/login/?next='))
                self.assertNotIn(self.project.name, response.content.decode())

    def test_non_member_cannot_read_project_or_round_help(self):
        self.client.force_login(self.outsider)
        for path in [self.help, self.round_help]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, self.project.name, status_code=404)
                self.assertNotContains(response, self.project.goal, status_code=404)

    def test_round_from_different_project_is_rejected_even_when_user_is_member_of_both(self):
        Membership.objects.create(project=self.other, user=self.owner, role='coder')
        other = Round.objects.create(project=self.other, book=self.foreign_code.book,
                                     name='其他项目的虚构轮次', kind='pilot')
        response = self.client.get(self.help + f'&round={other.pk}')
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, other.name, status_code=404)

    def test_invalid_or_ambiguous_context_is_rejected(self):
        for query in ['project=', 'project=wrong', 'project=0', 'project=-1', 'project=999999999',
                      f'project={self.project.pk}&project={self.other.pk}', f'round={self.r.pk}',
                      f'project={self.project.pk}&round=', f'project={self.project.pk}&round=wrong',
                      f'project={self.project.pk}&round=999999999',
                      f'project={self.project.pk}&round={self.r.pk}&round={self.r.pk}']:
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/guide/?' + query).status_code, 404)

    def test_viewer_keeps_project_context_without_edit_or_start_permissions(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.round_help)
        sidebar = self.sidebar(response)
        self.assertEqual(response.context['role'], 'viewer')
        self.assertIn(self.project.name, sidebar)
        self.assertNotIn(f'href="/p/{self.project.pk}/code/"', sidebar)
        self.assertNotIn(f'href="/p/{self.project.pk}/members/"', sidebar)
        self.assertNotIn(f'href="/p/{self.project.pk}/rounds/new/"', sidebar)
        self.assertEqual(self.client.get(f'/p/{self.project.pk}/code/').status_code, 403)

    def test_private_quick_round_access_matches_workbench_policy(self):
        personal, _ = start_quick_coding(self.project, self.coder, self.book.pk, '07b6d71a-48bf-45f7-8fa0-c132997f7bac')
        path = self.help + f'&round={personal.pk}'
        for user, status in [(self.coder, 200), (self.owner, 200), (self.viewer, 404)]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(path).status_code, status)

    def test_reviewer_and_coder_receive_their_existing_navigation_permissions(self):
        for role in ['reviewer', 'coder']:
            Membership.objects.filter(project=self.project, user=self.coder).update(role=role)
            self.client.force_login(self.coder)
            response = self.client.get(self.help)
            sidebar = self.sidebar(response)
            self.assertEqual(response.context['role'], role)
            self.assertNotIn(f'href="/p/{self.project.pk}/members/"', sidebar)
            self.assertEqual(f'href="/p/{self.project.pk}/rounds/new/"' in sidebar, role == 'reviewer')
