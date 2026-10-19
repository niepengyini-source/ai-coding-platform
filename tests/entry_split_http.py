"""Real HTTP entry/undo checks; restricted to the fictional localhost UI database."""
import json
import os
import re
import runpy
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

if os.environ.get('DJANGO_SETTINGS_MODULE') != 'config.ui_test_settings':
    raise SystemExit('Set DJANGO_SETTINGS_MODULE=config.ui_test_settings; never run against research data.')

import django
django.setup()
from django.conf import settings

if (settings.SETTINGS_MODULE != 'config.ui_test_settings' or
        Path(settings.DATABASES['default']['NAME']).resolve() != (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve() or
        settings.SESSION_COOKIE_NAME != 'ui_sessionid' or settings.CSRF_COOKIE_NAME != 'ui_csrftoken'):
    raise SystemExit('This script may only use the separate fictional UI settings and database.')

quick = runpy.run_path(str(Path(__file__).with_name('quick_mode_http.py')))
fixture = quick['fixture']
request, check = quick['request'], quick['check']
owner, project, round_ = quick['owner'], quick['project'], quick['round_']
credentials, cookies, codes = quick['credentials'], fixture['cookies'], quick['codes']
baseline = len(fixture['checks'])

from coding.models import Assignment, AuditEvent, Project, SourceRecord, Unit


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


direct = build_opener(HTTPCookieProcessor(cookies), NoRedirect())


def csrf_token():
    return next(cookie.value for cookie in cookies if cookie.name == 'ui_csrftoken')


def send(path, values=None, *, payload=None, csrf=True, multipart=None):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
        if csrf:
            headers['X-CSRFToken'] = csrf_token()
    elif multipart is not None:
        boundary = 'fiction-' + uuid.uuid4().hex
        filename, content = multipart
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n'
                f'{csrf_token()}\r\n--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                'Content-Type: text/csv\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    elif values is not None:
        values = dict(values)
        if csrf:
            values['csrfmiddlewaretoken'] = csrf_token()
        data = urlencode(values, doseq=True).encode()
    try:
        with direct.open(Request('http://127.0.0.1:8001' + path, data=data, headers=headers), timeout=20) as response:
            return response.status, response.read(), response.headers
    except HTTPError as error:
        return error.code, error.read(), error.headers


def create_project(label):
    name = '入口与撤回验收 · ' + label + ' · ' + uuid.uuid4().hex[:8]
    status, _, headers = send('/projects/new/', {'name': name, 'goal': '仅用于虚构界面验收', 'description': ''})
    created = Project.objects.get(owner=owner, name=name)
    check('entry_create_' + label, status == 302 and headers['Location'] == f'/p/{created.pk}/'
          and created.codebooks.count() == 1)
    return created


def upload_material(target):
    raw = ('group_id,created_at,speaker_id,content\nF1,2026-10-17 10:00,FictionA,'
           '虚构讨论。先确定目标，再检查结果。\n').encode('utf-8')
    url = f'/p/{target.pk}/materials/'
    status, body, _ = send(url, multipart=('entry-fiction.csv', raw))
    match = re.search(rb'name="draft_id" value="([^"]+)"', body)
    check('entry_material_preview', status == 200 and match is not None)
    status, _, headers = send(url, {'draft_id': match[1].decode(), 'sheet_name': '',
                                   'text_column': 'content', 'context_column': 'group_id'})
    check('entry_material_confirm', status == 302 and headers['Location'] == f'/p/{target.pk}/units/'
          and Unit.objects.filter(record__document__project=target, active=True).count() == 1)


def add_rule(target):
    book = target.codebooks.first()
    status, _, headers = send(f'/p/{target.pk}/books/{book.pk}/', {'revision': book.revision,
        'key': 'FICTION', 'name': '虚构类别', 'definition': '仅用于界面验收，不是真实研究规则'})
    check('entry_rule_added', status == 302 and headers['Location'] == f'/p/{target.pk}/books/{book.pk}/'
          and book.codes.count() == 1)


empty = create_project('尚未导入')
material_only = create_project('仅有材料')
rules_only = create_project('仅有规则')
upload_material(material_only)
add_rule(rules_only)

for target, destination, message in [
        (empty, f'/p/{empty.pk}/materials/', '尚未导入有效编码单元'),
        (material_only, f'/p/{material_only.pk}/books/{material_only.codebooks.first().pk}/', '没有可用的编码规则'),
        (rules_only, f'/p/{rules_only.pk}/materials/', '尚未导入有效编码单元')]:
    for suffix in ['code/', 'quick-start/']:
        status, _, headers = send(f'/p/{target.pk}/{suffix}')
        check(f'entry_redirect_{target.pk}_{suffix}', status == 302 and headers['Location'] == destination)
        status, body, _ = request(destination)
        check(f'entry_notice_{target.pk}_{suffix}', status == 200 and message.encode() in body)
        if suffix == 'code/':
            notice = next(item for item in re.findall(rb'<div class="notice[^"]*" role="status">(.*?)</div>', body, re.S)
                          if message.encode() in item)
            if destination.endswith('/materials/'):
                check(f'entry_notice_upload_action_{target.pk}',
                      f'href="/p/{target.pk}/materials/#material-upload"'.encode() in notice
                      and b'id="material-upload"' in body)
            else:
                check(f'entry_notice_rule_import_action_{target.pk}',
                      f'href="/p/{target.pk}/books/{target.codebooks.first().pk}/import/"'.encode() in notice)
    status, _, headers = send(f'/p/{target.pk}/quick-start/', {'book_id': target.codebooks.first().pk,
                                                            'token': str(uuid.uuid4())})
    check(f'entry_post_gate_{target.pk}', status == 302 and headers['Location'] == destination
          and not target.rounds.exists())
    for page in [f'/p/{target.pk}/', f'/p/{target.pk}/materials/', f'/p/{target.pk}/units/',
                 f'/p/{target.pk}/books/{target.codebooks.first().pk}/']:
        status, body, _ = request(page)
        check('entry_button_' + page, status == 200 and f'href="/p/{target.pk}/code/"'.encode() in body)

status, _, headers = send(f'/p/{project.pk}/code/')
check('entry_ready_resumes_round', status == 302 and headers['Location'] == f'/r/{round_.pk}/')

parent = round_.assignments.select_related('unit__record').get(pk=credentials['assignment'], coder=owner)
original_record = SourceRecord.objects.values().get(pk=parent.unit.record_id)
status, result = quick['save'](parent, primary=codes[0].pk, note='虚构拆分前的原句判断')
parent.refresh_from_db()
check('split_parent_draft_saved', status == 200 and parent.note == '虚构拆分前的原句判断')
status, body, _ = request(f'/r/{round_.pk}/export/mine/?format=json')
original_count = json.loads(body)['row_count']
check('split_baseline_export', status == 200 and original_count == 18)

split_payload = {'revision': parent.revision, 'book_id': round_.book_id, 'token': str(uuid.uuid4()),
                 'cuts': [5, 10], 'start': 6}
split_url = f'/api/assignments/{parent.pk}/split/'
check('split_csrf_required', send(split_url, payload=split_payload, csrf=False)[0] == 403)
check('split_get_read_only', send(split_url)[0] == 405)
status, body, _ = send(split_url, payload=split_payload)
split_result = json.loads(body)
children = list(parent.segments.filter(superseded=False).select_related('unit__record').order_by('unit__start'))
check('split_three_units_and_selected_child', status == 200 and len(children) == 3
      and split_result['next_url'] == f'/r/{round_.pk}/?a={children[1].pk}')
check('split_original_metadata_unchanged', SourceRecord.objects.values().get(pk=parent.unit.record_id) == original_record
      and ''.join(child.unit.text for child in children) == parent.unit.text
      and all(child.unit.record.metadata == original_record['metadata'] for child in children))
status, _, _ = send(split_url, payload=split_payload)
check('split_replay_is_idempotent', status == 200 and parent.segments.filter(superseded=False).count() == 3)
status, body, _ = request(f'/r/{round_.pk}/export/mine/?format=json')
rows = json.loads(body)['rows']
check('split_export_only_current_fragments', status == 200 and len(rows) == original_count + 2
      and not any(row['assignment_id'] == parent.pk for row in rows)
      and all(sum(row['assignment_id'] == child.pk for row in rows) == 1 for child in children))

child = children[1]
child_url = f'/r/{round_.pk}/?a={child.pk}'
status, body, _ = request(child_url)
match = re.search(rb'<script id="split-restore-data" type="application/json">(.*?)</script>', body, re.S)
check('split_child_has_restore_button_and_data', status == 200 and b'data-unsplit-confirm' in body
      and match is not None)
restore = json.loads(match[1])
check('split_child_restore_scope', restore['parent_id'] == parent.pk and restore['part_count'] == 3
      and not restore['requires_confirmation'])
status, result = quick['save'](child, primary=codes[1].pk, note='虚构片段判断：撤回后须保留历史')
child.refresh_from_db()
check('split_child_draft_saved', status == 200 and child.primary_id == codes[1].pk)
status, body, _ = request(child_url)
restore = json.loads(re.search(rb'<script id="split-restore-data" type="application/json">(.*?)</script>', body, re.S)[1])
check('split_coded_child_requests_confirmation', restore['requires_confirmation'] and restore['coded_count'] == 1)
undo_url = f'/api/assignments/{child.pk}/unsplit/'
undo = {**restore, 'revision': child.revision, 'book_id': round_.book_id, 'token': str(uuid.uuid4())}
check('undo_csrf_required', send(undo_url, payload=undo, csrf=False)[0] == 403)
check('undo_get_read_only', send(undo_url)[0] == 405)
check('undo_coded_child_needs_confirmation', send(undo_url, payload=undo)[0] == 409)
undo['confirm_coded'] = True
status, body, _ = send(undo_url, payload=undo)
undo_result = json.loads(body)
parent.refresh_from_db()
check('undo_restores_parent_and_original_coding', status == 200 and undo_result['next_url'] == f'/r/{round_.pk}/?a={parent.pk}'
      and not parent.superseded and parent.primary_id == codes[0].pk and parent.note == '虚构拆分前的原句判断')
check('undo_retains_child_and_source_history', parent.segments.filter(superseded=True).count() == 3
      and Assignment.objects.get(pk=child.pk).note == '虚构片段判断：撤回后须保留历史'
      and SourceRecord.objects.values().get(pk=parent.unit.record_id) == original_record)
status, body, _ = request(f'/p/{project.pk}/history/{child.pk}/')
check('undo_child_history_still_accessible', status == 200 and '虚构片段判断：撤回后须保留历史'.encode() in body)
status, body, _ = request(f'/r/{round_.pk}/export/mine/?format=json')
rows = json.loads(body)['rows']
check('undo_export_restores_original_once', status == 200 and len(rows) == original_count
      and sum(row['assignment_id'] == parent.pk for row in rows) == 1
      and not any(row['assignment_id'] in [item.pk for item in children] for row in rows))
restored_row = next(row for row in rows if row['assignment_id'] == parent.pk)
check('undo_export_preserves_metadata', all(restored_row['meta_' + key] == value
      for key, value in original_record['metadata'].items()))
status, body, _ = request(f'/api/rounds/{round_.pk}/progress/')
check('undo_progress_returns_original_count', status == 200 and json.loads(body)['my_total'] == original_count)
audit_count = AuditEvent.objects.filter(project=project, action='annotation.unsplit').count()
status, body, _ = send(undo_url, payload=undo)
check('undo_replay_is_idempotent', status == 200 and json.loads(body) == undo_result and
      AuditEvent.objects.filter(project=project, action='annotation.unsplit').count() == audit_count == 1)
check('undo_replay_cannot_change_payload', send(undo_url, payload={**undo, 'confirm_coded': False})[0] == 409)
check('old_split_replay_cannot_resplit', send(split_url, payload=split_payload)[0] == 409)

for username in [credentials['coder_username'], credentials['empty_username']]:
    request('/logout/', {})
    request('/login/')
    request('/login/', {'username': username, 'password': credentials['password']})
    check('undo_other_user_denied_' + username, send(undo_url, payload=undo)[0] == 404)
    check('entry_other_project_denied_' + username, send(f'/p/{empty.pk}/code/')[0] == 404)
request('/logout/', {})
request('/login/')
request('/login/', {'username': credentials['username'], 'password': credentials['password']})

browser_fixture = {'owner_username': owner.username, 'projects': {
    'empty': {'id': empty.pk, 'url': f'/p/{empty.pk}/'},
    'material_only': {'id': material_only.pk, 'url': f'/p/{material_only.pk}/',
                      'book': material_only.codebooks.first().pk},
    'rules_only': {'id': rules_only.pk, 'url': f'/p/{rules_only.pk}/', 'book': rules_only.codebooks.first().pk},
    'ready': {'id': project.pk, 'url': f'/p/{project.pk}/'}},
    'round': round_.pk, 'assignment': parent.pk, 'workbench_url': f'/r/{round_.pk}/?a={parent.pk}'}
(settings.RUNTIME_DIR / 'ui-entry-split-fixture.json').write_text(json.dumps(browser_fixture, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'entry_split_http_passed': len(fixture['checks']) - baseline,
                  'total_http_passed': len(fixture['checks']),
                  'browser_fixture': 'runtime/ui-entry-split-fixture.json'}, ensure_ascii=False))
