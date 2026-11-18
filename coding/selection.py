"""Read-only selection of active project units; display positions are one-based."""
from collections import defaultdict
import random
import re

from .models import Unit


def parse_positions(value, total, limit=10000):
    """Expand a bounded union of positions/ranges, with overlaps removed."""
    value = re.sub(r'\s*-\s*', '-', value.strip())
    if not value:
        return []
    parts = re.split(r'[\s,，、;；]+', value)
    if len(parts) > limit:
        raise ValueError(f'一次最多选择{limit}个材料单元。')
    ranges = []
    for part in parts:
        match = re.fullmatch(r'([0-9]{1,18})(?:-([0-9]{1,18}))?', part)
        if not match:
            raise ValueError('请填写材料顺序号或区间，例如3、8、15-30。')
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= total:
            raise ValueError(f'材料顺序号须在1至{total}之间，区间起点不能大于终点。')
        ranges.append((start, end))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if sum(end - start + 1 for start, end in merged) > limit:
        raise ValueError(f'一次最多选择{limit}个材料单元。')
    return [position for start, end in merged for position in range(start, end + 1)]


def select_round_units(project, count, seed, stratify='', unit_ids=None,
                       selection_mode='random', start_position=None):
    if selection_mode not in ('random', 'sequential', 'manual'):
        raise ValueError('请选择有效的材料选取方式。')
    if count < 0:
        raise ValueError('选取数量不能小于0。')
    if selection_mode != 'random' and stratify:
        raise ValueError('按顺序选取和手动选择不能同时使用分层随机抽样。')
    units = list(Unit.objects.filter(record__document__project=project, active=True,
                                    record__document__units_confirmed=True).select_related('record'))
    if not units:
        raise ValueError('请先导入材料并确认编码单元。')
    requested_ids = set(unit_ids or [])
    if requested_ids:
        if not requested_ids.issubset({unit.pk for unit in units}):
            raise ValueError('指定编号必须是本项目当前有效的编码单元；已拆分、合并或排除的旧编号不能再分配。')
        if selection_mode == 'sequential':
            raise ValueError('按顺序选取时，请使用起始材料顺序号，不同时指定系统编号。')
        units = [unit for unit in units if unit.pk in requested_ids]
    if selection_mode == 'manual':
        if not requested_ids:
            raise ValueError('请先选择需要分配的材料单元。')
        if len(units) > 10000:
            raise ValueError('一次最多手动选择10000个材料单元。')
        if count not in (0, len(units)):
            raise ValueError('手动选择会使用全部已选单元，选取数量须与已选数量一致。')
        return units
    if selection_mode == 'sequential':
        start = 1 if start_position is None else start_position
        if not 1 <= start <= len(units):
            raise ValueError(f'起始材料顺序号须在1至{len(units)}之间。')
        remaining = units[start - 1:]
        if count > len(remaining):
            raise ValueError(f'从第{start}条开始只剩{len(remaining)}个有效单元，选取数量超过剩余数量。')
        return remaining[:count] if count else remaining
    count = count or len(units)
    if count > len(units):
        raise ValueError('抽样数量超过现有单元数。')
    rng = random.Random(seed)
    if not stratify:
        return rng.sample(units, count)
    groups = defaultdict(list)
    for unit in units:
        groups[str(unit.record.metadata.get(stratify, ''))].append(unit)
    ordered = list(groups.values())
    rng.shuffle(ordered)
    for group in ordered:
        rng.shuffle(group)
    selected = []
    while len(selected) < count:
        for group in ordered:
            if group and len(selected) < count:
                selected.append(group.pop())
    return selected
