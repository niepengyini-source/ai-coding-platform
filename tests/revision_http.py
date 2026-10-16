"""Real HTTP regression and browser fixtures, strictly in the separate UI database."""
import hashlib
import http.cookiejar
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.ui_test_settings'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from coding.models import Assignment, Code, Codebook, Membership, Project, SourceDocument, SourceRecord, Unit
from coding.services import create_round, save_assignment

if Path(settings.DATABASES['default']['NAME']).resolve() != (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve():
    raise SystemExit('This test may only use the isolated UI database.')

cookies = http.cookiejar.CookieJar()
opener = build_opener(HTTPCookieProcessor(cookies))
checks = []


def check(name, value):
    if not value:
        raise AssertionError(name)
    checks.append(name)


def request(path, values=None, csrf=True):
    if values is not None:
        values = dict(values)
        if csrf:
            values['csrfmiddlewaretoken'] = next(c.value for c in cookies if c.name == 'ui_csrftoken')
    body = urlencode(values, doseq=True).encode() if values is not None else None
    try:
        with opener.open(Request('http://127.0.0.1:8001' + path, data=body), timeout=20) as result:
            return result.status, result.read(), result.headers
    except HTTPError as error:
        return error.code, error.read(), error.headers


suffix, password = uuid.uuid4().hex[:12], uuid.uuid4().hex + '-Fiction!'
User = get_user_model()
owner = User.objects.create_user('ui_revision_owner_' + suffix, password=password)
coder = User.objects.create_user('ui_revision_coder_' + suffix, password=password)
empty = User.objects.create_user('ui_revision_empty_' + suffix, password=password)
project = Project.objects.create(name='新版界面验收 · 纯虚构', owner=owner)
for user, role in [(owner, 'owner'), (coder, 'coder')]:
    Membership.objects.create(project=project, user=user, role=role)
book = Codebook.objects.create(project=project, version=1, policy='multi')
codes = [Code.objects.create(book=book, key=f'C{i:02}', name=f'示例类别{i}', definition='仅用于虚构界面验收') for i in range(25)]
raw = 'group_id,created_at,content\nG1,2026-10-01,虚构讨论\n'.encode()
doc = SourceDocument.objects.create(project=project, name='revision-fiction.csv', sha256=hashlib.sha256(raw).hexdigest(),
    raw=raw, text_column='content', context_column='group_id')
units = []
for position in range(1, 19):
    row = SourceRecord.objects.create(document=doc, position=position, text=f'虚构语句第{position}条。先确定目标，再检查进度。',
        context_key='G1', metadata={'group_id': 'G1', 'created_at': '2026-10-01 10:00', 'speaker_id': 'S1'})
    units.append(Unit.objects.create(record=row, end=len(row.text), text=row.text))
round_ = create_round(project, owner, book.pk, '界面验收编码批次', 'pilot', [owner.pk, coder.pk], 0, 2026, 'all', 25)
for user in [owner, coder]:
    for a in round_.assignments.filter(coder=user).order_by('id')[:3]:
        save_assignment(a.pk, user, {'revision': 0, 'token': str(uuid.uuid4()), 'action': 'save',
            'primary': codes[0].pk, 'secondary': [], 'uncertain': False, 'no_code': False,
            'note': '虚构编码判断，非研究材料'})
focus = round_.assignments.get(coder=owner, unit=units[8])
credentials = {'username': owner.username, 'password': password, 'empty_username': empty.username,
    'coder_username': coder.username, 'project': project.pk, 'round': round_.pk, 'assignment': focus.pk}
(settings.RUNTIME_DIR / 'ui-revision-credentials.json').write_text(json.dumps(credentials), encoding='utf-8')

status, body, _ = request('/login/')
check('login_no_sidebar', status == 200 and b'<aside class="sidebar">' not in body)
status, body, _ = request('/login/', {'username': owner.username, 'password': password})
check('home_two_modules_no_sidebar', status == 200 and '我的项目'.encode() in body and '加入小组'.encode() in body
      and b'<aside class="sidebar">' not in body)
status, body, _ = request('/projects/')
check('projects_sidebar_times_and_small_create', status == 200 and '创建时间'.encode() in body and '最后更新'.encode() in body
      and b'<aside class="sidebar">' in body and b'id="page-back"' in body and f'/p/{project.pk}/'.encode() in body)
status, body, _ = request(f'/p/{project.pk}/')
check('subsequent_page_has_sidebar', status == 200 and b'<aside class="sidebar">' in body)
status, body, _ = request(f'/r/{round_.pk}/?a={focus.pk}')
text = body.decode()
check('context_defaults_two_each', status == 200 and len(re.findall(r'class="context-row(?: current)?\s*"', text)) == 5)
check('actions_before_codes_disabled_initially', text.index('data-action="submit_next"') < text.index('data-primary-fallback')
      and 'data-action="submit_next" disabled' in text and 'data-action="save" disabled' in text)
assets = set(re.findall(r'(?:src|href)="(/static/[^\"]+)"', text))
for asset in assets:
    check('asset_' + asset.rsplit('/', 1)[-1], request(asset)[0] == 200)
status, body, _ = request(f'/api/assignments/{focus.pk}/context/?direction=before&cursor=7')
check('three_more_context', status == 200 and [r['position'] for r in json.loads(body)['records']] == [4, 5, 6])
url = f'/p/{project.pk}/archive/'
status, body, _ = request(url)
check('archive_preview_open_while_active', status == 200 and '下载项目快照 ZIP'.encode() in body)
custom = {'action': 'custom', 'scope': 'all', 'mode': 'exact', 'metadata_field': 'group_id', 'metadata_value': 'G1',
    'selection': 'first', 'limit': '2', 'format': 'json', 'page': '1'}
status, body, headers = request(url, custom)
rows = json.loads(body)['rows']
check('custom_latest_n_and_original_metadata', status == 200 and len(rows) == 2 and all(
    r['meta_group_id'] == 'G1' and r['meta_created_at'] == '2026-10-01 10:00' and r['status'] == 'draft' for r in rows))
status, body, _ = request(url, dict(custom, selection='selected', selected=[rows[0]['assignment_id']]))
check('custom_specific_selection', status == 200 and json.loads(body)['row_count'] == 1)
for extension in ['csv', 'xlsx', 'tsv']:
    status, body, headers = request(url, dict(custom, format=extension))
    check('custom_format_' + extension, status == 200 and '.' + extension in headers.get('Content-Disposition', ''))
status, body, _ = request(url, {'action': 'full'})
with zipfile.ZipFile(io.BytesIO(body)) as archive:
    manifest = json.loads(archive.read('manifest.json'))
    check('active_snapshot_keeps_state', status == 200 and manifest['rounds'][0]['state'] == 'active'
          and not manifest['changes_project_state'] and not manifest['rounds'][0]['final_is_concluded'])
    check('snapshot_preserves_raw', archive.read(f'00_raw/document_{doc.pk}.csv') == raw)
round_.refresh_from_db()
check('snapshot_did_not_lock', round_.state == 'active')
check('snapshot_requires_csrf', request(url, {'action': 'full'}, csrf=False)[0] == 403)
request('/logout/', {})
request('/login/')
request('/login/', {'username': coder.username, 'password': password})
status, body, _ = request('/projects/')
check('joined_project_appears', status == 200 and '小组项目'.encode() in body and f'/p/{project.pk}/'.encode() in body)
check('member_cannot_full_snapshot', request(url, {'action': 'full'})[0] == 403)
status, body, _ = request(url, dict(custom, scope='all'))
check('member_cannot_all_answers', status == 200 and body.lstrip().startswith(b'<!doctype html>'))
check('member_cannot_other_context', request(f'/api/assignments/{focus.pk}/context/?direction=before&cursor=7')[0] == 404)
request('/logout/', {})
request('/login/')
request('/login/', {'username': empty.username, 'password': password})
status, body, _ = request('/projects/')
check('empty_projects_central_create', status == 200 and '还没有创建项目，快点创建属于自己的新项目吧。'.encode() in body
      and body.count(b'class="button workspace-create"') == 1)
status, body, _ = request('/groups/join/')
check('empty_group_shows_no_projects', status == 200 and '暂无项目'.encode() in body)
print(json.dumps({'passed': len(checks), 'checks': checks, 'browser_fixture': 'runtime/ui-revision-credentials.json'}, ensure_ascii=False))
