from collections import Counter
from itertools import combinations
import math
from .hierarchy import top_level_ids


def kappa(left, right):
    if not left:
        return None
    n = len(left)
    a, b = Counter(left), Counter(right)
    observed = sum(x == y for x, y in zip(left, right)) / n
    expected = sum(a[x] * b[x] for x in a.keys() | b.keys()) / n ** 2
    return None if math.isclose(expected, 1) else round((observed - expected) / (1 - expected), 6)


def alpha(matrix):
    import numpy as np
    import krippendorff
    try:
        result = float(krippendorff.alpha(reliability_data=np.array(matrix, dtype=float), level_of_measurement='nominal'))
        return round(result, 6) if math.isfinite(result) else None
    except (ValueError, ZeroDivisionError):
        return None


def report_for(round_obj):
    assignments = list(round_obj.assignments.select_related('coder', 'primary'))
    coder_ids = sorted({a.coder_id for a in assignments})
    unit_ids = sorted({a.unit_id for a in assignments})
    by_key = {(a.coder_id, a.unit_id): a for a in assignments}
    codes = list(round_obj.book.codes.all())
    previously_visible = set(round_obj.sampling.get('previously_visible_unit_ids', [])) & set(unit_ids)
    roots = top_level_ids(codes)
    has_hierarchy = any(code.parent_id for code in codes)
    def labels(a):
        if not a or a.status != 'submitted' or a.uncertain:
            return None
        return frozenset(([a.primary_id] if a.primary_id else []) + a.secondary)
    pairs = []
    for first, second in combinations(coder_ids, 2):
        comparable = [(labels(by_key.get((first, u))), labels(by_key.get((second, u)))) for u in unit_ids]
        comparable = [(a, b) for a, b in comparable if a is not None and b is not None]
        n = len(comparable)
        entry = {'coders': [first, second], 'n': n,
                 'agreement': round(sum(a == b for a, b in comparable) / n, 6) if n else None,
                 'kappa': kappa([next(iter(a), 0) for a, b in comparable], [next(iter(b), 0) for a, b in comparable]) if round_obj.book.policy == 'single' else None}
        primary_pairs = [(by_key.get((first, u)), by_key.get((second, u))) for u in unit_ids]
        primary_pairs = [(a.primary_id or 0, b.primary_id or 0) for a, b in primary_pairs
                         if a and b and labels(a) is not None and labels(b) is not None]
        entry['primary_kappa'] = kappa([a for a, b in primary_pairs], [b for a, b in primary_pairs])
        entry['primary_agreement'] = round(sum(a == b for a, b in primary_pairs) / len(primary_pairs), 6) if primary_pairs else None
        secondary_pairs = [(frozenset(a.secondary), frozenset(b.secondary))
                           for u in unit_ids for a, b in [(by_key.get((first, u)), by_key.get((second, u)))]
                           if a and b and labels(a) is not None and labels(b) is not None]
        entry['secondary_agreement'] = (round(sum(a == b for a, b in secondary_pairs) / len(secondary_pairs), 6)
                                        if secondary_pairs and round_obj.book.policy == 'multi' else None)
        dimensions = [(roots.get(a, 0), roots.get(b, 0)) for a, b in primary_pairs] if has_hierarchy else []
        dimensions = [(a, b) for a, b in dimensions if a is not None and b is not None]
        entry['dimension_n'] = len(dimensions)
        entry['dimension_agreement'] = round(sum(a == b for a, b in dimensions) / len(dimensions), 6) if dimensions else None
        entry['dimension_kappa'] = kappa([a for a, b in dimensions], [b for a, b in dimensions])
        pairs.append(entry)
    matrix = [[(next(iter(lab), 0) if lab is not None else float('nan'))
               for u in unit_ids for lab in [labels(by_key.get((c, u)))]] for c in coder_ids]
    per_code = []
    for code in codes:
        binary_matrix = [[(int(code.id in lab) if lab is not None else float('nan'))
                          for u in unit_ids for lab in [labels(by_key.get((c, u)))]] for c in coder_ids]
        pair_values = []
        for first, second in combinations(coder_ids, 2):
            ab = [(labels(by_key.get((first, u))), labels(by_key.get((second, u)))) for u in unit_ids]
            ab = [(int(code.id in a), int(code.id in b)) for a, b in ab if a is not None and b is not None]
            pair_values.append({'coders': [first, second], 'n': len(ab),
                                'kappa': kappa([a for a, b in ab], [b for a, b in ab]),
                                'agreement': round(sum(a == b for a, b in ab) / len(ab), 6) if ab else None})
        per_code.append({'key': code.key, 'name': code.name, 'alpha': alpha(binary_matrix), 'pairs': pair_values})
    primary_matrix = [[(a.primary_id or 0) if labels(a) is not None else float('nan')
                       for u in unit_ids for a in [by_key.get((c, u))]] for c in coder_ids]
    dimension_matrix = [[(roots.get(a.primary_id, 0) if a.primary_id else 0) if labels(a) is not None else float('nan')
                         for u in unit_ids for a in [by_key.get((c, u))]] for c in coder_ids] if has_hierarchy else []
    return {'round_id': round_obj.id, 'cycle': round_obj.cycle, 'book_version': round_obj.book.version,
            'unit_count': len(unit_ids), 'coder_count': len(coder_ids),
            'sample_history_recorded': 'previously_visible_unit_ids' in round_obj.sampling,
            'previously_visible_unit_count': len(previously_visible),
            'previously_visible_unit_ids': sorted(previously_visible),
            'coder_names': {str(a.coder_id): a.coder.username for a in assignments},
            'uncertain_count': sum(a.uncertain for a in assignments),
            'assignment_count': len(assignments),
            'uncertain_rate': round(sum(a.uncertain for a in assignments) / len(assignments), 6) if assignments else None,
            'excluded_or_missing_count': sum(labels(a) is None for a in assignments),
            'method': '名义类别；仅提交且非不确定的独立编码；无代码作为明确空集；缺失按有效配对处理。主、次代码分别比较，次代码空集也参与比较；多标签逐代码二元比较。一级维度仅使用编码本显式设置的最顶层父代码，对主代码归类，不按名称猜测；单个代码比较不合并子代码。不确定比例以全部分配记录为分母。',
            'undefined_note': '—表示样本不足或类别无变异，不能解释成零分或满分。培训练习结果不代表独立编码信度。',
            'training': round_obj.kind == 'training', 'pairs': pairs,
            'nominal_alpha': alpha(matrix) if round_obj.book.policy == 'single' else None,
            'primary_alpha': alpha(primary_matrix),
            'has_hierarchy': has_hierarchy,
            'dimension_alpha': alpha(dimension_matrix) if has_hierarchy else None,
            'hierarchy_snapshot': [{'id': c.id, 'key': c.key, 'name': c.name, 'parent_id': c.parent_id, 'top_level_id': roots[c.id]} for c in codes],
            'per_code': per_code,
            'snapshot': [{'id': a.id, 'unit_id': a.unit_id, 'coder_id': a.coder_id, 'revision': a.revision,
                          'primary': a.primary_id, 'secondary': a.secondary, 'uncertain': a.uncertain,
                          'no_code': a.no_code, 'status': a.status} for a in assignments]}
