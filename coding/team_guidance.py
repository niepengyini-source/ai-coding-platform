"""Optional owner invitation journey; joining does not create coding assignments."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .account_forms import InvitationForm, JoinReviewForm
from .models import ProjectInvitation
from .views import access


@require_GET
@login_required
def team_setup(request, project_id):
    project, role = access(request, project_id, ['owner'])
    now = timezone.now()
    invitation = ProjectInvitation.objects.filter(project=project).first()
    valid_invitation = bool(invitation and invitation.enabled and invitation.expires_at > now)
    new_invitation = request.session.pop('new_invitation_' + str(project.pk), None)
    new_code = (new_invitation['code'] if new_invitation and valid_invitation
                and new_invitation['revision'] == invitation.revision else None)
    collaborators = (project.memberships.filter(role__in=['coder', 'reviewer'], user__is_active=True)
                     .exclude(user=project.owner).select_related('user').order_by('id'))
    collaborator_count = collaborators.count()
    requests_page = Paginator(project.join_requests.filter(status='pending').select_related('user'), 20).get_page(
        request.GET.get('join_page'))
    for application in requests_page:
        application.review_form = JoinReviewForm(auto_id=f'team-request-{application.pk}-%s',
            initial={'revision': application.revision, 'role': 'coder'})
    stage = 3 if collaborator_count else 2 if valid_invitation or requests_page.paginator.count else 1
    from .collaborator_invites import historical_collaborators, pending_direct_invites
    sent_invites = pending_direct_invites(project)
    if stage == 1 and sent_invites:
        stage = 2
    return render(request, 'coding/team_setup.html', {
        'project': project, 'role': role, 'team_stage': stage,
        'team_steps': [{'number': i, 'label': label, 'current': i == stage, 'complete': i < stage}
                       for i, label in enumerate(('邀请或选择成员', '成员加入与审核', '开始协作'), 1)],
        'invitation': invitation, 'valid_invitation': valid_invitation, 'new_code': new_code,
        'invite_form': InvitationForm(initial={'revision': invitation.revision if invitation else 0, 'days': 7}),
        'requests_page': requests_page, 'collaborator_count': collaborator_count,
        'collaborators': collaborators[:20],
        'has_team_tasks': (project.rounds.exclude(kind='quick').filter(assignments__superseded=False).exists()
                           or project.rounds.exclude(kind='quick').filter(source_tasks__isnull=False).exists()),
        'historical_members': historical_collaborators(project, request.user),
        'sent_direct_invites': sent_invites,
        'now': now,
    })
