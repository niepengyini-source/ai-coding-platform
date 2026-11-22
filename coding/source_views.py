"""HTTP surface for optional original-source coding; GET requests do not create work."""
import json
from bisect import bisect_right
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import Count
from django.db.models.functions import Substr
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .document_reading import reading_blocks
from .exporting import ExportTable, export_response
from .models import AuditEvent, Codebook, Membership, Round, SourceDocument
from .source_models import SourceAnnotation, SourceCodingTask
from .source_coding import (add_workspace_code, canonical_source, code_data, coding_documents, finish_source_coding,
    progress_for, save_source_annotation, set_source_completed, source_annotation_data, start_source_coding, task_data)
from .views import access, api_error, body_json, round_access


def _task(request, task_id):
    task = get_object_or_404(SourceCodingTask.objects.select_related('round__book', 'round__project', 'document'),
                             pk=task_id, coder=request.user)
    access(request, task.round.project_id)
    if not task.round.sampling.get('source_coding'):
        raise Http404
    return task


def _overview(task, blocks):
    starts = [block['start'] for block in blocks]
    rows = []
    annotations = task.annotations.annotate(_preview=Substr('text', 1, 240)).defer('text', 'note')
    for annotation in annotations:
        page = max(0, bisect_right(starts, annotation.start) - 1) // 30 + 1
        data = source_annotation_data(annotation, overview=True)
        data['url'] = reverse('workbench', args=[task.round_id]) + '?' + urlencode(
            {'task': task.pk, 'reader_page': page, 'annotation': annotation.pk})
        rows.append(data)
    return rows


@login_required
@require_http_methods(['GET', 'POST'])
def start(request, project_id):
    project, role = access(request, project_id, ['owner', 'reviewer', 'coder'])
    book = project.codebooks.first()
    documents = coding_documents(project)
    document_id = request.POST.get('document_id') or request.GET.get('document')
    selected = None
    if document_id:
        selected = get_object_or_404(documents, pk=document_id)
        documents = documents.filter(pk=selected.pk)
    error = None
    if request.method == 'POST':
        try:
            if not book:
                raise ValueError('项目缺少初始编码本，请联系平台维护者恢复。')
            round_obj, created = start_source_coding(project, request.user, book.pk,
                [selected.pk] if selected else None)
            task = round_obj.source_tasks.filter(coder=request.user, document=selected).first() if selected else None
            destination = reverse('workbench', args=[round_obj.pk])
            return redirect(destination + (f'?task={task.pk}' if task else ''))
        except (ValueError, OperationalError) as exc:
            error = '数据库忙，请稍后重试；本次尚未确认开始。' if isinstance(exc, OperationalError) else str(exc)
    resume = Round.objects.filter(project=project, book=book, started_by=request.user, kind='quick',
                                  state='active', sampling__source_coding=True).order_by('-id').first()
    book_ready = bool(book and book.codes.exists())
    prepare_codebook = selected and book and not book_ready and not resume and role in ('owner', 'reviewer')
    return render(request, 'coding/source_start.html', {'project': project, 'role': role, 'book': book,
        'documents': documents, 'document_id': selected.pk if selected else '', 'error': error,
        'resume_round': resume, 'source_preview': selected, 'book_ready': book_ready,
        'prepare_codebook': prepare_codebook})


@login_required
@require_GET
def workbench(request, round_id):
    round_obj, role = round_access(request, round_id)
    if not round_obj.sampling.get('source_coding'):
        raise Http404
    tasks = round_obj.source_tasks.filter(coder=request.user).select_related('document').annotate(annotation_count=Count('annotations'))
    if request.GET.get('task'):
        task = get_object_or_404(tasks, pk=request.GET['task'])
    else:
        task = tasks.filter(completed=False).first() or tasks.first()
    if not task:
        raise Http404('此批次没有分配给你的材料。')
    text, original_blocks = canonical_source(task.document)
    blocks = reading_blocks(original_blocks)
    selected = None
    annotation_id = request.GET.get('annotation')
    if annotation_id:
        selected = get_object_or_404(task.annotations, pk=annotation_id)
    starts = [block['start'] for block in blocks]
    focus_page = (max(0, bisect_right(starts, selected.start) - 1) // 30 + 1) if selected else 1
    page = Paginator(blocks, 30).get_page(request.GET.get('reader_page') or focus_page)
    visible = list(page.object_list)
    def page_url(number):
        return reverse('workbench', args=[round_obj.pk]) + '?' + urlencode({'task': task.pk, 'reader_page': number})
    urls = {'annotations': reverse('source_annotations', args=[task.pk]),
        'complete': reverse('source_complete', args=[task.pk]),
        'finish': reverse('source_finish', args=[round_obj.pk]),
        'export': reverse('source_export', args=[round_obj.pk]) + '?format=xlsx',
        'history': reverse('source_history', args=[1]).replace('1/history/', '')}
    source_data = {'task': task_data(task), 'round': {'id': round_obj.pk, 'state': round_obj.state, 'revision': round_obj.revision},
        'book': {'id': round_obj.book_id, 'version': round_obj.book.version, 'policy': round_obj.book.policy,
                 'revision': round_obj.book.revision},
        'text': text[visible[0]['start']:visible[-1]['end']] if visible else '',
        'start': visible[0]['start'] if visible else 0, 'blocks': visible,
        'annotations': _overview(task, blocks), 'codes': [code_data(code) for code in round_obj.book.codes.all()],
        'progress': progress_for(round_obj, request.user),
        'selected_annotation_id': selected.pk if selected else None,
        'can_add_codes': role in ('owner', 'reviewer') and round_obj.state == 'active' and not round_obj.book.frozen,
        'urls': urls}
    return render(request, 'coding/source_workbench.html', {'project': round_obj.project, 'role': role,
        'round': round_obj, 'book': round_obj.book, 'source_task': task, 'source_tasks': tasks,
        'blocks': visible, 'reader_page': page, 'reading_document': task.document, 'source_data': source_data,
        'previous_reader_url': page_url(page.previous_page_number()) if page.has_previous() else '',
        'next_reader_url': page_url(page.next_page_number()) if page.has_next() else ''})


@login_required
@require_http_methods(['GET', 'POST'])
def annotations(request, task_id):
    task = _task(request, task_id)
    if request.method == 'GET':
        if request.GET.get('id'):
            annotation = get_object_or_404(task.annotations, pk=request.GET['id'])
            return JsonResponse({'annotation': source_annotation_data(annotation), 'task': task_data(task)})
        _, blocks = canonical_source(task.document)
        return JsonResponse({'annotations': _overview(task, reading_blocks(blocks)), 'task': task_data(task),
                             'progress': progress_for(task.round, request.user)})
    try:
        data = body_json(request)
        annotation = save_source_annotation(task.pk, request.user, data)
        task.refresh_from_db()
        result = {'annotation': source_annotation_data(annotation), 'task': task_data(task),
                  'progress': progress_for(task.round, request.user)}
        if data.get('new_code') is not None:
            book = Codebook.objects.get(pk=annotation.book_snapshot['id'])
            result.update(codes=[code_data(code) for code in book.codes.all()], book_revision=book.revision)
        return JsonResponse(result)
    except (ValueError, OperationalError) as error:
        return api_error(error)


@login_required
@require_POST
def complete(request, task_id):
    task = _task(request, task_id)
    try:
        task = set_source_completed(task.pk, request.user, body_json(request))
        return JsonResponse({'task': task_data(task), 'progress': progress_for(task.round, request.user)})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@login_required
@require_POST
def codes(request, round_id):
    round_obj, _ = round_access(request, round_id, ['owner', 'reviewer'])
    try:
        return JsonResponse(add_workspace_code(round_obj.pk, request.user, body_json(request)))
    except (ValueError, OperationalError) as error:
        return api_error(error)


@login_required
@require_POST
def finish(request, round_id):
    round_obj, _ = round_access(request, round_id)
    try:
        result = finish_source_coding(round_obj.pk, request.user, body_json(request).get('revision'))
        return JsonResponse({'round': {'id': result.pk, 'state': result.state, 'revision': result.revision},
                             'next_url': reverse('workbench', args=[result.pk])})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@login_required
@require_GET
def history(request, annotation_id):
    annotation = get_object_or_404(SourceAnnotation.objects.select_related('task__round'), pk=annotation_id,
                                  task__coder=request.user)
    access(request, annotation.task.round.project_id)
    events = AuditEvent.objects.filter(project=annotation.task.round.project, object_type='SourceAnnotation',
        object_id=str(annotation.pk), action='source.annotation.save').select_related('actor').order_by('-id')[:50]
    return JsonResponse({'events': [{'created_at': event.created_at.isoformat(), 'actor': event.actor.username,
        'before': event.before, 'after': event.after, 'reason': event.reason} for event in events]})


@login_required
@require_GET
def export(request, round_id):
    round_obj, _ = round_access(request, round_id)
    if not round_obj.sampling.get('source_coding'):
        raise Http404
    file_format = request.GET.get('format', 'xlsx')
    if file_format not in ('xlsx', 'csv', 'json', 'tsv', 'docx'):
        return JsonResponse({'error': '请选择Excel、CSV、TSV、JSON或Word格式。'}, status=400)
    entries = SourceAnnotation.objects.filter(task__round=round_obj, task__coder=request.user)
    if file_format == 'docx':
        summaries = {}
        snapshots = entries.order_by('-updated_at', '-pk').values_list('task__document_id', 'book_snapshot')
        for document_id, snapshot in snapshots.iterator():
            for code in snapshot.get('codes', []):
                summary = summaries.setdefault(code['id'], {'code': code, 'sources': set(), 'references': 0})
                summary['sources'].add(document_id)
                summary['references'] += 1
        headers = ['名称', '描述', '来源数', '引用数']
        rows = [[summary['code']['name'], summary['code']['definition'], len(summary['sources']), summary['references']]
                for summary in sorted(summaries.values(), key=lambda item: item['code']['key'])]
        table = ExportTable(headers, rows, '编码汇总')
        try:
            return export_response(table, headers, headers, file_format, '我的原文编码汇总',
                                   word_table_widths=(2280, 4380, 1170, 1170))
        except ValueError as error:
            return JsonResponse({'error': str(error)}, status=400)

    headers = ['原文件', '编码片段', '阅读文本起点', '阅读文本终点', 'PDF页码', '原记录行号',
               '主代码', '次代码', '并列代码', '复核标记', '编码备注', '编码员', '编码本版本', '保存版本', '保存时间', '材料已完成']
    entries = entries.select_related('task__document', 'task__coder').defer('task__document__raw')
    has_regions = any(entry.region is not None for entry in entries)
    if has_regions:
        headers.append('图像区域')
    rows, sources = [], {}
    for entry in entries:
        document = entry.task.document
        blocks, start, end = [], '', ''
        if entry.region is None:
            if document.pk not in sources:
                sources[document.pk] = canonical_source(document)[1]
            start, end = entry.start, entry.end
            blocks = [block for block in sources[document.pk] if block['end'] > start and block['start'] < end]
        snapshot = entry.book_snapshot
        keys = {code['id']: code['key'] for code in snapshot.get('codes', [])}
        row = [document.name, entry.text, start, end,
            ','.join(dict.fromkeys(str(block['page']) for block in blocks if block.get('page'))),
            ','.join(dict.fromkeys(str(block['row']) for block in blocks if block.get('row'))),
            keys.get(entry.primary_id, ''), '|'.join(keys.get(pk, '') for pk in entry.secondary),
            '|'.join(keys.get(pk, '') for pk in entry.labels), '需复核' if entry.uncertain else '已确认', entry.note,
            entry.task.coder.username, snapshot.get('version', ''), entry.revision,
            entry.updated_at.isoformat(), entry.task.completed]
        if has_regions:
            if file_format == 'json':
                row.append(entry.region)
            else:
                row.append(json.dumps(entry.region, separators=(',', ':')) if entry.region is not None else '')
        rows.append(row)
    table = ExportTable(headers, rows, round_obj.name)
    try:
        return export_response(table, headers, headers, file_format, '我的原文编码')
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
