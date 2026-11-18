from django.contrib.auth.decorators import login_required
from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .ai_recommendations import payload, recommend
from .models import Assignment
from .views import api_error, body_json


@require_POST
@login_required
def assignment_recommend(request, assignment_id):
    assignment = get_object_or_404(Assignment.objects.select_related('round__book', 'unit__record'),
                                   pk=assignment_id, coder=request.user)
    try:
        data = body_json(request)
        response = JsonResponse({'recommendation': payload(recommend(assignment, request.user, data.get('fingerprint')))})
    except (TimeoutError, ConnectionError):
        response = JsonResponse({'error': '推荐暂时无法完成，请重试或继续人工编码。'}, status=503)
    except (ValueError, OperationalError) as error:
        response = api_error(error)
    response['Cache-Control'] = 'no-store'
    return response
