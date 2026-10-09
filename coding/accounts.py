from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError, OperationalError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from .account_forms import RegistrationForm, JoinGroupForm, InvitationForm, JoinReviewForm
from .models import Profile, ProjectJoinRequest
from .group_services import (issue_invitation, revoke_invitation, request_membership,
                             review_membership, cancel_membership_request)


@sensitive_post_parameters('password1', 'password2')
@require_http_methods(['GET', 'POST'])
def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = RegistrationForm(request.POST if request.method == 'POST' else None)
    if not settings.PLATFORM_ALLOW_REGISTRATION:
        return render(request, 'registration/register.html', {'form': form}, status=403)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
                Profile.objects.create(user=user, must_change_password=False)
        except IntegrityError:
            form.add_error('username', '用户名已被使用，请换一个用户名。')
        except OperationalError:
            form.add_error(None, '后台忙，未确认注册成功，请稍后重试。')
        else:
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, '账号已创建。你可以通过邀请码加入小组，也可以创建自己的编码项目。')
            return redirect('home')
    return render(request, 'registration/register.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def join_group(request):
    form = JoinGroupForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            project, state = request_membership(request.user, form.cleaned_data['invitation_code'],
                                                form.cleaned_data['note'])
        except ValueError as exc:
            form.add_error('invitation_code', str(exc))
        except OperationalError:
            form.add_error(None, '后台忙，未确认申请成功，请稍后重试。')
        else:
            if state == 'member':
                messages.info(request, '你已经是这个项目小组的成员。')
                return redirect('project', project.id)
            messages.success(request, '你已申请加入“' + project.name + '”，请等待负责人审核。')
            return redirect('home')
    return render(request, 'coding/join_group.html', {'form': form})


def _message_error(request, error):
    messages.error(request, '后台忙，操作没有确认完成，请稍后重试。' if isinstance(error, OperationalError) else str(error))


@require_POST
@login_required
def invitation_manage(request, project_id):
    from .views import access
    project, role = access(request, project_id, ['owner'])
    form = InvitationForm(request.POST)
    action = request.POST.get('action')
    if not form.is_valid() or action not in ('issue', 'revoke'):
        return HttpResponse('请填写有效的邀请期限和操作版本。', status=400)
    try:
        if action == 'issue':
            invitation, code = issue_invitation(project, request.user, form.cleaned_data['revision'],
                                                 form.cleaned_data['days'])
            request.session['new_invitation_' + str(project.id)] = {'code': code, 'revision': invitation.revision}
            messages.success(request, '新邀请码已生成，原邀请码立即失效；已提交的申请仍可审核。')
        else:
            revoke_invitation(project, request.user, form.cleaned_data['revision'])
            request.session.pop('new_invitation_' + str(project.id), None)
            messages.success(request, '邀请码已停用。已加入成员不受影响；已提交的申请仍可审核。')
    except (ValueError, OperationalError) as exc:
        _message_error(request, exc)
    return redirect('members', project.id)


@require_POST
@login_required
def join_review(request, project_id, application_id):
    from .views import access
    project, role = access(request, project_id, ['owner'])
    get_object_or_404(ProjectJoinRequest, pk=application_id, project=project)
    form = JoinReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, '审核内容不完整或权限不正确，请刷新后重新选择。')
        return redirect('members', project.id)
    data = form.cleaned_data
    try:
        review_membership(project, request.user, application_id, data['revision'],
                          data['action'], data['role'], data['decision_note'])
        messages.success(request, '申请已通过，成员可进入项目；编码任务仍需在安排任务时分配。' if data['action'] == 'approve' else '申请已拒绝。')
    except (ValueError, OperationalError) as exc:
        _message_error(request, exc)
    return redirect('members', project.id)


@require_POST
@login_required
def join_cancel(request, application_id):
    get_object_or_404(ProjectJoinRequest, pk=application_id, user=request.user)
    try:
        raw_revision = request.POST.get('revision', '')
        if not raw_revision.isascii() or not raw_revision.isdigit() or len(raw_revision) > 18:
            raise ValueError('申请版本不正确，请刷新后重试。')
        revision = int(raw_revision)
        cancel_membership_request(request.user, application_id, revision)
        messages.success(request, '申请已撤回，没有退出已加入的项目。')
    except (ValueError, OperationalError) as exc:
        _message_error(request, exc)
    return redirect('home')
