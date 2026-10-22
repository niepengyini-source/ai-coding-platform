"""Human-readable review markers, separate from submission and round state."""


def review_label(primary=None, no_code=False, uncertain=False):
    if uncertain:
        return '需复核'
    return '已确认' if primary or no_code else '未判断'
