"""Self-registration and group membership QA on fictional projects only."""
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from .models import Profile, Membership, Project, ProjectInvitation, ProjectJoinRequest, AuditEvent
from .group_services import issue_invitation, invitation_digest
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS, PLATFORM_ALLOW_REGISTRATION=True)
class AccountTests(TestCase):
    def setUp(self):
        cache.clear()
        for name, value in fixture().items():
            setattr(self, name, value)

    def invitation(self):
        invitation = ProjectInvitation.objects.filter(project=self.project).first()
        return issue_invitation(self.project, self.owner, invitation.revision if invitation else 0, 7)[1]

    def apply(self, user=None, code=None, **extra):
        self.client.force_login(user or self.outsider)
        data = {'invitation_code': code or self.invitation(), 'note': '虚构申请：负责访谈编码'}
        data.update(extra)
        return self.client.post('/groups/join/', data)

    def review(self, application, **extra):
        self.client.force_login(self.owner)
        data = {'revision': application.revision, 'action': 'approve', 'role': 'coder', 'decision_note': '已核对身份'}
        data.update(extra)
        return self.client.post(f'/p/{self.project.id}/requests/{application.id}/review/', data)

    def test_registration_public_entry_and_no_cache(self):
        self.assertContains(self.client.get('/login/'), '创建自己的账号')
        response = self.client.get('/accounts/register/')
        self.assertContains(response, '不需要填写邮箱或手机号')
        self.assertContains(response, 'autocomplete="new-password"')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/groups/join/').status_code, 302)

    def test_registration_success_hashes_password_no_privileges_or_membership(self):
        response = self.client.post('/accounts/register/', {
            'username': 'new_researcher', 'password1': 'Synthetic!Pass58', 'password2': 'Synthetic!Pass58',
            'is_staff': '1', 'is_superuser': '1', 'role': 'owner', 'next': 'https://example.invalid/'})
        self.assertRedirects(response, '/')
        user = get_user_model().objects.get(username='new_researcher')
        self.assertTrue(user.check_password('Synthetic!Pass58'))
        self.assertNotEqual(user.password, 'Synthetic!Pass58')
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(Profile.objects.get(user=user).must_change_password)
        self.assertFalse(Membership.objects.filter(user=user).exists())
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)
        self.assertNotContains(self.client.get('/'), self.project.name)
        self.assertEqual(self.client.get(f'/p/{self.project.id}/').status_code, 404)
        self.assertEqual(self.client.get('/groups/join/').status_code, 200)

    def test_registration_validation_and_duplicate_case(self):
        for username, first, second in [('bad username', 'Synthetic!Pass58', 'Synthetic!Pass58'),
            ('short', 'tiny', 'tiny'), ('mismatch', 'Synthetic!Pass58', 'Different!Pass59'),
            ('TEST_OWNER', 'Synthetic!Pass58', 'Synthetic!Pass58')]:
            response = self.client.post('/accounts/register/', {'username': username, 'password1': first, 'password2': second})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
        self.assertEqual(get_user_model().objects.count(), 4)

    def test_registration_integrity_error_is_readable_not_server_error(self):
        from django.db import IntegrityError
        with patch('coding.account_forms.RegistrationForm.save', side_effect=IntegrityError):
            response = self.client.post('/accounts/register/', {'username': 'new_researcher',
                'password1': 'Synthetic!Pass58', 'password2': 'Synthetic!Pass58'})
        self.assertContains(response, '用户名已被使用')

    def test_registration_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post('/accounts/register/', {'username': 'csrf_user', 'password1': 'Synthetic!Pass58', 'password2': 'Synthetic!Pass58'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username='csrf_user').exists())

    @override_settings(PLATFORM_ALLOW_REGISTRATION=False)
    def test_registration_can_be_disabled(self):
        self.assertNotContains(self.client.get('/login/'), '创建自己的账号')
        self.assertEqual(self.client.get('/accounts/register/').status_code, 403)
        self.assertEqual(self.client.post('/accounts/register/', {'username': 'new_researcher',
            'password1': 'Synthetic!Pass58', 'password2': 'Synthetic!Pass58'}).status_code, 403)
        self.assertEqual(get_user_model().objects.count(), 4)

    def test_authenticated_user_register_does_not_create_second_account(self):
        self.client.force_login(self.coder)
        self.assertRedirects(self.client.post('/accounts/register/', {}), '/')
        self.assertEqual(get_user_model().objects.count(), 4)

    def test_account_registration_throttle_and_retry_header(self):
        for i in range(20):
            self.assertEqual(self.client.post('/accounts/register/', {}).status_code, 200)
        response = self.client.post('/accounts/register/', {})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '900')

    def test_join_throttle(self):
        self.client.force_login(self.outsider)
        for i in range(30):
            self.assertEqual(self.client.post('/groups/join/', {}).status_code, 200)
        self.assertEqual(self.client.post('/groups/join/', {}).status_code, 429)

    def test_invitation_display_once_digest_only_and_audit_no_code(self):
        self.client.force_login(self.owner)
        response = self.client.post(f'/p/{self.project.id}/invitation/', {'action': 'issue', 'revision': 0, 'days': 7}, follow=True)
        code = response.context['new_code']
        self.assertEqual(len(code.replace('-', '')), 20)
        self.assertContains(response, code)
        invitation = ProjectInvitation.objects.get(project=self.project)
        self.assertEqual(invitation.code_digest, invitation_digest(code.replace('-', '')))
        self.assertNotIn(code, json.dumps(list(AuditEvent.objects.values('before', 'after', 'reason'))))
        self.assertNotContains(self.client.get(f'/p/{self.project.id}/members/'), code)

    def test_invitation_owner_only_post_only_and_stale_update_rejected(self):
        for user in [self.coder, self.viewer]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(f'/p/{self.project.id}/invitation/', {'action': 'issue', 'revision': 0, 'days': 7}).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f'/p/{self.project.id}/invitation/').status_code, 405)
        self.invitation()
        response = self.client.post(f'/p/{self.project.id}/invitation/', {'action': 'issue', 'revision': 0, 'days': 7}, follow=True)
        self.assertContains(response, '另一个页面更新')
        self.assertEqual(ProjectInvitation.objects.get(project=self.project).revision, 1)

    def test_join_normalized_invitation_only_pending_and_no_material_access(self):
        code = self.invitation().lower().replace('-', ' - ')
        self.assertEqual(self.apply(code=code, role='owner', project_id=self.other.id).status_code, 302)
        application = ProjectJoinRequest.objects.get(project=self.project, user=self.outsider)
        self.assertEqual(application.status, 'pending')
        self.assertFalse(Membership.objects.filter(project=self.project, user=self.outsider).exists())
        home = self.client.get('/')
        self.assertContains(home, '等待审核')
        self.assertNotContains(home, self.project.goal)
        for path in [f'/p/{self.project.id}/', f'/p/{self.project.id}/units/',
                     f'/r/{self.r.id}/', f'/r/{self.r.id}/export/all/']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_invalid_expired_revoked_and_rotated_codes_not_accepted(self):
        old_code = self.invitation()
        response = self.apply(code='A' * 20)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.project.name)
        ProjectInvitation.objects.filter(project=self.project).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertContains(self.apply(code=old_code), '邀请码无效')
        new_code = self.invitation()
        self.assertContains(self.apply(code=old_code), '邀请码无效')
        ProjectInvitation.objects.filter(project=self.project).update(enabled=False)
        self.assertContains(self.apply(code=new_code), '邀请码无效')
        self.assertFalse(ProjectJoinRequest.objects.exists())

    def test_repeated_join_keeps_one_application_and_original_note(self):
        code = self.invitation()
        self.apply(code=code)
        self.apply(code=code, note='重放请求不改申请说明')
        self.assertEqual(ProjectJoinRequest.objects.count(), 1)
        self.assertEqual(ProjectJoinRequest.objects.get().revision, 1)
        self.assertEqual(ProjectJoinRequest.objects.get().note, '虚构申请：负责访谈编码')
        self.assertEqual(AuditEvent.objects.filter(action='join.request').count(), 1)

    def test_existing_member_join_does_not_change_role(self):
        self.apply(user=self.coder, role='owner')
        self.assertFalse(ProjectJoinRequest.objects.exists())
        self.assertEqual(Membership.objects.get(project=self.project, user=self.coder).role, 'coder')

    def test_approve_grants_selected_role_without_assigning_old_tasks(self):
        self.apply()
        application = ProjectJoinRequest.objects.get()
        self.review(application, role='reviewer')
        application.refresh_from_db()
        self.assertEqual(application.status, 'approved')
        self.assertEqual(Membership.objects.get(project=self.project, user=self.outsider).role, 'reviewer')
        self.assertFalse(self.r.assignments.filter(coder=self.outsider).exists())
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/p/{self.project.id}/').status_code, 200)
        self.assertContains(self.client.get('/'), '已加入')

    def test_approval_requires_owner_and_rejects_owner_role(self):
        self.apply()
        application = ProjectJoinRequest.objects.get()
        for user in [self.coder, self.viewer]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(f'/p/{self.project.id}/requests/{application.id}/review/', {}).status_code, 403)
        self.review(application, role='owner')
        application.refresh_from_db()
        self.assertEqual(application.status, 'pending')
        self.assertFalse(Membership.objects.filter(project=self.project, user=self.outsider).exists())

    def test_cross_project_application_id_not_accessible(self):
        self.apply()
        application = ProjectJoinRequest.objects.get()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(f'/p/{self.other.id}/requests/{application.id}/review/', {}).status_code, 404)

    def test_reject_resubmit_and_stale_owner_page_cannot_approve_new_request(self):
        code = self.invitation()
        self.apply(code=code)
        application = ProjectJoinRequest.objects.get()
        self.review(application, action='reject', decision_note='请补充身份')
        self.client.force_login(self.outsider)
        self.assertContains(self.client.get('/'), '请补充身份')
        self.apply(code=code, note='新的身份说明')
        self.review(application)
        application.refresh_from_db()
        self.assertEqual(application.status, 'pending')
        self.assertEqual(application.note, '新的身份说明')
        self.assertFalse(Membership.objects.filter(project=self.project, user=self.outsider).exists())

    def test_cancel_is_private_then_stale_review_cannot_grant_access(self):
        self.apply()
        application = ProjectJoinRequest.objects.get()
        self.client.force_login(self.coder)
        self.assertEqual(self.client.post(f'/groups/requests/{application.id}/cancel/', {'revision': 1}).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(f'/groups/requests/{application.id}/cancel/', {'revision': 1}).status_code, 302)
        self.review(application)
        application.refresh_from_db()
        self.assertEqual(application.status, 'cancelled')
        self.assertFalse(Membership.objects.filter(project=self.project, user=self.outsider).exists())

    def test_pending_still_reviewable_after_invitation_revoke(self):
        self.apply()
        application = ProjectJoinRequest.objects.get()
        self.client.force_login(self.owner)
        self.client.post(f'/p/{self.project.id}/invitation/', {'revision': 1, 'action': 'revoke', 'days': 7})
        self.review(application)
        self.assertTrue(Membership.objects.filter(project=self.project, user=self.outsider).exists())

    def test_legacy_direct_add_resolves_pending_preserves_existing_password(self):
        self.apply()
        old_hash = self.outsider.password
        self.client.force_login(self.owner)
        self.client.post(f'/p/{self.project.id}/members/', {'username': self.outsider.username, 'role': 'coder', 'password': ''})
        self.outsider.refresh_from_db()
        self.assertEqual(self.outsider.password, old_hash)
        self.assertEqual(ProjectJoinRequest.objects.get().status, 'approved')

    def test_approval_repeated_click_not_duplicate_or_role_change(self):
        self.apply()
        application = ProjectJoinRequest.objects.get()
        self.review(application)
        response = self.review(application, role='reviewer')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Membership.objects.filter(project=self.project, user=self.outsider).count(), 1)
        self.assertEqual(Membership.objects.get(project=self.project, user=self.outsider).role, 'coder')

    def test_member_page_unique_field_ids_and_safe_html(self):
        self.apply(note='<script>fictional()</script>')
        self.client.force_login(self.owner)
        response = self.client.get(f'/p/{self.project.id}/members/')
        application = ProjectJoinRequest.objects.get()
        self.assertContains(response, f'id="request-{application.id}-role"')
        self.assertContains(response, '&lt;script&gt;fictional()&lt;/script&gt;')
        self.assertNotContains(response, '<script>fictional()</script>')


@override_settings(**TEST_SETTINGS)
class ConcurrentGroupTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        for name, value in fixture().items():
            setattr(self, name, value)
        self.code = issue_invitation(self.project, self.owner, 0, 7)[1]

    def run_concurrent(self, path, data, user):
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(user)
        barrier = Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return clients[index].post(path, data).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, [0, 1]))

    def test_concurrent_join_only_one_pending_request(self):
        results = self.run_concurrent('/groups/join/', {'invitation_code': self.code, 'note': '虚构并发申请'}, self.outsider)
        self.assertEqual(results, [302, 302])
        self.assertEqual(ProjectJoinRequest.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='join.request').count(), 1)

    def test_concurrent_approval_only_one_membership_and_audit(self):
        application = ProjectJoinRequest.objects.create(project=self.project, user=self.outsider)
        results = self.run_concurrent(f'/p/{self.project.id}/requests/{application.id}/review/',
            {'revision': 1, 'action': 'approve', 'role': 'coder', 'decision_note': '已核对'}, self.owner)
        self.assertEqual(results, [302, 302])
        self.assertEqual(Membership.objects.filter(project=self.project, user=self.outsider).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='join.approve').count(), 1)
