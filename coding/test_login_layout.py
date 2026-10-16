"""Login layout checks with fictional accounts in Django's temporary test database."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from .tests import TEST_SETTINGS


@override_settings(**TEST_SETTINGS, PLATFORM_ALLOW_REGISTRATION=True)
class LoginLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            'login_layout_synthetic', password='Synthetic!Pass58')

    def setUp(self):
        cache.clear()

    def test_registration_has_only_the_top_tab(self):
        response = self.client.get('/login/')
        self.assertContains(response, 'workspace-auth-tabs')
        self.assertContains(response, 'href="/accounts/register/"', count=1)
        self.assertContains(response, 'aria-current="page">登录</span>')
        self.assertNotContains(response, '还没有账号')
        self.assertNotContains(response, '注册账号')
        self.assertNotContains(response, 'auth-switch')

    @override_settings(PLATFORM_ALLOW_REGISTRATION=False)
    def test_closed_registration_keeps_account_help(self):
        response = self.client.get('/login/')
        self.assertNotContains(response, 'href="/accounts/register/"')
        self.assertNotContains(response, '还没有账号')
        self.assertContains(response, '请联系项目负责人添加账号。')

    def test_login_keeps_fields_csrf_and_next(self):
        response = self.client.get('/login/?next=/groups/join/')
        for name in ['username', 'password', 'csrfmiddlewaretoken']:
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'name="next" value="/groups/join/"')
        self.assertContains(response, 'type="submit">登录</button>')

    def test_invalid_login_keeps_errors_and_destination(self):
        response = self.client.post('/login/', {
            'username': self.user.username, 'password': 'incorrect',
            'next': '/groups/join/',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertContains(response, 'class="errorlist nonfield"')
        self.assertContains(response, 'name="next" value="/groups/join/"')
        self.assertContains(response, 'href="/accounts/register/"', count=1)

    def test_login_still_requires_csrf_and_honors_next(self):
        client = Client(enforce_csrf_checks=True)
        data = {'username': self.user.username, 'password': 'Synthetic!Pass58',
                'next': '/groups/join/'}
        self.assertEqual(client.post('/login/', data).status_code, 403)
        client.get('/login/?next=/groups/join/')
        data['csrfmiddlewaretoken'] = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertRedirects(client.post('/login/', data), '/groups/join/')
