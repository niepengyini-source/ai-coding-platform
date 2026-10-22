"""Feedback regression checks through HTTP, using the isolated fictional UI DB.

Run task_selection_http.py first, then this script with config.ui_test_settings
and the QA server on 127.0.0.1:8001. Never use research accounts or materials.
"""
import json
import re
import uuid
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import journey_http as journey
from task_selection_http import hidden
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from coding.models import AuditEvent, Membership, Project, Unit


def section(text, element, attribute):
    match = re.search(r'<' + element + r'\b[^>]*' + attribute + r'[^>]*>(.*?)</' + element + '>', text, re.S)
    journey.check('section exists: ' + attribute, match is not None)
    return match[0]


def action_form(text, action):
    match = re.search(r'<form\b[^>]*action="' + re.escape(action) + r'"[^>]*>(.*?)</form>', text, re.S)
    journey.check('action form exists: ' + action, match is not None)
    return {name: unescape(value) for name, value in re.findall(r'name="([^\"]+)" value="([^\"]*)"', match[1])
            if name != 'csrfmiddlewaretoken'}


def local_path(base, href):
    parsed = urlsplit(urljoin(base, unescape(href)))
    journey.check('local link only', not parsed.netloc)
    return parsed.path + ('?' + parsed.query if parsed.query else '')


def clear_payload(assignment, note):
    assignment.refresh_from_db()
    return {'revision': assignment.revision, 'book_id': assignment.round.book_id,
            'primary': None, 'secondary': [], 'uncertain': False, 'no_code': False,
            'note': note, 'token': str(uuid.uuid4()), 'action': 'save'}


def main():
    fixture_path = settings.RUNTIME_DIR / 'task-arrangement-qa-credentials.json'
    if not fixture_path.is_file():
        raise SystemExit('Run task_selection_http.py on the isolated QA database first.')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    journey.check('fictional arrangement accounts only', fixture['username'].startswith('qa_arrangement_owner_')
                  and fixture['member_username'].startswith('qa_arrangement_member_'))
    owner = get_user_model().objects.get(username=fixture['username'])
    member = get_user_model().objects.get(username=fixture['member_username'])
    project = Project.objects.get(pk=fixture['project'], owner=owner)
    journey.check('expected fictional project membership', Membership.objects.filter(project=project).count() == 2
                  and Membership.objects.filter(project=project, user=member, role='coder').exists())
    journey.check('expected fictional 62-unit pool', Unit.objects.filter(record__document__project=project, active=True).count() == 62)
    book = project.codebooks.first()
    plan, other = book.codes.get(key='PLAN'), book.codes.get(key='CHECK')
    session, member_session = journey.Session(), journey.Session()
    session.login(owner, fixture['password'])
    member_session.login(member, fixture['password'])
    prefix, arrange = f'/p/{project.pk}', f'/p/{project.pk}/rounds/new/'

    # Allocate through the same preview/confirmation forms the owner uses.
    form = session.page(arrange, project)
    name = '虚构反馈验收-' + uuid.uuid4().hex[:10]
    values = {'action': 'preview', 'pool_hash': hidden(form, 'pool_hash'), 'name': name, 'kind': 'formal',
              'book_id': book.pk, 'coder_ids': [owner.pk, member.pk], 'selection_mode': 'sequential',
              'start_position': 1, 'count': 0, 'seed': 2026, 'distribution': 'all', 'double_percent': 25,
              'manual_positions': '', 'stratify': '', 'unit_ids': ''}
    response = session.send(arrange, values)
    journey.check('feedback round preview', response[0] == 200)
    response = session.send(arrange, {'action': 'confirm', 'preview_token': hidden(response[1].decode(), 'preview_token')})
    r = project.rounds.get(name=name)
    journey.check('feedback round allocated by HTTP', response[0] == 302 and response[2]['Location'] == f'/r/{r.pk}/'
                  and r.assignments.filter(coder=owner).count() == 62 and r.assignments.filter(coder=member).count() == 62)
    workbench = f'/r/{r.pk}/'
    ordered = ('unit__record__document_id', 'unit__record__position', 'unit__start', 'id')
    own_tasks = list(r.assignments.filter(coder=owner).order_by(*ordered))
    member_tasks = list(r.assignments.filter(coder=member).order_by(*ordered))
    current, focus = own_tasks[0], own_tasks[-1]
    own_note, peer_note = '虚构：清除代码后备注仍保留', '虚构私密答案：负责人不能在独立阶段查看此备注'
    page = session.page(workbench + f'?a={current.pk}', project)
    journey.check('clear and movable shortcut controls available', 'data-code-clear>清除主代码' in page
                  and all(token in page for token in ('data-shortcut-drag', 'data-shortcut-resize', 'data-shortcut-window-reset')))

    # Saving an empty draft must clear choices without deleting earlier audit snapshots.
    payload = journey.submit(session, current, plan, action='save', note=own_note)
    payload.update(revision=current.revision, secondary=[other.pk], token=str(uuid.uuid4()))
    journey.check('save optional secondary code', session.send(f'/api/assignments/{current.pk}/save/', payload=payload)[0] == 200)
    old_events = set(AuditEvent.objects.filter(project=project, object_id=str(current.pk),
                                             action__startswith='annotation.').values_list('pk', flat=True))
    response = session.send(f'/api/assignments/{current.pk}/save/', payload=clear_payload(current, own_note))
    current.refresh_from_db()
    latest = AuditEvent.objects.filter(project=project, object_id=str(current.pk), action='annotation.save').latest('pk')
    journey.check('clear draft preserves note and old choices in history', response[0] == 200
                  and current.primary_id is None and current.secondary == [] and current.note == own_note
                  and latest.before['primary'] == plan.pk and latest.before['secondary'] == [other.pk]
                  and latest.after['primary'] is None and latest.after['secondary'] == []
                  and old_events.issubset(set(AuditEvent.objects.filter(project=project).values_list('pk', flat=True))))
    history_url = prefix + f'/history/{current.pk}/'
    history = session.page(history_url, project)
    journey.check('cleared choices remain visible in personal history', '修改历史' in history
                  and str(plan) in history and str(other) in history and own_note in history)

    journey.submit(session, current, plan, note=own_note)
    page = session.page(workbench + f'?a={current.pk}', project)
    correction = prefix + f'/history/{current.pk}/reopen/'
    journey.check('submitted own task has inline correction', 'correction-entry' in page and f'action="{correction}"' in page)
    before = journey.business_state(project)
    journey.check('submitted choice cannot be silently cleared', session.send(
        f'/api/assignments/{current.pk}/save/', payload=clear_payload(current, own_note))[0] == 409)
    journey.check('locked save leaves history intact', journey.business_state(project) == before)
    values = action_form(page, correction)
    values['reason'] = '虚构：负责人误选后自行退回修改'
    journey.check('inline correction requires CSRF', session.send(correction, values, csrf=False)[0] == 403)
    response = session.send(correction, values)
    current.refresh_from_db()
    journey.check('owner reopens self without overwriting previous answer', response[0] == 302
                  and response[2]['Location'] == workbench + f'?a={current.pk}'
                  and current.status == 'draft' and current.primary_id == plan.pk
                  and AuditEvent.objects.filter(project=project, action='annotation.submit', object_id=str(current.pk)).exists()
                  and AuditEvent.objects.filter(project=project, action='annotation.reopen', object_id=str(current.pk)).exists())

    for task in member_tasks[:52]:
        journey.submit(member_session, task, other, note=peer_note)

    # Pagination preserves the owner's current item and independent task-list page.
    base = workbench + '?' + urlencode({'a': focus.pk, 'page': 2, 'submitted_coder': member.pk})
    first = section(session.page(base, project), 'details', 'id="submission-controls"')
    journey.check('first submission page contains 50 of 52 rows', first.count('class="return-task-form"') == 50
                  and '找到 52 条已提交任务' in first)
    next_link = re.search(r'href="([^\"]+)"[^>]*>下一页</a>', first)
    journey.check('submitted list offers next page', next_link is not None)
    second_url = local_path(workbench, next_link[1])
    params = parse_qs(urlsplit(second_url).query)
    journey.check('submission pagination retains owner focus and filters', params.get('a') == [str(focus.pk)]
                  and params.get('page') == ['2'] and params.get('submitted_coder') == [str(member.pk)]
                  and params.get('submitted_page') == ['2'])
    second_page = session.page(second_url, project)
    second = section(second_page, 'details', 'id="submission-controls"')
    journey.check('second submission page shows remaining records without answers', second.count('class="return-task-form"') == 2
                  and '2 / 2' in second and peer_note not in second_page
                  and f'data-endpoint="/api/assignments/{focus.pk}/save/"' in second_page)
    filtered_url = base + '&submitted_unit=' + str(member_tasks[51].unit_id)
    filtered = section(session.page(filtered_url, project), 'details', 'id="submission-controls"')
    journey.check('system unit and coder filters select one exact record', filtered.count('class="return-task-form"') == 1
                  and f'action="/assignments/{member_tasks[51].pk}/reopen/"' in filtered and '找到 1 条已提交任务' in filtered)
    invalid = section(session.page(base + '&submitted_unit=1e3', project), 'details', 'id="submission-controls"')
    journey.check('invalid system unit is explained instead of returning unrelated tasks', '请输入有效的系统单元编号' in invalid
                  and 'class="return-task-form"' not in invalid)

    # Role checks and blind independent answers remain intact on both GET and POST.
    before = journey.business_state(project)
    member_page = member_session.page(workbench + f'?a={member_tasks[51].pk}&submitted_page=2', project)
    journey.check('coder has no manager return list or self-correction privilege', 'id="submission-controls"' not in member_page
                  and 'correction-entry' not in member_page)
    denied = {'revision': current.revision, 'reason': '虚构越权请求'}
    journey.check('coder cannot reopen another task', member_session.send(f'/assignments/{current.pk}/reopen/', denied)[0] == 403)
    journey.check('coder cannot self-reopen submitted team task', member_session.send(
        prefix + f'/history/{member_tasks[51].pk}/reopen/', {'revision': member_tasks[51].revision, 'reason': '虚构越权请求'})[0] == 403)
    journey.check('owner cannot open peer answer in workbench', session.send(workbench + f'?a={member_tasks[51].pk}')[0] == 404)
    journey.check('owner cannot open peer personal history', session.send(prefix + f'/history/{member_tasks[51].pk}/')[0] == 404)
    journey.check('permission failures do not change project state', journey.business_state(project) == before)

    returned = member_tasks[50]
    return_action = f'/assignments/{returned.pk}/reopen/'
    values = action_form(second, return_action)
    values['reason'] = '虚构：第二页退回并保留筛选位置'
    response = session.send(return_action, values)
    returned.refresh_from_db()
    redirect = urlsplit(response[2].get('Location', ''))
    params = parse_qs(redirect.query)
    journey.check('manager returns second-page task without losing navigation', response[0] == 302
                  and returned.status == 'draft' and returned.primary_id == other.pk and returned.note == peer_note
                  and redirect.path == workbench and redirect.fragment == 'submission-controls'
                  and params.get('a') == [str(focus.pk)] and params.get('page') == ['2']
                  and params.get('submitted_page') == ['2'] and params.get('submitted_coder') == [str(member.pk)])
    second = section(session.page(second_url, project), 'details', 'id="submission-controls"')
    journey.check('second page remains reachable after return', second.count('class="return-task-form"') == 1 and '2 / 2' in second)

    for viewer, label, progress, badge, activity in (
            (session, '团队进度', '51/124', '我负责的', '团队最近编码'),
            (member_session, '我的任务进度', '51/62', '我加入的', '我上次编码')):
        card = section(viewer.page('/projects/', project), 'article', f'aria-labelledby="project-title-{project.pk}"')
        journey.check(label + ' card shows role-specific counts and collaboration', all(value in card for value in (
            label, progress, badge, '多人协作', '项目成员 2 人', '本轮有任务 2 人', '继续小组任务', activity)))
        latest_coding = AuditEvent.objects.filter(project=project, action__in=['annotation.save', 'annotation.submit'])
        if viewer is member_session:
            latest_coding = latest_coding.filter(actor=member)
        timestamp = timezone.localtime(latest_coding.latest('created_at').created_at).strftime('%Y.%m.%d %H:%M')
        journey.check(activity + ' shows actual coding time', timestamp in card)
    journey.check('join before project creation is discoverable', '加入小组' in session.page('/projects/new/', project))
    project_page = session.page(prefix + '/', project)
    journey.check('owner invitation entry is visible on project heading', f'href="{prefix}/team-setup/">邀请成员</a>' in project_page)
    journey.check('invitation guide loads without issuing a new invitation', '生成新邀请码' in session.page(prefix + '/team-setup/', project))
    session.page(prefix + '/progress/', project)

    browser_fixture = {'username': owner.username, 'password': fixture['password'], 'member_username': member.username,
                       'project': project.pk, 'round': r.pk, 'assignment': focus.pk, 'correction_assignment': current.pk,
                       'mouse_shortcut_url': workbench + f'?a={focus.pk}', 'submitted_page2_url': second_url,
                       'history_url': history_url, 'team_url': prefix + '/team-setup/'}
    (settings.RUNTIME_DIR / 'feedback-qa-credentials.json').write_text(json.dumps(browser_fixture), encoding='utf-8')
    print(json.dumps({'passed': len(journey.checks), 'assets': len(journey.assets),
                      'browser_fixture': 'runtime/feedback-qa-credentials.json',
                      'scope': 'real HTTP/CSRF; isolated fictional UI database; browser gestures require separate QA'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
