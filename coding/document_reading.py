"""Bounded, read-only document views using the same assigned coding units."""
from django.core.paginator import Paginator
from django.urls import reverse


def reading_blocks(blocks):
    result = []
    for block in blocks:
        text = block['text']
        for offset in range(0, len(text), 4000):
            result.append({**block, 'id': f'{block["id"]}-{offset}', 'text': text[offset:offset + 4000],
                           'start': block['start'] + offset,
                           'end': min(block['end'], block['start'] + offset + 4000),
                           'level': block.get('level', 0) if offset == 0 else 0})
    return result


def reading_context(assignment, params):
    if not assignment:
        return {}
    unit, record = assignment.unit, assignment.unit.record
    document = record.document
    normalized = document.normalized or {}
    document_type = normalized.get('type') == 'document'
    blocks = reading_blocks(normalized.get('blocks', [])) if document_type else []
    if document_type:
        focus = next((i for i, block in enumerate(blocks) if block['end'] > unit.start), 0)
        rows = [dict(block, current=block['end'] > unit.start and block['start'] < unit.end)
                for block in blocks]
    else:
        # Historical tabular materials remain records; no source data is rewritten.
        records = list(document.records.order_by('position').values('id', 'position', 'text'))
        focus = next((i for i, row in enumerate(records) if row['id'] == record.pk), 0)
        rows = [{'id': row['id'], 'text': row['text'], 'start': 0, 'end': len(row['text']),
                 'page': None, 'heading': '', 'level': 0, 'position': row['position'],
                 'current': row['id'] == record.pk} for row in records]
    paginator = Paginator(rows, 30)
    page = paginator.get_page(params.get('reader_page') or focus // 30 + 1)
    rendered = []
    for block in page:
        text = block['text']
        left = max(0, unit.start - block['start']) if block['current'] else len(text)
        right = min(len(text), unit.end - block['start']) if block['current'] else len(text)
        rendered.append({**block, 'before': text[:left], 'selected': text[left:right], 'after': text[right:]})
    def link(number):
        return reverse('workbench', args=[assignment.round_id]) + f'?a={assignment.pk}&reader_page={number}#reading-document'
    outline = [{'title': block['heading'], 'url': link(i // 30 + 1) + f'-block-{block["id"]}'}
               for i, block in enumerate(rows) if block.get('level') and block.get('heading')]
    recommended = (document.workbench_mode == 'document' or
                   document.workbench_mode == 'auto' and normalized.get('presentation') == 'document')
    return {'document_reading': {'document': document, 'blocks': rendered, 'page': page,
        'recommended': recommended or 'reader_page' in params, 'outline': outline[:200],
        'previous_url': link(page.previous_page_number()) if page.has_previous() else '',
        'next_url': link(page.next_page_number()) if page.has_next() else '',
        'focus_url': link(focus // 30 + 1), 'is_document': document_type}}
