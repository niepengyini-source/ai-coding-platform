from bisect import bisect_right
from collections import defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import OperationalError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .document_reading import reading_blocks
from .document_units import draft_ranges, source_record, update_document_units
from .models import SourceDocument
from .services import Conflict
from .views import access


BLOCKS_PER_PAGE = 30


def _pieces(text, start, end, ranges):
    changes = defaultdict(int, {start: 0, end: 0})
    for left, right in ranges:
        left, right = max(start, left), min(end, right)
        if left < right:
            changes[left] += 1
            changes[right] -= 1
    result, active, previous = [], 0, start
    for point in sorted(changes):
        if point > previous:
            value = text[previous:point]
            if result and result[-1]['selected'] == bool(active):
                result[-1]['text'] += value
            else:
                result.append({'text': value, 'selected': bool(active)})
        active += changes[point]
        previous = point
    return result


@login_required
@require_http_methods(['GET', 'POST'])
def prepare(request, project_id, document_id):
    project, role = access(request, project_id)
    document = get_object_or_404(SourceDocument, project=project, pk=document_id)
    if document.normalized.get('type') != 'document' or not document.normalized.get('text'):
        raise Http404
    if request.method == 'POST':
        try:
            document = update_document_units(document.pk, request.user, request.POST)
            message = ('统一片段已确认，可以开始编码或安排成员任务。' if request.POST.get('action') == 'confirm'
                       else '片段列表已更新；确认后才会用于新任务。')
            messages.success(request, message)
            query = urlencode({'page': request.POST.get('page', '1'), 'segments': request.POST.get('segments', '1')})
            return redirect(reverse('document_prepare', args=[project.pk, document.pk]) + '?' + query)
        except (ValueError, Conflict, OperationalError) as error:
            messages.error(request, '数据库正在忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
            document.refresh_from_db()
    try:
        record = source_record(document)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect('materials', project.pk)
    blocks = reading_blocks(document.normalized.get('blocks') or [
        {'id': 'text', 'text': record.text, 'start': 0, 'end': len(record.text)}])
    ranges = draft_ranges(document)
    starts = [block['start'] for block in blocks]
    page_number = request.GET.get('page', request.POST.get('page', 1))
    if request.GET.get('position', '').isdigit():
        page_number = max(0, bisect_right(starts, int(request.GET['position'])) - 1) // BLOCKS_PER_PAGE + 1
    block_page = Paginator(blocks, BLOCKS_PER_PAGE).get_page(page_number)
    visible = []
    for block in block_page:
        visible.append({**block, 'pieces': _pieces(record.text, block['start'], block['end'], ranges)})
    reader_start = visible[0]['start'] if visible else 0
    reader_end = visible[-1]['end'] if visible else 0
    segment_page = Paginator(list(enumerate(ranges)), 40).get_page(
        request.GET.get('segments', request.POST.get('segments', 1)))
    segment_page.object_list = [
        {'index': index, 'number': index + 1, 'start': start, 'end': end,
         'text': record.text[start:min(end, start + 180)] + ('…' if end - start > 180 else ''),
         'page': max(0, bisect_right(starts, start) - 1) // BLOCKS_PER_PAGE + 1,
         'source_page': blocks[max(0, bisect_right(starts, start) - 1)].get('page')}
        for index, (start, end) in segment_page.object_list]
    outline = [{'label': block.get('heading') or block['text'][:80],
                'page': index // BLOCKS_PER_PAGE + 1, 'id': block.get('id', index), 'level': block.get('level', 1)}
               for index, block in enumerate(blocks) if block.get('level')]
    if not outline:
        seen_pages = set()
        for index, block in enumerate(blocks):
            source_page = block.get('page')
            if source_page and source_page not in seen_pages:
                seen_pages.add(source_page)
                outline.append({'label': f'原件第 {source_page} 页', 'page': index // BLOCKS_PER_PAGE + 1,
                                'id': block.get('id', index), 'level': 1})
    return render(request, 'coding/document_prepare.html', {
        'project': project, 'role': role, 'document': document, 'can_prepare': role in ('owner', 'reviewer'),
        'blocks': visible, 'block_page': block_page, 'segment_page': segment_page,
        'outline': outline, 'fragment_count': len(ranges), 'can_undo': 'draft_undo' in document.normalized,
        'reader_data': {'start': reader_start, 'text': record.text[reader_start:reader_end],
                        'position': int(request.GET['position']) if request.GET.get('position', '').isdigit() else None},
    })
