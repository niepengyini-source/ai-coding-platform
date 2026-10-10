"""Low-friction personal coding and private, searchable annotation history."""
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Assignment, Code, Round, Unit
from .views import access
from .workflow import (annotation_events, close_quick_coding, counts, history_permissions,
                       reopen_from_history, round_counts, search_history, start_quick_coding)
from .workflow_forms import HistoryReopenForm, HistorySearchForm, QuickStartForm


def progress_data(project, user, role, params):
    assignments = Assignment.objects.filter(round__project=project)
    data = {'my': counts(assignments.filter(coder=user))}
    rows = Paginator(round_counts(project, user), 25).get_page(params.get('page'))
    data['rows'] = list(rows.object_list)
    for row in data['rows']:
        row['percent'] = round(row['submitted'] * 100 / row['total'], 1) if row['total'] else 0
    team_rows = None
    if role in ('owner', 'reviewer'):
        data['team'] = counts(assignments)
        members = (assignments.values('coder_id', 'coder__username')
                   .annotate(total=Count('id'), submitted=Count('id', filter=Q(status='submitted')),
                             saved=Count('id', filter=Q(status='draft', revision__gt=0)))
                   .order_by('coder__username', 'coder_id'))
        team_rows = Paginator(members, 25).get_page(params.get('members_page'))
        data['members'] = list(team_rows.object_list)
        for row in data['members']:
            row['percent'] = round(row['submitted'] * 100 / row['total'], 1) if row['total'] else 0
    return data, rows, team_rows


@login_required
def quick_start(request, project_id):
    project, role = access(request, project_id, ['owner', 'reviewer', 'coder'])
    books = project.codebooks.filter(codes__isnull=False).distinct()
    default = books.first()
    form = QuickStartForm(request.POST if request.method == 'POST' else None, project=project,
                          initial={'book_id': default.pk if default else None, 'token': uuid.uuid4()})
    if request.method == 'POST' and form.is_valid():
        try:
            r, created = start_quick_coding(project, request.user, form.cleaned_data['book_id'].pk,
                                           form.cleaned_data['token'])
            messages.success(request, '已开始本人的快速编码。' if created else '已打开原来的个人编码，没有重复创建。')
            return redirect('workbench', r.pk)
        except (ValueError, OperationalError) as error:
            form.add_error(None, '数据库正在忙，请稍后用同一页面重试。' if isinstance(error, OperationalError) else str(error))
    selected = books.filter(pk=form['book_id'].value()).first() if str(form['book_id'].value()).isdecimal() else default
    resume = (Round.objects.filter(project=project, book=selected, kind='quick', started_by=request.user,
                                   state='active').order_by('-id').first()) if selected else None
    return render(request, 'coding/quick_start.html', {'project': project, 'role': role, 'form': form,
        'unit_count': Unit.objects.filter(record__document__project=project, active=True).count(),
        'selected_book': selected, 'has_books': books.exists(), 'resume': resume})


@require_POST
@login_required
def quick_finish(request, project_id, round_id):
    project, role = access(request, project_id, ['owner', 'reviewer', 'coder'])
    r = get_object_or_404(Round, pk=round_id, project=project, kind='quick', started_by=request.user)
    try:
        revision = forms.IntegerField(min_value=0).clean(request.POST.get('revision'))
        close_quick_coding(r.pk, request.user, revision)
        messages.success(request, '这批个人编码已归档。原判断与修改历史保留；继续研究可开始新的批次。')
    except (ValueError, forms.ValidationError, OperationalError) as error:
        messages.error(request, '数据库正在忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return redirect('workbench', r.pk)


@require_GET
@login_required
def progress(request, project_id):
    project, role = access(request, project_id)
    data, rows, team_rows = progress_data(project, request.user, role, request.GET)
    return render(request, 'coding/progress.html', {'project': project, 'role': role, 'summary': data,
        'rows': rows, 'team_rows': team_rows, 'members_page': team_rows.number if team_rows else 1})


@require_GET
@login_required
def progress_api(request, project_id):
    project, role = access(request, project_id)
    data, _, _ = progress_data(project, request.user, role, request.GET)
    return JsonResponse(data)


def query_link(params, **changes):
    data = params.copy()
    data.pop('page', None)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    encoded = data.urlencode()
    return '?' + encoded if encoded else '?mode=quick'


@require_GET
@login_required
def history(request, project_id):
    project, role = access(request, project_id)
    data = request.GET.copy()
    if not data.get('mode'):
        data['mode'] = 'quick'
    form = HistorySearchForm(data, project=project, user=request.user)
    entries = search_history(project, request.user, form.cleaned_data) if form.is_valid() else Assignment.objects.none()
    page = Paginator(entries, 25).get_page(request.GET.get('page'))
    items = list(page.object_list)
    code_map = {c.pk: str(c) for c in Code.objects.filter(book_id__in={a.round.book_id for a in items})}
    for a in items:
        a.history_flags = history_permissions(a, request.user, role)
        a.secondary_display = [code_map.get(code_id, '无法对应的旧代码') for code_id in a.secondary]
    page.object_list = items
    exact_fields = ['unit_id', 'assignment_id', 'code', 'filename', 'round_id', 'book_version', 'status', 'uncertain', 'entry_kind']
    presets = [{'label': label, 'url': query_link(data, period=value, start=None, end=None)}
               for value, label in [('', '全部'), ('today', '今天'), ('week', '近7天'), ('month', '近30天')]]
    return render(request, 'coding/history.html', {'project': project, 'role': role, 'form': form, 'page': page,
        'presets': presets, 'exact_fields': [form[key] for key in exact_fields],
        'exact_open': data.get('mode') == 'exact' or any(data.get(key) for key in exact_fields),
        'previous_url': query_link(data, page=page.previous_page_number()) if page.has_previous() else '',
        'next_url': query_link(data, page=page.next_page_number()) if page.has_next() else ''})


def history_context(request, project, role, a, reopen_form=None):
    code_map = {c.pk: str(c) for c in a.round.book.codes.all()}
    events = Paginator(annotation_events(a), 20).get_page(request.GET.get('page'))
    for event in events:
        for name in ('before', 'after'):
            source = getattr(event, name)
            displayed = dict(source)
            displayed['primary_label'] = code_map.get(source.get('primary'), '—')
            displayed['secondary_labels'] = [code_map.get(code_id, '无法对应的旧代码') for code_id in source.get('secondary', [])]
            setattr(event, name + '_display', displayed)
    return {'project': project, 'role': role, 'record': a, 'events': events,
            'secondary_labels': [code_map.get(code_id, '无法对应的旧代码') for code_id in a.secondary],
            'flags': history_permissions(a, request.user, role),
            'reopen_form': reopen_form or HistoryReopenForm(initial={'revision': a.revision})}


def own_history_record(request, project):
    return Assignment.objects.filter(coder=request.user, round__project=project, revision__gt=0).select_related(
        'round__book', 'primary', 'unit__record__document')


@require_GET
@login_required
def history_detail(request, project_id, assignment_id):
    project, role = access(request, project_id)
    a = get_object_or_404(own_history_record(request, project), pk=assignment_id)
    return render(request, 'coding/history_detail.html', history_context(request, project, role, a))


@require_POST
@login_required
def history_reopen(request, project_id, assignment_id):
    project, role = access(request, project_id, ['owner', 'reviewer', 'coder'])
    a = get_object_or_404(own_history_record(request, project), pk=assignment_id)
    form = HistoryReopenForm(request.POST)
    if form.is_valid():
        try:
            reopen_from_history(a.pk, request.user, form.cleaned_data['revision'], form.cleaned_data['reason'])
            messages.success(request, '已开放这条记录供本人修改。旧答案和退回理由仍保留。')
            return redirect(f'/r/{a.round_id}/?a={a.pk}')
        except (ValueError, OperationalError) as error:
            form.add_error(None, '数据库正在忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return render(request, 'coding/history_detail.html', history_context(request, project, role, a, form))
