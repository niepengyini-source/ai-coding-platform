"""Simple entry pages; membership remains the source of project access."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max
from django.db.models.functions import Coalesce
from django.shortcuts import render


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
    return render(request, 'coding/projects.html', {
        'workspace_entry': True, 'memberships': _memberships(request.user)})


def group_context(request):
    return {
        'joined_memberships': _memberships(request.user).exclude(role='owner'),
        'applications': Paginator(request.user.project_join_requests.select_related('project'), 10)
            .get_page(request.GET.get('requests_page')),
    }
