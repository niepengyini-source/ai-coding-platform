import secrets
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.conf import settings
from coding.models import Profile


class Command(BaseCommand):
    help = '创建第一个本地账号，不重置已有账号；初始登录信息只写入私有runtime目录。'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='owner')

    def handle(self, *args, **options):
        username = options['username']
        if get_user_model().objects.filter(username=username).exists():
            self.stdout.write('账号已存在，未更改密码。')
            return
        password = secrets.token_urlsafe(18)
        user = get_user_model().objects.create_user(username=username, password=password)
        Profile.objects.create(user=user, must_change_password=True)
        path = settings.RUNTIME_DIR / '首次登录信息.txt'
        # Runtime-generated credentials, never a committed source file.
        path.write_text(f'AI辅助编码平台\n账号：{username}\n初始密码：{password}\n首次登录必须修改密码。修改后可自行删除本文件。\n不要上传runtime目录到GitHub。\n', encoding='utf-8')
        self.stdout.write(f'首次登录信息已保存在：{path}（未在终端显示密码）')
