"""Simple entry pages; membership remains the source of project access."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce
from django.shortcuts import render

from .models import Assignment


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


def group_context(request):
    return {
        'joined_memberships': _memberships(request.user).exclude(role='owner'),
        'applications': Paginator(request.user.project_join_requests.select_related('project'), 10)
            .get_page(request.GET.get('requests_page')),
    }
