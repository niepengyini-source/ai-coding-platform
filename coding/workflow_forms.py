from django import forms
from .models import Codebook, Round


class QuickStartForm(forms.Form):
    book_id = forms.ModelChoiceField(label='使用哪一版编码本', queryset=Codebook.objects.none())
    token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['book_id'].queryset = project.codebooks.filter(codes__isnull=False).distinct()
        self.fields['book_id'].label_from_instance = lambda book: f'v{book.version} · {book.title}'


class HistorySearchForm(forms.Form):
    mode = forms.ChoiceField(label='搜索方式', choices=[('quick', '快速搜索：包含关键词'), ('exact', '精确搜索：完整匹配')], initial='quick')
    q = forms.CharField(label='搜索词', required=False, max_length=250,
                       widget=forms.TextInput(attrs={'placeholder': '原文、备注、主代码、文件名或编号'}))
    period = forms.ChoiceField(label='时间范围', required=False, choices=[('', '全部时间'), ('today', '今天'),
                                           ('week', '近7天（含今天）'), ('month', '近30天（含今天）')])
    start = forms.DateTimeField(label='开始时间', required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'step': '60'}),
        input_formats=['%Y-%m-%dT%H:%M'])
    end = forms.DateTimeField(label='结束时间（含该分钟）', required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'step': '60'}),
        input_formats=['%Y-%m-%dT%H:%M'])
    unit_id = forms.IntegerField(label='单元编号', required=False, min_value=1, max_value=10**18-1)
    assignment_id = forms.IntegerField(label='编码记录编号', required=False, min_value=1, max_value=10**18-1)
    code = forms.CharField(label='主代码（完整编号或名称）', required=False, max_length=100)
    filename = forms.CharField(label='来源文件（完整名称）', required=False, max_length=200)
    round_id = forms.ModelChoiceField(label='编码批次 / 轮次', required=False, queryset=Round.objects.none())
    book_version = forms.IntegerField(label='编码本版本号', required=False, min_value=1, max_value=10**9)
    status = forms.ChoiceField(label='保存状态', required=False, choices=[('', '全部状态'), ('draft', '草稿'), ('submitted', '已提交')])
    uncertain = forms.ChoiceField(label='不确定标记', required=False, choices=[('', '不限'), ('yes', '有疑点'), ('no', '无疑点')])
    entry_kind = forms.ChoiceField(label='开始方式', required=False, choices=[('', '不限'), ('quick', '个人快速编码'), ('assigned', '已安排任务')])

    def __init__(self, *args, project, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['round_id'].queryset = project.rounds.filter(assignments__coder=user).distinct().order_by('-id')
        self.fields['round_id'].label_from_instance = lambda r: f'#{r.pk} · {r.name}'

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            self.add_error('end', '结束时间不能早于开始时间。')
        return data


class HistoryReopenForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    reason = forms.CharField(label='为什么需要修改', max_length=2000,
                            widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '简要说明修改原因，旧记录会保留。'}))
