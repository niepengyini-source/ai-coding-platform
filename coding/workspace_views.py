"""Simple entry pages; membership remains the source of project access."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.urls import reverse

from .models import Assignment, AuditEvent, Membership


def _memberships(user):
    return (user.membership_set.select_related('project')
            .annotate(project_updated_at=Coalesce(Max('project__auditevent__created_at'),
                                                 'project__created_at'))
            .order_by('-project_updated_at', '-project_id'))


@login_required
def entry(request):
    return render(request, 'coding/home.html', {'workspace_entry': True})


@login_required
def projects(request):
    memberships = list(_memberships(request.user))
    _add_coding_progress(memberships, request.user)
    _add_collaboration_cards(memberships, request.user)
    return render(request, 'coding/projects.html', {
        'memberships': memberships})


def _add_coding_progress(memberships, user):
    # One current personal batch per card, rather than double-counting repeated rounds.
    rows = (Assignment.objects.filter(coder=user, superseded=False,
                round__project_id__in=[membership.project_id for membership in memberships])
        .values('round_id', 'round__project_id', 'round__name', 'round__state')
        .annotate(total=Count('id'), submitted=Count('id', filter=Q(status='submitted')),
                  latest=Max('updated_at'), latest_draft=Max('updated_at', filter=Q(status='draft'))))
    selected = {}
    for row in rows:
        active = row['round__state'] == 'active'
        priority = (active, active and row['latest_draft'] is not None,
                    row['latest_draft'] if active and row['latest_draft'] else row['latest'], row['round_id'])
        previous = selected.get(row['round__project_id'])
        if previous is None or priority > previous[0]:
            row['complete'] = row['submitted'] == row['total']
            selected[row['round__project_id']] = (priority, row)
    for membership in memberships:
        progress = selected.get(membership.project_id)
        membership.coding_progress = progress[1] if progress else None


def _add_collaboration_cards(memberships, user):
    """Current-round quantities and safe entry links, fetched in bulk for the list."""
    project_ids = [membership.project_id for membership in memberships]
    if not project_ids:
        return
    member_counts = dict(Membership.objects.filter(project_id__in=project_ids, user__is_active=True)
                         .values('project_id').annotate(total=Count('id')).values_list('project_id', 'total'))
    eligible = Membership.objects.filter(project_id=OuterRef('round__project_id'),
                                          user_id=OuterRef('coder_id'), user__is_active=True).exclude(role='viewer')
    rows = (Assignment.objects.filter(round__project_id__in=project_ids, superseded=False).filter(Exists(eligible))
            .values('round_id', 'round__project_id', 'round__name', 'round__kind', 'round__state',
                    'round__created_at', 'coder_id')
            .annotate(total=Count('id'), submitted=Count('id', filter=Q(status='submitted'))).order_by())
    rounds = {}
    for row in rows:
        entry = rounds.setdefault(row['round_id'], {
            'round_id': row['round_id'], 'project_id': row['round__project_id'], 'name': row['round__name'],
            'kind': row['round__kind'], 'state': row['round__state'], 'created_at': row['round__created_at'],
            'total': 0, 'submitted': 0, 'coders': 0, 'my_total': 0, 'my_submitted': 0,
        })
        entry['total'] += row['total']
        entry['submitted'] += row['submitted']
        entry['coders'] += 1
        if row['coder_id'] == user.pk:
            entry['my_total'], entry['my_submitted'] = row['total'], row['submitted']
    by_project = {}
    for entry in rounds.values():
        by_project.setdefault(entry['project_id'], []).append(entry)
    recent = {row['project_id']: row for row in AuditEvent.objects.filter(
        project_id__in=project_ids, action__in=['annotation.save', 'annotation.submit'])
        .values('project_id').annotate(team=Max('created_at'), mine=Max('created_at', filter=Q(actor=user)))}

    def priority(entry):
        active = entry['state'] == 'active'
        return (active, active and entry['my_total'] > entry['my_submitted'],
                active and entry['total'] > entry['submitted'],
                entry['created_at'], entry['round_id'])

    for membership in memberships:
        entries = by_project.get(membership.project_id, [])
        team = sorted([entry for entry in entries if entry['kind'] != 'quick'], key=priority, reverse=True)
        mine = [entry for entry in team if entry['my_total']]
        personal = sorted([entry for entry in entries if entry['kind'] == 'quick' and entry['my_total']],
                          key=priority, reverse=True)
        available = (team if membership.role == 'owner' else mine) + personal
        active = [entry for entry in available if entry['state'] == 'active']
        current = active[0] if active else max(available, key=priority) if available else None
        membership.member_count = member_counts.get(membership.project_id, 0)
        membership.collaboration_label = ('多人协作' if any(entry['coders'] > 1 for entry in team) else
                                          '单人编码' if entries else '')
        membership.card_progress = None
        membership.assignment_coder_count = current['coders'] if current else 0
        if current:
            is_team = membership.role == 'owner' and current['kind'] != 'quick'
            total = current['total'] if is_team else current['my_total']
            submitted = current['submitted'] if is_team else current['my_submitted']
            membership.card_progress = {
                'name': current['name'], 'total': total, 'submitted': submitted,
                'complete': total > 0 and submitted == total,
                'label': '团队进度' if is_team else '我的任务进度',
                'state': current['state'],
                'state_label': {'active': '编码中', 'review': '协商中', 'closed': '已结束'}[current['state']],
            }
        activity = recent.get(membership.project_id, {})
        membership.last_coded_at = activity.get('team' if membership.role == 'owner' else 'mine')
        membership.activity_label = '团队最近编码' if membership.role == 'owner' else '我上次编码'
        active_mine = [entry for entry in mine if entry['state'] == 'active']
        team_entry = active_mine[0] if active_mine else mine[0] if mine else None
        membership.team_task_url = (reverse('project', args=[membership.project_id]) + '#project-rounds'
                                    if len(active_mine) > 1 else
                                    reverse('workbench', args=[team_entry['round_id']]) if team_entry else '')
        membership.team_task_label = ('继续小组任务' if any(entry['my_total'] > entry['my_submitted'] for entry in active_mine)
                                      else '查看小组任务')
        membership.waiting_for_assignment = (membership.role != 'viewer' and not mine and not personal
                                             and membership.member_count > 1)
        membership.personal_task_url = (reverse('workbench', args=[personal[0]['round_id']]) if personal else
                                        reverse('quick_start', args=[membership.project_id]))
        membership.personal_task_label = ('查看个人编码' if personal and (personal[0]['state'] != 'active'
                                           or personal[0]['my_total'] == personal[0]['my_submitted']) else
                                           '继续个人编码' if personal else '开始个人编码')


def group_context(request):
    return {
        'joined_memberships': _memberships(request.user).exclude(role='owner'),
        'applications': Paginator(request.user.project_join_requests.select_related('project'), 10)
            .get_page(request.GET.get('requests_page')),
    }
