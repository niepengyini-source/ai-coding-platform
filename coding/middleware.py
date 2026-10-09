import hashlib
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect
from .models import Profile


class PasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.path not in ('/accounts/password/', '/accounts/password/done/', '/logout/') and not request.path.startswith('/static/'):
            if Profile.objects.filter(user=request.user, must_change_password=True).exists():
                return redirect('password_change')
        return self.get_response(request)


class LoginThrottleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key = None
        if request.path == '/login/' and request.method == 'POST':
            raw = request.META.get('REMOTE_ADDR', '') + '|' + request.POST.get('username', '').casefold()
            key = 'login:' + hashlib.sha256(raw.encode()).hexdigest()
            if cache.get(key, 0) >= 10:
                return HttpResponse('尝试次数过多，请15分钟后再试。', status=429)
        response = self.get_response(request)
        if key:
            if request.user.is_authenticated:
                cache.delete(key)
            else:
                cache.set(key, cache.get(key, 0) + 1, 900)
        return response


class AccountThrottleMiddleware:
    """Bound registration/join attempts without requiring an external CAPTCHA service."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        scopes = {'/accounts/register/': ('register', 20), '/groups/join/': ('join', 30)}
        if request.method == 'POST' and request.path in scopes:
            scope, limit = scopes[request.path]
            raw = request.META.get('REMOTE_ADDR', '')
            if scope == 'join':
                raw += '|' + str(request.user.pk or 'anonymous')
            key = scope + ':' + hashlib.sha256(raw.encode()).hexdigest()
            cache.add(key, 0, 900)
            try:
                attempts = cache.incr(key)
            except ValueError:
                cache.add(key, 1, 900)
                attempts = 1
            if attempts > limit:
                response = HttpResponse('操作次数过多，请15分钟后再试。', status=429)
                response['Retry-After'] = '900'
                response['Cache-Control'] = 'no-store'
                return response
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response['Referrer-Policy'] = 'same-origin'
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if request.user.is_authenticated or request.path in ('/login/', '/accounts/register/'):
            response['Cache-Control'] = 'no-store'
        return response
