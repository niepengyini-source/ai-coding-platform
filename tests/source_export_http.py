"""Source-preserving exports over real HTTP; isolated fictional UI database only."""
import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime

import journey_http as journey  # Enforces UI settings, localhost:8001 and UI cookies.
from task_selection_http import hidden
from django.conf import settings
from django.contrib.auth import get_user_model
from openpyxl import Workbook, load_workbook
from coding.exporting import annotation_table
from coding.models import SourceDocument


def register(name, password):
    session = journey.Session()
    session.page('/accounts/register/')
    response = session.send('/accounts/register/', {'username': name, 'password1': password, 'password2': password})
    journey.check('register fictional source-export user', response[0] == 302)
    user = get_user_model().objects.get(username=name)
    session.login(user, password)
    return user, session


def material(session, project, filename, raw, sheet=''):
    path = f'/p/{project.pk}/materials/'
    session.page(path, project)
    response = session.send(path, upload=(filename, raw))
    journey.check('preview fictional material ' + filename, response[0] == 200)
    draft = hidden(response[1].decode(), 'draft_id')
    response = session.send(path, {'draft_id': draft, 'sheet_name': sheet, 'text_column': 'content',
                                   'context_column': 'group_id' if filename.endswith('.csv') else ''})
    journey.check('confirm fictional material ' + filename, response[0] == 302)
    return project.documents.get(name=filename)


def download(session, project, r, **changes):
    values = {'target': 'mine', 'round': r.pk, 'layout': 'source', 'format': 'source', 'filename': '虚构原表编码结果',
              'encoding': 'utf-8-sig', 'delimiter': ',', 'row_match': 'quick', 'selection': 'all', 'action': 'download'}
    values.update(changes)
    before = journey.business_state(project)
    result = session.send(f'/p/{project.pk}/exports/', values)
    journey.check('export POST does not alter coding or history', journey.business_state(project) == before)
    return result


def csv_rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    return reader.fieldnames, list(reader)


def main():
    suffix, password = uuid.uuid4().hex[:10], uuid.uuid4().hex + '-Source!'
    owner, session = register('qa_source_owner_' + suffix, password)
    _, outsider_session = register('qa_source_outsider_' + suffix, password)
    project = journey.create_project(session, owner, '原材料导出 · 虚构验收 · ' + suffix)
    book, _ = journey.prepare_project(session, project)
    heads = ['message_id', 'condition', 'group_id', 'sender_name', 'sender_type', 'agent_role', 'content',
             'created_at', 'primary_code', 'uncertain', 'source_message_id', 'source_content_preview_snapshot']
    text = '先确认任务，再核对结果。'
    records = [['SRC1', 'fictional', 'FG-SOURCE', '虚构学生甲', 'SenderType.student', '', text,
                '2026-10-22T12:00:00+08:00', 'OLD_A', 'yes', '', ''],
               ['SRC2', 'fictional', 'FG-SOURCE', '虚构智能体', 'SenderType.agent', 'facilitator', '请检查已有结果。',
                '2026-10-22T12:01:00+08:00', 'OLD_B', 'no', 'SRC1', '虚构引用摘要（不完整）']]
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(heads)
    writer.writerows(records)
    source = material(session, project, 'source-fiction.csv', stream.getvalue().encode('utf-8-sig'))
    workbook, excel = Workbook(), io.BytesIO()
    sheet = workbook.active
    sheet.title = '虚构材料'
    sheet.append(['message_id', 'content', 'sender_name', 'sender_type', 'created_at', 'score'])
    sheet.append(['XLSX1', '虚构Excel材料：计划后检查。', '虚构学生乙', 'SenderType.student', datetime(2026, 10, 22, 12), 3.5])
    workbook.save(excel)
    workbook.close()
    excel_source = material(session, project, 'source-fiction.xlsx', excel.getvalue(), '虚构材料')
    originals = {doc.pk: hashlib.sha256(bytes(doc.raw)).hexdigest() for doc in project.documents.all()}
    path = f'/p/{project.pk}/quick-start/'
    page = session.page(path, project)
    response = session.send(path, {'book_id': book.pk, 'token': hidden(page, 'token')})
    r = project.rounds.get(kind='quick', started_by=owner)
    journey.check('quick coding includes all five source units', response[0] == 302 and r.assignments.count() == 5)
    parent = r.assignments.select_related('unit__record').get(unit__record__document=source, unit__record__position=1)
    journey.check('old source codes are not imported as answers', parent.primary_id is None and not parent.uncertain)
    page = session.page(f'/r/{r.pk}/?a={parent.pk}', project)
    journey.check('source speaker facts remain available in workbench', '虚构学生甲' in page and '需复核' in page)
    reference_task = r.assignments.get(unit__record__document=source, unit__record__position=2)
    page = session.page(f'/r/{r.pk}/?a={reference_task.pk}', project)
    current_material = re.search(r'id="reading-current">(.*?)<blockquote id="current-unit-text"', page, re.S)
    reference = re.search(r'<details class="source-reference">(.*?)</details>', current_material[1], re.S) if current_material else None
    journey.check('current unit resolves the exact same-group source message rather than its preview', reference is not None
                  and '引用消息 · 原记录 1' in reference[1] and text in reference[1]
                  and '虚构引用摘要（不完整）' not in reference[1])
    before = journey.business_state(project)
    status, raw, headers = session.send(f'/api/assignments/{parent.pk}/context/?direction=after&cursor=1')
    journey.check('dynamic context with a reference returns JSON instead of a SQL error', status == 200
                  and headers.get_content_type() == 'application/json')
    context = json.loads(raw)
    information = context['records'][0]['source_information']
    journey.check('dynamic context resolves quoted text and keeps recipient separate', context['records'][0]['position'] == 2
                  and information['reference']['position'] == 1 and information['reference']['text'] == text
                  and all(item['label'] != '明确接收者' for item in information['items']))
    journey.check('dynamic reference lookup leaves original records and coding unchanged', journey.business_state(project) == before)
    response = session.send(f'/api/assignments/{parent.pk}/split/', payload={
        'cuts': [text.index('，') + 1], 'revision': parent.revision, 'book_id': book.pk, 'token': str(uuid.uuid4())})
    children = list(parent.segments.select_related('unit').order_by('unit__start'))
    journey.check('HTTP split creates two lossless coding fragments', response[0] == 200 and len(children) == 2
                  and ''.join(child.unit.text for child in children) == text)
    journey.submit(session, children[0], book.codes.get(key='PLAN'), action='save', note='虚构已确认判断')
    payload = journey.submit(session, children[1], book.codes.get(key='CHECK'), action='save', note='虚构需复核原因')
    payload.update(revision=children[1].revision, uncertain=True, token=str(uuid.uuid4()))
    journey.check('HTTP saves review flag without reversing its meaning', session.send(
        f'/api/assignments/{children[1].pk}/save/', payload=payload)[0] == 200)
    selected = [str(child.pk) for child in children]
    choice = {'selection': 'selected', 'selected': selected, 'visible_ids': ','.join(selected)}
    journey.check('source export still requires CSRF', session.send(f'/p/{project.pk}/exports/',
        {'target': 'mine', 'round': r.pk, 'layout': 'source', 'format': 'source', 'action': 'download'}, csrf=False)[0] == 403)
    status, raw, headers = download(session, project, r, **choice)
    exported_heads, exported = csv_rows(raw)
    journey.check('source CSV download has original order and exact selected count', status == 200
                  and 'attachment;' in headers.get('Content-Disposition', '') and exported_heads[:len(heads)] == heads
                  and len(exported) == 2 and headers.get('Cache-Control') == 'no-store')
    journey.check('fragments retain full original statement and all source fields', all(
        [row[key] for key in heads] == records[0] for row in exported)
        and [row['平台_编码片段'] for row in exported] == [child.unit.text for child in children])
    journey.check('platform review labels and codes do not overwrite raw legacy columns',
        [row['平台_复核标记'] for row in exported] == ['已确认', '需复核']
        and [row['平台_主代码'] for row in exported] == ['PLAN', 'CHECK']
        and all(row['primary_code'] == 'OLD_A' and row['uncertain'] == 'yes' for row in exported))
    status, raw, headers = download(session, project, r)
    journey.check('multiple sources download one ZIP', status == 200 and headers.get_content_type() == 'application/zip')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        journey.check('source ZIP has one result per original source', archive.testzip() is None and len(archive.namelist()) == 3)
        csv_name = next(name for name in archive.namelist() if name.startswith('source-fiction_') and name.endswith('.csv'))
        _, rows = csv_rows(archive.read(csv_name))
        journey.check('unmodified raw no marker remains no and unsaved AI stays unjudged', any(
            row['message_id'] == 'SRC2' and row['uncertain'] == 'no' and row['primary_code'] == 'OLD_B'
            and row['平台_复核标记'] == '未判断' for row in rows))
        excel_name = next(name for name in archive.namelist() if name.endswith('.xlsx'))
        book_output = load_workbook(io.BytesIO(archive.read(excel_name)))
        try:
            output = book_output.active
            journey.check('Excel numeric and datetime values survive source export', output.cell(2, 6).value == 3.5
                          and output.cell(2, 5).value == datetime(2026, 10, 22, 12))
            journey.check('source Excel header has no artificial fill', all(cell.fill.patternType is None for cell in output[1]))
        finally:
            book_output.close()
    status, raw, _ = download(session, project, r, format='xlsx', **choice)
    book_output = load_workbook(io.BytesIO(raw))
    try:
        journey.check('selected CSV fragments can also export as plain Excel', status == 200
                      and book_output.active.max_row == 3 and book_output.active.cell(2, 7).value == text
                      and all(cell.fill.patternType is None for cell in book_output.active[1]))
    finally:
        book_output.close()
    status, raw, headers = download(session, project, r, selection='selected', selected=['999999999'], visible_ids='999999999')
    journey.check('forged selection is rejected rather than exporting another answer', status == 200
                  and 'Content-Disposition' not in headers and '不在当前范围'.encode() in raw)
    journey.check('nonmember cannot open or export another users answers', outsider_session.send(
        f'/r/{r.pk}/?a={children[0].pk}')[0] == 404 and outsider_session.send(
        f'/p/{project.pk}/exports/?target=mine&round={r.pk}')[0] == 404)
    journey.check('active independent answers are not opened by source layout', session.send(
        f'/p/{project.pk}/exports/?target=all&round={r.pk}')[0] == 403)
    table = annotation_table(r, r.assignments.filter(coder=owner, superseded=False))
    status, raw, _ = download(session, project, r, layout='platform', format='json', fields=table.headers)
    legacy = json.loads(raw)
    journey.check('full platform JSON remains available', status == 200 and legacy['row_count'] == 6
                  and all('assignment_id' in row and 'unit_id' in row for row in legacy['rows']))
    journey.check('uploaded original bytes remain unchanged', originals == {
        doc.pk: hashlib.sha256(bytes(doc.raw)).hexdigest() for doc in SourceDocument.objects.filter(project=project)})
    export_url = f'/p/{project.pk}/exports/?target=mine&round={r.pk}'
    session.page(export_url, project)
    fixture = {'username': owner.username, 'password': password, 'project': project.pk, 'round': r.pk,
               'assignment': children[0].pk, 'review_assignment': children[1].pk,
               'reference_assignment': reference_task.pk, 'export_url': export_url,
               'source_document': source.pk, 'excel_document': excel_source.pk}
    (settings.RUNTIME_DIR / 'source-export-qa-credentials.json').write_text(json.dumps(fixture), encoding='utf-8')
    print(json.dumps({'passed': len(journey.checks), 'assets': len(journey.assets),
                      'scope': 'real HTTP/CSRF; isolated fictional UI database',
                      'browser_fixture': 'runtime/source-export-qa-credentials.json'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
