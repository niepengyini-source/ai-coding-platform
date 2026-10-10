"""Personal entry, bounded progress queries and searchable saved annotations."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import Assignment, AuditEvent, Codebook, Membership, Project, Round
from .services import Conflict, annotation_data, audit, create_round


def visible_rounds(project, user, role):
    rounds = project.rounds.all()
    if role not in ('owner', 'reviewer'):
        rounds = rounds.filter(~Q(kind='quick') | Q(started_by=user))
    return rounds


@transaction.atomic
def start_quick_coding(project, actor, book_id, token):
    Project.objects.select_for_update().get(pk=project.pk)
    if not Membership.objects.filter(project=project, user=actor, role__in=['owner', 'reviewer', 'coder']).exists():
        raise PermissionDenied
    try:
        token = str(uuid.UUID(str(token)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError('开始标识已失效，请刷新后重试。')
    replay = Round.objects.filter(project=project, kind='quick', started_by=actor,
                                  sampling__quick_start_token=token).first()
    if replay:
        if replay.book_id != book_id:
            raise Conflict('同一次开始请求不能改用其他规则，请刷新后重新选择。')
        return replay, False
    book = Codebook.objects.get(project=project, pk=book_id)
    existing = Round.objects.filter(project=project, kind='quick', started_by=actor,
                                    book=book, state='active').order_by('-id').first()
    if existing:
        return existing, False
    r = create_round(project, actor, book.pk, '个人编码 · v' + str(book.version), 'quick',
                     [actor.pk], 0, 2026, 'all', 0)
    r.sampling['quick_start_token'] = token
    r.save(update_fields=['sampling'])
    audit(project, actor, 'quick.start', r, after={'book_version': book.version, 'personal': True})
    return r, True


@transaction.atomic
def reopen_from_history(assignment_id, actor, revision, reason):
    reference = Assignment.objects.only('round_id').get(pk=assignment_id, coder=actor)
    r = Round.objects.select_for_update().get(pk=reference.round_id)
    a = Assignment.objects.select_for_update().get(pk=assignment_id, coder=actor)
    membership = Membership.objects.get(project=r.project, user=actor)
    personal = r.kind == 'quick' and r.started_by_id == actor.pk
    if membership.role not in ('owner', 'reviewer') and not (personal and membership.role == 'coder'):
        raise PermissionDenied('多人任务已提交后，需要负责人或复核者退回。')
    if r.state != 'active' or a.status != 'submitted':
        raise ValueError('仅编码中已提交的记录可以退回，已归档记录不能改写。')
    if revision != a.revision:
        raise Conflict('这条记录已变化，请刷新历史页面后再操作。')
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise ValueError('请填写修改原因，最多2000字。')
    before = annotation_data(a)
    a.status, a.last_token, a.revision = 'draft', '', a.revision + 1
    a.save(update_fields=['status', 'last_token', 'revision', 'updated_at'])
    audit(r.project, actor, 'annotation.reopen', a, before=before, after=annotation_data(a), reason=reason)
    return a


@transaction.atomic
def close_quick_coding(round_id, actor, revision):
    r = Round.objects.select_for_update().get(pk=round_id, kind='quick', started_by=actor)
    if not Membership.objects.filter(project=r.project, user=actor, role__in=['owner', 'reviewer', 'coder']).exists():
        raise PermissionDenied
    if r.state == 'closed':
        return r
    if revision != r.revision:
        raise Conflict('编码批次状态已变化，请刷新后再归档。')
    if r.state != 'active' or not r.assignments.exists() or r.assignments.filter(status='draft').exists():
        raise ValueError('请先提交本批全部个人编码，再完成归档。')
    if r.assignments.exclude(coder=actor).exists() or r.decisions.exists() or r.reports.exists():
        raise ValueError('本批包含其他工作流程，不能按个人快速编码归档。')
    r.state, r.revision = 'closed', r.revision + 1
    r.save(update_fields=['state', 'revision'])
    audit(r.project, actor, 'quick.close', r, before={'state': 'active'},
          after={'state': 'closed', 'personal': True}, reason='本人完成并归档个人编码，不是多人协商定稿。')
    return r


def counts(queryset):
    values = queryset.aggregate(total=Count('id'), submitted=Count('id', filter=Q(status='submitted')),
                                saved=Count('id', filter=Q(status='draft', revision__gt=0)))
    values['unstarted'] = values['total'] - values['submitted'] - values['saved']
    values['remaining'] = values['total'] - values['submitted']
    values['percent'] = round(values['submitted'] * 100 / values['total'], 1) if values['total'] else 0
    return values


def round_counts(project, user):
    return (Assignment.objects.filter(round__project=project, coder=user)
            .values('round_id', 'round__name', 'round__kind', 'round__state', 'round__book__version')
            .annotate(total=Count('id'), submitted=Count('id', filter=Q(status='submitted')),
                      saved=Count('id', filter=Q(status='draft', revision__gt=0)))
            .order_by('-round_id'))


def search_history(project, user, cleaned):
    # Searching a private history never adds other coders' answers to the query.
    entries = Assignment.objects.filter(round__project=project, coder=user, revision__gt=0)
    query = cleaned.get('q', '')
    suffix = 'iexact' if cleaned.get('mode') == 'exact' else 'icontains'
    if query:
        condition = Q(**{'unit__text__' + suffix: query}) | Q(**{'note__' + suffix: query})
        for field in ('primary__key', 'primary__name', 'unit__record__document__name', 'round__name'):
            condition |= Q(**{field + '__' + suffix: query})
        if query.isascii() and query.isdecimal() and 0 < len(query) <= 18:
            condition |= Q(unit_id=int(query)) | Q(pk=int(query))
        entries = entries.filter(condition)
    for field, lookup in [('unit_id', 'unit_id'), ('assignment_id', 'id'),
                          ('book_version', 'round__book__version'), ('status', 'status')]:
        if cleaned.get(field):
            entries = entries.filter(**{lookup: cleaned[field]})
    if cleaned.get('round_id'):
        entries = entries.filter(round=cleaned['round_id'])
    if cleaned.get('code'):
        entries = entries.filter(Q(primary__key__iexact=cleaned['code']) | Q(primary__name__iexact=cleaned['code']))
    if cleaned.get('filename'):
        entries = entries.filter(unit__record__document__name__iexact=cleaned['filename'])
    if cleaned.get('uncertain'):
        entries = entries.filter(uncertain=cleaned['uncertain'] == 'yes')
    if cleaned.get('entry_kind') == 'quick':
        entries = entries.filter(round__kind='quick')
    elif cleaned.get('entry_kind') == 'assigned':
        entries = entries.exclude(round__kind='quick')
    start, end = cleaned.get('start'), cleaned.get('end')
    if start or end:
        if start:
            entries = entries.filter(updated_at__gte=start)
        if end:
            entries = entries.filter(updated_at__lt=end + timedelta(minutes=1))
    else:
        period = cleaned.get('period')
        days = {'today': 1, 'week': 7, 'month': 30}.get(period)
        if days:
            today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
            entries = entries.filter(updated_at__gte=today - timedelta(days=days - 1),
                                      updated_at__lt=today + timedelta(days=1))
    return entries.select_related('round__book', 'primary', 'unit__record__document').order_by('-updated_at', '-id')


def history_permissions(a, user, role):
    active = a.coder_id == user.pk and a.round.state == 'active' and role in ('owner', 'reviewer', 'coder')
    return {'editable': active and a.status == 'draft',
            'reopenable': active and a.status == 'submitted' and
            (role in ('owner', 'reviewer') or (a.round.kind == 'quick' and a.round.started_by_id == user.pk))}


def annotation_events(a):
    return AuditEvent.objects.filter(project=a.round.project, object_type='Assignment', object_id=str(a.pk),
                                     action__startswith='annotation.').select_related('actor').order_by('-created_at', '-id')
