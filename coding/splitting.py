"""Personal segmentation never changes the project's shared material pool."""
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Assignment, AuditEvent, Comment, Decision, Membership, Round, Unit
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
    if ('book_id' in data or r.sampling.get('rules_updated')) and data.get('book_id') != r.book_id:
        raise Conflict('编码规则已更新，请刷新核对当前规则后再拆分。')
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
        event = _latest_split(a)
        segments = list(a.segments.filter(pk__in=event.after.get('assignments', []) if event else [])
                        .select_related('unit').order_by('unit__start'))
        if a.last_token == token and [(s.unit.start, s.unit.end) for s in segments] == spans:
            return a
        raise Conflict('这条任务已拆分，请刷新后继续编码新单元。')
    if AuditEvent.objects.filter(project=r.project, object_type='Assignment', object_id=str(a.pk),
                                  action='annotation.split', after__token=token).exists():
        raise Conflict('这次拆分请求已经撤回，请重新预览后使用新请求。')
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
        peers = Assignment.objects.filter(round=r, superseded=False, unit__record=a.unit.record,
            unit__start=left, unit__end=right).exclude(coder=actor)
        peer_units = list(peers.values_list('unit_id', flat=True).distinct())
        if len(peer_units) > 1:
            raise Conflict('同一片段有多个协作版本，请联系负责人核对后再拆分。')
        if peer_units:
            unit = Unit.objects.get(pk=peer_units[0])
            previous = Assignment.objects.select_for_update().filter(round=r, coder=actor, unit=unit).first()
            if previous:
                if not previous.superseded or previous.parent_id != a.pk:
                    raise Conflict('该片段已有其他来源的本人任务，请刷新核对后再拆分。')
                old = {**annotation_data(previous), 'unit_id': unit.pk, 'parent_id': previous.parent_id,
                    'superseded': previous.superseded, 'round_id': r.pk, 'book_id': r.book_id,
                    'updated_at': previous.updated_at.isoformat(), 'unit': {'id': unit.pk,
                        'record_id': unit.record_id, 'start': unit.start, 'end': unit.end, 'text': unit.text}}
                previous.primary_id, previous.secondary, previous.note = None, [], ''
                previous.uncertain, previous.no_code, previous.superseded = False, False, False
                previous.status, previous.last_token = 'draft', ''
                previous.revision += 1
                previous.save()
                audit(r.project, actor, 'annotation.resplit', previous, before=old,
                    after={**annotation_data(previous), 'unit_id': unit.pk, 'parent_id': a.pk,
                           'superseded': False, 'book_id': r.book_id},
                    reason='重新拆分匹配协作片段，先保留旧判断快照，再开始新的草稿。')
                children.append(previous)
            else:
                children.append(Assignment.objects.create(round=r, coder=actor, unit=unit, parent=a))
            continue
        existing = Assignment.objects.filter(round=r, parent__isnull=False,
            unit__record=a.unit.record, unit__start=left, unit__end=right).exclude(
                unit_id__in=Assignment.objects.filter(round=r, coder=actor).values('unit_id'))
        existing = existing.select_related('unit').first()
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
                 'token': token,
                 'assignments': [c.pk for c in children], 'units': [c.unit_id for c in children]},
          reason='本人拆分当前单元，原文和旧判断保留。')
    return a


def _latest_split(a):
    return AuditEvent.objects.filter(project_id=a.round.project_id, object_type='Assignment',
        object_id=str(a.pk), action='annotation.split').order_by('-id').first()


def _split_branch(parent):
    """Only follow the latest split generation, retaining earlier generations as history."""
    event = _latest_split(parent)
    direct_ids = event.after.get('assignments', []) if event else []
    direct = list(parent.segments.filter(pk__in=direct_ids, round_id=parent.round_id,
                                        coder_id=parent.coder_id).select_related('unit'))
    if not direct_ids or len(direct) != len(direct_ids):
        raise Conflict('拆分记录不完整，请刷新后重试。')
    descendants, pending = [], direct
    while pending:
        descendants.extend(pending)
        pending = list(Assignment.objects.filter(parent_id__in=[entry.pk for entry in pending],
            round_id=parent.round_id, coder_id=parent.coder_id).select_related('unit'))
    live = [entry for entry in descendants if not entry.superseded]
    ordered = sorted(live, key=lambda entry: (entry.unit.start, entry.pk))
    cursor = parent.unit.start
    for entry in ordered:
        if (entry.unit.record_id != parent.unit.record_id or entry.unit.start != cursor or
                entry.unit.end > parent.unit.end):
            raise Conflict('拆分分支已变化，请刷新后核对。')
        cursor = entry.unit.end
    if not ordered or cursor != parent.unit.end:
        raise Conflict('拆分分支已变化，请刷新后核对。')
    return event, descendants, live


def _branch_token(parent, event, descendants, current_id):
    # The current form may flush its draft after preview; its version is checked separately.
    state = [[entry.pk, entry.parent_id, entry.superseded,
              None if entry.pk == current_id else entry.revision,
              None if entry.pk == current_id else entry.last_token]
             for entry in sorted(descendants, key=lambda entry: entry.pk)]
    raw = json.dumps([parent.pk, parent.revision, parent.round.book_id, event.pk, state], separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _branch_locked(parent, descendants):
    unit_ids = [parent.unit_id, *[entry.unit_id for entry in descendants]]
    return (Decision.objects.filter(round_id=parent.round_id, unit_id__in=unit_ids).exists() or
            Comment.objects.filter(round_id=parent.round_id, unit_id__in=unit_ids).exists())


def split_restore_options(a, actor):
    """Private workbench data; nearest eligible parent first, without other coders' answers."""
    if (a is None or a.coder_id != actor.pk or a.superseded or a.round.state != 'active' or
            (a.round.kind == 'quick' and a.round.started_by_id != actor.pk) or
            not Membership.objects.filter(project_id=a.round.project_id, user=actor,
                                          role__in=['owner', 'reviewer', 'coder']).exists()):
        return []
    options, child, seen = [], a, set()
    while child.parent_id and child.parent_id not in seen:
        seen.add(child.parent_id)
        parent = Assignment.objects.select_related('unit', 'round').filter(pk=child.parent_id,
            round_id=a.round_id, coder=actor, superseded=True).first()
        if parent is None:
            break
        try:
            event, descendants, live = _split_branch(parent)
        except Conflict:
            break
        if a.pk not in [entry.pk for entry in live] or _branch_locked(parent, descendants):
            break
        coded = sum(bool(entry.revision or entry.primary_id or entry.secondary or entry.note or
                         entry.no_code or entry.uncertain or entry.status == 'submitted') for entry in descendants)
        options.append({'parent_id': parent.pk, 'parent_revision': parent.revision,
            'branch_token': _branch_token(parent, event, descendants, a.pk),
            'part_count': len(live), 'coded_count': coded, 'requires_confirmation': bool(coded),
            'text': parent.unit.text})
        child = parent
    return options


@transaction.atomic
def restore_personal_split(assignment_id, actor, data):
    ref = Assignment.objects.only('round_id').get(pk=assignment_id, coder=actor)
    r = Round.objects.select_for_update().get(pk=ref.round_id)
    a = Assignment.objects.select_for_update().get(pk=assignment_id, coder=actor)
    if ((r.kind == 'quick' and r.started_by_id != actor.pk) or not Membership.objects.filter(
            project_id=r.project_id, user=actor, role__in=['owner', 'reviewer', 'coder']).exists()):
        raise PermissionDenied
    if ('book_id' in data or r.sampling.get('rules_updated')) and data.get('book_id') != r.book_id:
        raise Conflict('编码规则已更新，请刷新核对当前规则后再撤回拆分。')
    if r.state != 'active':
        raise Conflict('本批编码已锁定，不能撤回拆分。')
    try:
        token = str(uuid.UUID(str(data.get('token', ''))))
    except (TypeError, ValueError, AttributeError):
        raise ValueError('撤回请求标识无效，请刷新后重试。')
    if (type(data.get('parent_id')) is not int or data['parent_id'] < 1 or
            not isinstance(data.get('branch_token'), str) or len(data['branch_token']) != 64 or
            type(data.get('confirm_coded', False)) is not bool):
        raise ValueError('撤回参数无效，请刷新后重试。')
    if type(data.get('revision')) is not int or type(data.get('parent_revision')) is not int:
        raise Conflict('任务版本已变化，请刷新后再撤回。')
    request_state = {'assignment_id': a.pk, 'parent_id': data['parent_id'],
        'revision': data.get('revision'), 'parent_revision': data.get('parent_revision'),
        'branch_token': data['branch_token'], 'confirm_coded': data.get('confirm_coded', False)}
    replay = AuditEvent.objects.filter(project_id=r.project_id, actor=actor, action='annotation.unsplit',
                                      after__token=token).first()
    if replay:
        if replay.after.get('request') != request_state:
            raise Conflict('同一次撤回请求不能更改内容，请刷新后重试。')
        parent = Assignment.objects.get(pk=data['parent_id'], round=r, coder=actor)
        if parent.superseded or parent.revision != replay.after.get('revision'):
            raise Conflict('撤回后的记录已变化，请刷新后继续。')
        return parent
    if a.superseded or type(data.get('revision')) is not int or data['revision'] != a.revision:
        raise Conflict('当前单元已变化，请刷新后再撤回。')
    parent = Assignment.objects.select_for_update().select_related('unit', 'round').filter(
        pk=data['parent_id'], round=r, coder=actor, superseded=True).first()
    if parent is None or type(data.get('parent_revision')) is not int or data['parent_revision'] != parent.revision:
        raise Conflict('原单元已变化，请刷新后再撤回。')
    event, descendants, live = _split_branch(parent)
    if a.pk not in [entry.pk for entry in live]:
        raise Conflict('当前单元不属于这次拆分，请刷新后重试。')
    if _branch_locked(parent, descendants):
        raise Conflict('这些单元已有协商记录，不能撤回拆分。')
    if _branch_token(parent, event, descendants, a.pk) != data['branch_token']:
        raise Conflict('其他片段已变化，请刷新核对后再撤回拆分。')
    if not data.get('confirm_coded', False) and any(
            entry.revision or entry.primary_id or entry.secondary or entry.note or entry.no_code or
            entry.uncertain or entry.status == 'submitted' for entry in descendants):
        raise Conflict('拆分后的片段已有编码，请确认保留历史并撤回拆分。')
    before = {**annotation_data(parent), 'superseded': True,
        'descendants': [{'id': entry.pk, 'unit_id': entry.unit_id, 'parent_id': entry.parent_id,
            'superseded': entry.superseded, **annotation_data(entry)} for entry in descendants]}
    for entry in live:
        entry.superseded, entry.revision, entry.last_token = True, entry.revision + 1, ''
    Assignment.objects.bulk_update(live, ['superseded', 'revision', 'last_token'])
    rules_changed = AuditEvent.objects.filter(project_id=r.project_id, object_type='Round',
        object_id=str(r.pk), action='round.book_update', id__gt=event.pk).exists()
    if parent.status == 'submitted' and rules_changed:
        parent.status = 'draft'
    parent.superseded, parent.revision, parent.last_token = False, parent.revision + 1, token
    parent.save(update_fields=['superseded', 'revision', 'last_token', 'status', 'updated_at'])
    r.revision += 1
    r.save(update_fields=['revision'])
    audit(r.project, actor, 'annotation.unsplit', parent, before=before,
        after={**annotation_data(parent), 'superseded': False, 'token': token, 'request': request_state,
               'retired_assignments': [entry.pk for entry in live]},
        reason='本人撤回拆分，恢复原单元；片段与已有编码保留在历史中。')
    return parent


@transaction.atomic
def suggest_split(assignment_id, actor, data):
    ref = Assignment.objects.only('round_id').get(pk=assignment_id, coder=actor)
    r = Round.objects.select_for_update().get(pk=ref.round_id)
    a = Assignment.objects.select_related('unit').get(pk=assignment_id, coder=actor)
    if ('book_id' in data or r.sampling.get('rules_updated')) and data.get('book_id') != r.book_id:
        raise Conflict('编码规则已更新，请刷新核对当前规则后再提出建议。')
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
