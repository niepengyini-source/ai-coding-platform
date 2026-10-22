"""Append platform results to the selected records' original source columns.

The caller supplies an already permission-filtered ExportTable. This module never
adds unselected source rows, including blank or deduplicated import rows.
"""
import hashlib
import io
import re
import zipfile
from collections import OrderedDict
from copy import copy
from datetime import date, datetime, time
from pathlib import Path

from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header

from .exporting import ExportTable, MAX_EXPORT_ROWS, export_response
from .importing import excel_workbook, parse_upload
from .models import Assignment, Decision, SourceDocument, Unit


def _source_rows(document, positions):
    """Read headers and exact source values, preserving Excel scalar types."""
    raw = bytes(document.raw)
    try:
        if hashlib.sha256(raw).hexdigest() != document.sha256:
            raise ValueError('原文件完整性校验未通过。')
        headers, rows = parse_upload(document.name, raw, document.sheet_name or None)
        if document.text_column not in headers or any(pos < 1 or pos > len(rows) for pos in positions):
            raise ValueError('原始列或行位置无法对应。')
        selected = {pos: [rows[pos - 1][key] for key in headers] for pos in positions}
        if Path(document.name).suffix.lower() == '.xlsx':
            workbook = excel_workbook(raw)
            try:
                sheet = workbook[document.sheet_name] if document.sheet_name else workbook.worksheets[0]
                for position, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 1):
                    if position in positions:
                        selected[position] = list(values[:len(headers)])
                    if position >= max(positions):
                        break
            finally:
                workbook.close()
        return headers, rows, selected
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ValueError(f'原文件“{document.name}”无法准确对应，请改用“完整平台数据”导出。') from error


def _unique_labels(headers, labels):
    used, result = set(headers), []
    for label in labels:
        base, suffix = '平台_' + label, 2
        candidate = base
        while candidate in used:
            candidate = f'{base}_{suffix}'
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _filename(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:100] or '编码结果'


def _iso(value):
    return value.isoformat() if isinstance(value, (date, datetime, time)) else value


def _xlsx_response(document, source_headers, labels, rows, entries, filename):
    """Copy the selected source table's ordinary cell layout, without decoration."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    for row in [labels] + rows:
        if any(isinstance(value, str) and len(value) > 32767 for value in row):
            raise ValueError('某字段超过Excel单元格32767字限制，请改用JSON或CSV，平台不会截断内容。')
        if any(isinstance(value, str) and any(ord(char) < 32 and char not in '\n\r\t' for char in value)
               for value in row):
            raise ValueError('数据含Office不支持的控制字符，请选择JSON或CSV；平台不会删除原文。')
    original = None
    workbook = Workbook(write_only=True)
    try:
        if Path(document.name).suffix.lower() == '.xlsx':
            # _source_rows has already applied the bounded XML/ZIP import checks.
            original = load_workbook(io.BytesIO(bytes(document.raw)), read_only=False,
                                     data_only=False, keep_links=False)
        source = (original[document.sheet_name] if document.sheet_name else original.worksheets[0]) if original else None
        sheet = workbook.create_sheet(source.title if source else '编码数据')
        if source:
            workbook.loaded_theme = original.loaded_theme
            sheet.sheet_format = copy(source.sheet_format)
            for letter, dimension in source.column_dimensions.items():
                if dimension.min <= len(source_headers):
                    target = sheet.column_dimensions[letter]
                    for attribute in ('width', 'hidden', 'bestFit', 'outlineLevel', 'collapsed', 'min'):
                        setattr(target, attribute, getattr(dimension, attribute))
                    target.max = min(dimension.max, len(source_headers))
                    # Register style components in the new workbook, not source indexes.
                    for attribute in ('font', 'border', 'alignment', 'fill', 'protection', 'number_format'):
                        setattr(target, attribute, copy(getattr(dimension, attribute)))
            sheet.freeze_panes = source.freeze_panes
        else:
            sheet.freeze_panes = 'A2'
        for index in range(len(source_headers) + 1, len(labels) + 1):
            sheet.column_dimensions[get_column_letter(index)].width = 24
        positions = [1] + [entry['record_position'] + 1 for entry in entries]
        for output_row, (values, source_row) in enumerate(zip([labels] + rows, positions), 1):
            if source and source_row in source.row_dimensions:
                dimension = source.row_dimensions[source_row]
                sheet.row_dimensions[output_row].height = dimension.height
                sheet.row_dimensions[output_row].hidden = dimension.hidden
            cells = []
            for column, value in enumerate(values, 1):
                cell = WriteOnlyCell(sheet, value=value)
                if isinstance(value, str):
                    cell.data_type = 's'  # Source formulas remain literal text, never executable.
                if source:
                    previous = source.cell(source_row, min(column, len(source_headers)))
                    cell.font, cell.border = copy(previous.font), copy(previous.border)
                    cell.alignment = copy(previous.alignment)
                    if column <= len(source_headers):
                        cell.protection = copy(previous.protection)
                        cell.number_format, cell.fill = previous.number_format, copy(previous.fill)
                else:
                    cell.font = Font(name='宋体', size=11)
                    cell.alignment = Alignment(wrap_text=True, vertical='top')
                if output_row == 1:
                    cell.fill = PatternFill(fill_type=None)
                    color = cell.font.color
                    if color and ((color.type == 'rgb' and color.rgb[-6:].upper() == 'FFFFFF')
                                  or (color.type == 'indexed' and color.indexed == 9)
                                  or (color.type == 'theme' and color.theme == 0)):
                        font = copy(cell.font)
                        font.color = '000000'
                        cell.font = font
                    if not cell.has_style:
                        # Keep an explicit no-fill cell style over any colored column style.
                        cell.alignment = Alignment(horizontal='general')
                cells.append(cell)
            sheet.append(cells)
        data = io.BytesIO()
        workbook.save(data)
    finally:
        workbook.close()
        if original:
            original.close()
    response = HttpResponse(data.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = content_disposition_header(True, _filename(filename) + '.xlsx')
    response['Cache-Control'] = 'no-store'
    return response


def source_export_response(project, table, file_format, filename, *, encoding='utf-8-sig', delimiter=','):
    """Export one file per source, without changing the uploaded source itself."""
    if file_format not in ('source', 'csv', 'tsv', 'xlsx', 'json'):
        raise ValueError('原材料样式支持按原格式、Excel、CSV、TSV或JSON导出。')
    required = {'document_id', 'record_position', 'unit_id', 'unit_text', 'span_start', 'span_end'}
    if not required.issubset(table.headers) or len(set(table.headers)) != len(table.headers):
        raise ValueError('当前内容不支持原材料样式，请使用完整平台数据。')
    if not table.rows:
        raise ValueError('没有可导出的记录，请先调整筛选条件。')
    if len(table.rows) > MAX_EXPORT_ROWS:
        raise ValueError('一次最多导出50000行，请按轮次或编码员分别导出。')

    groups = OrderedDict()
    values = []
    for row in table.rows:
        if len(row) != len(table.headers):
            raise ValueError('导出记录格式已变化，请刷新后重试。')
        entry = dict(zip(table.headers, row))
        if entry.get('project_id', project.pk) != project.pk:
            raise ValueError('导出记录不属于当前项目。')
        for key in ('document_id', 'record_position', 'unit_id'):
            if isinstance(entry[key], bool) or not isinstance(entry[key], int) or entry[key] < 1:
                raise ValueError('导出记录来源不正确，请刷新后重试。')
        groups.setdefault(entry['document_id'], []).append(entry)
        values.append(entry)

    units = {unit.pk: unit for unit in Unit.objects.filter(
        pk__in={entry['unit_id'] for entry in values}, record__document__project=project).select_related('record')}
    for entry in values:
        unit = units.get(entry['unit_id'])
        if (unit is None or unit.record.document_id != entry['document_id']
                or unit.record.position != entry['record_position'] or unit.text != entry['unit_text']
                or unit.start != entry['span_start'] or unit.end != entry['span_end']):
            raise ValueError('编码单元与原始材料无法对应，请刷新后重试。')

    # Fetch only timestamps/provenance for the selected, caller-visible answers.
    answer_times = {}
    for field, model in (('assignment_id', Assignment), ('decision_id', Decision)):
        ids = {entry[field] for entry in values if entry.get(field)}
        if not ids:
            continue
        answers = {answer.pk: answer for answer in model.objects.filter(
            pk__in=ids, round__project=project).select_related('round').only(
                'id', 'unit_id', 'round_id', 'revision', 'updated_at', 'round__name')}
        for entry in values:
            if not entry.get(field):
                continue
            answer = answers.get(entry[field])
            if (answer is None or answer.unit_id != entry['unit_id'] or answer.round_id != entry.get('round_id')
                    or answer.revision != entry.get('revision')):
                raise ValueError('编码记录在导出期间发生变化，请刷新后重试。')
            answer_times[(field, answer.pk)] = (answer.updated_at, answer.round.name)

    documents = SourceDocument.objects.filter(project=project, pk__in=groups).order_by('pk')
    if documents.count() != len(groups):
        raise ValueError('部分原文件不属于当前项目，请重新选择记录。')
    result = None
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, 'w', zipfile.ZIP_DEFLATED) as archive:
        for document in documents.iterator(chunk_size=1):
            entries = groups[document.pk]
            headers, normalized, originals = _source_rows(document, {entry['record_position'] for entry in entries})
            has_codes = 'primary_code' in table.headers
            has_decision = 'decision_id' in table.headers
            additions = ['编码片段']
            if has_codes:
                additions += ['主代码', '次代码', '无适用代码', '复核标记', '编码备注',
                              '定稿者' if has_decision else '编码员', '保存时间', '记录状态', '轮次']
            additions += ['单元编号', '原记录行号', '原文起点', '原文终点']
            if 'active' in table.headers:
                additions += ['单元有效', '单元调整说明']
            if 'assignment_id' in table.headers:
                additions += ['编码任务编号', '拆分来源任务']
            labels = headers + _unique_labels(headers, additions)
            rows = []
            for entry in entries:
                unit, position = units[entry['unit_id']], entry['record_position']
                source = normalized[position - 1]
                if (source[document.text_column] != unit.record.text
                        or {key: value for key, value in source.items() if key != document.text_column}
                        != unit.record.metadata):
                    raise ValueError(f'原文件“{document.name}”与导入记录不一致，请改用完整平台数据。')
                extra = [entry['unit_text']]
                if has_codes:
                    judged = bool(entry.get('revision') and (entry.get('primary_code') or entry.get('no_code')
                                                           or entry.get('uncertain')))
                    review = '需复核' if judged and entry.get('uncertain') else '已确认' if judged else '未判断'
                    field = 'decision_id' if has_decision else 'assignment_id'
                    stamp, round_name = answer_times.get((field, entry.get(field)), (None, entry.get('round_name', table.title)))
                    saved_at = timezone.localtime(stamp).isoformat() if stamp and entry.get('revision') else ''
                    state = ('定稿记录' if has_decision else
                             '未保存' if not entry.get('revision') else
                             {'draft': '草稿', 'submitted': '已提交'}.get(entry.get('status'), entry.get('status', '')))
                    extra += [entry.get('primary_code', ''), entry.get('secondary_code', ''),
                              ('是' if entry.get('no_code') else '否') if judged else '', review,
                              entry.get('decision_note' if has_decision else 'coder_note', ''),
                              entry.get('decided_by' if has_decision else 'coder', ''), saved_at, state, round_name]
                extra += [unit.pk, position, unit.start, unit.end]
                if 'active' in table.headers:
                    extra += ['是' if entry['active'] else '否', entry.get('unit_note', '')]
                if 'assignment_id' in table.headers:
                    extra += [entry['assignment_id'], entry.get('parent_assignment_id', '')]
                rows.append(originals[position] + extra)
            chosen_format = file_format
            if file_format == 'source':
                chosen_format = 'csv' if Path(document.name).suffix.lower() == '.csv' else 'xlsx'
            if chosen_format != 'xlsx':
                rows = [[_iso(value) for value in row] for row in rows]
            output = ExportTable(labels, rows, table.title)
            result = (_xlsx_response(document, headers, labels, rows, entries, filename) if chosen_format == 'xlsx' else
                      export_response(output, labels, labels, chosen_format, filename,
                                      encoding=encoding, delimiter=delimiter))
            if len(groups) > 1:
                source_name = Path(document.name).stem
                if document.sheet_name:
                    source_name += '_' + document.sheet_name
                archive.writestr(f'{_filename(source_name)}_来源{document.pk}.{chosen_format}', result.content)
    if len(groups) == 1:
        return result
    response = HttpResponse(archive_data.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = content_disposition_header(True, _filename(filename) + '.zip')
    response['Cache-Control'] = 'no-store'
    return response
