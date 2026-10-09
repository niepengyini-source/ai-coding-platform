"""Deterministic label differences; semantic causes still require human review."""
from collections import defaultdict
from itertools import combinations
from .hierarchy import top_level_ids


DIFFERENCE_NAMES = {
    'incomplete': '尚未提交（培训练习）',
    'uncertain': '有人标记不确定',
    'dimension_difference': '主代码所属一级维度不同',
    'primary_difference': '主代码不同',
    'secondary_difference': '次代码不同',
    'primary_secondary_swap': '主次代码互换',
    'no_code_difference': '对是否适用代码判断不同',
}


def disagreement_rows(round_obj):
    codes = list(round_obj.book.codes.all())
    roots = top_level_ids(codes)
    has_hierarchy = any(code.parent_id for code in codes)
    groups = defaultdict(list)
    for assignment in round_obj.assignments.select_related('unit__record__document', 'coder', 'primary').order_by('unit_id', 'coder_id'):
        groups[assignment.unit_id].append(assignment)
    decisions = {decision.unit_id: decision for decision in round_obj.decisions.filter(cycle=round_obj.cycle)}
    rows = []
    for unit_id, entries in groups.items():
        types = set()
        if any(a.status != 'submitted' for a in entries):
            types.add('incomplete')
        if any(a.uncertain for a in entries):
            types.add('uncertain')
        submitted = [a for a in entries if a.status == 'submitted']
        for left, right in combinations(submitted, 2):
            if left.primary_id != right.primary_id:
                types.add('primary_difference')
                if has_hierarchy and roots.get(left.primary_id, 0) != roots.get(right.primary_id, 0):
                    types.add('dimension_difference')
            if set(left.secondary) != set(right.secondary):
                types.add('secondary_difference')
            if left.no_code != right.no_code:
                types.add('no_code_difference')
            if (left.primary_id and right.primary_id and left.primary_id != right.primary_id
                    and left.primary_id in right.secondary and right.primary_id in left.secondary):
                types.add('primary_secondary_swap')
        ordered_types = [key for key in DIFFERENCE_NAMES if key in types]
        decision = decisions.get(unit_id)
        rows.append({'unit': entries[0].unit, 'entries': entries, 'types': ordered_types,
                     'type_names': [DIFFERENCE_NAMES[key] for key in ordered_types],
                     'needs_attention': bool(types), 'single_coder': len(entries) == 1,
                     'resolved': bool(decision and not decision.uncertain), 'decision': decision})
    return rows
