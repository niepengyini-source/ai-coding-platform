"""Fast collaboration invites from historical projects, plus one-click direct member addition."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .group_services import mark_approved
from .models import Membership, Project, ProjectDirectInvitation, ProjectJoinRequest
from .services import audit


VALID_ROLES = {'coder', 'reviewer', 'viewer'}
ROLE_LABELS = dict(Membership.ROLES)


def historical_collaborators(project, actor):
    """Return active users who shared any project with actor, excluding current project members."""
    current_ids = set(project.memberships.values_list('user_id', flat=True))
    shared_project_ids = list(Membership.objects.filter(user=actor).exclude(project=project)
                              .values_list('project_id', flat=True))
    if not shared_project_ids:
        return []
    rows = (Membership.objects.filter(project_id__in=shared_project_ids, user__is_active=True)
            .exclude(user_id__in=current_ids)
            .select_related('user', 'project')
            .order_by('-project__created_at', '-id'))
    pending_by_user = {inv.invitee_id: inv for inv in project.direct_invitations.filter(status='pending')}
    grouped = {}
    for row in rows:
        entry = grouped.get(row.user_id)
        if entry is None:
            entry = {
                'user': row.user,
                'recent_role': row.role if row.role in VALID_ROLES else 'coder',
                'projects': [],
                'pending_invite': pending_by_user.get(row.user_id),
            }
            grouped[row.user_id] = entry
        if row.project.name not in entry['projects'] and len(entry['projects']) < 3:
            entry['projects'].append(row.project.name)
    return list(grouped.values())


def pending_direct_invites(project):
    return list(project.direct_invitations.filter(status='pending').select_related('invitee', 'inviter'))


def received_direct_invites(user):
    return list(ProjectDirectInvitation.objects.filter(invitee=user, status='pending', project__trashed_at__isnull=True)
                .select_related('project', 'inviter').order_by('-created_at', '-id'))


def _owner(project, actor):
    if not Membership.objects.filter(project=project, user=actor, role='owner').exists():
        raise PermissionDenied('只有项目负责人可以邀请或添加成员。')


@transaction.atomic
def invite_or_add_collaborators(project, actor, *, user_ids=None, username='', role='coder', mode='invite'):
    project = Project.objects.select_for_update().get(pk=project.pk, trashed_at__isnull=True)
    _owner(project, actor)
    if role not in VALID_ROLES:
        raise ValueError('请选择有效的成员权限（编码员、复核者或只读成员）。')
    if mode not in ('invite', 'direct_add'):
        raise ValueError('请选择“发送邀请”或“直接加入项目”。')
    User = get_user_model()
    targets = []
    if user_ids:
        clean_ids = []
        for raw in user_ids:
            text = str(raw).strip()
            if not text.isascii() or not text.isdecimal() or len(text) > 18:
                raise ValueError('成员编号不正确，请刷新页面后重试。')
            clean_ids.append(int(text))
        targets.extend(User.objects.filter(pk__in=clean_ids, is_active=True).order_by('id'))
    typed = (username or '').strip()
    if typed:
        found = User.objects.filter(username=typed, is_active=True).first()
        if not found:
            raise ValueError(f'未找到已注册账号「{typed}」。若同伴尚未注册，请让其先注册账号或使用邀请码。')
        if all(u.pk != found.pk for u in targets):
            targets.append(found)
    if not targets:
        raise ValueError('请先勾选至少一位历史合作成员，或输入已有成员用户名。')

    existing_ids = set(project.memberships.values_list('user_id', flat=True))
    added_names, invited_names, skipped_names = [], [], []
    now = timezone.now()
    for target in targets:
        if target.pk in existing_ids:
            skipped_names.append(target.username)
            continue
        if mode == 'direct_add':
            member, created = Membership.objects.get_or_create(project=project, user=target, defaults={'role': role})
            if created:
                application = ProjectJoinRequest.objects.filter(project=project, user=target, status='pending').first()
                if application:
                    mark_approved(application, actor, '负责人直接拉入项目。')
                ProjectDirectInvitation.objects.filter(project=project, invitee=target, status='pending').update(
                    status='accepted', responded_at=now)
                audit(project, actor, 'membership.direct_add', member,
                      after={'username': target.username, 'role': role})
                added_names.append(target.username)
        else:
            inv = ProjectDirectInvitation.objects.filter(project=project, invitee=target).first()
            if inv:
                inv.inviter = actor
                inv.role = role
                inv.status = 'pending'
                inv.created_at = now
                inv.responded_at = None
                inv.revision += 1
                inv.save()
            else:
                inv = ProjectDirectInvitation.objects.create(project=project, inviter=actor, invitee=target, role=role)
            audit(project, actor, 'invitation.direct_send', inv,
                  after={'username': target.username, 'role': role, 'revision': inv.revision})
            invited_names.append(target.username)
    return {'added': added_names, 'invited': invited_names, 'skipped': skipped_names, 'role_label': ROLE_LABELS[role]}


@transaction.atomic
def cancel_direct_invite(project, actor, invite_id):
    project = Project.objects.select_for_update().get(pk=project.pk, trashed_at__isnull=True)
    _owner(project, actor)
    inv = get_object_or_404(ProjectDirectInvitation, pk=invite_id, project=project)
    if inv.status == 'pending':
        inv.status = 'cancelled'
        inv.responded_at = timezone.now()
        inv.revision += 1
        inv.save(update_fields=['status', 'responded_at', 'revision'])
        audit(project, actor, 'invitation.direct_cancel', inv, after={'username': inv.invitee.username})
    return inv


@transaction.atomic
def respond_direct_invite(user, invite_id, action):
    inv = get_object_or_404(ProjectDirectInvitation.objects.select_related('project'), pk=invite_id, invitee=user)
    if inv.project.trashed_at is not None:
        raise Http404('该项目已被移入回收站，暂不可加入。')
    project = Project.objects.select_for_update().get(pk=inv.project_id, trashed_at__isnull=True)
    inv = ProjectDirectInvitation.objects.select_for_update().get(pk=inv.pk)
    if inv.status != 'pending':
        raise ValueError('该邀请已被处理或已撤回。')
    if action not in ('accept', 'decline'):
        raise ValueError('请选择同意加入或暂不加入。')
    now = timezone.now()
    if action == 'accept':
        member, created = Membership.objects.get_or_create(project=project, user=user, defaults={'role': inv.role})
        inv.status = 'accepted'
        inv.responded_at = now
        inv.revision += 1
        inv.save(update_fields=['status', 'responded_at', 'revision'])
        application = ProjectJoinRequest.objects.filter(project=project, user=user, status='pending').first()
        if application:
            mark_approved(application, inv.inviter, '成员接受直接邀请加入项目。')
        if created:
            audit(project, user, 'invitation.direct_accept', member,
                  after={'username': user.username, 'role': member.role})
        return project, 'accepted'
    inv.status = 'declined'
    inv.responded_at = now
    inv.revision += 1
    inv.save(update_fields=['status', 'responded_at', 'revision'])
    audit(project, user, 'invitation.direct_decline', inv, after={'username': user.username})
    return project, 'declined'


def _redirect_target(request, project_id):
    return redirect('team_setup' if request.POST.get('team_setup') == 'yes' else 'members', project_id)


@require_POST
@login_required
def collaborator_action_view(request, project_id):
    from .views import access
    project, _ = access(request, project_id, ['owner'])
    try:
        result = invite_or_add_collaborators(
            project, request.user,
            user_ids=request.POST.getlist('user_ids'),
            username=request.POST.get('username', ''),
            role=request.POST.get('role', 'coder'),
            mode=request.POST.get('mode', 'invite'),
        )
        if result['added']:
            messages.success(request, f"已将 {'、'.join(result['added'])} 直接加入本项目（{result['role_label']}）。")
        elif result['invited']:
            messages.success(request, f"已向 {'、'.join(result['invited'])} 发出项目邀请（{result['role_label']}），对方在“我的项目”点同意即可加入。")
        elif result['skipped']:
            messages.info(request, f"{'、'.join(result['skipped'])} 已经是本项目成员。")
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return _redirect_target(request, project.pk)


@require_POST
@login_required
def direct_invite_cancel_view(request, project_id, invite_id):
    from .views import access
    project, _ = access(request, project_id, ['owner'])
    try:
        inv = cancel_direct_invite(project, request.user, invite_id)
        messages.success(request, f'已撤回对 {inv.invitee.username} 的邀请。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return _redirect_target(request, project.pk)


@require_POST
@login_required
def direct_invite_respond_view(request, invite_id):
    action = request.POST.get('action', '')
    try:
        project, state = respond_direct_invite(request.user, invite_id, action)
        if state == 'accepted':
            messages.success(request, f'你已加入项目「{project.name}」。')
            return redirect('project', project.pk)
        messages.info(request, f'已谢绝加入「{project.name}」。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return redirect('join_group' if request.POST.get('from_page') == 'join_group' else 'projects')
