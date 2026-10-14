"""Small, source-ordered context windows within the same imported conversation."""
from .models import SourceRecord


def context_page(record, direction, cursor=None, size=3):
    cursor = record.position if cursor is None else cursor
    if direction not in ('before', 'after') or isinstance(cursor, bool) or not isinstance(cursor, int):
        raise ValueError('上下文位置不正确，请刷新后重试。')
    if cursor < 1 or cursor > 10**18 or (direction == 'before' and cursor > record.position) or (direction == 'after' and cursor < record.position):
        raise ValueError('上下文位置不正确，请刷新后重试。')
    records = SourceRecord.objects.filter(document_id=record.document_id, context_key=record.context_key)
    records = (records.filter(position__lt=cursor).order_by('-position') if direction == 'before'
               else records.filter(position__gt=cursor).order_by('position'))
    items = list(records[:size + 1])
    has_more = len(items) > size
    items = items[:size]
    next_cursor = items[-1].position if items else cursor
    if direction == 'before':
        items.reverse()
    return items, has_more, next_cursor
