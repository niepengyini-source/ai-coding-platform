"""Prepare shared document ranges without rewriting original text or old tasks."""
from copy import deepcopy

from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Membership, Project, SourceDocument, Unit
from .services import Conflict, audit


def source_record(document):
    record = document.records.order_by('position').first()
    if document.normalized.get('type') != 'document' or record is None:
        raise ValueError('此材料不是待整理的长文档。')
    if document.normalized.get('text') != record.text:
        raise ValueError('原文与阅读版本不一致，请重新核对材料。')
    return record


def published_ranges(document):
    return [[u.start, u.end] for u in Unit.objects.filter(record__document=document, active=True)
            .order_by('start', 'end', 'pk')]


def draft_ranges(document):
    return deepcopy(document.normalized.get('draft_units', published_ranges(document)))


def _integer(value, label):
    if isinstance(value, bool):
        raise ValueError(label + '格式不正确。')
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(label + '格式不正确。')
    if str(result) != str(value):
        raise ValueError(label + '格式不正确。')
    return result


def _valid_range(start, end, text):
    if not 0 <= start < end <= len(text) or not text[start:end].strip():
        raise ValueError('请在原文中选择有效的连续文字。')


@transaction.atomic
def update_document_units(document_id, actor, data):
    project_id = SourceDocument.objects.values_list('project_id', flat=True).get(pk=document_id)
    Project.objects.select_for_update().get(pk=project_id)
    document = SourceDocument.objects.select_for_update().get(pk=document_id)
    if not Membership.objects.filter(project_id=document.project_id, user=actor,
                                     role__in=['owner', 'reviewer']).exists():
        raise PermissionDenied
    if _integer(data.get('revision'), '材料版本') != document.unit_revision:
        raise Conflict('其他页面已更新片段，请刷新后核对；本次修改未保存。')
    record = source_record(document)
    normalized = deepcopy(document.normalized)
    ranges = draft_ranges(document)
    before = deepcopy(ranges)
    action = data.get('action')
    if action == 'add':
        start, end = _integer(data.get('start'), '起点'), _integer(data.get('end'), '终点')
        _valid_range(start, end, record.text)
        # Browser form submission normalizes line endings to CRLF.
        selected_text = data.get('text')
        if not isinstance(selected_text, str) or selected_text.replace('\r\n', '\n') != record.text[start:end]:
            raise ValueError('所选文字与原文位置不一致，请重新划选。')
        if [start, end] in ranges:
            raise ValueError('这个片段已在候选列表中。')
        if len(ranges) >= 10000:
            raise ValueError('单份材料最多确认10000个片段，请分批整理。')
        ranges.append([start, end])
        ranges.sort()
    elif action == 'suggest':
        suggestions = []
        seen = {tuple(pair) for pair in ranges}
        blocks = normalized.get('blocks', [])
        if normalized.get('source', {}).get('format') == 'pdf':
            pages = {}
            for block in blocks:
                if not block.get('level') and block.get('page'):
                    pages[block['page']] = pages.get(block['page'], 0) + 1
            if pages and all(count == 1 for count in pages.values()):
                raise ValueError('此PDF当前按页提取，尚未识别可靠段落。请对照原件划选需要编码的句子或段落。')
        if any(block.get('end', 0) - block.get('start', 0) > 10000 for block in blocks):
            raise ValueError('材料包含过长的连续文本块，请先在原文中划选片段，避免把整篇内容当成一个段落。')
        for block in blocks:
            if block.get('level'):
                continue
            start, end = block.get('start'), block.get('end')
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(record.text):
                if record.text[start:end].strip() and (start, end) not in seen:
                    suggestions.append([start, end])
                    seen.add((start, end))
        if not suggestions:
            raise ValueError('没有新的正文段落可以加入，请直接划选需要编码的文字。')
        if len(ranges) + len(suggestions) > 10000:
            raise ValueError('候选段落超过10000个，请改为分段划选。')
        ranges = sorted(ranges + suggestions)
    elif action == 'remove':
        index = _integer(data.get('index'), '片段编号')
        if not 0 <= index < len(ranges):
            raise ValueError('该片段已变化，请刷新候选列表。')
        ranges.pop(index)
    elif action == 'undo':
        previous = normalized.get('draft_undo')
        if previous is None:
            raise ValueError('没有可以撤回的片段修改。')
        ranges = deepcopy(previous)
        normalized.pop('draft_undo', None)
    elif action == 'confirm':
        if not ranges:
            raise ValueError('请先加入并核对至少一个编码片段。')
        existing = {}
        for unit in Unit.objects.filter(record=record).order_by('pk'):
            existing.setdefault((unit.start, unit.end, unit.text), unit)
        selected_ids = []
        for start, end in ranges:
            _valid_range(start, end, record.text)
            key = (start, end, record.text[start:end])
            unit = existing.get(key)
            if unit is None:
                unit = Unit.objects.create(record=record, start=start, end=end, text=key[2])
            selected_ids.append(unit.pk)
        # Old assignments keep their immutable Unit, even when future task ranges change.
        Unit.objects.filter(record=record, active=True).exclude(pk__in=selected_ids).update(active=False)
        Unit.objects.filter(pk__in=selected_ids).update(active=True)
        document.units_confirmed = True
        normalized.pop('draft_undo', None)
    else:
        raise ValueError('无效的片段整理操作。')
    if action not in ('confirm', 'undo'):
        normalized['draft_undo'] = before
    if action != 'confirm':
        document.units_confirmed = ranges == published_ranges(document) and bool(ranges)
    normalized['draft_units'] = ranges
    document.normalized = normalized
    document.unit_revision += 1
    document.save(update_fields=['normalized', 'units_confirmed', 'unit_revision'])
    audit(document.project, actor, 'document.units.' + action, document,
          before={'ranges': before}, after={'ranges': ranges, 'confirmed': document.units_confirmed,
                                           'revision': document.unit_revision})
    return document
