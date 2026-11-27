"""Project recycle bin: reversible soft-trash, restoration, and safe cascade purge."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (AIRecommendation, Assignment, AuditEvent, Code, Codebook, CodebookImportDraft,
                     Comment, Decision, Membership, Project, ProjectDirectInvitation,
                     ProjectInvitation, ProjectJoinRequest, ReliabilityReport, Round,
                     SourceDocument, SourceRecord, Unit, UploadDraft)
from .services import audit
from .source_models import SourceAnnotation, SourceCodingTask


def _lock_owned_project(project_id, actor):
    membership = Membership.objects.filter(project_id=project_id, user=actor).first()
    if not membership:
        raise Http404
    if membership.role != 'owner':
        raise PermissionDenied('只有项目负责人可以移入回收站、恢复或彻底删除项目。')
    project = Project.objects.select_for_update().get(pk=project_id)
    if not Membership.objects.filter(project=project, user=actor, role='owner').exists():
        raise PermissionDenied('只有项目负责人可以管理项目回收站。')
    return project


@transaction.atomic
def trash_project(project_id, actor):
    project = _lock_owned_project(project_id, actor)
    if project.trashed_at is None:
        project.trashed_at = timezone.now()
        project.revision += 1
        project.save(update_fields=['trashed_at', 'revision'])
        audit(project, actor, 'project.trash', project, after={'trashed_at': project.trashed_at.isoformat()})
    return project


@transaction.atomic
def restore_project(project_id, actor):
    project = _lock_owned_project(project_id, actor)
    if project.trashed_at is not None:
        project.trashed_at = None
        project.revision += 1
        project.save(update_fields=['trashed_at', 'revision'])
        audit(project, actor, 'project.restore', project)
    return project


def _delete_project_tree(project):
    SourceAnnotation.objects.filter(task__round__project=project).delete()
    SourceAnnotation.objects.filter(task__document__project=project).delete()
    SourceCodingTask.objects.filter(round__project=project).delete()
    SourceCodingTask.objects.filter(document__project=project).delete()
    AIRecommendation.objects.filter(assignment__round__project=project).delete()
    Comment.objects.filter(round__project=project).delete()
    Decision.objects.filter(round__project=project).delete()
    ReliabilityReport.objects.filter(round__project=project).delete()
    Assignment.objects.filter(round__project=project).update(parent=None)
    Assignment.objects.filter(round__project=project).delete()
    Round.objects.filter(project=project).delete()
    Unit.objects.filter(record__document__project=project).delete()
    SourceRecord.objects.filter(document__project=project).delete()
    SourceDocument.objects.filter(project=project).delete()
    UploadDraft.objects.filter(project=project).delete()
    CodebookImportDraft.objects.filter(book__project=project).delete()
    Code.objects.filter(book__project=project).update(parent=None)
    Code.objects.filter(book__project=project).delete()
    Codebook.objects.filter(project=project).delete()
    ProjectDirectInvitation.objects.filter(project=project).delete()
    ProjectJoinRequest.objects.filter(project=project).delete()
    ProjectInvitation.objects.filter(project=project).delete()
    AuditEvent.objects.filter(project=project).delete()
    Membership.objects.filter(project=project).delete()
    project.delete()


@transaction.atomic
def purge_project(project_id, actor):
    project = _lock_owned_project(project_id, actor)
    if project.trashed_at is None:
        raise ValueError('请先将项目移入回收站，核对无误后再彻底删除。')
    name = project.name
    _delete_project_tree(project)
    return name


@transaction.atomic
def empty_user_trash(actor):
    ids = list(Project.objects.filter(memberships__user=actor, memberships__role='owner',
                                      trashed_at__isnull=False).values_list('pk', flat=True))
    count = 0
    for pid in ids:
        project = Project.objects.select_for_update().filter(pk=pid, trashed_at__isnull=False).first()
        if project and Membership.objects.filter(project=project, user=actor, role='owner').exists():
            _delete_project_tree(project)
            count += 1
    return count


@require_GET
@login_required
def trash_list(request):
    projects = list(Project.objects.filter(memberships__user=request.user, memberships__role='owner',
                                           trashed_at__isnull=False)
                    .annotate(member_count=Count('memberships', distinct=True),
                              doc_count=Count('documents', distinct=True),
                              round_count=Count('rounds', distinct=True))
                    .order_by('-trashed_at', '-id'))
    return render(request, 'coding/projects_trash.html', {'trashed_projects': projects})


@require_POST
@login_required
def project_trash_view(request, project_id):
    try:
        project = trash_project(project_id, request.user)
        messages.success(request, f'已将「{project.name}」移入回收站，项目数据完整保留，可随时在回收站恢复。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return redirect('projects')


@require_POST
@login_required
def project_restore_view(request, project_id):
    try:
        project = restore_project(project_id, request.user)
        messages.success(request, f'项目「{project.name}」已恢复到“我的项目”。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    remaining = Project.objects.filter(memberships__user=request.user, memberships__role='owner',
                                       trashed_at__isnull=False).exists()
    return redirect('projects_trash' if remaining else 'projects')


@require_POST
@login_required
def project_purge_view(request, project_id):
    try:
        name = purge_project(project_id, request.user)
        messages.success(request, f'已彻底删除项目「{name}」。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return redirect('projects_trash')


@require_POST
@login_required
def trash_empty_view(request):
    try:
        count = empty_user_trash(request.user)
        messages.success(request, f'已清空回收站（共彻底清理 {count} 个项目）。' if count else '回收站已为空。')
    except OperationalError:
        messages.error(request, '数据库忙，请稍后重试。')
    return redirect('projects_trash')
