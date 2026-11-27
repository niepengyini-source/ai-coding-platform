"""Select project units, review a signed plan, then explicitly create its tasks."""
import hashlib
import json
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models.fields.json import KeyTextTransform
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .models import Membership, Project, Round, Unit
from .round_forms import RoundForm
from .selection import select_round_units
from .services import create_round
from .source_coding import coding_documents
from .source_rounds import DocumentRoundForm, create_document_round
from .views import access


PLAN_SALT = 'coding.task-arrangement.v1'
MAX_SELECTION = 10000


def pool_snapshot(project):
    ids = list(Unit.objects.filter(record__document__project=project, active=True, record__document__units_confirmed=True).values_list('pk', flat=True))
    digest = hashlib.sha256(','.join(map(str, ids)).encode()).hexdigest()
    return ids, digest


def metadata_fields(project):
    fields = set()
    for row in Unit.objects.filter(record__document__project=project, active=True, record__document__units_confirmed=True).values_list('record__metadata', flat=True)[:500]:
        fields.update(row)
    return sorted(fields)


def form_data(arguments):
    values = dict(arguments)
    values['unit_ids'] = ','.join(map(str, values.get('unit_ids') or []))
    values['start_position'] = values.get('start_position') or ''
    return values


def form_page(request, project, role, form, error='', *, doc_form=None, task_type=None):
    ids, digest = pool_snapshot(project)
    coding_docs = list(coding_documents(project))
    requested_type = request.GET.get('type')
    if task_type not in ('document', 'unit'):
        if requested_type in ('document', 'unit'):
            task_type = requested_type
        elif not ids and coding_docs:
            task_type = 'document'
        else:
            task_type = 'unit'
    if doc_form is None:
        default_coders = [
            m.user_id for m in project.memberships.exclude(role='viewer').filter(user__is_active=True)
        ]
        doc_form = DocumentRoundForm(project=project, initial={
            'coder_ids': default_coders or [request.user.pk],
            'document_ids': [d.pk for d in coding_docs],
        })
    return render(request, 'coding/round_form.html', {
        'project': project, 'role': role, 'form': form, 'error': error,
        'doc_form': doc_form, 'task_type': task_type,
        'has_coding_documents': bool(coding_docs), 'has_confirmed_units': bool(ids),
        'current_book': project.codebooks.first(), 'unit_index': ids, 'pool_hash': digest,
        'documents': project.documents.all(), 'metadata_fields': metadata_fields(project),
        'unit_choices_url': reverse('round_unit_choices', args=[project.pk]),
    })


def plan_page(request, project, role, arguments, chosen, ids, token, page=1):
    positions = {pk: index for index, pk in enumerate(ids, 1)}
    units = Paginator(chosen, 40).get_page(page)
    for unit in units:
        unit.selection_position = positions[unit.pk]
    counts = {pk: 0 for pk in arguments['coder_ids']}
    double_count = round(len(chosen) * arguments['double_percent'] / 100)
    for index in range(len(chosen)):
        coders = arguments['coder_ids']
        if arguments['distribution'] == 'all':
            selected = coders
        elif arguments['distribution'] == 'mixed' and index < double_count and len(coders) >= 2:
            selected = [coders[index % len(coders)], coders[(index + 1) % len(coders)]]
        else:
            selected = [coders[index % len(coders)]]
        for pk in selected:
            counts[pk] += 1
    members = list(Membership.objects.filter(project=project, user_id__in=counts).select_related('user'))
    for member in members:
        member.task_count = counts[member.user_id]
    return render(request, 'coding/round_preview.html', {
        'project': project, 'role': role, 'plan': arguments, 'preview_token': token,
        'units': units, 'members': members, 'unit_count': len(chosen),
        'book': project.codebooks.get(pk=arguments['book_id']),
        'kind_label': dict(Round._meta.get_field('kind').choices)[arguments['kind']],
        'selection_label': dict(RoundForm.base_fields['selection_mode'].choices)[arguments['selection_mode']],
        'distribution_label': dict(RoundForm.base_fields['distribution'].choices)[arguments['distribution']],
    })


def destination(r, user):
    return redirect('workbench', r.pk) if r.assignments.filter(coder=user, superseded=False).exists() else redirect('progress', r.project_id)


def arrange_round(request, project_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    current = project.codebooks.first()
    initial = {'book_id': current.pk if current else None, 'coder_ids': [request.user.pk]}
    if request.method == 'POST' and request.POST.get('arrangement_mode') == 'source_document':
        doc_form = DocumentRoundForm(request.POST, project=project)
        if doc_form.is_valid():
            try:
                r = create_document_round(project, request.user, doc_form.cleaned_data)
                messages.success(request, f'已分配整篇材料任务「{r.name}」。')
                if r.source_tasks.filter(coder=request.user).exists():
                    return redirect('workbench', r.pk)
                return redirect('progress', project.pk)
            except (ValueError, OperationalError) as error:
                doc_form.add_error(None, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
        return form_page(request, project, role, RoundForm(project=project, initial=initial),
                         doc_form=doc_form, task_type='document')
    action = request.POST.get('action', '')
    if request.method == 'POST' and action in ('confirm', 'edit', 'page'):
        try:
            payload = signing.loads(request.POST.get('preview_token', ''), salt=PLAN_SALT, max_age=1800)
            if payload['project'] != project.pk or payload['actor'] != request.user.pk:
                raise PermissionDenied
            form = RoundForm(form_data(payload['arguments']), project=project, initial=initial)
            if action == 'edit':
                return form_page(request, project, role, form)
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=project.pk)
                access(request, project.pk, ['owner', 'reviewer'])
                existing = project.rounds.filter(sampling__arrangement_token=payload['nonce']).first()
                if action == 'confirm' and existing:
                    return destination(existing, request.user)
                ids, digest = pool_snapshot(project)
                book = project.codebooks.first()
                if digest != payload['pool_hash'] or not book or book.pk != payload['arguments']['book_id'] or book.revision != payload['book_revision']:
                    raise ValueError('材料或编码本已变化，请重新预览任务安排。')
                if not form.is_valid():
                    return form_page(request, project, role, form, '成员或可用材料有变化，请核对后重新预览。')
                arguments = form.cleaned_data
                chosen = select_round_units(project, **{key: arguments[key] for key in (
                    'count', 'seed', 'stratify', 'unit_ids', 'selection_mode', 'start_position')})
                if [unit.pk for unit in chosen] != payload['selected_ids']:
                    raise ValueError('选取结果已变化，请重新预览任务安排。')
                if action == 'confirm':
                    r = create_round(project, request.user, **arguments)
                    positions = {pk: index for index, pk in enumerate(ids, 1)}
                    r.sampling.update(arrangement_token=payload['nonce'], pool_hash=digest,
                                      selection_positions=[positions[unit.pk] for unit in chosen])
                    r.save(update_fields=['sampling'])
                    return destination(r, request.user)
            return plan_page(request, project, role, arguments, chosen, ids,
                             request.POST['preview_token'], request.POST.get('page', 1))
        except signing.BadSignature:
            return form_page(request, project, role, RoundForm(project=project, initial=initial),
                             '预览已过期或无效，请重新核对任务安排。')
        except (KeyError, TypeError):
            return form_page(request, project, role, RoundForm(project=project, initial=initial),
                             '预览内容不完整，请重新预览任务安排。')
        except (ValueError, OperationalError) as error:
            return form_page(request, project, role, form,
                             '数据库忙，请稍后用同一预览重试。' if isinstance(error, OperationalError) else str(error))
    form = RoundForm(request.POST if request.method == 'POST' else None, project=project, initial=initial)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=project.pk)
                access(request, project.pk, ['owner', 'reviewer'])
                form = RoundForm(request.POST, project=project, initial=initial)
                if form.is_valid():
                    # Existing clients keep their original POST behavior. The new UI previews explicitly.
                    if not action:
                        return destination(create_round(project, request.user, **form.cleaned_data), request.user)
                    ids, digest = pool_snapshot(project)
                    if request.POST.get('pool_hash') and request.POST['pool_hash'] != digest:
                        raise ValueError('材料已变化，请刷新页面后重新选择；尚未分配任务。')
                    arguments = form.cleaned_data
                    chosen = select_round_units(project, **{key: arguments[key] for key in (
                        'count', 'seed', 'stratify', 'unit_ids', 'selection_mode', 'start_position')})
                    book = project.codebooks.get(pk=arguments['book_id'])
                    payload = {'project': project.pk, 'actor': request.user.pk, 'nonce': uuid.uuid4().hex,
                               'arguments': arguments, 'selected_ids': [unit.pk for unit in chosen],
                               'pool_hash': digest, 'book_revision': book.revision}
                    token = signing.dumps(payload, salt=PLAN_SALT, compress=True)
                    return plan_page(request, project, role, arguments, chosen, ids, token)
        except (ValueError, OperationalError) as error:
            form.add_error(None, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return form_page(request, project, role, form)


@require_http_methods(['GET', 'POST'])
@login_required
def unit_choices(request, project_id):
    project, _ = access(request, project_id, ['owner', 'reviewer'])
    try:
        params = json.loads(request.body) if request.method == 'POST' else request.GET
        if not isinstance(params, dict) and request.method == 'POST':
            raise ValueError('筛选内容无效。')
        ids, digest = pool_snapshot(project)
        positions = {pk: index for index, pk in enumerate(ids, 1)}
        query = Unit.objects.filter(pk__in=ids).select_related('record__document')
        for field in ('q', 'document', 'metadata_field', 'metadata_value'):
            if not isinstance(params.get(field, ''), str):
                raise ValueError('筛选内容无效。')
        q = params.get('q', '')[:200].strip()
        if q:
            query = query.filter(text__icontains=q)
        document = params.get('document', '')
        if document:
            if not document.isdecimal() or len(document) > 18:
                raise ValueError('请选择来源文件。')
            query = query.filter(record__document_id=int(document))
        key, value = params.get('metadata_field', ''), params.get('metadata_value', '')[:200].strip()
        if key:
            if key not in metadata_fields(project):
                raise ValueError('请选择材料中的附加信息字段。')
            if value:
                query = query.annotate(selection_meta=KeyTextTransform(key, 'record__metadata')).filter(selection_meta__icontains=value)
        elif value:
            raise ValueError('请先选择附加信息字段。')
        if 'selected' in params:
            selected = params['selected']
            if not isinstance(selected, list) or len(selected) > MAX_SELECTION or any(
                    type(pk) is not int or pk not in positions for pk in selected):
                raise ValueError('已选单元无效或材料已变化，请刷新核对。')
            query = query.filter(pk__in=selected)
        page = Paginator(query, 40).get_page(params.get('page', 1))
        data = {'pool_hash': digest, 'total_units': len(ids), 'count': page.paginator.count,
                'page': page.number, 'pages': page.paginator.num_pages,
                'results': [{'id': unit.pk, 'position': positions[unit.pk], 'text': unit.text[:300],
                             'document': unit.record.document.name, 'source_position': unit.record.position}
                            for unit in page]}
        if params.get('all') in (True, '1'):
            if page.paginator.count > MAX_SELECTION:
                raise ValueError('一次最多选择10000条，请缩小筛选范围。')
            data['ids'] = list(query.values_list('pk', flat=True))
        response = JsonResponse(data)
        response['Cache-Control'] = 'no-store'
        return response
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'error': '筛选内容无效，请检查条件；一次最多选择10000条。'}, status=400)
    except OperationalError:
        return JsonResponse({'error': '数据库忙，请稍后重试。'}, status=503)
