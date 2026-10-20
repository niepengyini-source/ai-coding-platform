from django import forms
import re
from .models import Round, Unit
from .selection import parse_positions


class RoundForm(forms.Form):
    name = forms.CharField(label='任务名称', max_length=120, help_text='给这次工作起个名称，方便以后查找。')
    kind = forms.ChoiceField(label='工作阶段', choices=[choice for choice in Round._meta.get_field('kind').choices if choice[0] != 'quick'], initial='pilot', help_text='培训用于共同练习；试编码用于检验规则；正式编码用于正式分析。')
    book_id = forms.TypedChoiceField(label='采用的编码本', coerce=int)
    coder_ids = forms.TypedMultipleChoiceField(label='编码员（可选多人）', coerce=int, widget=forms.CheckboxSelectMultiple)
    selection_mode = forms.ChoiceField(label='怎样选取材料', required=False, initial='random',
                                      choices=[('sequential', '从指定位置连续选取'), ('manual', '手动选择'), ('random', '随机抽取')])
    start_position = forms.IntegerField(label='从第几条材料开始', min_value=1, initial=1, required=False,
                                        help_text='按项目材料当前的顺序从1开始计数，不是系统编号。')
    count = forms.IntegerField(label='本轮共选多少条', min_value=0, initial=0, required=False,
                               help_text='这是本轮材料总数，不是每位成员的数量。0表示全部；连续选取时表示起点之后全部。')
    seed = forms.IntegerField(label='随机种子', initial=2026, required=False,
                              help_text='保留相同数字可重复得到相同随机样本，通常不用修改。')
    stratify = forms.ChoiceField(label='分层字段（可选，均衡抽取各层）', required=False)
    distribution = forms.ChoiceField(label='任务分配', choices=[('all', '每位编码员编码同一批单元'),
        ('distributed', '单人分工'), ('mixed', '分工＋部分双人复核')], initial='all')
    double_percent = forms.IntegerField(label='双人复核比例（%）', min_value=0, max_value=100, initial=25, required=False,
                                        help_text='只用于“分工＋部分双人复核”，表示本轮多少材料交给两人分别编码。')
    manual_positions = forms.CharField(label='批量填写材料顺序号', required=False, max_length=100000,
                                       widget=forms.Textarea(attrs={'rows': 3}),
                                       help_text='可混合填写不连续的序号和区间，例如3、8、15-30。重复序号自动去重。')
    unit_ids = forms.CharField(label='指定单元编号（选填）', required=False, max_length=100000,
                              widget=forms.Textarea(attrs={'rows': 3}),
                              help_text='留空则从全部有效单元抽取。再次试编码可填上轮分歧单元和新样本编号，用逗号、空格或换行分隔。抽样数填0会分配所有指定单元；填写数量则从指定池中抽样。')

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        current = project.codebooks.first()
        self.fields['book_id'].choices = [(current.id, current.title + '（当前编码本）')] if current else []
        self.fields['coder_ids'].choices = [(m.user_id, m.user.username) for m in project.memberships.exclude(role='viewer').select_related('user').filter(user__is_active=True)]
        fields = set()
        for meta in Unit.objects.filter(record__document__project=project, active=True).values_list('record__metadata', flat=True)[:500]:
            fields.update(meta)
        self.fields['stratify'].choices = [('', '不分层')] + [(k, k) for k in sorted(fields)]

    def clean(self):
        data = super().clean()
        data['coder_ids'] = list(dict.fromkeys(data.get('coder_ids', [])))
        mode = data['selection_mode'] = data.get('selection_mode') or 'random'
        data['count'] = data.get('count') or 0
        data['seed'] = data.get('seed') if data.get('seed') is not None else 2026
        data['double_percent'] = data.get('double_percent') if data.get('double_percent') is not None else 25
        positions = data.pop('manual_positions', '')
        if mode == 'manual':
            ids = list(Unit.objects.filter(record__document__project=self.project, active=True).values_list('pk', flat=True))
            try:
                chosen = set(data.get('unit_ids', []))
                chosen.update(ids[position - 1] for position in parse_positions(positions, len(ids)))
                if not chosen:
                    self.add_error('unit_ids', '请先选择材料，或填写材料顺序号。')
                elif len(chosen) > 10000:
                    self.add_error('unit_ids', '一次最多手动选择10000个材料单元。')
                elif not chosen.issubset(set(ids)):
                    self.add_error('unit_ids', '已选材料不在本项目当前有效单元中，请重新选择。')
                else:
                    data['unit_ids'] = [pk for pk in ids if pk in chosen]
                    data['count'] = len(chosen)
            except ValueError as error:
                self.add_error('manual_positions', str(error))
            data['start_position'] = None
            data['stratify'] = ''
        elif mode == 'sequential':
            data['start_position'] = data.get('start_position') or 1
            data['unit_ids'] = []
            data['stratify'] = ''
        else:
            data['start_position'] = None
        if data.get('distribution') == 'mixed' and len(data.get('coder_ids', [])) < 2:
            self.add_error('coder_ids', '部分双人复核需要至少两位编码员。')
        return data

    def clean_unit_ids(self):
        value = self.cleaned_data['unit_ids'].strip()
        if not value:
            return []
        parts = [part for part in re.split(r'[\s,，;；]+', value) if part]
        if len(parts) > 10000 or any(len(part) > 18 or not part.isascii() or not part.isdecimal() or int(part) < 1 for part in parts):
            raise forms.ValidationError('请输入有效的正整数单元编号，最多10000个。')
        result = [int(part) for part in parts]
        if len(set(result)) != len(result) and self.data.get('selection_mode') != 'manual':
            raise forms.ValidationError('单元编号不能重复，请核对指定样本。')
        return list(dict.fromkeys(result))
