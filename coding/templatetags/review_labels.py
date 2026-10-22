from django import template

from coding.review_labels import review_label

register = template.Library()


@register.filter
def review_marker(value):
    if isinstance(value, dict):
        return review_label(value.get('primary'), value.get('no_code'), value.get('uncertain'))
    return review_label(getattr(value, 'primary_id', None), getattr(value, 'no_code', False),
                        getattr(value, 'uncertain', False))
