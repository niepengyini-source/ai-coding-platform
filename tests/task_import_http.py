"""Local real-HTTP regression; fictional UI database only, not research data."""
import csv
import io
import json
import os
import re
import runpy
import sys
import uuid
from pathlib import Path

if os.environ.get('DJANGO_SETTINGS_MODULE') != 'config.ui_test_settings':
    raise SystemExit('Use only config.ui_test_settings and localhost:8001.')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
entry = runpy.run_path(str(Path(__file__).with_name('entry_split_http.py')))
send, request, check = entry['send'], entry['request'], entry['check']
project, owner, round_ = entry['project'], entry['owner'], entry['round_']
baseline = len(entry['fixture']['checks'])

from coding.codebook_files import FIELDS
from coding.models import CodebookImportDraft, SourceRecord, Round
from coding.workflow import coding_assignments

last = coding_assignments(round_, owner).last()
status, body, _ = request(f'/r/{round_.pk}/?a={last.pk}')
check('last_task_button_and_choices', status == 200 and b'data-coding-last disabled' in body
      and b'data-task-save' in body and '已经是最后一个了'.encode() in body)
status, body, _ = request(f'/api/rounds/{round_.pk}/progress/?current={last.pk}')
data = json.loads(body)
check('last_task_real_pending_private_url', status == 200 and data['my_remaining'] > 0
      and data['next_unfinished_url'].startswith(f'/r/{round_.pk}/?a='))
peer = round_.assignments.filter(coder=entry['quick']['fixture']['coder']).first()
check('last_task_peer_current_denied', request(f'/api/rounds/{round_.pk}/progress/?current={peer.pk}')[0] == 404)

def material_upload(raw):
    url = f'/p/{project.pk}/materials/'
    status, body, _ = send(url, multipart=('fictional-append.csv', raw))
    draft = re.search(rb'name="draft_id" value="([^"]+)"', body)
    check('append_upload_preview', status == 200 and draft is not None)
    return send(url, {'draft_id': draft[1].decode(), 'text_column': 'content', 'context_column': 'group_id'})

raw = 'id,group_id,created_at,content\nF1,G1,2026-10-17 10:00,虚构新增材料。\n'
before = SourceRecord.objects.filter(document__project=project).count()
check('append_initial_material', material_upload(raw.encode())[0] == 302)
check('append_exact_file_retry', material_upload(raw.encode())[0] == 302
      and SourceRecord.objects.filter(document__project=project).count() == before + 1)
mixed = raw + 'F1,G1,2026-10-17 10:01,虚构新增材料。\nF1,G1,2026-10-17 10:01,虚构新增材料。\n'
check('append_same_text_different_time_kept', material_upload(mixed.encode())[0] == 302
      and SourceRecord.objects.filter(document__project=project).count() == before + 2)
check('append_old_tasks_unchanged', coding_assignments(round_, owner).count() == 18)

book = project.codebooks.first()
first = book.codes.first()
headers = [field for field, _, _ in FIELDS]
values = {field: (first.parent.key if first.parent else '') if field == 'parent_key' else getattr(first, field)
          for field in headers}
stream = io.StringIO(newline='')
writer = csv.DictWriter(stream, fieldnames=headers)
writer.writeheader(); writer.writerow(values)
new_rule = {field: '' for field in headers}
new_rule.update(key='ADDED', name='虚构新增规则', definition='仅用于导入验收', color='#4372e8')
writer.writerow(new_rule); writer.writerow(new_rule)

def book_upload(target, raw):
    url = f'/p/{project.pk}/books/{target.pk}/import/'
    # The entry helper's multipart path handles materials only; send rule fields explicitly.
    boundary = 'fiction-' + uuid.uuid4().hex
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n{entry["csrf_token"]()}\r\n'
               f'--{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\nupload\r\n'
               f'--{boundary}\r\nContent-Disposition: form-data; name="auto_preview"\r\n\r\nyes\r\n'
               f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fictional-rules.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() + raw + f'\r\n--{boundary}--\r\n'.encode()
    from urllib.request import Request
    with entry['direct'].open(Request('http://127.0.0.1:8001'+url, data=payload,
            headers={'Content-Type':'multipart/form-data; boundary='+boundary}), timeout=20) as response:
        return response.status, response.read(), response.headers

# NoRedirect intentionally raises for the normal successful preview redirect.
from urllib.error import HTTPError
try:
    book_upload(book, stream.getvalue().encode())
except HTTPError as error:
    check('rules_merge_preview_redirect', error.code == 302 and 'stage=preview' in error.headers['Location'])
draft = CodebookImportDraft.objects.filter(book=book, user=owner).latest('created_at')
check('rules_preview_counts', draft.preview['import_stats']['duplicate_count'] == 2
      and len(draft.preview['codes']) == 1 and draft.preview['settings']['mode'] == 'merge')
status, _, _ = send(f'/p/{project.pk}/books/{book.pk}/import/', {'action':'confirm','draft_id':str(draft.pk),'confirmed':'yes'})
current = project.codebooks.first()
check('rules_merge_keeps_existing_and_old_tasks', status == 302 and current.pk != book.pk
      and current.codes.count() == book.codes.count() + 1 and round_.book_id == book.pk)
try:
    book_upload(current, stream.getvalue().encode())
except HTTPError as error:
    check('rules_repeat_preview_redirect', error.code == 302 and 'stage=preview' in error.headers['Location'])
draft = CodebookImportDraft.objects.filter(book=current, user=owner).latest('created_at')
versions = project.codebooks.count()
status, _, _ = send(f'/p/{project.pk}/books/{current.pk}/import/', {'action':'confirm','draft_id':str(draft.pk),'confirmed':'yes'})
check('rules_repeat_no_new_version', status == 302 and project.codebooks.count() == versions
      and project.codebooks.first().pk == current.pk)

status, body, _ = request('/projects/')
check('project_progress_visible', status == 200 and b'workspace-coding-count' in body and b'/18' in body)
complete_project = entry['create_project']('已完成进度')
entry['upload_material'](complete_project)
entry['add_rule'](complete_project)
complete_book = complete_project.codebooks.first()
status, _, _ = send(f'/p/{complete_project.pk}/quick-start/', {'book_id':complete_book.pk,'token':str(uuid.uuid4())})
complete_round = Round.objects.get(project=complete_project, kind='quick', started_by=owner)
complete_task = complete_round.assignments.get(coder=owner)
status, _, _ = send(f'/api/assignments/{complete_task.pk}/save/', payload={
    'revision':complete_task.revision,'token':str(uuid.uuid4()),'book_id':complete_book.pk,'action':'submit',
    'primary':complete_book.codes.first().pk,'secondary':[],'uncertain':False,'no_code':False,'note':'虚构完成验收'})
check('completion_task_confirmed', status == 200)
status, body, _ = request(f'/api/rounds/{complete_round.pk}/progress/?current={complete_task.pk}')
check('completion_zero_pending', status == 200 and json.loads(body)['my_remaining'] == 0
      and json.loads(body)['next_unfinished_url'] == '')
status, body, _ = request('/projects/')
check('completed_project_green_indicator', status == 200 and b'workspace-coding-count is-complete' in body
      and '1/1<strong>已完成</strong>'.encode() in body)
print(json.dumps({'task_import_http_passed':len(entry['fixture']['checks'])-baseline,
                  'total_http_passed':len(entry['fixture']['checks'])}, ensure_ascii=False))
