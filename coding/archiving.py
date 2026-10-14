"""Read-only project snapshots and permission-scoped, searchable annotation exports."""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Func, Q, TextField
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .archive_forms import ArchiveSearchForm, ArchiveSelectionForm
from .exporting import MAX_EXPORT_ROWS, archive_annotation_table, export_response
from .models import Assignment, Code, Membership, Project
from .services import project_archive


class MetadataText(Func):
    """Look up one literal CSV/XLSX heading, never a JSON path or array index."""
    output_field = TextField()

    def __init__(self, expression, key):
        self.key = key
        super().__init__(expression)

    def as_sqlite(self, compiler, connection, **extra_context):
        lhs, params = compiler.compile(self.source_expressions[0])
        return f'(SELECT CAST(value AS TEXT) FROM JSON_EACH({lhs}) WHERE key = %s)', [*params, self.key]

    def as_postgresql(self, compiler, connection, **extra_context):
        lhs, params = compiler.compile(self.source_expressions[0])
        return f'({lhs} ->> %s)', [*params, self.key]


def search_archive(project, user, role, data):
    # Unlike coding workbenches, this explicit archive screen permits managers to
    # snapshot all saved answers; members remain restricted to their own answers.
    entries = Assignment.objects.filter(round__project=project, revision__gt=0)
    if data.get('scope') != 'all' or role not in ('owner', 'reviewer'):
        entries = entries.filter(coder=user)
    if not data.get('include_superseded'):
        entries = entries.filter(superseded=False)
    suffix = 'iexact' if data.get('mode') == 'exact' else 'icontains'
    query = data.get('q')
    if query:
        condition = Q(unit__text__iexact=query) if suffix == 'iexact' else Q(unit__text__icontains=query)
        for field in ('note', 'primary__key', 'primary__name', 'unit__record__document__name', 'round__name'):
            condition |= Q(**{field + '__' + suffix: query})
        if query.isascii() and query.isdecimal() and len(query) <= 18:
            condition |= Q(unit_id=int(query)) | Q(pk=int(query))
        entries = entries.filter(condition)
    for field, lookup in [('document', 'unit__record__document'), ('round_id', 'round'), ('status', 'status')]:
        if data.get(field):
            entries = entries.filter(**{lookup: data[field]})
    if data.get('code'):
        matching = Code.objects.filter(book__project=project).filter(
            Q(**{'key__' + suffix: data['code']}) | Q(**{'name__' + suffix: data['code']}))
        code_ids = list(matching.values_list('id', flat=True))
        condition = Q(primary_id__in=code_ids)
        if code_ids:
            # JSON arrays store numeric IDs. Digit boundaries avoid matching ID 1
            # inside 11, and work for old records with more than one secondary code.
            condition |= Q(secondary__regex=r'(^|[^0-9])(' + '|'.join(str(pk) for pk in code_ids) + r')($|[^0-9])')
        entries = entries.filter(condition)
    if data.get('metadata_field'):
        entries = entries.annotate(archive_metadata=MetadataText('unit__record__metadata', data['metadata_field']))
        entries = entries.filter(**{'archive_metadata__' + suffix: data['metadata_value']})
    if data.get('start'):
        entries = entries.filter(updated_at__gte=data['start'])
    if data.get('end'):
        entries = entries.filter(updated_at__lt=data['end'] + timedelta(minutes=1))
    return entries.select_related('round__book', 'primary', 'unit__record__document', 'coder').order_by('-updated_at', '-id')


@login_required
def archive(request, project_id):
    try:
        return archive_page(request, project_id)
    except OperationalError:
        return HttpResponse('数据库暂时繁忙，尚未生成导出文件。原始编码没有改变；请稍后返回归档页面重试。',
                            status=503, content_type='text/plain; charset=utf-8')


def archive_page(request, project_id):
    membership = get_object_or_404(Membership.objects.select_related('project'), project_id=project_id, user=request.user)
    project, role = membership.project, membership.role
    if request.method == 'POST' and request.POST.get('action') == 'full':
        if role not in ('owner', 'reviewer'):
            raise PermissionDenied
        with transaction.atomic():
            project = Project.objects.select_for_update().get(pk=project.pk)
            data = project_archive(project)
        response = HttpResponse(data, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="project_{project.pk}_snapshot.zip"'
        response['Cache-Control'] = 'no-store'
        return response

    parameters = (request.POST if request.method == 'POST' else request.GET).copy()
    defaults = {'scope': 'mine', 'mode': 'quick'}
    for key, value in defaults.items():
        parameters.setdefault(key, value)
    search_form = ArchiveSearchForm(parameters, project=project, user=request.user, role=role)
    entries = Assignment.objects.none()
    valid_search = search_form.is_valid()
    if valid_search:
        entries = search_archive(project, request.user, role, search_form.cleaned_data)
    page = Paginator(entries, 25).get_page(1 if request.POST.get('action') == 'preview' else parameters.get('page'))
    parameters['page'] = str(page.number)
    preview = list(page.object_list)
    selection_form = ArchiveSelectionForm(parameters if request.method == 'POST' and parameters.get('action') == 'custom' else None,
        visible_ids=[a.pk for a in preview], initial={'page': str(page.number),
            'selection': parameters.get('selection', 'all'), 'limit': parameters.get('limit', ''),
            'format': parameters.get('format', 'xlsx')})
    error = ''
    if request.method == 'POST':
        if request.POST.get('action') not in ('custom', 'preview'):
            error = '请选择完整项目快照或自定义导出。'
        elif request.POST.get('action') == 'custom' and valid_search and selection_form.is_valid():
            values = selection_form.cleaned_data
            chosen = entries
            if values['selection'] == 'selected':
                chosen = chosen.filter(pk__in=set(values['selected']))
            elif values['selection'] == 'first':
                chosen = chosen[:values['limit']]
            selected_entries = list(chosen[:MAX_EXPORT_ROWS + 1])
            if not selected_entries:
                error = '没有匹配的编码记录，请调整筛选条件。'
            elif len(selected_entries) > MAX_EXPORT_ROWS:
                error = '一次最多导出50000条，请缩小筛选范围或选择前 N 条。'
            else:
                table = archive_annotation_table(selected_entries, project.name)
                try:
                    return export_response(table, table.headers, table.headers, values['format'],
                                           f'{project.name}_编码记录_{timezone.localtime():%Y%m%d_%H%M}')
                except ValueError as exc:
                    error = str(exc)
    checked_ids = set(parameters.getlist('selected'))
    secondary_codes = {(code.book_id, code.pk): str(code) for code in Code.objects.filter(
        book_id__in={a.round.book_id for a in preview}, pk__in={pk for a in preview for pk in a.secondary})}
    for a in preview:
        a.archive_checked = str(a.pk) in checked_ids
        a.metadata_preview = ' · '.join(f'{key}: {value}' for key, value in a.unit.record.metadata.items())
        a.secondary_display = [secondary_codes[(a.round.book_id, pk)] for pk in a.secondary
                               if (a.round.book_id, pk) in secondary_codes]
    def page_url(number):
        query = parameters.copy()
        for name in ('action', 'csrfmiddlewaretoken', 'selected', 'selection', 'limit', 'format'):
            query.pop(name, None)
        query['page'] = number
        return '?' + query.urlencode()
    return render(request, 'coding/archive.html', {
        'project': project, 'role': role, 'search_form': search_form, 'selection_form': selection_form,
        'page': page, 'preview': preview, 'error': error, 'can_full_archive': role in ('owner', 'reviewer'),
        'advanced_open': any(parameters.get(key) for key in ('document', 'round_id', 'code', 'status',
                             'metadata_field', 'metadata_value', 'start', 'end', 'include_superseded')) or bool(search_form.errors),
        'previous_url': page_url(page.previous_page_number()) if page.has_previous() else '',
        'next_url': page_url(page.next_page_number()) if page.has_next() else '',
    })
