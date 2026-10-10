"""Real HTTP workflow checks using only fictional records in the UI-test database.

Start config.ui_test_settings at 127.0.0.1:8001, then run from the source root:
python -m tests.workflow_http
Never uses the research database or prints test passwords.
"""
import hashlib
import http.cookiejar
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
from django.db import transaction
from django.utils import timezone
from coding.models import Assignment, Code, Codebook, Membership, Project, Round, SourceDocument, SourceRecord, Unit


if Path(settings.DATABASES['default']['NAME']).resolve() != (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve():
    raise SystemExit('Only the separate ui-smoke database is allowed.')

BASE = 'http://127.0.0.1:8001'
cookies = http.cookiejar.CookieJar()
opener = build_opener(HTTPCookieProcessor(cookies))
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def request(path, data=None, json_body=False, csrf=True):
    body, headers = None, {}
    if data is not None:
        data = dict(data)
        if csrf:
            token = next(c.value for c in cookies if c.name == 'ui_csrftoken')
            if json_body:
                headers['X-CSRFToken'] = token
            else:
                data['csrfmiddlewaretoken'] = token
        if json_body:
            body = json.dumps(data).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        else:
            body = urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        with opener.open(Request(BASE + path, data=body, headers=headers), timeout=15) as response:
            return response.status, response.read().decode('utf-8'), response.geturl()
    except HTTPError as error:
        return error.code, error.read().decode('utf-8'), error.geturl()


status, _, _ = request('/login/')
check('isolated_test_server', status == 200 and any(c.name == 'ui_csrftoken' for c in cookies)
      and not any(c.name == 'csrftoken' for c in cookies))

suffix = uuid.uuid4().hex
password = uuid.uuid4().hex + '-Synthetic!'
with transaction.atomic():
    owner = get_user_model().objects.create_user('ui_flow_owner_' + suffix, password=password)
    coder = get_user_model().objects.create_user('ui_flow_coder_' + suffix, password=password)
    project = Project.objects.create(name='快速编码 · 纯虚构HTTP检查', owner=owner,
                                     goal='独立测试个人开始、历史修改和进度，不含真实研究材料。')
    Membership.objects.create(project=project, user=owner, role='owner')
    Membership.objects.create(project=project, user=coder, role='coder')
    book = Codebook.objects.create(project=project, version=1, title='纯虚构规则')
    code = Code.objects.create(book=book, key='PLAN', name='计划', definition='虚构规则：提出下一步。')
    Code.objects.create(book=book, key='CHECK', name='检查', definition='虚构规则：核对结果。')
    raw = 'text\n先明确目标。\n再检查结果。\n'.encode('utf-8')
    document = SourceDocument.objects.create(project=project, name='synthetic-workflow.csv',
        sha256=hashlib.sha256(raw).hexdigest(), raw=raw, text_column='text')
    for index, text in enumerate(['先明确目标。', '再检查结果。'], 1):
        record = SourceRecord.objects.create(document=document, position=index, text=text, context_key='fictional')
        Unit.objects.create(record=record, text=text, start=0, end=len(text))

status, _, _ = request('/login/', {'username': coder.username, 'password': password})
check('ordinary_member_login', status == 200 and any(c.name == 'ui_sessionid' for c in cookies))
quick_url = f'/p/{project.pk}/quick-start/'
status, page, _ = request(quick_url)
check('quick_page_is_read_only', status == 200 and '不需要填写任务安排表' in page
      and not Round.objects.filter(project=project).exists())
start = {'book_id': book.pk, 'token': str(uuid.uuid4())}
status, _, _ = request(quick_url, start, csrf=False)
check('quick_start_csrf', status == 403 and not Round.objects.filter(project=project).exists())
status, page, url = request(quick_url, start)
r = Round.objects.get(project=project, kind='quick')
check('quick_start_self_only', status == 200 and f'/r/{r.pk}/' in url and '这是个人快速编码' in page
      and r.started_by_id == coder.pk and r.assignments.count() == 2
      and not r.assignments.exclude(coder=coder).exists())
book.refresh_from_db()
check('rule_version_fixed', book.frozen)
request(quick_url, start)
check('quick_start_replay', Round.objects.filter(project=project).count() == 1)
first, second = list(r.assignments.order_by('id'))


def save(a, action='save', **extra):
    a.refresh_from_db()
    payload = {'revision': a.revision, 'primary': code.pk, 'secondary': [], 'no_code': False,
               'uncertain': False, 'note': '原判断 · 纯虚构', 'action': action, 'token': str(uuid.uuid4()), **extra}
    result = request(f'/api/assignments/{a.pk}/save/', payload, json_body=True)
    a.refresh_from_db()
    return result, payload


result, _ = save(first)
check('real_draft_save', result[0] == 200 and first.revision == 1 and first.status == 'draft')
result, old_payload = save(second, 'submit')
check('real_submission', result[0] == 200 and second.status == 'submitted')
progress_url = f'/api/projects/{project.pk}/progress/'
status, page, _ = request(progress_url)
data = json.loads(page)
check('progress_separates_draft', status == 200 and data['my'] == {
    'total': 2, 'submitted': 1, 'saved': 1, 'unstarted': 0, 'remaining': 1, 'percent': 50.0})
check('progress_member_privacy', 'team' not in data and 'members' not in data and '原判断' not in page)
status, page, _ = request(f'/p/{project.pk}/progress/')
check('progress_page_renders', status == 200 and 'data-progress-endpoint' in page and '已保存草稿' in page)
history_url = f'/p/{project.pk}/history/'
status, page, _ = request(history_url + '?' + urlencode({'q': '目标'}))
check('quick_history_search', status == 200 and '找到 1 条记录' in page)
status, page, _ = request(history_url + '?' + urlencode({'mode': 'exact', 'q': '目标'}))
check('exact_search_does_not_match_substring', status == 200 and '找到 0 条记录' in page)
status, page, _ = request(history_url + '?' + urlencode({'mode': 'exact', 'q': '先明确目标。'}))
check('exact_history_search', status == 200 and '找到 1 条记录' in page)
status, page, _ = request(history_url + '?' + urlencode({'unit_id': first.unit_id, 'code': 'PLAN',
    'status': 'draft', 'filename': document.name}))
check('combined_precise_conditions', status == 200 and '找到 1 条记录' in page
      and f'?a={first.pk}' in page)
start_minute = timezone.localtime(first.updated_at).strftime('%Y-%m-%dT%H:%M')
end_minute = timezone.localtime(second.updated_at).strftime('%Y-%m-%dT%H:%M')
status, page, _ = request(history_url + '?' + urlencode({'start': start_minute, 'end': end_minute}))
check('minute_range_includes_seconds', status == 200 and '找到 2 条记录' in page)
status, page, _ = request(history_url + '?start=invalid-date')
check('invalid_date_does_not_return_all', status == 200 and '找到 0 条记录' in page and 'errorlist' in page)

reopen_url = f'/p/{project.pk}/history/{second.pk}/reopen/'
reason = '纯虚构修改原因：补充判断依据'
status, page, url = request(reopen_url, {'revision': second.revision, 'reason': reason})
second.refresh_from_db()
check('submitted_personal_record_reopens', status == 200 and f'?a={second.pk}' in url
      and second.status == 'draft' and second.note == '原判断 · 纯虚构')
status, _, _ = request(f'/api/assignments/{second.pk}/save/', old_payload, json_body=True)
check('old_save_cannot_overwrite_reopened_record', status == 409)
result, _ = save(second, 'submit', note='新判断 · 纯虚构')
check('corrected_submission_saved', result[0] == 200 and second.note == '新判断 · 纯虚构')
status, page, _ = request(f'/p/{project.pk}/history/{second.pk}/')
check('before_after_and_reason_remain', status == 200 and '原判断 · 纯虚构' in page
      and '新判断 · 纯虚构' in page and reason in page)
save(first, 'submit')
status, _, _ = request(f'/p/{project.pk}/quick/{r.pk}/finish/', {'revision': r.revision})
r.refresh_from_db()
check('personal_archive_not_fake_consensus', status == 200 and r.state == 'closed' and not r.decisions.exists())
result, _ = save(second, note='不得覆盖归档')
check('archived_answer_locked', result[0] == 409 and second.note == '新判断 · 纯虚构')
request('/logout/', {})
print(json.dumps({'passed': len(checks), 'checks': checks,
                  'scope': 'local real HTTP; fictional isolated database; not browser or multi-device QA'},
                 ensure_ascii=False))
