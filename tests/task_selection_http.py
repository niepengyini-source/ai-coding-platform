"""Real HTTP/CSRF checks for task selection and optional invitations, fictional UI DB only."""
import json
import re
import uuid
from html import unescape

import journey_http as journey
from django.conf import settings
from django.contrib.auth import get_user_model
from coding.models import Membership, ProjectJoinRequest, Round, Unit


def hidden(text, name):
    match = re.search(r'name="' + name + r'" value="([^\"]+)"', text)
    journey.check('form field ' + name, match is not None)
    return unescape(match[1])


def register(suffix, password):
    session = journey.Session()
    session.page('/accounts/register/')
    name = 'qa_arrangement_' + suffix
    response = session.send('/accounts/register/', {'username': name, 'password1': password, 'password2': password})
    journey.check('register fixture', response[0] == 302)
    user = get_user_model().objects.get(username=name)
    session.login(user, password)
    return user, session


def main():
    suffix, password = uuid.uuid4().hex[:10], uuid.uuid4().hex + '-Arrange!'
    owner, session = register('owner_' + suffix, password)
    member, member_session = register('member_' + suffix, password)
    project = journey.create_project(session, owner, '任务安排与邀请 · 虚构验收 · ' + suffix)
    book, _ = journey.prepare_project(session, project)
    prefix = f'/p/{project.pk}'
    team_url = prefix + '/team-setup/'
    page = session.page(team_url, project)
    journey.check('optional invitation step one', 'data-team-stage="1"' in page)
    journey.check('invitation needs CSRF', session.send(prefix + '/invitation/',
        {'revision': 0, 'days': 7, 'action': 'issue', 'team_setup': 'yes'}, csrf=False)[0] == 403)
    response = session.send(prefix + '/invitation/', {'revision': 0, 'days': 7, 'action': 'issue', 'team_setup': 'yes'})
    journey.check('invitation returns to guided branch', response[0] == 302 and response[2]['Location'] == team_url)
    page = session.page(team_url, project)
    code = re.search(r'id="team-invitation-code"[^>]+value="([^\"]+)"', page)[1]
    journey.check('invitation step two', 'data-team-stage="2"' in page)
    member_session.page('/groups/join/')
    response = member_session.send('/groups/join/', {'invitation_code': code, 'note': '虚构协作者'})
    journey.check('member requests without gaining membership', response[0] == 302
        and not Membership.objects.filter(project=project, user=member).exists())
    application = ProjectJoinRequest.objects.get(project=project, user=member)
    page = session.page(team_url, project)
    journey.check('owner sees application and code is not redisplayed', member.username in page and code not in page)
    response = session.send(prefix + f'/requests/{application.pk}/review/', {
        'revision': application.revision, 'action': 'approve', 'role': 'coder', 'decision_note': '', 'team_setup': 'yes'})
    journey.check('approval returns to guide without creating tasks', response[0] == 302
        and response[2]['Location'] == team_url and not project.rounds.exists())
    page = session.page(team_url, project)
    journey.check('owner can code and see member progress', 'data-team-stage="3"' in page
        and '开始／继续编码' in page and '查看成员进度' in page)
    journey.check('member sees joined project', project.name in member_session.page('/projects/'))

    raw = ('message_id,group_id,created_at,speaker_id,content\n' + '\n'.join(
        f'SELECT{i},FG{i % 2},2026-10-20 10:{i % 60:02d},FS1,虚构批量材料{i}：' + ('计划' if i % 2 else '检查')
        for i in range(1, 61))).encode('utf-8')
    response = session.send(prefix + '/materials/', upload=('selection-fiction.csv', raw))
    draft = hidden(response[1].decode(), 'draft_id')
    response = session.send(prefix + '/materials/', {'draft_id': draft, 'sheet_name': '',
        'text_column': 'content', 'context_column': 'group_id'})
    journey.check('bulk fixture imported through UI', response[0] == 302
        and Unit.objects.filter(record__document__project=project, active=True).count() == 62)
    arrange_url, choices_url = prefix + '/rounds/new/', prefix + '/rounds/units/'
    page = session.page(arrange_url, project)
    journey.check('three selection modes in guided form', all(label in page for label in ('安排什么工作', '谁来编码', '选哪些材料')))
    response = session.send(choices_url, payload={})
    choices = json.loads(response[1])
    journey.check('choices paginate and preserve positions', choices['count'] == 62
        and [row['position'] for row in choices['results']] == list(range(1, 41)))
    pool_hash = choices['pool_hash']
    filtered = json.loads(session.send(choices_url, payload={'q': '计划', 'all': True})[1])
    journey.check('all matching includes noncontiguous and beyond first page', filtered['count'] == 30
        and len(filtered['ids']) == 30 and filtered['results'][0]['position'] == 3)
    journey.check('empty selection remains empty', json.loads(session.send(choices_url, payload={'selected': []})[1])['count'] == 0)
    journey.check('metadata filter returns exact original data', json.loads(session.send(choices_url,
        payload={'metadata_field': 'group_id', 'metadata_value': 'FG0', 'all': True})[1])['count'] == 30)
    journey.check('selector CSRF required', session.send(choices_url, payload={}, csrf=False)[0] == 403)

    unit_index = list(Unit.objects.filter(record__document__project=project, active=True).values_list('pk', flat=True))
    for mode, count, positions in [('sequential', 4, [7, 8, 9, 10]), ('manual', 0, [3, 8, 15, 16, 17, 47]), ('random', 5, None)]:
        values = {'action': 'preview', 'pool_hash': pool_hash, 'name': '虚构验收-' + mode, 'kind': 'pilot',
            'book_id': book.pk, 'coder_ids': [owner.pk, member.pk], 'selection_mode': mode, 'start_position': 7,
            'count': count, 'seed': 2026, 'distribution': 'all', 'double_percent': 25,
            'manual_positions': '3、8、15-17、47', 'stratify': '', 'unit_ids': ''}
        before = project.rounds.count()
        response = session.send(arrange_url, values)
        text = response[1].decode()
        journey.check(mode + ' preview does not allocate', response[0] == 200 and project.rounds.count() == before)
        token = hidden(text, 'preview_token')
        journey.check(mode + ' preview has both member counts', owner.username in text and member.username in text)
        edit = session.send(arrange_url, {'action': 'edit', 'preview_token': token})
        journey.check(mode + ' edit restores form without creating tasks', edit[0] == 200
            and 'round-arrangement'.encode() in edit[1] and project.rounds.count() == before)
        response = session.send(arrange_url, {'action': 'confirm', 'preview_token': token})
        r = project.rounds.get(name='虚构验收-' + mode)
        journey.check(mode + ' owner directly starts own task', response[0] == 302 and response[2]['Location'] == f'/r/{r.pk}/')
        chosen = r.sampling['unit_ids']
        journey.check(mode + ' allocation matches expected exact selection',
            (set(chosen) == {unit_index[position - 1] for position in positions} if positions else len(set(chosen)) == count))
        journey.check(mode + ' both members receive correct total', r.assignments.filter(coder=owner).count() == len(chosen)
            and r.assignments.filter(coder=member).count() == len(chosen))
        response = session.send(arrange_url, {'action': 'confirm', 'preview_token': token})
        journey.check(mode + ' confirm replay creates no duplicate', response[0] == 302 and project.rounds.count() == before + 1)
    r = project.rounds.get(name='虚构验收-sequential')
    assignment = r.assignments.filter(coder=member).first()
    journey.submit(member_session, assignment, book.codes.first())
    progress = session.page(prefix + '/progress/', project)
    journey.check('owner sees member progress and coding entry', member.username in progress
        and '开始／继续我的编码' in progress and f'data-member-progress="{member.pk}"' in progress)
    workbench = session.page(f'/r/{r.pk}/', project)
    journey.check('workbench links member progress', '成员进度' in workbench)
    fixture = {'username': owner.username, 'password': password, 'project': project.pk,
        'round': r.pk, 'member_username': member.username, 'arrange_url': arrange_url, 'team_url': team_url}
    (settings.RUNTIME_DIR / 'task-arrangement-qa-credentials.json').write_text(json.dumps(fixture), encoding='utf-8')
    print(json.dumps({'passed': len(journey.checks), 'assets': len(journey.assets),
        'scope': 'real HTTP and CSRF; isolated fictional UI database'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
