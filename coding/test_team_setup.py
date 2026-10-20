"""The optional team guide uses fictional users and the isolated test database."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .group_services import invitation_digest, issue_invitation
from .models import (Assignment, AuditEvent, Codebook, Membership, Project,
                     ProjectInvitation, ProjectJoinRequest, Round)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class TeamSetupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user('guide-owner', password='test-only-password')
        self.applicant = get_user_model().objects.create_user('guide-applicant', password='test-only-password')
        self.project = Project.objects.create(name='虚构邀请流程', owner=self.owner)
        Membership.objects.create(project=self.project, user=self.owner, role='owner')
        self.book = Codebook.objects.create(project=self.project, version=1)
        self.url = reverse('team_setup', args=[self.project.pk])
        self.invitation_url = reverse('invitation_manage', args=[self.project.pk])
        self.client.force_login(self.owner)

    def issue(self, **values):
        data = {'team_setup': 'yes', 'action': 'issue', 'revision': 0, 'days': 7}
        data.update(values)
        return self.client.post(self.invitation_url, data)

    def application(self, user=None, project=None, **values):
        return ProjectJoinRequest.objects.create(project=project or self.project,
                                                user=user or self.applicant, **values)

    def review(self, application, **values):
        data = {'team_setup': 'yes', 'action': 'approve', 'revision': application.revision,
                'role': 'coder', 'decision_note': ''}
        data.update(values)
        return self.client.post(reverse('join_review', args=[self.project.pk, application.pk]), data)

    def snapshot(self):
        return (ProjectInvitation.objects.count(), ProjectJoinRequest.objects.count(),
                Membership.objects.count(), Round.objects.count(), Assignment.objects.count(),
                AuditEvent.objects.count(), list(Codebook.objects.values('id', 'revision', 'frozen')))

    def test_get_first_step_does_not_mutate_business_data(self):
        before = self.snapshot()
        for _ in range(2):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['team_stage'], 1)
            self.assertContains(response, '第一步：生成邀请码')
            self.assertContains(response, '这是可选流程')
            self.assertContains(response, reverse('coding_entry', args=[self.project.pk]))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_only_owner_can_read_or_manage_the_guide(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for role in ('coder', 'reviewer', 'viewer'):
            Membership.objects.update_or_create(project=self.project, user=self.applicant, defaults={'role': role})
            self.client.force_login(self.applicant)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.issue().status_code, 403)
        Membership.objects.filter(project=self.project, user=self.applicant).delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(ProjectInvitation.objects.exists())

    def test_issue_stays_in_guide_and_raw_code_is_shown_only_once(self):
        response = self.issue(next='https://example.invalid/elsewhere')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        secret = self.client.session['new_invitation_' + str(self.project.pk)]['code']
        normalized = secret.replace('-', '')
        invitation = ProjectInvitation.objects.get(project=self.project)
        self.assertEqual(invitation.code_digest, invitation_digest(normalized))
        self.assertNotIn(secret, response.url)
        self.assertNotIn(normalized, response.url)
        events = json.dumps(list(AuditEvent.objects.values('before', 'after', 'reason')))
        self.assertNotIn(secret, events)
        self.assertNotIn(normalized, events)
        response = self.client.get(self.url)
        self.assertEqual(response.context['team_stage'], 2)
        self.assertContains(response, secret)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotContains(self.client.get(self.url), secret)
        self.assertEqual(ProjectInvitation.objects.count(), 1)
        self.assertFalse(Round.objects.exists())
        self.assertFalse(Assignment.objects.exists())

    def test_stale_or_revoked_session_code_is_not_rendered(self):
        self.issue()
        secret = self.client.session['new_invitation_' + str(self.project.pk)]['code']
        issue_invitation(self.project, self.owner, 1, 7)
        self.assertNotContains(self.client.get(self.url), secret)
        self.issue(revision=2)
        response = self.client.post(self.invitation_url, {'team_setup': 'yes', 'action': 'revoke',
                                                        'revision': 3, 'days': 7})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertNotIn('new_invitation_' + str(self.project.pk), self.client.session)
        self.assertEqual(self.client.get(self.url).context['team_stage'], 1)

    def test_member_registration_application_and_approval_end_at_coding(self):
        self.issue()
        secret = self.client.session['new_invitation_' + str(self.project.pk)]['code']
        member_client = Client()
        member_client.force_login(self.applicant)
        response = member_client.post(reverse('join_group'), {'invitation_code': secret, 'note': '虚构协作者'})
        self.assertRedirects(response, reverse('join_group'), fetch_redirect_response=False)
        application = ProjectJoinRequest.objects.get(project=self.project, user=self.applicant)
        response = self.client.get(self.url)
        self.assertEqual(response.context['team_stage'], 2)
        self.assertContains(response, self.applicant.username)
        response = self.review(application)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(Membership.objects.get(project=self.project, user=self.applicant).role, 'coder')
        response = self.client.get(self.url)
        self.assertEqual(response.context['team_stage'], 3)
        self.assertContains(response, '开始／继续编码')
        self.assertContains(response, '查看成员进度')
        for target in ('coding_entry', 'progress', 'round_new', 'project'):
            self.assertContains(response, reverse(target, args=[self.project.pk]))
        self.assertContains(response, '需要分工时，可先安排团队任务')
        self.assertFalse(Round.objects.exists())
        self.assertFalse(Assignment.objects.exists())

    def test_viewer_and_inactive_coder_do_not_count_as_coding_members(self):
        Membership.objects.create(project=self.project, user=self.applicant, role='viewer')
        response = self.client.get(self.url)
        self.assertEqual(response.context['team_stage'], 1)
        self.assertEqual(response.context['collaborator_count'], 0)
        Membership.objects.filter(project=self.project, user=self.applicant).update(role='coder')
        self.applicant.is_active = False
        self.applicant.save()
        self.assertEqual(self.client.get(self.url).context['team_stage'], 1)
        self.applicant.is_active = True
        self.applicant.save()
        Membership.objects.filter(project=self.project, user=self.applicant).update(role='reviewer')
        response = self.client.get(self.url)
        self.assertEqual(response.context['team_stage'], 3)
        self.assertEqual(response.context['collaborator_count'], 1)
        self.assertFalse(ProjectInvitation.objects.exists())

    def test_viewer_approval_does_not_suggest_coding_is_ready(self):
        application = self.application()
        self.review(application, role='viewer')
        self.assertEqual(self.client.get(self.url).context['team_stage'], 1)
        self.assertEqual(Membership.objects.get(project=self.project, user=self.applicant).role, 'viewer')

    def test_rejection_and_invalid_review_stay_on_the_guide(self):
        self.issue()
        application = self.application()
        response = self.review(application, role='owner')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        application.refresh_from_db()
        self.assertEqual(application.status, 'pending')
        response = self.review(application, action='reject', decision_note='虚构申请不通过')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        application.refresh_from_db()
        self.assertEqual(application.status, 'rejected')
        self.assertEqual(self.client.get(self.url).context['team_stage'], 2)
        self.assertFalse(Membership.objects.filter(project=self.project, user=self.applicant).exists())

    def test_invalid_and_concurrent_invitation_changes_preserve_state(self):
        response = self.issue(days=999)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse(ProjectInvitation.objects.exists())
        self.issue()
        invitation = ProjectInvitation.objects.get(project=self.project)
        original_digest = invitation.code_digest
        response = self.issue(revision=0)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        invitation.refresh_from_db()
        self.assertEqual(invitation.code_digest, original_digest)
        self.assertEqual(invitation.revision, 1)

    def test_revoked_or_expired_invitation_keeps_pending_applications_reviewable(self):
        invitation, _ = issue_invitation(self.project, self.owner, 0, 7)
        application = self.application()
        for changes in ({'enabled': False}, {'enabled': True, 'expires_at': timezone.now() - timedelta(days=1)}):
            ProjectInvitation.objects.filter(pk=invitation.pk).update(**changes)
            response = self.client.get(self.url)
            self.assertEqual(response.context['team_stage'], 2)
            self.assertContains(response, self.applicant.username)
        self.review(application)
        self.assertEqual(self.client.get(self.url).context['team_stage'], 3)

    def test_pending_requests_are_paginated_and_project_scoped(self):
        other = Project.objects.create(name='另一虚构项目', owner=self.owner)
        foreign = self.application(project=other, note='foreign-project-secret')
        self.application(status='approved', note='processed-application-secret')
        for i in range(21):
            user = get_user_model().objects.create_user(f'pending-{i}')
            self.application(user=user)
        response = self.client.get(self.url)
        self.assertEqual(response.context['requests_page'].paginator.count, 21)
        self.assertEqual(len(response.context['requests_page']), 20)
        self.assertNotContains(response, 'foreign-project-secret')
        self.assertNotContains(response, 'processed-application-secret')
        self.assertContains(response, '?join_page=2')
        self.assertEqual(len(self.client.get(self.url, {'join_page': 2}).context['requests_page']), 1)
        self.assertEqual(self.review(foreign).status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'pending')

    def test_old_members_actions_keep_their_original_redirects(self):
        self.assertEqual(self.client.post(self.invitation_url, {'action': 'issue', 'days': 999}).status_code, 400)
        response = self.client.post(self.invitation_url, {'action': 'issue', 'revision': 0, 'days': 7})
        self.assertRedirects(response, reverse('members', args=[self.project.pk]), fetch_redirect_response=False)
        application = self.application()
        response = self.client.post(reverse('join_review', args=[self.project.pk, application.pk]),
            {'action': 'approve', 'revision': application.revision, 'role': 'coder', 'decision_note': ''})
        self.assertRedirects(response, reverse('members', args=[self.project.pk]), fetch_redirect_response=False)

    def test_posts_still_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.invitation_url, {'team_setup': 'yes', 'action': 'issue',
                                                         'revision': 0, 'days': 7}).status_code, 403)
        application = self.application()
        self.assertEqual(client.post(reverse('join_review', args=[self.project.pk, application.pk]),
            {'team_setup': 'yes', 'action': 'approve', 'revision': 1, 'role': 'coder'}).status_code, 403)
        self.assertFalse(ProjectInvitation.objects.exists())
        application.refresh_from_db()
        self.assertEqual(application.status, 'pending')
