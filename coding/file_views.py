"""Import confirmation and export UI; the original CSV download URLs remain supported."""
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from .models import Codebook, CodebookImportDraft, Project, Round
from .views import access, review_access
from .codebook_files import (FIELDS, MAX_BYTES, parse_codebook, normalized_codes, draft_available,
                             apply_codebook_import, suggest_mapping, mapping_candidates, LOOKUP, normalize_header)
from .file_forms import CodebookMappingForm, ExportOptionsForm
from .exporting import annotation_table, final_table, disagreement_table, unit_table, book_table, export_response, MAX_EXPORT_ROWS
from .workflow import visible_rounds


def draft_for(request, book, draft_id):
    try:
        draft_id = uuid.UUID(str(draft_id))
    except (ValueError, TypeError, AttributeError):
        raise Http404
    return get_object_or_404(CodebookImportDraft, pk=draft_id, book=book, user=request.user)


def import_initial(book, filename):
    return {'title': Path(filename).stem[:120], 'policy': book.policy,
            'mode': 'append' if not book.frozen and not book.codes.exists() else 'new',
            'change_note': '从文件导入编码规则：' + filename}


def import_preview(book, form, rows, sheet, warnings, automatic=False):
    settings = {k: form.cleaned_data[k] for k in ('mode', 'title', 'policy', 'change_note')}
    mapping = form.mapping()
    existing = list(book.codes.select_related('parent')) if settings['mode'] == 'append' else []
    codes = normalized_codes(rows, mapping, existing)
    warnings = list(warnings)
    if not mapping['name']:
        warnings.append('文件未选择单独的名称列，显示名称沿用“' + mapping['key']
                        + '”列原文；没有用定义或例子代替名称。需要时可在未冻结的草稿中补充名称。')
    labels = {field: label for field, label, _ in FIELDS}
    for field, source in mapping.items():
        expected = LOOKUP.get(normalize_header(source)) if source else None
        if expected and expected != field:
            warnings.append('请核对：你把“' + source + '”列用作“' + labels[field]
                            + '”，但这个表头通常表示“' + labels[expected] + '”。请以原文件实际内容为准。')
    return {'codes': codes, 'settings': settings, 'mapping': mapping, 'sheet': sheet,
            'revision': book.revision, 'warnings': warnings, 'automatic': automatic,
            'name_from_key': not bool(mapping['name'])}


def save_import_preview(draft, user, book, preview):
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=book.project_id)
        locked_draft = CodebookImportDraft.objects.select_for_update().get(pk=draft.pk, user=user, book=book)
        if locked_draft.applied_book_id:
            return locked_draft.applied_book_id
        locked_draft.preview = preview
        locked_draft.save(update_fields=['preview'])
    return None


@login_required
def book_import(request, project_id, book_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    book = get_object_or_404(Codebook, project=project, pk=book_id)
    context = {'project': project, 'role': role, 'book': book}
    draft = None
    action = request.POST.get('action', '') if request.method == 'POST' else ''
    try:
        draft_id = request.POST.get('draft_id') or request.GET.get('draft')
        if draft_id:
            draft = draft_for(request, book, draft_id)
            context['draft'] = draft
            if draft.applied_book_id:
                messages.info(request, '这次文件已经导入，未重复添加代码。')
                return redirect('codebook', project.id, draft.applied_book_id)
            draft_available(draft)
        if action == 'confirm':
            if not draft:
                raise ValueError('请先上传并预览编码本。')
            if request.POST.get('confirmed') != 'yes':
                raise ValueError('请勾选确认已核对代码定义和层级。')
            target, created = apply_codebook_import(draft.pk, request.user, book)
            messages.success(request, f'导入完成：v{target.version} · {target.title}。代码可用于新建编码任务；旧任务规则不改变。')
            return redirect('codebook', project.id, target.id)
        if action == 'upload':
            upload = request.FILES.get('file')
            if not upload or upload.size > MAX_BYTES:
                raise ValueError('请选择文件，单个文件最大8MB。')
            raw = upload.read()
            # Validate before storing an upload. No external network or AI is used.
            headers, rows, sheets, selected_sheet, warnings = parse_codebook(upload.name, raw)
            draft = CodebookImportDraft.objects.create(book=book, user=request.user,
                    filename=Path(upload.name).name[:200], raw=raw)
            url = reverse('book_import', args=[project.id, book.id])
            # New UI requests the short path. Older upload clients can still open
            # the mapping page. Preview remains a CSRF-protected POST operation.
            if request.POST.get('auto_preview') == 'yes':
                initial = import_initial(book, draft.filename)
                data = dict(initial, **{'map_' + k: v for k, v in suggest_mapping(headers).items()})
                form = CodebookMappingForm(data, book=book, headers=headers, initial=initial)
                ambiguous = any(len(values) > 1 for values in mapping_candidates(headers).values())
                if not ambiguous and form.is_valid():
                    try:
                        preview = import_preview(book, form, rows, selected_sheet, warnings, automatic=True)
                        applied_book_id = save_import_preview(draft, request.user, book, preview)
                        if applied_book_id:
                            return redirect('codebook', project.id, applied_book_id)
                        return redirect(url + '?' + urlencode({'draft': str(draft.pk), 'stage': 'preview'}))
                    except ValueError as error:
                        messages.warning(request, '文件已保留，但还不能直接导入：' + str(error))
                        return redirect(url + '?' + urlencode({'draft': str(draft.pk), 'advanced': '1'}))
                messages.warning(request, '有些列名未明确识别，或多个来源列含义相同。请核对下方对应项，再生成预览；尚未写入编码本。')
            return redirect(url + '?' + urlencode({'draft': str(draft.id)}))
        if not draft:
            return render(request, 'coding/book_import.html', context)
        if request.GET.get('stage') == 'preview' and draft.preview:
            context.update(preview=draft.preview, page=Paginator(draft.preview['codes'], 20).get_page(request.GET.get('page')))
            return render(request, 'coding/book_import_preview.html', context)
        sheet = request.POST.get('sheet', '') or draft.preview.get('sheet', '')
        headers, rows, sheets, selected_sheet, warnings = parse_codebook(draft.filename, bytes(draft.raw), sheet)
        initial = import_initial(book, draft.filename)
        if draft.preview:
            initial.update(draft.preview['settings'])
            initial.update({'map_' + k: v for k, v in draft.preview['mapping'].items() if not v or v in headers})
        form = CodebookMappingForm(request.POST if action == 'preview' else None, book=book, headers=headers, initial=initial)
        candidates = mapping_candidates(headers)
        ambiguous_fields = [{'label': label, 'columns': candidates[field]}
                            for field, label, _ in FIELDS if len(candidates[field]) > 1]
        context.update(mapping_form=form, headers=headers, source_preview=[[r.get(h, '')[:160] for h in headers] for r in rows[:5]],
                       row_count=len(rows), sheets=sheets, selected_sheet=selected_sheet, warnings=warnings,
                       ambiguous_fields=ambiguous_fields,
                       show_advanced=bool(ambiguous_fields or request.GET.get('advanced') == '1'
                                          or any(key not in ('map_key', 'map_definition') for key in form.errors)))
        if action == 'preview' and form.is_valid():
            preview = import_preview(book, form, rows, selected_sheet, warnings)
            applied_book_id = save_import_preview(draft, request.user, book, preview)
            if applied_book_id:
                return redirect('codebook', project.id, applied_book_id)
            url = reverse('book_import', args=[project.id, book.id])
            return redirect(url + '?' + urlencode({'draft': str(draft.pk), 'stage': 'preview'}))
    except (ValueError, TypeError, OperationalError) as error:
        context['error'] = str(error) if not isinstance(error, OperationalError) else '数据库正在忙，未确认导入成功。请稍后重试。'
        if draft and draft.preview and action == 'confirm':
            context.update(preview=draft.preview, page=Paginator(draft.preview['codes'], 20).get_page(request.GET.get('page')))
            return render(request, 'coding/book_import_preview.html', context)
    return render(request, 'coding/book_import.html', context)


@login_required
def book_template(request, project_id, book_id, file_format):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    book = get_object_or_404(Codebook, project=project, pk=book_id)
    if file_format not in ('xlsx', 'csv', 'docx', 'txt', 'json'):
        raise Http404
    from .exporting import ExportTable
    fields = [f for f, _, _ in FIELDS]
    row = ['EXAMPLE', '示例代码（请替换）', '用自己的研究定义替换这一行', '', '什么情况下使用',
           '什么情况下不使用', '适用的虚构例子', '不适用的虚构例子', '容易混淆的情况', '主次和共现规则', '#4372e8']
    table = ExportTable(fields, [row], '编码本导入模板', book.policy)
    if file_format == 'txt':
        from django.http import HttpResponse
        from django.utils.http import content_disposition_header
        response = HttpResponse('\n'.join(f'{k}: {v}' for k, v in zip(fields, row)).encode('utf-8'), content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = content_disposition_header(True, '编码本导入模板.txt')
        response['Cache-Control'] = 'no-store'
        return response
    return export_response(table, fields, fields, file_format, '编码本导入模板', is_book=True)


TARGETS = [('codebook', '编码本'), ('units', '材料与编码单元'), ('mine', '我的独立编码'),
           ('all', '全员独立编码'), ('disagreements', '分歧与疑点清单'), ('final', '最终定稿数据')]


def export_table_for(request, project, role, params):
    target = params.get('target', 'codebook')
    if target == 'codebook':
        book_id = params.get('book')
        book = get_object_or_404(Codebook, project=project, pk=book_id) if book_id and book_id.isdecimal() else project.codebooks.first()
        if book_id and not book_id.isdecimal() or not book:
            raise Http404
        return book_table(book), book, None
    if target == 'units':
        return unit_table(project, params.get('include_inactive') == 'yes'), None, None
    if target not in dict(TARGETS):
        raise Http404
    round_id = params.get('round', '')
    if not round_id.isdecimal():
        raise ValueError('请先选择一个编码轮次，再显示导出选项。')
    r = get_object_or_404(Round.objects.select_related('book', 'project'), project=project, pk=round_id)
    if r.kind == 'quick' and r.started_by_id != request.user.pk and role not in ('owner', 'reviewer'):
        raise Http404
    if target == 'mine':
        if r.assignments.filter(coder=request.user).count() > MAX_EXPORT_ROWS:
            raise ValueError('一次最多导出50000行，请按轮次分批导出。')
        return annotation_table(r, r.assignments.filter(coder=request.user)), None, r
    if target in ('all', 'disagreements'):
        review_access(request, r, role)
        if r.assignments.count() > MAX_EXPORT_ROWS:
            raise ValueError('本轮独立判断超过50000行，请选择“我的独立编码”分别导出，或按更小的轮次分批。')
        return (annotation_table(r, r.assignments.all()) if target == 'all' else disagreement_table(r)), None, r
    if r.state != 'closed' or r.kind == 'quick':
        raise PermissionDenied('最终定稿需先完成协商并归档。')
    if r.decisions.filter(cycle=r.cycle).count() > MAX_EXPORT_ROWS:
        raise ValueError('一次最多导出50000行，请按轮次分批导出。')
    return final_table(r), None, r


@login_required
def exports(request, project_id):
    project, role = access(request, project_id)
    params = request.POST if request.method == 'POST' else request.GET
    context = {'project': project, 'role': role, 'targets': TARGETS, 'books': project.codebooks.all(),
               'rounds': visible_rounds(project, request.user, role).order_by('-id'), 'target': params.get('target', 'codebook'),
               'selected_round': params.get('round', ''), 'selected_book': params.get('book', ''),
               'include_inactive': params.get('include_inactive') == 'yes'}
    try:
        table, book, r = export_table_for(request, project, role, params)
        context.update(book=book, round=r, table=table, row_count=len(table.rows))
        form = ExportOptionsForm(request.POST if request.method == 'POST' else None, table=table, is_book=bool(book),
                initial={'format': 'xlsx', 'filename': f'{project.name}_{table.title or context["target"]}'[:100],
                         'encoding': 'utf-8-sig', 'delimiter': ',', 'fields': table.headers})
        if request.method == 'POST' and form.is_valid():
            values = form.cleaned_data
            fields, labels = [p[1] for p in form.selection], [p[2] for p in form.selection]
            return export_response(table, fields, labels, values['format'], values['filename'], encoding=values['encoding'],
                                   delimiter=values['delimiter'], is_book=bool(book))
        context.update(export_form=form, field_rows=form.field_rows(), sample=table.rows[:3], headers=table.headers)
    except (ValueError, TypeError, OperationalError) as error:
        context['error'] = str(error) if not isinstance(error, OperationalError) else '数据库正在忙，请稍后重试导出。'
    return render(request, 'coding/exports.html', context)
