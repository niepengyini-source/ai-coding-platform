"""Explicit, audited codebook updates without rewriting archived rules."""
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Assignment, AuditEvent, Codebook, Membership, Project, Round
from .services import Conflict, annotation_data, audit, clone_book


def _can_update(project, actor):
    if not actor.is_active or not Membership.objects.filter(
            project=project, user=actor, role__in=['owner', 'reviewer']).exists():
        raise PermissionDenied


def _check_revision(actual, expected, message):
    if type(expected) is not int or actual != expected:
        raise Conflict(message)


@transaction.atomic
def begin_update(project, actor, source, revision):
    Project.objects.select_for_update().get(pk=project.pk)
    _can_update(project, actor)
    source = Codebook.objects.select_for_update().filter(project=project, pk=source.pk).first()
    if not source:
        raise ValueError('请选择本项目的当前编码本。')
    _check_revision(source.revision, revision, '编码本已变化，请刷新后再更新。')
    latest = project.codebooks.select_for_update().first()
    # A double-click or second window should resume the same editable copy.
    if source.frozen and latest and not latest.frozen and AuditEvent.objects.filter(
            project=project, action='codebook.new_version', object_type='Codebook',
            object_id=str(latest.pk), after__from=source.pk).exists():
        return latest
    if not latest or latest.pk != source.pk:
        raise Conflict('当前编码本已变化，请打开最新编码本后再更新。')
    if not source.frozen:
        return source
    return clone_book(project, actor, source, source.title, source.policy, '在现有规则基础上更新')


def _code_mapping(source_id, target, events, book_ids):
    if source_id not in book_ids:
        raise ValueError('所选批次的规则不属于当前项目。')
    steps, seen, cursor = [], set(), target.pk
    while cursor != source_id:
        if cursor in seen:
            raise ValueError('编码本的更新来源无法核对，请保留原批次并联系负责人。')
        seen.add(cursor)
        event = events.get(str(cursor))
        data = event.after if event else {}
        parent = data.get('from')
        mapping = data.get('code_map')
        if type(parent) is not int or parent not in book_ids or not isinstance(mapping, dict):
            raise ValueError('无法核对旧编码本与当前规则的对应关系，请保留原批次或重新编码。')
        steps.append(mapping)
        cursor = parent
    mapping = {code_id: code_id for code_id in Codebook.objects.get(pk=source_id).codes.values_list('pk', flat=True)}
    for step in reversed(steps):
        mapping = {old: step.get(str(current)) for old, current in mapping.items()}
        # A missing lineage entry is not guessed from a renamed code key.
        mapping = {old: current for old, current in mapping.items() if type(current) is int and current > 0}
    return mapping


def _snapshot(r, assignments):
    return {'book_id': r.book_id, 'book_version': r.book.version, 'cycle': r.cycle, 'revision': r.revision,
            'assignments': [{'id': entry.pk, 'unit_id': entry.unit_id, 'coder_id': entry.coder_id,
                             'parent_id': entry.parent_id, 'superseded': entry.superseded,
                             'updated_at': entry.updated_at.isoformat(),
                             **annotation_data(entry)} for entry in assignments]}


@transaction.atomic
def apply_book_update(project, actor, book, revision, round_revisions):
    Project.objects.select_for_update().get(pk=project.pk)
    _can_update(project, actor)
    book = Codebook.objects.select_for_update().filter(project=project, pk=book.pk).first()
    if not book or project.codebooks.first().pk != book.pk:
        raise Conflict('当前编码本已变化，请刷新后再更新批次。')
    _check_revision(book.revision, revision, '编码本已变化，请刷新核对规则后再更新批次。')
    if not isinstance(round_revisions, dict) or not round_revisions or any(
            type(pk) is not int or not 0 < pk < 2 ** 63 for pk in round_revisions):
        raise ValueError('请选择要更新的进行中批次。')
    rounds = list(Round.objects.select_for_update().filter(
        project=project, pk__in=round_revisions).order_by('pk'))
    if len(rounds) != len(round_revisions):
        raise ValueError('请选择本项目的进行中批次。')
    for r in rounds:
        _check_revision(r.revision, round_revisions[r.pk], '编码批次已变化，请刷新后重新选择。')
        if r.state != 'active':
            raise ValueError('只能更新进行中的批次；协商中或已归档的批次保持原规则。')
    rounds = [r for r in rounds if r.book_id != book.pk]
    if not rounds:
        raise ValueError('所选批次已使用当前编码本，无需更新。')
    target_ids = set(book.codes.values_list('pk', flat=True))
    if not target_ids:
        raise ValueError('请先添加至少一个代码，再更新批次。')
    events = {event.object_id: event for event in AuditEvent.objects.filter(
        project=project, action='codebook.new_version', object_type='Codebook').order_by('id')}
    book_ids = set(project.codebooks.values_list('pk', flat=True))
    changed = []
    for r in rounds:
        mapping = _code_mapping(r.book_id, book, events, book_ids)
        entries = list(Assignment.objects.select_for_update().filter(round=r).order_by('pk'))
        for entry in entries:
            used = ([entry.primary_id] if entry.primary_id else []) + entry.secondary
            if any(type(code_id) is not int or mapping.get(code_id) not in target_ids for code_id in used):
                raise ValueError('所选批次仍使用已删除或无法对应的代码，请保留相关代码后再更新。')
            if book.policy == 'single' and entry.secondary:
                raise ValueError('所选批次含有次代码，不能直接改用单标签规则。')
        changed.append((r, entries, mapping, _snapshot(r, entries)))
    # All selected batches are checked before any answer is changed.
    for r, entries, mapping, before in changed:
        previous = {entry['id']: entry for entry in before['assignments']}
        for entry in entries:
            entry.primary_id = mapping[entry.primary_id] if entry.primary_id else None
            entry.secondary = [mapping[code_id] for code_id in entry.secondary]
            if entry.status == 'submitted' and not entry.superseded:
                entry.status = 'draft'
            if entry.revision:
                entry.revision += 1
            entry.last_token = ''
        Assignment.objects.bulk_update(entries, ['primary', 'secondary', 'status', 'revision', 'last_token'])
        r.book = book
        r.cycle += 1
        r.revision += 1
        r.sampling = {**r.sampling, 'rules_updated': True}
        r.save(update_fields=['book', 'cycle', 'revision', 'sampling'])
        AuditEvent.objects.bulk_create([AuditEvent(project=project, actor=actor,
            action='annotation.rules_update', object_type='Assignment', object_id=str(entry.pk),
            before={**previous[entry.pk], 'book_id': before['book_id'], 'book_version': before['book_version']},
            after={**annotation_data(entry), 'book_id': book.pk, 'book_version': book.version},
            reason='编码规则更新，保留原标签并重新核对。')
            for entry in entries if previous[entry.pk]['revision']])
        audit(project, actor, 'round.book_update', r, before=before, after=_snapshot(r, entries),
              reason='更新规则并保留已有答案，已提交记录退回草稿等待复核。')
    if not book.frozen:
        book.frozen = True
        book.revision += 1
        book.save(update_fields=['frozen', 'revision'])
        audit(project, actor, 'codebook.freeze', book, after={'version': book.version}, reason='应用规则更新到进行中批次。')
    return len(changed)
