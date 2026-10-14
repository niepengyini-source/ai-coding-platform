"""Personal segmentation never changes the project's shared material pool."""
import uuid

from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Assignment, AuditEvent, Membership, Round, Unit
from .services import Conflict, annotation_data, audit


def split_input(a, data):
    offset = data.get('offset')
    if type(offset) is not int or not 0 < offset < len(a.unit.text):
        raise ValueError('请选择文本中间的拆分位置。')
    if not a.unit.text[:offset].strip() or not a.unit.text[offset:].strip():
        raise ValueError('拆分后的两段都需要包含文字。')
    try:
        token = str(uuid.UUID(str(data.get('token', ''))))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('拆分请求标识无效，请刷新后重试。')
    return offset, token


@transaction.atomic
def split_personal(assignment_id, actor, data):
    ref = Assignment.objects.only('round_id').get(pk=assignment_id, coder=actor)
    r = Round.objects.select_for_update().get(pk=ref.round_id)
    a = Assignment.objects.select_for_update().select_related('unit__record').get(pk=assignment_id, coder=actor)
    if ((r.kind == 'quick' and r.started_by_id != actor.pk) or not Membership.objects.filter(
            project=r.project, user=actor, role__in=['owner', 'reviewer', 'coder']).exists()):
        raise PermissionDenied
    if 'cuts' in data:
        cuts, length = data['cuts'], len(a.unit.text)
        if (not isinstance(cuts, list) or not 1 <= len(cuts) <= 49 or
                any(type(cut) is not int or not 0 < cut < length for cut in cuts) or
                any(left >= right for left, right in zip(cuts, cuts[1:]))):
            raise ValueError('请选择有效分界，一次最多拆分为50个单元。')
        boundaries = [0, *cuts, length]
        if any(not a.unit.text[left:right].strip() for left, right in zip(boundaries, boundaries[1:])):
            raise ValueError('每个编码单元都需要包含文字，请调整选择。')
        start = data.get('start', 0)
        if type(start) is not int or not 0 <= start < length:
            raise ValueError('所选片段位置无效，请重新选择。')
        end = next(boundary for boundary in boundaries if boundary > start)
        try:
            token = str(uuid.UUID(str(data.get('token', ''))))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('拆分请求标识无效，请刷新后重试。')
    elif 'start' in data or 'end' in data:
        start, end = data.get('start'), data.get('end')
        length = len(a.unit.text)
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
            raise ValueError('请选择当前文字中的有效片段。')
        if not a.unit.text[start:end].strip():
            raise ValueError('所选片段需要包含文字。')
        # Keep whitespace with its neighbouring text rather than losing characters.
        if not a.unit.text[:start].strip():
            start = 0
        if not a.unit.text[end:].strip():
            end = length
        if start == 0 and end == length:
            raise ValueError('请选择一部分文字，不要选中整条。')
        try:
            token = str(uuid.UUID(str(data.get('token', ''))))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('拆分请求标识无效，请刷新后重试。')
        boundaries = sorted({0, start, end, length})
    else:
        end, token = split_input(a, data)
        start = 0
        boundaries = [0, end, len(a.unit.text)]
    spans = [(a.unit.start + left, a.unit.start + right) for left, right in zip(boundaries, boundaries[1:])]
    if a.superseded:
        segments = list(a.segments.select_related('unit').order_by('unit__start'))
        if a.last_token == token and [(s.unit.start, s.unit.end) for s in segments] == spans:
            return a
        raise Conflict('这条任务已拆分，请刷新后继续编码新单元。')
    if r.state != 'active':
        raise Conflict('本批编码已锁定，不能拆分。')
    if type(data.get('revision')) is not int or data['revision'] != a.revision:
        raise Conflict('任务版本已变化，请刷新后重新核对。')
    if a.unit.record.text[a.unit.start:a.unit.end] != a.unit.text:
        raise Conflict('原文位置不匹配，不能拆分；请联系负责人核对材料。')
    children = []
    before = {**annotation_data(a), 'unit_id': a.unit_id, 'start': a.unit.start, 'end': a.unit.end, 'superseded': False}
    for left, right in spans:
        # Matching boundaries in the same collaborative round remain comparable.
        existing = Assignment.objects.filter(round=r, parent__isnull=False,
            unit__record=a.unit.record, unit__start=left, unit__end=right).select_related('unit').first()
        unit = existing.unit if existing else Unit.objects.create(record=a.unit.record, start=left, end=right,
            text=a.unit.record.text[left:right], active=False, note='编码拆分，来源任务 #' + str(a.pk))
        children.append(Assignment.objects.create(round=r, coder=actor, unit=unit, parent=a))
    a.superseded, a.revision, a.last_token = True, a.revision + 1, token
    a.save(update_fields=['superseded', 'revision', 'last_token', 'updated_at'])
    r.revision += 1
    r.save(update_fields=['revision'])
    audit(r.project, actor, 'annotation.split', a,
          before=before,
          after={**annotation_data(a), 'superseded': True, 'offset': end, 'start': start, 'end': end,
                 'cuts': boundaries[1:-1],
                 'assignments': [c.pk for c in children], 'units': [c.unit_id for c in children]},
          reason='本人拆分当前单元，原文和旧判断保留。')
    return a


@transaction.atomic
def suggest_split(assignment_id, actor, data):
    ref = Assignment.objects.only('round_id').get(pk=assignment_id, coder=actor)
    r = Round.objects.select_for_update().get(pk=ref.round_id)
    a = Assignment.objects.select_related('unit').get(pk=assignment_id, coder=actor)
    if not Membership.objects.filter(project=r.project, user=actor, role__in=['owner', 'reviewer', 'coder']).exists():
        raise PermissionDenied
    if r.kind == 'quick' or r.state != 'active' or a.superseded:
        raise Conflict('仅编码中的多人任务可提出建议。')
    offset, token = split_input(a, data)
    reason = data.get('reason', '')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('请说明拆分理由，最多2000字。')
    existing = AuditEvent.objects.filter(project=r.project, actor=actor, action='unit.split_suggest',
                                        after__token=token).first()
    if existing:
        if existing.object_id != str(a.pk) or existing.after.get('offset') != offset or existing.reason != reason.strip():
            raise Conflict('同一次建议请求不能改变单元或位置，请重新预览。')
        return existing
    return audit(r.project, actor, 'unit.split_suggest', a,
                 after={'unit_id': a.unit_id, 'round_id': r.pk, 'offset': offset, 'token': token}, reason=reason.strip())
