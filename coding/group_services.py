"""Invitation and approval operations serialize on the project, like member edits."""
import hashlib
import secrets
from datetime import timedelta
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from .account_forms import INVITE_ALPHABET
from .models import Project, Membership, ProjectInvitation, ProjectJoinRequest
from .services import Conflict, audit


def invitation_digest(code):
    return hashlib.sha256(code.encode('ascii')).hexdigest()


def _owner(project, user):
    if not Membership.objects.filter(project=project, user=user, role='owner').exists():
        raise PermissionDenied


@transaction.atomic
def issue_invitation(project, actor, revision, days):
    project = Project.objects.select_for_update().get(pk=project.pk)
    _owner(project, actor)
    invitation = ProjectInvitation.objects.filter(project=project).first()
    if revision != (invitation.revision if invitation else 0):
        raise Conflict('邀请码已被另一个页面更新，请刷新后再操作。')
    if days not in (1, 7, 30):
        raise ValueError('请选择有效的邀请期限。')
    code = ''.join(secrets.choice(INVITE_ALPHABET) for _ in range(20))
    values = {'code_digest': invitation_digest(code), 'enabled': True,
              'expires_at': timezone.now() + timedelta(days=days), 'created_by': actor}
    if invitation:
        before = {'revision': invitation.revision, 'enabled': invitation.enabled}
        for key, value in values.items():
            setattr(invitation, key, value)
        invitation.revision += 1
        invitation.save()
    else:
        before = {}
        invitation = ProjectInvitation.objects.create(project=project, **values)
    audit(project, actor, 'invitation.issue', invitation, before=before,
          after={'revision': invitation.revision, 'expires_at': invitation.expires_at.isoformat()})
    return invitation, '-'.join(code[start:start + 5] for start in range(0, 20, 5))


@transaction.atomic
def revoke_invitation(project, actor, revision):
    project = Project.objects.select_for_update().get(pk=project.pk)
    _owner(project, actor)
    invitation = ProjectInvitation.objects.filter(project=project).first()
    if not invitation or invitation.revision != revision:
        raise Conflict('邀请码已变化，请刷新后再操作。')
    invitation.enabled = False
    invitation.revision += 1
    invitation.save()
    audit(project, actor, 'invitation.revoke', invitation, after={'revision': invitation.revision})


@transaction.atomic
def request_membership(user, code, note):
    digest = invitation_digest(code)
    invitation = ProjectInvitation.objects.filter(code_digest=digest).first()
    if not invitation:
        raise ValueError('邀请码无效、已过期或已停用，请联系负责人获取新的邀请码。')
    project = Project.objects.select_for_update().get(pk=invitation.project_id)
    # Recheck after acquiring the same lock used for rotation/revocation.
    invitation = ProjectInvitation.objects.filter(project=project, code_digest=digest,
        enabled=True, expires_at__gt=timezone.now()).first()
    if not invitation:
        raise ValueError('邀请码无效、已过期或已停用，请联系负责人获取新的邀请码。')
    if Membership.objects.filter(project=project, user=user).exists():
        return project, 'member'
    application = ProjectJoinRequest.objects.filter(project=project, user=user).first()
    if application and application.status == 'pending':
        return project, 'pending'
    if application:
        application.status, application.note = 'pending', note
        application.requested_at = timezone.now()
        application.decision_note, application.reviewed_by, application.reviewed_at = '', None, None
        application.revision += 1
        application.save()
    else:
        application = ProjectJoinRequest.objects.create(project=project, user=user, note=note)
    audit(project, user, 'join.request', application,
          after={'username': user.username, 'revision': application.revision}, reason=note)
    return project, 'requested'


def mark_approved(application, actor, note=''):
    """Caller holds the project lock, including legacy direct member creation."""
    application.status, application.reviewed_by, application.reviewed_at = 'approved', actor, timezone.now()
    application.decision_note = note
    application.revision += 1
    application.save()
    audit(application.project, actor, 'join.approve', application,
          after={'username': application.user.username, 'revision': application.revision}, reason=note)


@transaction.atomic
def review_membership(project, actor, application_id, revision, action, role, note):
    project = Project.objects.select_for_update().get(pk=project.pk)
    _owner(project, actor)
    application = ProjectJoinRequest.objects.select_related('user').get(pk=application_id, project=project)
    if application.status != 'pending' or application.revision != revision:
        raise Conflict('申请已被处理或撤回，请刷新后查看最新状态。')
    if action not in ('approve', 'reject') or role not in ('coder', 'reviewer', 'viewer'):
        raise ValueError('只能分配编码员、复核者或只读成员权限。')
    if action == 'approve':
        if not application.user.is_active:
            raise ValueError('该账号已停用，不能加入小组。')
        member, created = Membership.objects.get_or_create(project=project, user=application.user,
                                                          defaults={'role': role})
        # A concurrent/direct addition must not be downgraded by an old application.
        if created:
            audit(project, actor, 'membership.set', member,
                  after={'username': application.user.username, 'role': member.role})
        mark_approved(application, actor, note)
    else:
        application.status, application.decision_note = 'rejected', note
        application.reviewed_by, application.reviewed_at = actor, timezone.now()
        application.revision += 1
        application.save()
        audit(project, actor, 'join.reject', application,
              after={'username': application.user.username, 'revision': application.revision}, reason=note)


@transaction.atomic
def cancel_membership_request(user, application_id, revision):
    application = ProjectJoinRequest.objects.get(pk=application_id, user=user)
    project = Project.objects.select_for_update().get(pk=application.project_id)
    application.refresh_from_db()
    if application.status != 'pending' or application.revision != revision:
        raise Conflict('申请状态已变化，请刷新后查看。')
    application.status = 'cancelled'
    application.revision += 1
    application.save()
    audit(project, user, 'join.cancel', application, after={'revision': application.revision})
