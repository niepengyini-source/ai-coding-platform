from django import forms
from .codebook_files import FIELDS, suggest_mapping
from .exporting import FORMATS, BASE_LABELS


class CodebookMappingForm(forms.Form):
    mode = forms.ChoiceField(label='导入方式')
    title = forms.CharField(label='编码本名称（另存为新版本时使用）', max_length=120)
    policy = forms.ChoiceField(label='新版本标签方式', choices=[('single', '单标签'), ('multi', '主代码＋可选次代码')])
    change_note = forms.CharField(label='本次导入说明', max_length=2000,
                                  widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, book, headers, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('new', '另存为新版本（已有版本保留）')]
        if not book.frozen:
            choices.append(('append', '加入当前草稿（已有规则保留）'))
        self.fields['mode'].choices = choices
        guessed = suggest_mapping(headers)
        for field, label, required in FIELDS:
            if field == 'name':
                label, required = '显示名称来源', False
                empty_label = '沿用编码编号或原代码文字（无需单独名称列）'
            else:
                empty_label = '— 请选择对应列 —' if required else '— 不导入这一字段 —'
            self.fields['map_' + field] = forms.ChoiceField(label=label + ('（必填）' if required else '（可选）'),
                choices=[('', empty_label)] + [(h, h) for h in headers],
                required=required, initial=guessed[field])

    def optional_mapping_fields(self):
        return [self['map_' + field] for field, _, _ in FIELDS if field not in ('key', 'definition')]

    def mapping(self):
        return {field: self.cleaned_data.get('map_' + field, '') for field, _, _ in FIELDS}


class ExportOptionsForm(forms.Form):
    format = forms.ChoiceField(label='文件格式')
    filename = forms.CharField(label='文件名称（不用填写扩展名）', max_length=100)
    encoding = forms.ChoiceField(label='CSV / TSV字符编码', choices=[('utf-8-sig', 'UTF-8（推荐，Excel可直接打开）'),
                                                                  ('gb18030', 'GB18030（部分中文软件）')])
    delimiter = forms.ChoiceField(label='CSV分隔符', choices=[(',', '逗号（标准CSV）'), (';', '分号'), ('\t', '制表符')])
    fields = forms.MultipleChoiceField(label='导出字段', widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, table, is_book=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.table = table
        self.fields['format'].choices = FORMATS + ([('docx', 'Word文档（.docx，编码本）')] if is_book else [])
        self.fields['fields'].choices = [(h, BASE_LABELS.get(h, '原始附加字段') + ' · ' + h) for h in table.headers]
        for index, key in enumerate(table.headers):
            meaning = BASE_LABELS.get(key, key)
            self.fields[f'label_{index}'] = forms.CharField(label='列名', max_length=250, required=False, initial=key,
                    widget=forms.TextInput(attrs={'aria-label': meaning + '的导出列名'}))
            self.fields[f'order_{index}'] = forms.IntegerField(label='顺序', min_value=1, max_value=10000,
                    required=False, initial=index + 1, widget=forms.NumberInput(attrs={'aria-label': meaning + '的列顺序'}))

    def clean(self):
        data = super().clean()
        selected = set(data.get('fields', []))
        if len(selected) > 300:
            self.add_error('fields', '一次最多导出300列，请减少选择的字段。')
        pairs = []
        for index, key in enumerate(self.table.headers):
            if key in selected:
                label = data.get(f'label_{index}', '').strip() or key
                if any(ord(c) < 32 for c in label):
                    self.add_error(f'label_{index}', '列名不能含换行、制表符或控制字符。')
                pairs.append((data.get(f'order_{index}') or index + 1, key, label))
        if len({p[0] for p in pairs}) != len(pairs):
            self.add_error(None, '所选字段的顺序不能重复；例如填写1、2、3。')
        if len({p[2] for p in pairs}) != len(pairs):
            self.add_error(None, '所选字段的列名不能重复。')
        self.selection = sorted(pairs)
        return data

    def field_rows(self):
        selected = set(self['fields'].value() or [])
        return [{'key': key, 'label': BASE_LABELS.get(key, '原始附加字段'), 'selected': key in selected,
                 'name_field': self[f'label_{index}'], 'order_field': self[f'order_{index}']}
                for index, key in enumerate(self.table.headers)]
