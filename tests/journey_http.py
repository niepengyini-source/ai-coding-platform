"""Full HTTP journey using fictional records in the isolated localhost UI database.

Start config.ui_test_settings on 127.0.0.1:8001, then run with that settings module.
The script creates new test records only; credentials stay in ignored runtime/.
"""
import hashlib
import http.cookiejar
import io
import json
import os
import re
import uuid
import zipfile
from html import unescape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


if os.environ.get('DJANGO_SETTINGS_MODULE') != 'config.ui_test_settings':
    raise SystemExit('Use config.ui_test_settings and the isolated localhost:8001 server only.')

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from coding.models import Assignment, AuditEvent, Code, Decision, Membership, Project, Round, SourceRecord, Unit


if (Path(settings.DATABASES['default']['NAME']).resolve() !=
        (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve() or
        settings.SESSION_COOKIE_NAME != 'ui_sessionid' or settings.CSRF_COOKIE_NAME != 'ui_csrftoken'):
    raise SystemExit('Only the separate UI-test database and UI cookies are allowed.')

BASE = 'http://127.0.0.1:8001'
checks, assets = [], set()


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), NoRedirect())

    def token(self):
        return next(cookie.value for cookie in self.cookies if cookie.name == 'ui_csrftoken')

    def send(self, path, values=None, *, payload=None, upload=None, csrf=True):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Only local UI-test paths are allowed.')
        data, headers = None, {}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
            if csrf:
                headers['X-CSRFToken'] = self.token()
        elif upload is not None:
            filename, raw = upload
            boundary = 'journey-' + uuid.uuid4().hex
            fields = {'csrfmiddlewaretoken': self.token(), **(values or {})}
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                     for key, value in fields.items()]
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                          'Content-Type: text/csv\r\n\r\n').encode() + raw + b'\r\n')
            data = b''.join(parts) + f'--{boundary}--\r\n'.encode()
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        elif values is not None:
            fields = dict(values)
            if csrf:
                fields['csrfmiddlewaretoken'] = self.token()
            data = urlencode(fields, doseq=True).encode()
        try:
            with self.opener.open(Request(BASE + path, data=data, headers=headers), timeout=30) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def page(self, path, project=None):
        before = business_state(project) if project else None
        status, raw, _ = self.send(path)
        check('page ' + path, status == 200)
        text = raw.decode('utf-8')
        for asset in set(re.findall(r'(?:src|href)="(/static/[^\"]+)"', text)) - assets:
            check('asset ' + asset, self.send(asset)[0] == 200)
            assets.add(asset)
        action = re.search(r'class="button journey-primary" href="([^\"]+)"', text)
        if action:
            target = urlsplit(unescape(action[1]))
            destination = target.path + ('?' + target.query if target.query else '')
            check('next action opens ' + destination, self.follow(self.send(destination))[0] == 200)
        if project:
            check('GET read-only ' + path, business_state(project) == before)
        return text

    def follow(self, response):
        status, raw, headers = response
        for _ in range(8):
            if status not in (301, 302, 303):
                return status, raw, headers
            target = urlsplit(headers['Location'])
            if target.netloc and target.netloc != '127.0.0.1:8001':
                raise AssertionError('Unexpected external redirect')
            status, raw, headers = self.send(target.path + ('?' + target.query if target.query else ''))
        raise AssertionError('Redirect loop')

    def login(self, user, password):
        self.page('/login/')
        response = self.send('/login/', {'username': user.username, 'password': password})
        check('login ' + user.username, response[0] == 302 and response[2]['Location'] == '/')
        status, raw, _ = self.follow(response)
        check('landing modules ' + user.username, status == 200 and '我的项目'.encode() in raw
              and '加入小组'.encode() in raw and b'<aside class="sidebar">' not in raw)


def business_state(project):
    return {
        'rounds': list(project.rounds.order_by('pk').values('id', 'state', 'cycle', 'revision', 'book_id')),
        'books': list(project.codebooks.order_by('pk').values('id', 'revision', 'frozen')),
        'codes': Code.objects.filter(book__project=project).count(),
        'units': list(Unit.objects.filter(record__document__project=project).order_by('pk').values('id', 'active')),
        'records': SourceRecord.objects.filter(document__project=project).count(),
        'assignments': list(Assignment.objects.filter(round__project=project).order_by('pk').values(
            'id', 'status', 'revision', 'primary_id', 'superseded', 'note')),
        'decisions': list(Decision.objects.filter(round__project=project).order_by('pk').values('id', 'revision')),
        'audit': AuditEvent.objects.filter(project=project).count(),
    }


def heading(text):
    match = re.search(r'<h2 id="project-next-step">(.*?)</h2>', text, re.S)
    return unescape(match[1]) if match else ''


def create_project(session, owner, name):
    session.page('/projects/new/')
    response = session.send('/projects/new/', {'name': name, 'goal': '虚构验收：判断讨论中的计划与检查。', 'description': ''})
    project = Project.objects.get(owner=owner, name=name)
    check('create project redirect', response[0] == 302 and response[2]['Location'] == f'/p/{project.pk}/')
    page = session.page(f'/p/{project.pk}/', project)
    check('new project starts at materials', heading(page) == '准备编码材料')
    check('new project has one empty book', project.codebooks.count() == 1 and not project.codebooks.first().codes.exists())
    response = session.send(f'/p/{project.pk}/code/')
    check('both missing routes to material', response[0] == 302 and response[2]['Location'] == f'/p/{project.pk}/materials/')
    return project


def preparation_next(session, text, project, destination):
    link = re.search(r'class="button setup-next" href="([^\"]+)"', text)
    check('preparation points to ' + destination, link is not None and unescape(link[1]) == destination)
    before = business_state(project)
    status, body, _ = session.follow(session.send(unescape(link[1])))
    check('preparation next opens ' + destination, status == 200)
    check('preparation navigation is read-only', business_state(project) == before)
    return body.decode('utf-8')


def prepare_project(session, project):
    path = f'/p/{project.pk}/materials/'
    page = session.page(path, project)
    check('upload begins preparation step one', 'data-setup-stage="1"' in page)
    raw = ('message_id,group_id,created_at,speaker_id,content\n'
           'F1,FG1,2026-10-17 10:00,FS1,虚构讨论：先安排目标。\n'
           'F2,FG1,2026-10-17 10:01,FS2,虚构讨论：检查一下结果。\n').encode('utf-8')
    before = SourceRecord.objects.filter(document__project=project).count()
    status, body, _ = session.send(path, upload=('journey-fiction.csv', raw))
    match = re.search(rb'name="draft_id" value="([^\"]+)"', body)
    check('material preview before confirmation', status == 200 and match is not None
          and SourceRecord.objects.filter(document__project=project).count() == before)
    check('material preview keeps step one and no bypass', b'data-setup-stage="1"' in body
          and b'class="button setup-next"' not in body)
    response = session.send(path, {'draft_id': match[1].decode(), 'sheet_name': '',
                                  'text_column': 'content', 'context_column': 'group_id'})
    check('material confirmation redirects', response[0] == 302 and response[2]['Location'] == f'/p/{project.pk}/units/')
    check('two units preserve all source metadata', Unit.objects.filter(record__document__project=project).count() == 2
          and all(row.metadata['group_id'] == 'FG1' and row.metadata['created_at'].startswith('2026-10-17')
                  for row in SourceRecord.objects.filter(document__project=project)))
    check('material completion recommends codebook', heading(session.page(f'/p/{project.pk}/', project)) == '准备编码本')
    book = project.codebooks.first()
    response = session.send(f'/p/{project.pk}/code/')
    check('missing rules routes to current codebook', response[0] == 302
          and response[2]['Location'] == f'/p/{project.pk}/books/{book.pk}/')
    page = session.page(f'/p/{project.pk}/units/', project)
    check('material completed guide is step two', 'data-setup-stage="2"' in page and '材料已就绪' in page)
    page = preparation_next(session, page, project, f'/p/{project.pk}/books/{book.pk}/')
    book_path = f'/p/{project.pk}/books/{book.pk}/import/'
    page = preparation_next(session, page, project, book_path)
    rules = 'Code,Name,Definition\nPLAN,计划,明确下一步目标或任务。\nCHECK,检查,核对进度或结果。\n'.encode('utf-8')
    response = session.send(book_path, {'action': 'upload', 'auto_preview': 'yes'}, upload=('fictional-rules.csv', rules))
    status, body, _ = session.follow(response)
    check('rules preview stays step two until confirmation', status == 200 and b'data-setup-stage="2"' in body
          and not project.codebooks.first().codes.exists() and b'class="button setup-next"' not in body)
    draft = re.search(rb'name="draft_id" value="([^\"]+)"', body)
    check('rules preview has confirmation form', draft is not None)
    response = session.send(book_path, {'draft_id': draft[1].decode(), 'action': 'confirm', 'confirmed': 'yes'})
    status, body, _ = session.follow(response)
    book = project.codebooks.first()
    check('rules confirmed and next step visible', status == 200 and book.codes.count() == 2
          and b'data-setup-stage="3"' in body)
    page = preparation_next(session, body.decode('utf-8'), project, f'/p/{project.pk}/code/')
    check('confirmation ends guide without loop', 'data-setup-stage="3"' in page
          and '确认编码准备' in page and 'class="button setup-next"' not in page and not project.rounds.exists())
    page = session.page(f'/p/{project.pk}/', project)
    check('resources ready recommends start', heading(page) == '材料与编码本已准备好')
    check('project has all five guide labels', all(label in page for label in (
        '研究与材料准备', '编码本与单元准备', '试编码与改进', '正式编码', '协商定稿与成果')))
    return book, raw


def submit(session, assignment, code, action='submit', note='虚构验收判断'):
    assignment.refresh_from_db()
    payload = {'revision': assignment.revision, 'book_id': assignment.round.book_id,
               'primary': code.pk, 'secondary': [], 'uncertain': False, 'no_code': False,
               'note': note, 'token': str(uuid.uuid4()), 'action': action}
    response = session.send(f'/api/assignments/{assignment.pk}/save/', payload=payload)
    assignment.refresh_from_db()
    check('save assignment ' + str(assignment.pk), response[0] == 200 and assignment.primary_id == code.pk
          and assignment.status == ('submitted' if action == 'submit' else 'draft'))
    return payload


def verify_zip(session, project, raw, label):
    before = business_state(project)
    response = session.send(f'/p/{project.pk}/archive/', {'action': 'full'})
    check(label + ' zip download', response[0] == 200 and response[1].startswith(b'PK'))
    with zipfile.ZipFile(io.BytesIO(response[1])) as archive:
        check(label + ' zip integrity', archive.testzip() is None)
        manifest = json.loads(archive.read('manifest.json'))
        document = project.documents.first()
        check(label + ' raw retained', archive.read(f'00_raw/document_{document.pk}.csv') == raw)
        check(label + ' snapshot no transition', not manifest['changes_project_state'])
        for name, item in manifest['files'].items():
            content = archive.read(name)
            check(label + ' archive hash ' + name,
                  hashlib.sha256(content).hexdigest() == item['sha256'] and len(content) == item['bytes'])
    check(label + ' export leaves original state', business_state(project) == before)


def main():
    owner_session = Session()
    login = owner_session.page('/login/')
    check('isolated server cookies', any(c.name == 'ui_csrftoken' for c in owner_session.cookies)
          and not any(c.name == 'csrftoken' for c in owner_session.cookies))
    check('login only auth tabs', '<aside class="sidebar">' not in login and '还没有账号' not in login)
    owner_session.page('/accounts/register/')
    suffix, password = uuid.uuid4().hex[:10], uuid.uuid4().hex + '-Journey!'
    username = 'qa_journey_owner_' + suffix
    response = owner_session.send('/accounts/register/', {'username': username, 'password1': password, 'password2': password})
    check('real CSRF registration', response[0] == 302 and get_user_model().objects.filter(username=username).exists())
    owner = get_user_model().objects.get(username=username)
    check('registration landing', owner_session.follow(response)[0] == 200)
    check('logout is POST', owner_session.send('/logout/')[0] == 405)
    check('logout success', owner_session.send('/logout/', {})[0] == 302)
    owner_session.login(owner, password)
    projects = owner_session.page('/projects/')
    check('new account has central create', '还没有创建项目，快点创建属于自己的新项目吧。' in projects)

    personal = create_project(owner_session, owner, '工作引导 · 个人虚构验收 · ' + suffix)
    book, raw = prepare_project(owner_session, personal)
    code = book.codes.get(key='PLAN')
    quick_url = f'/p/{personal.pk}/quick-start/'
    rounds = personal.rounds.count()
    page = owner_session.page(quick_url, personal)
    check('quick confirmation hidden current book', '确认编码准备' in page and 'type="hidden" name="book_id"' in page
          and '<select name="book_id"' not in page and personal.rounds.count() == rounds)
    token = re.search(r'name="token" value="([^\"]+)"', page)[1]
    data = {'book_id': book.pk, 'token': token}
    check('quick start requires CSRF', owner_session.send(quick_url, data, csrf=False)[0] == 403)
    response = owner_session.send(quick_url, data)
    personal_round = personal.rounds.get(kind='quick')
    check('quick starts own assignments', response[0] == 302 and response[2]['Location'] == f'/r/{personal_round.pk}/'
          and personal_round.assignments.count() == 2)
    owner_session.send(quick_url, data)
    check('quick replay does not duplicate', personal.rounds.count() == 1)
    page = owner_session.page(quick_url, personal)
    check('resume has one primary action', page.count('继续我的编码 →') == 1 and '开始个人编码 →' not in page)
    task = personal_round.assignments.order_by('pk').first()
    owner_session.page(f'/r/{personal_round.pk}/?a={task.pk}', personal)
    submit(owner_session, task, code, action='save', note='虚构草稿保留历史')
    submit(owner_session, task, code, note='虚构正式提交保留历史')
    submit(owner_session, personal_round.assignments.order_by('pk').last(), book.codes.get(key='CHECK'))
    check('submitted batch next step is explicit finish', heading(owner_session.page(f'/p/{personal.pk}/', personal)) == '本批个人编码已提交')
    owner_session.page(f'/p/{personal.pk}/history/', personal)
    owner_session.page(f'/p/{personal.pk}/history/{task.pk}/', personal)
    personal_round.refresh_from_db()
    response = owner_session.send(f'/p/{personal.pk}/quick/{personal_round.pk}/finish/', {'revision': personal_round.revision})
    personal_round.refresh_from_db()
    check('personal explicit finish without fake consensus', response[0] == 302 and personal_round.state == 'closed'
          and not personal_round.decisions.exists())
    check('completed guide leads export', heading(owner_session.page(f'/p/{personal.pk}/', personal)) == '本轮编码已结束')
    status, body, _ = owner_session.send(f'/r/{personal_round.pk}/export/mine/?format=json')
    result = json.loads(body)
    check('personal export has two coded records', status == 200 and result['row_count'] == 2
          and {row['primary_code'] for row in result['rows']} == {'PLAN', 'CHECK'})
    owner_session.page(f'/p/{personal.pk}/exports/?target=mine&round={personal_round.pk}', personal)
    owner_session.page(f'/p/{personal.pk}/archive/', personal)
    verify_zip(owner_session, personal, raw, 'personal')
    history_before = business_state(personal)

    team = create_project(owner_session, owner, '工作引导 · 团队虚构验收 · ' + suffix)
    team_book, team_raw = prepare_project(owner_session, team)
    check('new project does not reset previous project', business_state(personal) == history_before)
    members = []
    for index in range(2):
        user = get_user_model().objects.create_user(f'qa_journey_member{index}_{suffix}', password=password)
        Membership.objects.create(project=team, user=user, role='coder')
        members.append((user, Session()))
    round_url = f'/p/{team.pk}/rounds/new/'
    owner_session.page(round_url, team)
    response = owner_session.send(round_url, {'name': '虚构正式编码轮次', 'kind': 'formal', 'book_id': team_book.pk,
        'coder_ids': [user.pk for user, _ in members], 'count': 0, 'seed': 2026, 'stratify': '',
        'distribution': 'all', 'double_percent': 25, 'unit_ids': ''})
    team_round = team.rounds.get(kind='formal')
    check('manager without own tasks lands progress', response[0] == 302
          and response[2]['Location'] == f'/p/{team.pk}/progress/'
          and not team_round.assignments.filter(coder=owner).exists())
    owner_session.page(f'/p/{team.pk}/progress/', team)
    check('manager guide points to team progress', heading(owner_session.page(f'/p/{team.pk}/', team)) == '查看团队编码进度')
    check('review unavailable before explicit transition', owner_session.send(f'/r/{team_round.pk}/review/')[0] == 403)
    for index, (user, session) in enumerate(members):
        session.login(user, password)
        check('member sees joined project', team.name in session.page('/projects/'))
        page = session.page(f'/p/{team.pk}/', team)
        check('member next step is own coding', heading(page) == '继续我的编码')
        for assignment in team_round.assignments.filter(coder=user).order_by('pk'):
            session.page(f'/r/{team_round.pk}/?a={assignment.pk}', team)
            submit(session, assignment, team_book.codes.get(key='PLAN' if index == 0 else 'CHECK'))
        check('member completion waits for owner', heading(session.page(f'/p/{team.pk}/', team)) == '我的任务已提交')
    team_round.refresh_from_db()
    check('all submissions do not auto disclose', team_round.state == 'active')
    check('owner sees explicit review guidance', heading(owner_session.page(f'/p/{team.pk}/', team)) == '本轮独立编码已全部提交')
    check('independent report stays blind before review', owner_session.send(f'/r/{team_round.pk}/report/')[0] == 403)
    check('GET manage forbidden', owner_session.send(f'/r/{team_round.pk}/manage/')[0] == 405)
    response = owner_session.send(f'/r/{team_round.pk}/manage/', {'revision': team_round.revision, 'action': 'lock'})
    team_round.refresh_from_db()
    check('owner explicitly opens review', response[0] == 302 and team_round.state == 'review')
    owner_session.page(f'/r/{team_round.pk}/report/', team)
    check('review next step is consensus', heading(owner_session.page(f'/p/{team.pk}/', team)) == '继续协商定稿')
    owner_session.page(f'/r/{team_round.pk}/disagreements/', team)
    for unit_id in team_round.assignments.values_list('unit_id', flat=True).distinct():
        owner_session.page(f'/r/{team_round.pk}/review/?unit={unit_id}', team)
        response = owner_session.send(f'/api/rounds/{team_round.pk}/units/{unit_id}/decision/', payload={
            'revision': 0, 'primary': team_book.codes.get(key='PLAN').pk, 'secondary': [],
            'uncertain': False, 'no_code': False, 'note': '虚构协商：依据定义核对后定稿。'})
        check('consensus saved ' + str(unit_id), response[0] == 200)
    check('final decisions recommend explicit finish', heading(owner_session.page(f'/p/{team.pk}/', team)) == '本轮结论已填写')
    team_round.refresh_from_db()
    response = owner_session.send(f'/r/{team_round.pk}/manage/', {'revision': team_round.revision, 'action': 'close'})
    team_round.refresh_from_db()
    check('team round explicitly closes', response[0] == 302 and team_round.state == 'closed')
    check('team closed guide leads export', heading(owner_session.page(f'/p/{team.pk}/', team)) == '本轮编码已结束')
    owner_session.page(f'/p/{team.pk}/exports/?target=final&round={team_round.pk}', team)
    status, body, _ = owner_session.send(f'/r/{team_round.pk}/export/final/?format=json')
    result = json.loads(body)
    check('final export has two distinct source units', status == 200 and result['row_count'] == 2
          and {row['unit_id'] for row in result['rows']} == set(team_round.assignments.values_list('unit_id', flat=True)))
    owner_session.page(f'/p/{team.pk}/archive/', team)
    verify_zip(owner_session, team, team_raw, 'team')
    check('independent answers remain after consensus', team_round.assignments.count() == 4
          and set(team_round.assignments.values_list('primary__key', flat=True)) == {'PLAN', 'CHECK'})
    check('personal earlier draft audit preserved', AuditEvent.objects.filter(
        project=personal, action='annotation.save', object_id=str(task.pk)).exists())
    check('previous project remains unchanged through team journey', business_state(personal) == history_before)
    ready = create_project(owner_session, owner, '工作引导 · 已准备好界面 · ' + suffix)
    prepare_project(owner_session, ready)
    response = owner_session.send(round_url, {'name': '虚构界面浏览：进行中的试编码', 'kind': 'pilot',
        'book_id': team_book.pk, 'coder_ids': [owner.pk], 'count': 0, 'seed': 2026, 'stratify': '',
        'distribution': 'all', 'double_percent': 25, 'unit_ids': ''})
    active = team.rounds.latest('pk')
    check('manager with own task lands workbench', response[0] == 302
          and response[2]['Location'] == f'/r/{active.pk}/' and active.assignments.filter(coder=owner).count() == 2)
    owner_session.page(f'/r/{active.pk}/', team)
    owner_session.page(f'/guide/?project={team.pk}', team)
    credentials = {'username': owner.username, 'password': password, 'personal_project': personal.pk,
        'personal_round': personal_round.pk, 'team_project': team.pk, 'team_round': team_round.pk,
        'ready_project': ready.pk, 'active_project': team.pk, 'active_round': active.pk,
        'active_assignment': active.assignments.filter(coder=owner).first().pk,
        'member_usernames': [user.username for user, _ in members]}
    (settings.RUNTIME_DIR / 'journey-qa-credentials.json').write_text(json.dumps(credentials), encoding='utf-8')
    print(json.dumps({'passed': len(checks), 'assets': len(assets), 'checks': checks,
        'browser_fixture': 'runtime/journey-qa-credentials.json',
        'scope': 'real HTTP/CSRF; isolated fictional UI database; not browser or multi-device QA'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
