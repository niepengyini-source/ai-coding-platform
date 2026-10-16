"""Row selection over the export center's existing permission-filtered tables."""
import json

from django import forms

from .exporting import MAX_EXPORT_ROWS


class ExportRowsForm(forms.Form):
    row_query = forms.CharField(label='搜索记录', required=False, max_length=250,
        widget=forms.TextInput(attrs={'placeholder': '语句、代码、编码员、组别或编号'}))
    row_match = forms.ChoiceField(label='搜索方式', choices=[('quick', '包含关键词'), ('exact', '完整匹配')])
    row_metadata_field = forms.ChoiceField(label='原始字段', required=False)
    row_metadata_value = forms.CharField(label='字段值', required=False, max_length=250)
    row_status = forms.ChoiceField(label='编码状态', required=False,
        choices=[('', '全部状态'), ('unsaved', '未保存'), ('draft', '已保存草稿'), ('submitted', '已提交')])
    selection = forms.ChoiceField(label='导出范围', choices=[('all', '全部筛选结果'), ('first', '前 N 条'),
                                                               ('selected', '我勾选的记录')])
    limit = forms.IntegerField(label='导出条数', required=False, min_value=1, max_value=MAX_EXPORT_ROWS)

    def __init__(self, params, *, table, records, download=False):
        data = params.copy()
        data.setdefault('row_match', 'quick')
        data.setdefault('selection', 'all')
        super().__init__(data)
        self.download = download
        metadata_keys = set()
        for record in records:
            metadata_keys.update(record['metadata_values'])
        self.fields['row_metadata_field'].choices = [('', '— 请选择 —')] + [(key, key) for key in sorted(metadata_keys)]
        if 'status' not in table.headers:
            del self.fields['row_status']

    def clean(self):
        data = super().clean()
        if bool(data.get('row_metadata_field')) != bool(data.get('row_metadata_value')):
            self.add_error('row_metadata_value', '请同时选择原始字段并填写字段值。')
        if self.download and data.get('selection') == 'first' and not data.get('limit'):
            self.add_error('limit', '请填写要导出的条数。')
        return data


def table_records(table):
    """Use persistent database IDs, not row positions that could change on refresh."""
    identity = 'unit_id'
    if 'assignment_id' in table.headers:
        identity = 'assignment_id'
    elif 'decision_id' in table.headers and 'independent_labels_json' not in table.headers:
        identity = 'decision_id'
    for row in table.rows:
        values = dict(zip(table.headers, row))
        metadata = json.loads(values.get('metadata_json') or '{}')
        yield {'key': str(values[identity]), 'row': row, 'values': values,
               'text': values.get('unit_text', ''), 'primary': values.get('primary_code', ''),
               'secondary': values.get('secondary_code', ''), 'coder': values.get('coder', ''),
               'position': values.get('record_position', ''), 'metadata_values': metadata,
               'metadata': ' · '.join(f'{key}: {value}' for key, value in metadata.items()),
               'status': ('未保存' if not values.get('revision') else
                          {'draft': '草稿', 'submitted': '已提交'}.get(values.get('status'), ''))
                         if 'status' in values else ''}


def filter_records(records, data):
    exact = data.get('row_match') == 'exact'

    def matches(value, query):
        text = str(value).casefold()
        return text == query if exact else query in text

    query = data.get('row_query', '').casefold()
    metadata_key = data.get('row_metadata_field')
    metadata_value = data.get('row_metadata_value', '').casefold()
    status = data.get('row_status')
    result = []
    for record in records:
        values = record['values']
        if query:
            searchable = [values.get(key, '') for key in ('unit_id', 'assignment_id', 'unit_text',
                          'primary_code', 'coder', 'coder_note', 'document_name', 'record_position')]
            searchable.extend(str(values.get('secondary_code', '')).split('|'))
            searchable.extend(record['metadata_values'].values())
            if not any(matches(value, query) for value in searchable):
                continue
        if metadata_key and (metadata_key not in record['metadata_values'] or
                not matches(record['metadata_values'][metadata_key], metadata_value)):
            continue
        if status == 'unsaved' and values.get('revision'):
            continue
        if status in ('draft', 'submitted') and (values.get('status') != status or not values.get('revision')):
            continue
        result.append(record)
    return result


def selected_record_ids(params, allowed_ids):
    """Merge page checkboxes with earlier pages, rechecking visibility on every POST."""
    def parse(value):
        parts = value.split(',') if value else []
        if len(parts) > MAX_EXPORT_ROWS or any(not part.isascii() or not part.isdecimal() or len(part) > 20 for part in parts):
            raise ValueError('勾选记录无效，请重新筛选并勾选。')
        return set(parts)

    retained = parse(params.get('retained_selected', ''))
    visible = parse(params.get('visible_ids', ''))
    checked = set(params.getlist('selected'))
    if len(checked) > MAX_EXPORT_ROWS or (retained | visible | checked) - allowed_ids:
        raise ValueError('部分记录已变化或不在当前范围，请重新筛选并勾选。')
    return (retained - visible) | checked
