"""Authenticated access to retained source files and per-material display choice."""
import hashlib
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST

from .models import SourceDocument
from .services import audit
from .views import access


@login_required
@require_GET
def original(request, project_id, document_id, preview=False):
    project, _ = access(request, project_id)
    document = get_object_or_404(SourceDocument, pk=document_id, project=project)
    raw = bytes(document.raw)
    if hashlib.sha256(raw).hexdigest() != document.sha256:
        return HttpResponse('原件校验未通过，请联系负责人核对备份。', status=409)
    content_type = 'application/octet-stream'
    if preview:
        extension = Path(document.name).suffix.lower()
        if extension == '.pdf' and raw.startswith(b'%PDF-'):
            content_type = 'application/pdf'
        elif extension == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n'):
            content_type = 'image/png'
        elif extension in ('.jpg', '.jpeg') and raw.startswith(b'\xff\xd8\xff'):
            content_type = 'image/jpeg'
        else:
            raise Http404
    response = HttpResponse(raw, content_type=content_type)
    response['Content-Disposition'] = content_disposition_header(not preview, document.name)
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def display_mode(request, project_id, document_id):
    project, _ = access(request, project_id, ['owner', 'reviewer'])
    mode = request.POST.get('workbench_mode')
    if mode not in ('auto', 'unit', 'document'):
        return HttpResponse('请选择有效的阅读模式。', status=400)
    with transaction.atomic():
        document = get_object_or_404(SourceDocument.objects.select_for_update(), pk=document_id, project=project)
        if request.POST.get('revision') != str(document.unit_revision):
            messages.error(request, '材料设置已更新，请刷新后重试。')
        else:
            before = document.workbench_mode
            document.workbench_mode = mode
            document.unit_revision += 1
            document.save(update_fields=['workbench_mode', 'unit_revision'])
            audit(project, request.user, 'source.display_mode', document,
                  before={'mode': before}, after={'mode': mode})
            messages.success(request, '阅读模式已保存，原有编码和任务保持不变。')
    return redirect('materials', project.pk)
