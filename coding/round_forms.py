from django import forms
import re
from .models import Round, Unit


class RoundForm(forms.Form):
    name = forms.CharField(label='轮次名称', max_length=120)
    kind = forms.ChoiceField(label='工作阶段', choices=[choice for choice in Round._meta.get_field('kind').choices if choice[0] != 'quick'], initial='pilot')
    book_id = forms.TypedChoiceField(label='采用的编码本', coerce=int)
    coder_ids = forms.TypedMultipleChoiceField(label='编码员（可选多人）', coerce=int, widget=forms.CheckboxSelectMultiple)
    count = forms.IntegerField(label='抽样单元数（0表示全部）', min_value=0, initial=0)
    seed = forms.IntegerField(label='抽样随机种子', initial=2026)
    stratify = forms.ChoiceField(label='分层字段（可选，均衡抽取各层）', required=False)
    distribution = forms.ChoiceField(label='任务分配', choices=[('all', '每位编码员编码同一批单元'),
        ('distributed', '单人分工'), ('mixed', '分工＋部分双人复核')], initial='all')
    double_percent = forms.IntegerField(label='混合分工的双人复核比例（%）', min_value=0, max_value=100, initial=25)
    unit_ids = forms.CharField(label='指定单元编号（选填）', required=False, max_length=100000,
                              widget=forms.Textarea(attrs={'rows': 3}),
                              help_text='留空则从全部有效单元抽取。再次试编码可填上轮分歧单元和新样本编号，用逗号、空格或换行分隔。抽样数填0会分配所有指定单元；填写数量则从指定池中抽样。')

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['book_id'].choices = [(b.id, f'v{b.version} · {b.title}') for b in project.codebooks.all()]
        self.fields['coder_ids'].choices = [(m.user_id, m.user.username) for m in project.memberships.exclude(role='viewer').select_related('user').filter(user__is_active=True)]
        fields = set()
        for meta in Unit.objects.filter(record__document__project=project, active=True).values_list('record__metadata', flat=True)[:500]:
            fields.update(meta)
        self.fields['stratify'].choices = [('', '不分层')] + [(k, k) for k in sorted(fields)]

    def clean(self):
        data = super().clean()
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
        if len(set(result)) != len(result):
            raise forms.ValidationError('单元编号不能重复，请核对指定样本。')
        return result
