"""Present the next step without changing research or assignment state."""
from django import template
from django.db.models import Q
from django.urls import reverse

from coding.guidance import preparation_guidance, project_guidance
from coding.models import Unit


register = template.Library()
GUIDED_PAGES = {'project', 'materials', 'units', 'codebook', 'progress',
                'workbench', 'review', 'disagreements', 'reliability', 'book_import', 'quick_start'}
PREPARATION_PAGES = {'project', 'materials', 'units', 'codebook', 'book_import', 'quick_start'}
STAGE_BAR_EXCLUDED = {'workbench', 'guide', 'login', 'register', 'home', 'projects',
                      'project_new', 'join_group', 'password_change', 'password_change_done'}


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
    page = request.resolver_match.url_name
    book = context.get('book')
    workflow['preparation'] = None
    if page in PREPARATION_PAGES and not (page == 'codebook' and book and book.pk != workflow['book_id']):
        workflow['preparation'] = preparation_guidance(project, role, workflow, page,
            material_preview=page == 'materials' and bool(context.get('draft')),
            book_preview=bool(context.get('preview')))
    return workflow


def _reviewable_round(project, role, current_round=None):
    def eligible(r):
        if r is None or r.project_id != project.pk or r.kind == 'quick':
            return False
        if role == 'viewer' and r.state != 'closed':
            return False
        sync_shared = r.sampling.get('collab_mode') == 'sync' and r.sampling.get('share_annotations')
        return r.state != 'active' or r.kind == 'training' or bool(sync_shared)

    if eligible(current_round):
        return current_round
    for r in project.rounds.exclude(kind='quick').order_by('-id'):
        if eligible(r):
            return r
    return None


@register.simple_tag(takes_context=True)
def project_stage_bar(context):
    request = context.get('request')
    project, role = context.get('project'), context.get('role')
    if not request or not project or not role or not request.user.is_authenticated:
        return None
    if not request.resolver_match or request.resolver_match.url_name in STAGE_BAR_EXCLUDED:
        return None
    page = request.resolver_match.url_name
    manager = role in ('owner', 'reviewer')
    latest_book = project.codebooks.first()
    has_units = (Unit.objects.filter(record__document__project=project, active=True).exists()
                 or project.documents.exists())
    has_codes = bool(latest_book and latest_book.codes.exists())
    has_rounds = project.rounds.exists()
    review_round = _reviewable_round(project, role, context.get('round'))
    page_stage = (
        1 if page in {'materials', 'units', 'document_prepare'} else
        2 if page in {'codebook', 'book_import', 'source_book_import', 'code_references'} else
        3 if page in {'quick_start', 'source_start', 'round_new', 'progress', 'history', 'history_detail', 'history_reopen'} else
        4 if page in {'disagreements', 'review', 'reliability'} else
        5 if page in {'exports', 'archive'} else 0
    )
    workflow = context.get('workflow')
    rec_stage = 0
    if page == 'project' and workflow:
        wp = workflow.get('phase', 0)
        rec_stage = (1 if wp == 1 else 2 if wp == 2 else 3 if wp in (3, 4) and workflow.get('round_state') != 'review'
                     else 4 if workflow.get('round_state') == 'review' else 5 if wp == 5 else 3)
    steps = [
        {'number': 1, 'label': '材料准备',
         'url': reverse('materials' if manager else 'units', args=[project.pk]),
         'done': has_units},
        {'number': 2, 'label': '编码规则',
         'url': reverse('codebook', args=[project.pk, latest_book.pk]) if latest_book else reverse('project', args=[project.pk]),
         'done': has_codes},
        {'number': 3, 'label': '编码进度' if role == 'viewer' else '开始编码',
         'url': reverse('progress' if role == 'viewer' else 'coding_entry', args=[project.pk]),
         'done': has_rounds},
        {'number': 4, 'label': '分歧与协商',
         'url': reverse('disagreements', args=[review_round.pk]) if review_round else '',
         'done': bool(review_round and review_round.state == 'closed'),
         'disabled_hint': '多人轮次全部提交并开放协商后可查看（独立盲编阶段严格隔离他人答案）'},
        {'number': 5, 'label': '报告与导出',
         'url': reverse('exports', args=[project.pk]),
         'done': False},
    ]
    for item in steps:
        item['current'] = item['number'] == page_stage
        item['recommended'] = item['number'] == rec_stage
    return {'steps': steps}
