from datetime import timedelta

from django import forms
from .exporting import FORMATS, MAX_EXPORT_ROWS
from .models import Round, SourceDocument


class ArchiveSearchForm(forms.Form):
    scope = forms.ChoiceField(label='编码员', choices=[('mine', '仅我的编码')], initial='mine')
    mode = forms.ChoiceField(label='搜索方式', choices=[('quick', '快速搜索：包含关键词'), ('exact', '精确搜索：完整匹配')], initial='quick')
    q = forms.CharField(label='关键词', max_length=250, required=False,
                        widget=forms.TextInput(attrs={'placeholder': '原文、备注、代码、文件名或编号'}))
    document = forms.ModelChoiceField(label='来源文件', queryset=SourceDocument.objects.none(), required=False,
                                      empty_label='全部文件')
    round_id = forms.ModelChoiceField(label='编码批次', queryset=Round.objects.none(), required=False,
                                      empty_label='全部批次')
    code = forms.CharField(label='主 / 次代码', max_length=100, required=False)
    status = forms.ChoiceField(label='保存状态', required=False,
                               choices=[('', '草稿与已提交'), ('draft', '草稿'), ('submitted', '已提交')])
    metadata_field = forms.CharField(label='原始字段名', max_length=200, required=False,
                                     widget=forms.TextInput(attrs={'placeholder': '例如 group_id 或 condition'}))
    metadata_value = forms.CharField(label='字段值 / 组别', max_length=250, required=False,
                                     widget=forms.TextInput(attrs={'placeholder': '例如 A组'}))
    start = forms.DateTimeField(label='最后保存：开始时间', required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'step': '60'}),
        input_formats=['%Y-%m-%dT%H:%M'])
    end = forms.DateTimeField(label='结束时间（含该分钟）', required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'step': '60'}),
        input_formats=['%Y-%m-%dT%H:%M'])
    include_superseded = forms.BooleanField(label='包含已拆分的原记录', required=False)

    def __init__(self, *args, project, user, role, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document'].queryset = project.documents.all().order_by('name', 'id')
        self.fields['document'].label_from_instance = lambda doc: f'{doc.name} · #{doc.pk}'
        rounds = project.rounds.all()
        if role not in ('owner', 'reviewer'):
            rounds = rounds.filter(assignments__coder=user).distinct()
        else:
            self.fields['scope'].choices = [*self.fields['scope'].choices, ('all', '全部编码员')]
        self.fields['round_id'].queryset = rounds.order_by('-id')
        self.fields['round_id'].label_from_instance = lambda r: f'{r.name} · #{r.pk}'

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            self.add_error('end', '结束时间不能早于开始时间。')
        if data.get('end'):
            try:
                data['end'] + timedelta(minutes=1)
            except OverflowError:
                self.add_error('end', '结束时间超出支持范围，请选择较早的时间。')
        if bool(data.get('metadata_field')) != bool(data.get('metadata_value')):
            self.add_error('metadata_value', '按组别等原始信息筛选时，请同时填写原始字段名和字段值。')
        return data


class ArchiveSelectionForm(forms.Form):
    selection = forms.ChoiceField(label='导出范围', choices=[('all', '全部匹配记录'), ('first', '前 N 条记录'),
                                                              ('selected', '本页勾选的记录')], initial='all')
    limit = forms.IntegerField(label='N 条', required=False, min_value=1, max_value=MAX_EXPORT_ROWS)
    format = forms.ChoiceField(label='文件格式', choices=FORMATS, initial='xlsx')
    page = forms.IntegerField(required=False, min_value=1, max_value=10**9, initial=1,
                               widget=forms.HiddenInput)
    selected = forms.TypedMultipleChoiceField(required=False, coerce=int, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, visible_ids, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['selected'].choices = [(pk, str(pk)) for pk in visible_ids]

    def clean(self):
        data = super().clean()
        if data.get('selection') == 'first' and not data.get('limit'):
            self.add_error('limit', '请填写要导出的条数。')
        if data.get('selection') == 'selected' and not data.get('selected'):
            self.add_error('selected', '请先勾选本页要导出的记录。')
        return data
