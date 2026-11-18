"""Read a coder's current references across both kinds of coding work."""
from django.core.paginator import Paginator
from django.db.models import F, IntegerField, JSONField, Q, Value
from django.db.models.functions import Substr

from .models import Assignment, SourceAnnotation, SourceDocument


def matching_code_ids(ids):
    condition = Q(primary_id__in=ids)
    if ids:
        # Old answers may carry several numeric IDs. Match complete array elements.
        pattern = r'(^|[^0-9])(' + '|'.join(map(str, ids)) + r')($|[^0-9])'
        condition |= Q(secondary__regex=pattern) | Q(labels__regex=pattern)
    return condition


def my_code_references(code, user, page_number):
    match = matching_code_ids([code.pk])
    project_id = code.book.project_id
    fixed = Assignment.objects.filter(match, coder=user, round__book=code.book, round__project_id=project_id,
        unit__record__document__project_id=project_id, revision__gt=0, superseded=False, no_code=False).order_by().values(
        'pk', 'updated_at', batch=F('round__name'), batch_id=F('round_id'),
        file_id=F('unit__record__document_id'), filename=F('unit__record__document__name'),
        sheet=F('unit__record__document__sheet_name'), excerpt=Substr('unit__text', 1, 401), row_number=F('unit__record__position'),
        left_offset=F('unit__start'), right_offset=F('unit__end'),
        source_task=Value(None, output_field=IntegerField()), area=Value(None, output_field=JSONField()),
        state=F('status'))
    selections = SourceAnnotation.objects.filter(match, task__coder=user, task__round__book=code.book,
        task__round__project_id=project_id, task__document__project_id=project_id, no_code=False).order_by().values(
        'pk', 'updated_at', batch=F('task__round__name'), batch_id=F('task__round_id'),
        file_id=F('task__document_id'), filename=F('task__document__name'),
        sheet=F('task__document__sheet_name'), excerpt=Substr('text', 1, 401), row_number=Value(None, output_field=IntegerField()),
        left_offset=F('start'), right_offset=F('end'), source_task=F('task_id'), area=F('region'),
        state=Value('saved'))

    rows = fixed.union(selections, all=True).order_by('-updated_at', '-pk', 'state')
    page = Paginator(rows, 25).get_page(page_number)
    file_ids = {row['file_id'] for row in page}
    pdf_blocks = dict(SourceDocument.objects.filter(project_id=project_id, pk__in=file_ids,
        normalized__source__format='pdf').values_list('pk', 'normalized__blocks'))
    for row in page:
        if row['area']:
            area = row['area']
            row['location'] = (f"图片区域 · 左 {area['x']:.1%}，上 {area['y']:.1%}，"
                               f"宽 {area['width']:.1%}，高 {area['height']:.1%}")
            continue
        start, end = row['left_offset'], row['right_offset']
        pages = [block['page'] for block in pdf_blocks.get(row['file_id'], []) or []
                 if block.get('page') and block['start'] < end and block['end'] > start]
        location = []
        if pages:
            span = str(pages[0]) if pages[0] == pages[-1] else f'{pages[0]}–{pages[-1]}'
            location.append(f'第 {span} 页')
        elif row['row_number'] is not None:
            location.append(f"原记录 {row['row_number']}")
        span = str(end) if end == start + 1 else f'{start + 1}–{end}'
        location.append(f'第 {span} 字')
        row['location'] = ' · '.join(location)
    return page
