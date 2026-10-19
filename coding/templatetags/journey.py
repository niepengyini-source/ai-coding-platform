"""Present the next step without changing research or assignment state."""
from django import template

from coding.guidance import project_guidance


register = template.Library()
GUIDED_PAGES = {'project', 'materials', 'units', 'codebook', 'progress',
                'workbench', 'review', 'disagreements', 'reliability'}


@register.simple_tag(takes_context=True)
def project_workflow(context):
    request = context.get('request')
    project, role = context.get('project'), context.get('role')
    if not request or not project or not request.user.is_authenticated:
        return None
    if not request.resolver_match or request.resolver_match.url_name not in GUIDED_PAGES:
        return None
    workflow = project_guidance(project, request.user, role, round_=context.get('round'))
    workflow['show_action'] = workflow['next_url'].split('?')[0] != request.path
    return workflow
