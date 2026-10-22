"""Original-table exports use fictional sources and never modify uploaded bytes."""
import csv
import hashlib
import io
import json
import zipfile
from copy import copy
from datetime import datetime
from unittest.mock import patch
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Color, Font, PatternFill, Side

from .exporting import ExportTable, annotation_table, final_table, unit_table
from .importing import parse_upload
from .models import Assignment, Code, Codebook, Decision, Project, Round, SourceDocument, SourceRecord, Unit
from .source_exports import source_export_response
from .tests import TEST_SETTINGS


@override_settings(**TEST_SETTINGS)
class SourceExportsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('source_owner')
        self.peer = get_user_model().objects.create_user('source_peer')
        self.project = Project.objects.create(owner=self.user, name='虚构原表研究')
        self.book = Codebook.objects.create(project=self.project, version=1, policy='multi')
        self.code = Code.objects.create(book=self.book, key='PLAN', name='计划', definition='虚构定义')
        self.r = Round.objects.create(project=self.project, book=self.book, kind='pilot', name='虚构第一轮')
        self.document, self.units = self.source('原表.csv', ['time', 'content', 'group', 'uncertain', 'primary_code'], [
            ['08:00', '先核对，再查资料。', 'G1', 'yes', '原人工标签'],
            ['08:01', '相同句子', 'G1', 'no', ''],
            ['08:02', '相同句子', 'G2', 'yes', ''],
        ])
        self.assignments = [Assignment.objects.create(round=self.r, unit=unit, coder=self.user)
                            for unit in self.units]

    def source(self, name, headers=None, rows=None, *, raw=None, sheet='', text_column='content'):
        if raw is None:
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)
            raw = stream.getvalue().encode('utf-8-sig')
        document = SourceDocument.objects.create(project=self.project, name=name, raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(), text_column=text_column, sheet_name=sheet)
        _, parsed = parse_upload(name, raw, sheet or None)
        units = []
        for position, row in enumerate(parsed, 1):
            record = SourceRecord.objects.create(document=document, position=position, text=row[text_column],
                metadata={key: value for key, value in row.items() if key != text_column})
            units.append(Unit.objects.create(record=record, text=record.text, end=len(record.text)))
        return document, units

    def table(self, assignments=None):
        return annotation_table(self.r, self.r.assignments.filter(
            pk__in=[a.pk for a in (self.assignments if assignments is None else assignments)]))

    def mark(self, assignment=None, **changes):
        assignment = assignment or self.assignments[0]
        values = {'primary': self.code, 'revision': 1, 'note': '虚构判断依据'}
        values.update(changes)
        Assignment.objects.filter(pk=assignment.pk).update(**values)
        assignment.refresh_from_db()
        return assignment

    def export(self, table=None, format='json', **options):
        return source_export_response(self.project, table or self.table(), format, '虚构结果', **options)

    def json_rows(self, table=None):
        response = self.export(table)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('attachment;', response['Content-Disposition'])
        return json.loads(response.content)['rows']

    def workbook(self, response):
        return load_workbook(io.BytesIO(response.content), read_only=False, data_only=False)

    def test_original_headers_order_and_values_before_platform_fields(self):
        self.mark()
        rows = self.json_rows(self.table([self.assignments[0]]))
        self.assertEqual(list(rows[0])[:5], ['time', 'content', 'group', 'uncertain', 'primary_code'])
        self.assertEqual(rows[0]['primary_code'], '原人工标签')
        self.assertEqual(rows[0]['uncertain'], 'yes')
        self.assertEqual(rows[0]['平台_主代码'], 'PLAN')
        self.assertEqual(rows[0]['平台_复核标记'], '已确认')
        self.assertEqual(rows[0]['平台_编码备注'], '虚构判断依据')
        self.assertEqual(rows[0]['平台_轮次'], self.r.name)
        self.assertTrue(rows[0]['平台_保存时间'])

    def test_platform_review_flag_never_uses_original_yes_no(self):
        self.mark(self.assignments[0], uncertain=True)
        self.mark(self.assignments[1], uncertain=False)
        rows = self.json_rows()
        self.assertEqual([row['平台_复核标记'] for row in rows], ['需复核', '已确认', '未判断'])
        self.assertEqual([row['uncertain'] for row in rows], ['yes', 'no', 'yes'])
        self.assertEqual(rows[2]['平台_保存时间'], '')
        self.assertEqual(rows[2]['平台_记录状态'], '未保存')

    def test_empty_saved_draft_is_not_claimed_confirmed(self):
        self.mark(primary=None, note='只保存了备注')
        row = self.json_rows()[0]
        self.assertEqual(row['平台_复核标记'], '未判断')
        self.assertEqual(row['平台_记录状态'], '草稿')

    def test_explicit_no_applicable_code_is_not_a_blank_answer(self):
        self.mark(primary=None, no_code=True, status='submitted')
        row = self.json_rows()[0]
        self.assertEqual(row['平台_无适用代码'], '是')
        self.assertEqual(row['平台_复核标记'], '已确认')
        self.assertEqual(row['平台_记录状态'], '已提交')

    def test_split_rows_repeat_complete_original_data_and_keep_individual_labels(self):
        record = self.units[0].record
        left = Unit.objects.create(record=record, text=record.text[:5], start=0, end=5)
        right = Unit.objects.create(record=record, text=record.text[5:], start=5, end=len(record.text))
        children = [Assignment.objects.create(round=self.r, coder=self.user, unit=unit,
                    primary=self.code, revision=1, parent=self.assignments[0]) for unit in (left, right)]
        rows = self.json_rows(self.table(children))
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['content'] for row in rows], [record.text, record.text])
        self.assertEqual([row['平台_编码片段'] for row in rows], [left.text, right.text])
        self.assertEqual([row['time'] for row in rows], ['08:00', '08:00'])
        self.assertEqual([row['group'] for row in rows], ['G1', 'G1'])
        self.assertEqual([row['平台_拆分来源任务'] for row in rows], [self.assignments[0].pk] * 2)

    def test_selected_source_position_not_matching_text_chooses_row(self):
        row = self.json_rows(self.table([self.assignments[2]]))[0]
        self.assertEqual(row['content'], '相同句子')
        self.assertEqual(row['time'], '08:02')
        self.assertEqual(row['group'], 'G2')
        self.assertEqual(row['平台_原记录行号'], 3)

    def test_unselected_blank_and_deduplicated_source_rows_are_not_appended(self):
        stream = io.StringIO(newline='')
        csv.writer(stream).writerows([['content'], ['重复'], ['重复'], [''], ['选中']])
        document, units = self.source('跳过行.csv', raw=stream.getvalue().encode())
        units[1].delete()
        units[2].delete()
        assignment = Assignment.objects.create(round=self.r, unit=units[3], coder=self.user)
        rows = self.json_rows(self.table([assignment]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content'], '选中')
        self.assertEqual(rows[0]['平台_原记录行号'], 4)

    def test_multiple_coders_are_separate_rows_without_fabricating_consensus(self):
        self.mark()
        peer = Assignment.objects.create(round=self.r, unit=self.units[0], coder=self.peer,
            uncertain=True, note='虚构另一位判断', revision=1)
        rows = self.json_rows(self.table([self.assignments[0], peer]))
        self.assertEqual([row['平台_编码员'] for row in rows], [self.user.username, self.peer.username])
        self.assertEqual([row['平台_复核标记'] for row in rows], ['已确认', '需复核'])
        self.assertTrue(all('平台_定稿者' not in row for row in rows))

    def test_conflicting_platform_headings_do_not_overwrite_original(self):
        _, units = self.source('同名列.csv', ['content', '平台_主代码', '平台_主代码_2'], [['原句', '旧值1', '旧值2']])
        assignment = Assignment.objects.create(round=self.r, unit=units[0], coder=self.user, primary=self.code, revision=1)
        row = self.json_rows(self.table([assignment]))[0]
        self.assertEqual(row['平台_主代码'], '旧值1')
        self.assertEqual(row['平台_主代码_2'], '旧值2')
        self.assertEqual(row['平台_主代码_3'], 'PLAN')

    def test_final_results_keep_author_time_and_cannot_be_misread_as_independent(self):
        Decision.objects.create(round=self.r, unit=self.units[0], primary=self.code, author=self.user,
                                note='虚构协商依据', uncertain=True)
        row = self.json_rows(final_table(self.r))[0]
        self.assertEqual(row['平台_定稿者'], self.user.username)
        self.assertEqual(row['平台_记录状态'], '定稿记录')
        self.assertEqual(row['平台_复核标记'], '需复核')
        self.assertEqual(row['平台_编码备注'], '虚构协商依据')
        self.assertTrue(row['平台_保存时间'])

    def test_material_export_only_adds_unit_information(self):
        row = self.json_rows(unit_table(self.project))[0]
        self.assertIn('平台_编码片段', row)
        self.assertIn('平台_单元有效', row)
        self.assertNotIn('平台_主代码', row)
        self.assertNotIn('平台_复核标记', row)

    def test_source_csv_and_explicit_tsv_json_formats(self):
        for format, separator in [('source', ','), ('csv', ','), ('tsv', '\t')]:
            response = self.export(format=format)
            values = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=separator))
            self.assertEqual(values[0][:5], ['time', 'content', 'group', 'uncertain', 'primary_code'])
            self.assertEqual(values[1][1], self.units[0].record.text)
            suffix = '.tsv' if format == 'tsv' else '.csv'
            self.assertTrue(response['Content-Disposition'].rstrip('"').endswith(suffix))

    def test_multiple_source_files_are_separate_unique_safe_zip_members(self):
        _, other_units = self.source('原表.csv', ['content', 'other'], [['另一份原文', '保留']])
        assignment = Assignment.objects.create(round=self.r, unit=other_units[0], coder=self.user)
        response = self.export(self.table([self.assignments[0], assignment]), format='source')
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertEqual(response['Cache-Control'], 'no-store')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            self.assertEqual(len(set(archive.namelist())), 2)
            self.assertTrue(all('/' not in name and '\\' not in name for name in archive.namelist()))
            outputs = [list(csv.reader(io.StringIO(archive.read(name).decode('utf-8-sig')))) for name in archive.namelist()]
        self.assertEqual([len(rows) for rows in outputs], [2, 2])
        self.assertEqual(outputs[0][0][:5], ['time', 'content', 'group', 'uncertain', 'primary_code'])
        self.assertEqual(outputs[1][0][:2], ['content', 'other'])

    def excel_source(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = '真实选定表'
        sheet.append(['number', 'created', 'flag', 'content', 'formula'])
        sheet.append([12.5, datetime(2026, 10, 22, 8, 30), True, 'Excel原句', '=2+3'])
        sheet.column_dimensions['A'].width = 18
        sheet.column_dimensions['D'].width = 46
        sheet.row_dimensions[2].height = 48
        sheet['B2'].number_format = 'yyyy-mm-dd hh:mm'
        sheet['D2'].font = Font(name='Arial', size=13, italic=True)
        sheet['D2'].alignment = Alignment(wrap_text=True, horizontal='left')
        sheet['D2'].border = Border(bottom=Side(style='thin', color='333333'))
        for cell in sheet[1]:
            cell.fill = PatternFill('solid', fgColor='116699')
            cell.font = Font(name='Arial', size=12, color='FFFFFF')
        unused = workbook.create_sheet('不要导出')
        unused.append(['private'])
        unused.append(['不属于选定材料'])
        stream = io.BytesIO()
        workbook.save(stream)
        workbook.close()
        document, units = self.source('输入.xlsx', raw=stream.getvalue(), sheet=sheet.title)
        assignment = Assignment.objects.create(round=self.r, unit=units[0], coder=self.user)
        return document, assignment

    def test_excel_original_types_and_formula_text_preserved_without_execution(self):
        _, assignment = self.excel_source()
        workbook = self.workbook(self.export(self.table([assignment]), format='source'))
        try:
            sheet = workbook.active
            self.assertEqual(workbook.sheetnames, ['真实选定表'])
            self.assertEqual(sheet['A2'].value, 12.5)
            self.assertEqual(sheet['B2'].value, datetime(2026, 10, 22, 8, 30))
            self.assertIs(sheet['C2'].value, True)
            self.assertEqual(sheet['E2'].value, '=2+3')
            self.assertEqual(sheet['E2'].data_type, 's')
        finally:
            workbook.close()

    def test_excel_layout_retains_plain_source_style_but_headers_have_no_fill(self):
        _, assignment = self.excel_source()
        workbook = self.workbook(self.export(self.table([assignment]), format='xlsx'))
        try:
            sheet = workbook.active
            self.assertTrue(all(cell.fill.fill_type is None for cell in sheet[1]))
            self.assertEqual(sheet.column_dimensions['A'].width, 18)
            self.assertEqual(sheet.column_dimensions['D'].width, 46)
            self.assertEqual(sheet.row_dimensions[2].height, 48)
            self.assertEqual(sheet['D2'].font.name, 'Arial')
            self.assertEqual(sheet['D2'].font.sz, 13)
            self.assertTrue(sheet['D2'].font.italic)
            self.assertTrue(sheet['D2'].alignment.wrap_text)
            self.assertEqual(sheet['D2'].border.bottom.style, 'thin')
            self.assertEqual(sheet['B2'].number_format, 'yyyy-mm-dd hh:mm')
            self.assertEqual(sheet['A1'].font.color.rgb, '00000000')
        finally:
            workbook.close()

    def test_csv_to_excel_has_no_colored_header(self):
        workbook = self.workbook(self.export(format='xlsx'))
        try:
            self.assertTrue(all(cell.fill.fill_type is None for cell in workbook.active[1]))
            self.assertEqual(workbook.active['F2'].font.name, '宋体')
            self.assertEqual(workbook.active['F2'].font.sz, 11)
        finally:
            workbook.close()

    def test_added_excel_columns_follow_source_row_style_without_fill_or_number_format(self):
        document, assignment = self.excel_source()
        original = load_workbook(io.BytesIO(bytes(document.raw)))
        source = original.active
        source['E1'].font = Font(name='Calibri', size=14, bold=True)
        source['E1'].alignment = Alignment(horizontal='center', vertical='center')
        source['E1'].border = Border(top=Side(style='thin', color='444444'))
        source['E2'].font = Font(name='Calibri', size=10, italic=True)
        source['E2'].alignment = Alignment(horizontal='right', vertical='top', wrap_text=True)
        source['E2'].border = Border(bottom=Side(style='double', color='222222'))
        source['E2'].number_format = 'yyyy-mm-dd'
        source['E2'].fill = PatternFill('solid', fgColor='DDDDDD')
        stream = io.BytesIO()
        original.save(stream)
        original.close()
        raw = stream.getvalue()
        SourceDocument.objects.filter(pk=document.pk).update(raw=raw, sha256=hashlib.sha256(raw).hexdigest())
        exported = self.workbook(self.export(self.table([assignment]), format='xlsx'))
        try:
            sheet = exported.active
            for source_cell, appended in ((sheet['E1'], sheet[1][5:]), (sheet['E2'], sheet[2][5:])):
                for cell in appended:
                    self.assertEqual(copy(cell.font), copy(source_cell.font))
                    self.assertEqual(copy(cell.alignment), copy(source_cell.alignment))
                    self.assertEqual(copy(cell.border), copy(source_cell.border))
                    self.assertEqual(cell.number_format, 'General')
                    self.assertIsNone(cell.fill.fill_type)
            self.assertEqual(sheet['E2'].number_format, 'yyyy-mm-dd')
            self.assertEqual(sheet['E2'].fill.fgColor.rgb[-6:], 'DDDDDD')
        finally:
            exported.close()

    def test_column_style_is_registered_in_export_workbook_not_reused_by_index(self):
        document, assignment = self.excel_source()
        original = load_workbook(io.BytesIO(bytes(document.raw)))
        column = original.active.column_dimensions['A']
        column.font = Font(name='Courier New', size=18, color='AA1100')
        column.number_format = '000.00'
        column.alignment = Alignment(horizontal='right')
        stream = io.BytesIO()
        original.save(stream)
        original.close()
        raw = stream.getvalue()
        SourceDocument.objects.filter(pk=document.pk).update(raw=raw, sha256=hashlib.sha256(raw).hexdigest())
        exported = self.workbook(self.export(self.table([assignment]), format='xlsx'))
        try:
            column = exported.active.column_dimensions['A']
            self.assertEqual(column.font.name, 'Courier New')
            self.assertEqual(column.font.sz, 18)
            self.assertEqual(column.font.color.rgb[-6:], 'AA1100')
            self.assertEqual(column.number_format, '000.00')
            self.assertEqual(column.alignment.horizontal, 'right')
            self.assertEqual(column.width, 18)
        finally:
            exported.close()

    def test_default_header_has_explicit_no_fill_style_over_colored_source_column(self):
        original = Workbook()
        sheet = original.active
        sheet.append(['content'])
        sheet.append(['原句'])
        sheet.column_dimensions['A'].fill = PatternFill('solid', fgColor='FF3300')
        stream = io.BytesIO()
        original.save(stream)
        original.close()
        _, units = self.source('整列填色.xlsx', raw=stream.getvalue())
        assignment = Assignment.objects.create(round=self.r, unit=units[0], coder=self.user)
        response = self.export(self.table([assignment]), format='xlsx')
        namespace = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            sheet_xml = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            styles = ElementTree.fromstring(archive.read('xl/styles.xml')).find('x:cellXfs', namespace)
            header = sheet_xml.find(".//x:c[@r='A1']", namespace)
            column = sheet_xml.find('x:cols/x:col', namespace)
            self.assertIsNotNone(header.get('s'))
            self.assertEqual(styles[int(header.get('s'))].get('fillId'), '0')
            self.assertNotEqual(styles[int(column.get('style'))].get('fillId'), '0')

    def test_original_theme_is_retained_for_theme_colored_source_fonts(self):
        document, assignment = self.excel_source()
        original = load_workbook(io.BytesIO(bytes(document.raw)))
        self.assertIn(b'4F81BD', original.loaded_theme)
        theme = original.loaded_theme.replace(b'4F81BD', b'AA3300')
        original.loaded_theme = theme
        original.active['D2'].font = Font(name='Arial', color=Color(theme=4))
        stream = io.BytesIO()
        original.save(stream)
        original.close()
        raw = stream.getvalue()
        SourceDocument.objects.filter(pk=document.pk).update(raw=raw, sha256=hashlib.sha256(raw).hexdigest())
        exported = self.workbook(self.export(self.table([assignment]), format='xlsx'))
        try:
            self.assertEqual(exported.loaded_theme, theme)
            self.assertEqual(exported.active['D2'].font.color.theme, 4)
            self.assertTrue(all(cell.fill.fill_type is None for cell in exported.active[1]))
        finally:
            exported.close()

    def test_excel_to_json_serializes_original_dates(self):
        _, assignment = self.excel_source()
        row = self.json_rows(self.table([assignment]))[0]
        self.assertEqual(row['created'], '2026-10-22T08:30:00')
        self.assertEqual(row['number'], 12.5)
        self.assertIs(row['flag'], True)

    def test_plain_text_defaults_to_an_uncolored_excel_table(self):
        _, units = self.source('原文.txt', raw='原文第一行\n原文第二行'.encode('utf-8'), text_column='text')
        assignment = Assignment.objects.create(round=self.r, unit=units[1], coder=self.user)
        workbook = self.workbook(self.export(self.table([assignment]), format='source'))
        try:
            sheet = workbook.active
            self.assertEqual(sheet['A1'].value, 'text')
            self.assertEqual(sheet['A2'].value, '原文第二行')
            self.assertTrue(all(cell.fill.fill_type is None for cell in sheet[1]))
        finally:
            workbook.close()

    def test_mixed_source_formats_keep_each_sources_native_table_format(self):
        _, assignment = self.excel_source()
        response = self.export(self.table([self.assignments[0], assignment]), format='source')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual({name.rsplit('.', 1)[-1] for name in archive.namelist()}, {'csv', 'xlsx'})
            for name in archive.namelist():
                if name.endswith('.xlsx'):
                    workbook = load_workbook(io.BytesIO(archive.read(name)))
                    try:
                        self.assertEqual(workbook.active['A2'].value, 12.5)
                        self.assertTrue(all(cell.fill.fill_type is None for cell in workbook.active[1]))
                    finally:
                        workbook.close()

    def test_csv_formula_injection_and_filename_are_safely_encoded(self):
        _, units = self.source('攻击名.csv', ['content'], [['=HYPERLINK("test")']])
        assignment = Assignment.objects.create(round=self.r, unit=units[0], coder=self.user)
        response = source_export_response(self.project, self.table([assignment]), 'csv', '../坏名\r\n"')
        row = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))[1]
        self.assertEqual(row[0], '\'=HYPERLINK("test")')
        self.assertNotIn('\r', response['Content-Disposition'])
        self.assertNotIn('\n', response['Content-Disposition'])

    def test_wrong_source_position_and_modified_raw_never_guess_matching_text(self):
        table = self.table([self.assignments[0]])
        table.rows[0][table.headers.index('record_position')] = 2
        with self.assertRaisesMessage(ValueError, '无法对应'):
            self.export(table)
        SourceDocument.objects.filter(pk=self.document.pk).update(raw=b'content\nchanged\n')
        with self.assertRaisesMessage(ValueError, '完整平台数据'):
            self.export(self.table([self.assignments[0]]))

    def test_missing_and_corrupt_source_are_clear_errors(self):
        for raw in (b'', b'\x00bad'):
            SourceDocument.objects.filter(pk=self.document.pk).update(raw=raw)
            with self.assertRaisesMessage(ValueError, '完整平台数据'):
                self.export(self.table([self.assignments[0]]))

    def test_foreign_project_unit_and_stale_answer_are_rejected(self):
        self.mark()
        table = self.table([self.assignments[0]])
        Assignment.objects.filter(pk=self.assignments[0].pk).update(revision=2)
        with self.assertRaisesMessage(ValueError, '发生变化'):
            self.export(table)
        table = self.table([self.assignments[0]])
        table.rows[0][table.headers.index('project_id')] = self.project.pk + 1
        with self.assertRaisesMessage(ValueError, '不属于当前项目'):
            self.export(table)

    def test_unsupported_empty_and_oversized_requests_are_rejected(self):
        table = self.table()
        with self.assertRaisesMessage(ValueError, '没有可导出'):
            self.export(ExportTable(table.headers, []))
        with self.assertRaisesMessage(ValueError, '支持'):
            self.export(format='docx')
        with patch('coding.source_exports.MAX_EXPORT_ROWS', 2):
            with self.assertRaisesMessage(ValueError, '50000'):
                self.export(table)

    def test_export_gets_no_business_writes_and_preserves_original_bytes(self):
        before = bytes(self.document.raw)
        with CaptureQueriesContext(connection) as queries:
            self.export(self.table())
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
                             for query in queries))
        self.document.refresh_from_db()
        self.assertEqual(bytes(self.document.raw), before)
        self.assertEqual(Assignment.objects.filter(round=self.r).count(), 3)
        self.assertEqual(self.r.assignments.filter(revision__gt=0).count(), 0)
