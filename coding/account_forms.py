import re
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import Membership

INVITE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


class PlatformLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = '用户名'
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        self.fields['password'].label = '密码'


class RegistrationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = '用户名'
        self.fields['username'].help_text = '用于登录；可以使用字母、数字及 @ . + - _，请记住大小写。'
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        for name, label in [('password1', '设置密码'), ('password2', '再输入一次密码')]:
            self.fields[name].label = label
            self.fields[name].max_length = 128
            self.fields[name].widget.attrs.update({'autocomplete': 'new-password', 'maxlength': 128})


class JoinGroupForm(forms.Form):
    invitation_code = forms.CharField(label='小组邀请码', max_length=100,
        help_text='向项目负责人索取；粘贴时包含的短横线或空格会自动忽略。',
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'spellcheck': 'false'}))
    note = forms.CharField(label='申请说明（可选）', max_length=500, required=False,
        help_text='说明你的姓名或在小组里的工作，方便负责人辨认。',
        widget=forms.Textarea(attrs={'rows': 3}))

    def clean_invitation_code(self):
        code = re.sub(r'[\s-]', '', self.cleaned_data['invitation_code']).upper()
        if len(code) != 20 or any(char not in INVITE_ALPHABET for char in code):
            raise forms.ValidationError('邀请码格式不正确，请完整复制负责人提供的20位邀请码。')
        return code


class InvitationForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    days = forms.TypedChoiceField(label='邀请码有效期', coerce=int,
        choices=[(1, '1天'), (7, '7天'), (30, '30天')], initial=7)


class JoinReviewForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[('approve', 'approve'), ('reject', 'reject')])
    role = forms.ChoiceField(label='加入后的权限', choices=Membership.ROLES[1:], initial='coder')
    decision_note = forms.CharField(label='审核说明（可选）', max_length=500, required=False,
                                   widget=forms.TextInput(attrs={'placeholder': '申请人可看到此说明'}))
