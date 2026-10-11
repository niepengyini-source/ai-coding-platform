"""Real HTTP checks on port 8001, using fictional accounts and the UI-test DB only."""
import hashlib
import http.cookiejar
import io
import json
import os
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.ui_test_settings'
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from openpyxl import Workbook
from coding.models import Assignment, AuditEvent, Code, Codebook, Membership, Project, Round, SourceDocument, Unit
from coding.services import create_round

if Path(settings.DATABASES['default']['NAME']).resolve() != (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve():
    raise SystemExit('Only the separate UI-test database is allowed.')

cookies = http.cookiejar.CookieJar()
opener = build_opener(HTTPCookieProcessor(cookies))
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def request(path, data=None, *, json_body=False, file=None, csrf=True):
    headers, body = {}, None
    if data is not None:
        data = dict(data)
        if csrf:
            token = next(c.value for c in cookies if c.name == 'ui_csrftoken')
            if json_body:
                headers['X-CSRFToken'] = token
            else:
                data['csrfmiddlewaretoken'] = token
        if file is not None:
            boundary = 'fiction-' + uuid.uuid4().hex
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                     for key, value in data.items()]
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fiction.xlsx"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + file + b'\r\n')
            parts.append(f'--{boundary}--\r\n'.encode())
            body = b''.join(parts)
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        elif json_body:
            body = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        else:
            body = urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        with opener.open(Request('http://127.0.0.1:8001' + path, data=body, headers=headers), timeout=15) as response:
            return response.status, response.read().decode('utf-8-sig'), response.geturl()
    except HTTPError as error:
        return error.code, error.read().decode('utf-8'), error.geturl()


status, _, _ = request('/login/')
check('isolated_server', status == 200 and any(c.name == 'ui_csrftoken' for c in cookies))
suffix = uuid.uuid4().hex
password = uuid.uuid4().hex + '-Fiction!'
owner = get_user_model().objects.create_user('ui_simple_owner_' + suffix, password=password)
coder = get_user_model().objects.create_user('ui_simple_coder_' + suffix, password=password)
project = Project.objects.create(name='简便工作台 · 纯虚构HTTP验收', owner=owner)
for user, role in [(owner, 'owner'), (coder, 'coder')]:
    Membership.objects.create(project=project, user=user, role=role)
book = Codebook.objects.create(project=project, version=1, title='纯虚构规则')
plan = Code.objects.create(book=book, key='PLAN', name='计划', definition='虚构：确定下一步', positive_example='先明确目标。')
Code.objects.create(book=book, key='CHECK', name='检查', definition='虚构：核对结果')


def login(user):
    status, _, _ = request('/login/', {'username': user.username, 'password': password})
    check('login_' + user.username.split('_')[2], status == 200)


login(owner)
workbook = Workbook()
workbook.active.title = '说明'
sheet = workbook.create_sheet('对话')
sheet.append(['Message Content', 'conversation_id', 'speaker'])
sheet.append(['先😊讨论，再检查', 'A', '虚构甲'])
sheet.append(['明确目标。', 'A', '虚构乙'])
output = io.BytesIO()
workbook.save(output)
workbook.close()
raw = output.getvalue()
materials_url = f'/p/{project.pk}/materials/'
status, page, _ = request(materials_url, {}, file=raw)
check('multi_sheet_empty_cover', status == 200 and '查看这张表' in page and '对话' in page)
draft = project.uploaddraft_set.get(user=owner)
status, page, _ = request(materials_url, {'draft_id': draft.pk, 'sheet_name': '对话', 'action': 'preview'})
check('switch_sheet_without_import', status == 200 and 'Message Content' in page and not project.documents.exists())
confirm = {'draft_id': draft.pk, 'sheet_name': '对话', 'text_column': 'Message Content', 'context_column': 'conversation_id'}
status, _, _ = request(materials_url, confirm)
doc = project.documents.get()
check('import_preserves_bytes_and_sheet', status == 200 and bytes(doc.raw) == raw and doc.sheet_name == '对话'
      and doc.sha256 == hashlib.sha256(raw).hexdigest() and doc.records.count() == 2)
request(materials_url, confirm)
check('duplicate_confirmation_does_not_duplicate', project.documents.count() == 1)

login(coder)
start_url = f'/p/{project.pk}/quick-start/'
start = {'book_id': book.pk, 'token': str(uuid.uuid4())}
request(start_url, start)
r = Round.objects.get(project=project, kind='quick')
status, page, url = request(f'/p/{project.pk}/code/')
check('continue_directly_with_real_rules', status == 200 and f'/r/{r.pk}/' in url and 'data-code-picker' in page
      and '先明确目标。' in page and '确认并下一条' in page)
a = r.assignments.order_by('id').first()
split_url = f'/api/assignments/{a.pk}/split/'
split = {'revision': 0, 'offset': 2, 'token': str(uuid.uuid4())}
status, _, _ = request(split_url, split, json_body=True, csrf=False)
check('split_requires_csrf', status == 403 and not a.segments.exists())
status, body, _ = request(split_url, split, json_body=True)
check('personal_split_success', status == 200 and json.loads(body)['next_url'].startswith(f'/r/{r.pk}/?a='))
request(split_url, split, json_body=True)
children = list(a.segments.select_related('unit').order_by('unit__start'))
check('split_replay_no_duplicate', len(children) == 2 and AuditEvent.objects.filter(project=project, action='annotation.split').count() == 1)
check('split_unicode_and_original_pool', children[0].unit.text == '先😊' and ''.join(c.unit.text for c in children) == a.unit.text
      and Unit.objects.filter(record__document=doc, active=True).count() == 2)
status, body, _ = request(f'/api/rounds/{r.pk}/progress/')
check('live_progress_without_original_parent', status == 200 and json.loads(body)['my_total'] == 3)
status, _, _ = request(f'/r/{r.pk}/?a={a.pk}')
check('old_link_reopens_live_work', status == 200)
save = {'revision': 0, 'primary': plan.pk, 'secondary': [], 'uncertain': False, 'no_code': False,
        'note': '纯虚构：根据计划定义选择', 'token': str(uuid.uuid4()), 'action': 'submit'}
status, body, _ = request(f'/api/assignments/{children[0].pk}/save/', save, json_body=True)
check('submit_acknowledges_next_segment', status == 200 and json.loads(body)['next_url'] == f'/r/{r.pk}/?a={children[1].pk}')
status, page, _ = request(f'/p/{project.pk}/history/?' + urlencode({'q': '纯虚构'}))
check('searchable_history_after_coding', status == 200 and '纯虚构：根据计划定义选择' in page)
status, csv_text, _ = request(f'/r/{r.pk}/export/mine/')
check('export_tracks_parent_without_double_count', status == 200 and 'parent_assignment_id' in csv_text
      and len(csv_text.strip().splitlines()) == 4)
status, _, _ = request(f'/api/assignments/{a.pk}/save/', {**save, 'token': split['token']}, json_body=True)
check('old_parent_cannot_be_saved', status == 409)

team = create_round(project, owner, book.pk, '纯虚构多人任务', 'pilot', [owner.pk, coder.pk], 0, 2026, 'all', 25)
team_a = team.assignments.get(coder=coder, unit=a.unit)
snapshot = list(team.assignments.values())
status, _, _ = request(f'/api/assignments/{team_a.pk}/split/', split, json_body=True)
check('shared_round_cannot_be_directly_split', status == 403)
status, body, _ = request(f'/api/assignments/{team_a.pk}/split-suggestion/', {**split, 'reason': '虚构：两个行为需分开'}, json_body=True)
check('shared_suggestion_preserves_all_answers', status == 200 and list(team.assignments.values()) == snapshot)
login(owner)
status, page, _ = request(f'/p/{project.pk}/units/')
check('owner_sees_suggestion', status == 200 and '虚构：两个行为需分开' in page)
status, _, _ = request(split_url, split, json_body=True)
check('cannot_split_another_member_task', status == 404)

login(coder)
for entry in r.assignments.filter(superseded=False, status='draft'):
    status, _, _ = request(f'/api/assignments/{entry.pk}/save/', {**save, 'token': str(uuid.uuid4())}, json_body=True)
    check('submit_remaining_' + str(entry.pk), status == 200)
r.refresh_from_db()
status, _, _ = request(f'/p/{project.pk}/quick/{r.pk}/finish/', {'revision': r.revision})
r.refresh_from_db()
check('personal_archive_ignores_replaced_parent', status == 200 and r.state == 'closed')

print(json.dumps({'passed': len(checks), 'checks': checks}, ensure_ascii=False))
