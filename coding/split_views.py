"""Reversible personal splitting; all material and annotation history is retained."""
from django.contrib.auth.decorators import login_required
from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Assignment
from .splitting import restore_personal_split
from .views import access, api_error, body_json


@require_POST
@login_required
def assignment_unsplit(request, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related('round'), pk=assignment_id, coder=request.user)
    access(request, a.round.project_id, ['owner', 'reviewer', 'coder'])
    try:
        parent = restore_personal_split(a.pk, request.user, body_json(request))
        response = JsonResponse({'ok': True, 'next_url': reverse('workbench', args=[parent.round_id]) +
                                 '?a=' + str(parent.pk)})
        response['Cache-Control'] = 'no-store'
        return response
    except (ValueError, OperationalError) as error:
        return api_error(error)
