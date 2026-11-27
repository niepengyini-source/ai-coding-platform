from django import template
register = template.Library()

@register.filter
def coefficient(value):
    return '—' if value is None else f'{value:.3f}'

@register.filter
def percentage(value):
    return '—' if value is None else f'{value * 100:.1f}%'


@register.filter
def coder_pair(coders, names):
    if not coders:
        return '—'
    mapping = names if isinstance(names, dict) else {}
    return ' / '.join(mapping.get(str(coder_id), str(coder_id)) for coder_id in coders)
