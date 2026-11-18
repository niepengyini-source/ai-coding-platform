"""Small optional workbench tools; existing task entry needs no new settings."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .models import Assignment, Codebook, Decision, Project, SourceAnnotation
from .services import audit, clone_book
from .views import access


def code_is_used(code):
    for model, lookup in ((Assignment, 'round__book'), (Decision, 'round__book'),
                          (SourceAnnotation, 'task__round__book')):
        if model.objects.filter(primary=code).exists():
            return True
        rows = model.objects.filter(**{lookup: code.book_id}).values_list('secondary', 'labels')
        if any(code.pk in secondary or code.pk in labels for secondary, labels in rows):
            return True
    return False


@login_required
@require_POST
def book_policy(request, project_id, book_id):
    project, _ = access(request, project_id, ['owner', 'reviewer'])
    policy = request.POST.get('policy')
    if policy not in dict(Codebook._meta.get_field('policy').choices):
        return HttpResponse('请选择有效的标签规则。', status=400)
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        book = get_object_or_404(Codebook.objects.select_for_update(), pk=book_id, project=project)
        if book.pk != project.codebooks.first().pk or request.POST.get('revision') != str(book.revision):
            messages.error(request, '编码本已变化，请打开当前版本后重试。')
        elif policy != book.policy:
            used = (book.frozen or Assignment.objects.filter(round__book=book, revision__gt=0).exists()
                    or SourceAnnotation.objects.filter(task__round__book=book).exists())
            before = book.policy
            if used:
                book = clone_book(project, request.user, book, book.title, policy, '调整标签方式；已有工作保留原规则')
            else:
                book.policy = policy
                book.revision += 1
                book.save(update_fields=['policy', 'revision'])
            audit(project, request.user, 'codebook.policy', book, before={'policy': before}, after={'policy': policy})
            messages.success(request, '标签方式已保存。已有任务不自动转换，新工作使用当前规则。')
    return redirect('codebook', project.pk, book.pk)
