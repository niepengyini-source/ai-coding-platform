"""Personal segmentation never changes the project's shared material pool."""
import uuid

from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Assignment, AuditEvent, Membership, Round, Unit
from .services import Conflict, audit


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
    if (r.kind != 'quick' or r.started_by_id != actor.pk or not Membership.objects.filter(
            project=r.project, user=actor, role__in=['owner', 'reviewer', 'coder']).exists()):
        raise PermissionDenied('多人任务请提出拆分建议，不直接更改已分配的单元。')
    offset, token = split_input(a, data)
    if a.superseded:
        segments = list(a.segments.select_related('unit').order_by('unit__start'))
        if a.last_token == token and segments and segments[0].unit.end == a.unit.start + offset:
            return a
        raise Conflict('这条任务已拆分，请刷新后继续编码新单元。')
    if (r.state != 'active' or a.status != 'draft' or a.revision != 0 or
            a.primary_id or a.secondary or a.note or a.no_code or a.uncertain):
        raise Conflict('只可拆分尚未保存、未提交的个人任务，已有判断不会删除。')
    if type(data.get('revision')) is not int or data['revision'] != a.revision:
        raise Conflict('任务版本已变化，请刷新后重新核对。')
    boundary = a.unit.start + offset
    if a.unit.record.text[a.unit.start:a.unit.end] != a.unit.text:
        raise Conflict('原文位置不匹配，不能拆分；请联系负责人核对材料。')
    children = []
    for start, end in [(a.unit.start, boundary), (boundary, a.unit.end)]:
        # Private fragments are not offered to another coder or a new shared round.
        unit = Unit.objects.create(record=a.unit.record, start=start, end=end,
            text=a.unit.record.text[start:end], active=False, note='个人编码拆分，来源任务 #' + str(a.pk))
        children.append(Assignment.objects.create(round=r, coder=actor, unit=unit, parent=a))
    a.superseded, a.revision, a.last_token = True, a.revision + 1, token
    a.save(update_fields=['superseded', 'revision', 'last_token', 'updated_at'])
    r.revision += 1
    r.save(update_fields=['revision'])
    audit(r.project, actor, 'annotation.split', a,
          before={'unit_id': a.unit_id, 'start': a.unit.start, 'end': a.unit.end, 'superseded': False},
          after={'superseded': True, 'offset': offset, 'assignments': [c.pk for c in children],
                 'units': [c.unit_id for c in children]}, reason='本人核对预览后拆分未编码的个人单元。')
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
