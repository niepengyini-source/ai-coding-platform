from django import template
register = template.Library()

@register.filter
def coefficient(value):
    return '—' if value is None else f'{value:.3f}'

@register.filter
def percentage(value):
    return '—' if value is None else f'{value * 100:.1f}%'
