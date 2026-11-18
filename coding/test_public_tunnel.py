"""Public HTTPS and LAN HTTP boundaries, with synthetic cookies and no database."""
from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.test import Client, SimpleTestCase, override_settings
from django.urls import path
from waitress.proxy_headers import proxy_headers_middleware


PUBLIC_HOST = 'synthetic.trycloudflare.com'
LAN_HOST = '192.168.3.77'


def probe(request):
    request.session['synthetic'] = True
    get_token(request)
    return HttpResponse('saved' if request.method == 'POST' else 'ready')


urlpatterns = [path('probe/', probe)]


@override_settings(
    PLATFORM_PUBLIC_HOST=PUBLIC_HOST,
    ALLOWED_HOSTS=[PUBLIC_HOST, LAN_HOST, '127.0.0.1'],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        'django.middleware.security.SecurityMiddleware',
        'coding.middleware.PublicTunnelMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
    ],
    SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies',
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    CSRF_TRUSTED_ORIGINS=[],
    SECURE_SSL_REDIRECT=False,
)
class PublicTunnelTests(SimpleTestCase):
    def test_public_https_has_secure_cookies_and_same_origin_submission(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get('/probe/', secure=True, HTTP_HOST=PUBLIC_HOST)
        self.assertEqual(response.status_code, 200)
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            self.assertTrue(response.cookies[name]['secure'])
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post('/probe/', {'csrfmiddlewaretoken': token}, secure=True,
            HTTP_HOST=PUBLIC_HOST, HTTP_ORIGIN='https://' + PUBLIC_HOST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'saved')

    def test_public_post_still_rejects_missing_token_and_foreign_origin(self):
        client = Client(enforce_csrf_checks=True)
        client.get('/probe/', secure=True, HTTP_HOST=PUBLIC_HOST)
        self.assertEqual(client.post('/probe/', {}, secure=True,
            HTTP_HOST=PUBLIC_HOST, HTTP_ORIGIN='https://' + PUBLIC_HOST).status_code, 403)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertEqual(client.post('/probe/', {'csrfmiddlewaretoken': token}, secure=True,
            HTTP_HOST=PUBLIC_HOST, HTTP_ORIGIN='https://foreign.trycloudflare.com').status_code, 403)

    def test_public_http_redirects_to_exact_https_host_and_path(self):
        response = self.client.get('/probe/?next=%2Fprojects%2F', HTTP_HOST=PUBLIC_HOST)
        self.assertIn(response.status_code, (301, 302, 307, 308))
        self.assertEqual(response['Location'], 'https://' + PUBLIC_HOST + '/probe/?next=%2Fprojects%2F')
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_lan_http_keeps_working_and_cookies_remain_usable(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get('/probe/', HTTP_HOST=LAN_HOST)
        self.assertEqual(response.status_code, 200)
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            self.assertFalse(response.cookies[name]['secure'])
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertEqual(client.post('/probe/', {'csrfmiddlewaretoken': token},
            HTTP_HOST=LAN_HOST, HTTP_ORIGIN='http://' + LAN_HOST).status_code, 200)

    def test_unlisted_public_host_is_rejected(self):
        self.assertEqual(self.client.get('/probe/', secure=True,
            HTTP_HOST='foreign.trycloudflare.com').status_code, 400)

    @override_settings(PLATFORM_PUBLIC_HOST='')
    def test_disabled_public_mode_does_not_redirect_or_change_cookies(self):
        response = self.client.get('/probe/', HTTP_HOST=PUBLIC_HOST)
        self.assertEqual(response.status_code, 200)
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            self.assertFalse(response.cookies[name]['secure'])


class TrustedProxyTests(SimpleTestCase):
    def forwarded_request(self, remote, enabled=True, forwarded_for='203.0.113.8, 198.51.100.12'):
        observed = {}

        def app(environ, start_response):
            observed.update(environ)
            start_response('200 OK', [])
            return [b'ok']

        wrapped = proxy_headers_middleware(app,
            trusted_proxy='127.0.0.1' if enabled else None,
            trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-for'} if enabled else set(),
            trusted_proxy_count=1, clear_untrusted=True)
        environ = {'REMOTE_ADDR': remote, 'wsgi.url_scheme': 'http',
            'HTTP_HOST': PUBLIC_HOST, 'SERVER_NAME': '127.0.0.1', 'SERVER_PORT': '8000',
            'HTTP_X_FORWARDED_PROTO': 'https', 'HTTP_X_FORWARDED_FOR': forwarded_for,
            'HTTP_X_FORWARDED_HOST': 'evil.invalid'}
        list(wrapped(environ, lambda status, headers: None))
        self.assertEqual(observed['HTTP_HOST'], PUBLIC_HOST)
        self.assertNotIn('HTTP_X_FORWARDED_HOST', observed)
        return observed

    def test_loopback_proxy_uses_https_and_rightmost_client_address(self):
        observed = self.forwarded_request('127.0.0.1')
        self.assertEqual(observed['wsgi.url_scheme'], 'https')
        self.assertEqual(observed['REMOTE_ADDR'], '198.51.100.12')

    def test_direct_lan_cannot_spoof_scheme_or_client_address(self):
        observed = self.forwarded_request(LAN_HOST)
        self.assertEqual(observed['wsgi.url_scheme'], 'http')
        self.assertEqual(observed['REMOTE_ADDR'], LAN_HOST)
        self.assertNotIn('HTTP_X_FORWARDED_FOR', observed)
        self.assertNotIn('HTTP_X_FORWARDED_PROTO', observed)

    def test_disabled_public_mode_does_not_trust_loopback_headers(self):
        observed = self.forwarded_request('127.0.0.1', enabled=False)
        self.assertEqual(observed['wsgi.url_scheme'], 'http')
        self.assertEqual(observed['REMOTE_ADDR'], '127.0.0.1')

    def test_ipv6_client_address_is_preserved(self):
        observed = self.forwarded_request('127.0.0.1', forwarded_for='203.0.113.8, 2001:db8::12')
        self.assertEqual(observed['REMOTE_ADDR'], '2001:db8::12')
