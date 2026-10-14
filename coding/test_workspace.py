"""Entry and project-list regression checks on fictional accounts only."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from .group_services import review_membership
from .models import AuditEvent, Membership, Project, ProjectJoinRequest
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS, PLATFORM_ALLOW_REGISTRATION=True)
class WorkspaceTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.owner = users.objects.create_user('workspace_owner', password='Synthetic!Pass58')
        self.peer = users.objects.create_user('workspace_peer', password='Synthetic!Pass58')
        self.other = users.objects.create_user('workspace_other', password='Synthetic!Pass58')
        self.project = Project.objects.create(name='虚构自建项目', owner=self.owner)
        self.group = Project.objects.create(name='虚构小组项目', owner=self.peer, goal='测试私有目标')
        Membership.objects.create(project=self.project, user=self.owner, role='owner')
        Membership.objects.create(project=self.group, user=self.peer, role='owner')

    def test_anonymous_workspace_routes_require_login(self):
        for path in ['/', '/projects/', '/groups/join/']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith('/login/?next='))

    def test_login_and_registration_have_no_sidebar_or_marketing(self):
        for path in ['/login/', '/accounts/register/']:
            response = self.client.get(path)
            self.assertContains(response, 'AI辅助编码平台')
            self.assertContains(response, 'workspace-auth')
            self.assertNotContains(response, '<aside class="sidebar">')
            self.assertNotContains(response, 'id="page-back"')
            self.assertNotContains(response, 'hero-title')
            self.assertContains(response, 'csrfmiddlewaretoken')

    def test_authenticated_login_page_still_has_no_sidebar(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get('/login/'), '<aside class="sidebar">')

    @override_settings(PLATFORM_ALLOW_REGISTRATION=False)
    def test_closed_registration_is_preserved(self):
        response = self.client.get('/accounts/register/')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, '暂不允许自行注册', status_code=403)
        self.assertNotContains(response, '<aside class="sidebar">', status_code=403)
        self.assertNotContains(self.client.get('/login/'), 'href="/accounts/register/"')

    def test_entry_only_has_two_modules_and_small_help(self):
        self.client.force_login(self.owner)
        response = self.client.get('/')
        self.assertContains(response, 'class="workspace-module"', count=2)
        self.assertContains(response, 'href="/projects/"')
        self.assertContains(response, 'href="/groups/join/"')
        self.assertContains(response, 'class="workspace-help"')
        self.assertNotContains(response, self.project.name)
        self.assertNotContains(response, '创建项目')
        self.assertNotContains(response, '<aside class="sidebar">')
        self.assertNotContains(response, 'id="page-back"')

    def test_empty_projects_has_only_prominent_create_action(self):
        self.client.force_login(self.other)
        response = self.client.get('/projects/')
        self.assertContains(response, '还没有创建项目，快点创建属于自己的新项目吧。')
        self.assertContains(response, 'href="/projects/new/"', count=1)
        self.assertContains(response, 'workspace-create')
        self.assertNotContains(response, 'workspace-modules')
        self.assertNotContains(response, '<aside class="sidebar">')
        self.assertNotContains(response, self.project.name)

    def test_owned_and_joined_projects_show_creation_and_update_times(self):
        Membership.objects.create(project=self.group, user=self.owner, role='coder')
        self.client.force_login(self.owner)
        response = self.client.get('/projects/')
        self.assertContains(response, self.project.name)
        self.assertContains(response, self.group.name)
        self.assertContains(response, '小组项目 · 编码员')
        self.assertContains(response, '创建时间', count=2)
        self.assertContains(response, '最后更新', count=2)
        self.assertContains(response, 'href="/projects/new/"', count=1)
        self.assertNotContains(response, 'workspace-create')
        self.assertNotContains(response, 'workspace-modules')
        self.assertNotContains(response, '<aside class="sidebar">')

    def test_pending_application_does_not_make_project_visible(self):
        ProjectJoinRequest.objects.create(project=self.group, user=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get('/projects/')
        self.assertNotContains(response, self.group.name)
        self.assertNotContains(response, self.group.goal)

    def test_approved_membership_automatically_appears_in_projects(self):
        application = ProjectJoinRequest.objects.create(project=self.group, user=self.owner)
        review_membership(self.group, self.peer, application.id, application.revision,
                          'approve', 'coder', '测试通过')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/projects/'), self.group.name)
        self.assertContains(self.client.get('/groups/join/'), '已加入')

    def test_update_time_is_latest_project_event_without_duplicate_cards(self):
        first = AuditEvent.objects.create(project=self.project, actor=self.owner,
                                         action='project.edit', object_type='Project', object_id=str(self.project.id))
        second = AuditEvent.objects.create(project=self.project, actor=self.owner,
                                          action='annotation.submit', object_type='Assignment', object_id='1')
        latest = timezone.now() + timedelta(hours=1)
        AuditEvent.objects.filter(pk=first.id).update(created_at=latest - timedelta(minutes=30))
        AuditEvent.objects.filter(pk=second.id).update(created_at=latest)
        self.client.force_login(self.owner)
        response = self.client.get('/projects/')
        membership = list(response.context['memberships'])[0]
        self.assertEqual(membership.project_updated_at, latest)
        self.assertContains(response, 'workspace-project-card', count=1)
        self.assertContains(response, timezone.localtime(latest).strftime('%Y.%m.%d %H:%M'))

    def test_project_without_audit_falls_back_to_creation_time(self):
        self.client.force_login(self.owner)
        membership = list(self.client.get('/projects/').context['memberships'])[0]
        self.assertEqual(membership.project_updated_at, membership.project.created_at)

    def test_group_page_keeps_sidebar_and_shows_simple_empty_state(self):
        self.client.force_login(self.owner)
        response = self.client.get('/groups/join/')
        self.assertContains(response, '<aside class="sidebar">')
        self.assertContains(response, 'id="page-back"')
        self.assertContains(response, '暂无项目')
        self.assertNotContains(response, self.project.name)
        self.assertNotContains(response, self.group.name)

    def test_group_list_only_includes_joined_projects(self):
        Membership.objects.create(project=self.group, user=self.owner, role='viewer')
        self.client.force_login(self.owner)
        response = self.client.get('/groups/join/')
        self.assertContains(response, self.group.name)
        self.assertNotContains(response, self.project.name)
        self.assertNotContains(response, '暂无项目')

    def test_invalid_join_keeps_membership_and_application_context(self):
        Membership.objects.create(project=self.group, user=self.owner, role='coder')
        ProjectJoinRequest.objects.create(project=self.group, user=self.owner,
                                          status='rejected', decision_note='测试审核说明')
        self.client.force_login(self.owner)
        response = self.client.post('/groups/join/', {'invitation_code': 'invalid', 'note': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertContains(response, self.group.name)
        self.assertContains(response, '测试审核说明')

    def test_applications_still_offer_withdrawal_and_pagination(self):
        for index in range(11):
            project = Project.objects.create(name=f'虚构待审核项目{index}', owner=self.peer)
            ProjectJoinRequest.objects.create(project=project, user=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get('/groups/join/')
        self.assertContains(response, '撤回申请', count=10)
        self.assertContains(response, 'requests_page=2')
        response = self.client.get('/groups/join/?requests_page=2')
        self.assertContains(response, '撤回申请', count=1)
        self.assertContains(response, 'requests_page=1')

    def test_project_and_creation_keep_sidebar(self):
        self.client.force_login(self.owner)
        for path in ['/projects/new/', f'/p/{self.project.id}/']:
            response = self.client.get(path)
            self.assertContains(response, '<aside class="sidebar">')
            self.assertContains(response, 'href="/projects/" class="nav-item"')

    def test_project_names_are_escaped(self):
        self.project.name = '<script>alert("synthetic")</script>'
        self.project.save()
        self.client.force_login(self.owner)
        response = self.client.get('/projects/')
        self.assertNotContains(response, self.project.name)
        self.assertContains(response, '&lt;script&gt;')

    def test_base_layout_does_not_shadow_child_pagination(self):
        data = fixture()
        self.client.force_login(data['owner'])
        response = self.client.get(f'/p/{data["project"].id}/units/')
        self.assertContains(response, data['units'][0].text)
        self.assertContains(response, f'/p/{data["project"].id}/units/{data["units"][0].id}/')
        self.assertContains(response, '1 / 1 · 共3个单元')
        response = self.client.get(f'/p/{data["project"].id}/history/')
        self.assertContains(response, '找到 0 条记录')
        self.assertContains(response, '1 / 1')
