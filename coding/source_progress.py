"""Source-document quantities stay separate from fixed-unit progress."""
from django.db.models import Count, Max, Q
from django.urls import reverse
from .source_models import SourceCodingTask


def project_rows(project, user, role):
    tasks = SourceCodingTask.objects.filter(round__project=project)
    if role not in ('owner', 'reviewer'):
        tasks = tasks.filter(coder=user)
    # Personal source rounds are always counted for their own coder only.
    tasks = tasks.filter(~Q(round__kind='quick') | Q(coder=user))
    rows = list(tasks.values('round_id', 'round__name', 'round__state', 'round__kind', 'round__sampling').annotate(
        total=Count('id', distinct=True), done=Count('id', filter=Q(completed=True), distinct=True),
        coders=Count('coder_id', distinct=True),
        annotations=Count('annotations', distinct=True)).order_by('-round_id'))
    for row in rows:
        sampling = row.pop('round__sampling') or {}
        row['collab_mode'] = sampling.get('collab_mode', 'independent')
        row['can_review'] = row['round__kind'] != 'quick' and (
            row['round__state'] != 'active' or row['round__kind'] == 'training'
            or (sampling.get('collab_mode') == 'sync' and sampling.get('share_annotations'))
        )
    return rows


def attach_project_cards(memberships, user):
    ids = [m.project_id for m in memberships]
    rows = (SourceCodingTask.objects.filter(round__project_id__in=ids, coder=user)
        .values('round_id', 'round__project_id', 'round__name', 'round__state', 'round__kind')
        .annotate(total=Count('id', distinct=True), done=Count('id', filter=Q(completed=True), distinct=True), latest=Max('updated_at'),
                  last_coded=Max('annotations__updated_at')).order_by('-round_id'))
    latest = {}
    for row in rows:
        previous = latest.get(row['round__project_id'])
        if not previous or (row['round__state'] == 'active', row['latest']) > (previous['round__state'] == 'active', previous['latest']):
            latest[row['round__project_id']] = row
    for member in memberships:
        row = latest.get(member.project_id)
        previous = member.coding_progress
        if row and (not previous or (row['round__state'] == 'active', row['latest']) >=
                    (previous['round__state'] == 'active', previous['latest'])) and (
                not member.card_progress or member.card_progress['label'] != '团队进度'):
            member.card_progress = {'name': row['round__name'], 'total': row['total'], 'submitted': row['done'],
                'complete': row['total'] > 0 and row['done'] == row['total'], 'label': '我的材料进度',
                'state': row['round__state'], 'state_label': '已结束' if row['round__state'] == 'closed' else '编码中'}
        if row and (not previous or (row['round__state'] == 'active', row['latest']) >=
                    (previous['round__state'] == 'active', previous['latest'])):
            member.personal_task_url = reverse('workbench', args=[row['round_id']])
            member.personal_task_label = '继续原文编码' if row['round__state'] == 'active' else '查看原文编码'
            member.waiting_for_assignment = False
            if not member.collaboration_label:
                member.collaboration_label = '团队整篇编码' if row['round__kind'] != 'quick' else '单人编码'
            if not member.assignment_coder_count:
                member.assignment_coder_count = 1
        if row and row['last_coded'] and (not member.last_coded_at or row['last_coded'] > member.last_coded_at):
            member.last_coded_at = row['last_coded']
