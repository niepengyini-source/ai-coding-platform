import re
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import Project, Code, Membership, Codebook


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'goal', 'description']
        labels = {'name': '项目名称', 'goal': '研究目标：这次编码要回答什么？', 'description': '材料范围与项目说明'}
        widgets = {'goal': forms.Textarea(attrs={'rows': 3}), 'description': forms.Textarea(attrs={'rows': 3})}


class MemberForm(forms.Form):
    username = forms.CharField(label='成员用户名', max_length=150)
    role = forms.ChoiceField(label='权限', choices=Membership.ROLES[1:])
    password = forms.CharField(label='新账号初始密码（已有账号留空）', required=False, widget=forms.PasswordInput)

    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        if not re.fullmatch(r'[\w.@+-]+', value):
            raise forms.ValidationError('用户名只使用字母、数字及@ . + - _。')
        return value

    def clean(self):
        data = super().clean()
        if data.get('username') and not get_user_model().objects.filter(username=data['username']).exists():
            if not data.get('password'):
                self.add_error('password', '新账号需要初始密码。')
            else:
                validate_password(data['password'], get_user_model()(username=data['username']))
        return data


class BookForm(forms.Form):
    title = forms.CharField(label='编码本名称', max_length=120)
    policy = forms.ChoiceField(label='标签方式', choices=Codebook._meta.get_field('policy').choices)
    change_note = forms.CharField(label='新版本的修改目的与影响范围', widget=forms.Textarea(attrs={'rows': 2}))


class CodeForm(forms.ModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Code
        fields = ['key', 'name', 'parent', 'definition', 'include_when', 'exclude_when', 'positive_example',
                  'negative_example', 'boundary_example', 'coexist_priority']
        labels = {'key': '代码标识（项目自行命名）', 'name': '代码名称', 'parent': '上级代码（可选）',
                  'definition': '定义', 'include_when': '什么情况下使用', 'exclude_when': '什么情况下不使用',
                  'positive_example': '正例', 'negative_example': '反例', 'boundary_example': '边界例',
                  'coexist_priority': '允许共现与主次优先规则'}
        widgets = {k: forms.Textarea(attrs={'rows': 2}) for k in ['definition', 'include_when', 'exclude_when',
                   'positive_example', 'negative_example', 'boundary_example', 'coexist_priority']}

    def __init__(self, *args, book, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['parent'].queryset = book.codes.exclude(pk=self.instance.pk)

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        seen = {self.instance.pk} if self.instance.pk else set()
        node = parent
        while node:
            if node.pk in seen:
                raise forms.ValidationError('代码层级不能形成循环。')
            seen.add(node.pk)
            node = node.parent
        return parent
