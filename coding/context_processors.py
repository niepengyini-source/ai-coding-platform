from django.conf import settings
from .models import Profile


def account_options(request):
    requires_password = (request.user.is_authenticated and request.resolver_match
        and request.resolver_match.url_name == 'password_change'
        and Profile.objects.filter(user=request.user, must_change_password=True).exists())
    return {'registration_open': settings.PLATFORM_ALLOW_REGISTRATION,
            'navigation_requires_password': bool(requires_password)}
