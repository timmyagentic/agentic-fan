"""Readable names with explicit provenance; never infer location from a prefix.

Small M4 mapping adapted from Stats (MIT); see THIRD_PARTY_NOTICES.md.
Labels describe measurement points, not a count/order of physical CPU cores.
"""
from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache

REFERENCE = 'https://github.com/exelban/stats/blob/4ecf4f736056eb488c75aa2fa62e749abd87c4a4/Modules/Sensors/values.swift'
GROUPS = {
    'cpu': 'CPU 处理器', 'gpu': 'GPU 图形处理器', 'memory': '内存附近',
    'storage': 'SSD / 闪存', 'wireless': '无线网络', 'power': '电源管理',
    'battery': '电池', 'neural': '神经网络引擎', 'system': '其他已识别部件',
    'unknown': '未识别测点',
}
# Matching is case-sensitive and chip-scoped; the same key can mean something
# different on another generation. Keep unlisted keys unknown.
M4_POINTS = {
    'cpu': {
        '能效区': ('Te05', 'Te0S', 'Te09', 'Te0H'),
        '性能区': ('Tp01', 'Tp05', 'Tp09', 'Tp0D', 'Tp0V', 'Tp0Y', 'Tp0b', 'Tp0e'),
    },
    'memory': {'附近': ('Tm0p', 'Tm1p', 'Tm2p')},
}


@lru_cache(maxsize=1)
def machine_identity():
    if sys.platform != 'darwin':
        return {'model': None, 'chip': None}
    def get(name):
        try:
            result = subprocess.run(['/usr/sbin/sysctl', '-n', name], capture_output=True, text=True, timeout=3)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return {'model': get('hw.model'), 'chip': get('machdep.cpu.brand_string')}


def describe(row, device):
    result = {'display_name': '未识别测点 · ' + row['name'], 'group': 'unknown',
              'label_status': 'unknown', 'label_source': None,
              'label_note': '暂未找到适用于此机型的可靠名称，保留原始标识。',
              'label_verified': False}
    def label(name, group, basis, note, source):
        result.update(display_name=name, group=group, label_status=basis, label_note=note, label_source=source)
    chip = device.get('chip') or ''
    key = row.get('key') or row['name']
    if row['source'] == 'smc' and chip in ('Apple M4', 'Apple M4 Pro', 'Apple M4 Max'):
        note = '依据公开 M4 命名映射；编号用于区分测点，不代表物理核心编号。'
        for group, sections in M4_POINTS.items():
            for area, keys in sections.items():
                if key in keys:
                    prefix = 'CPU ' + area if group == 'cpu' else '内存附近'
                    label(f'{prefix} · 测点 {keys.index(key) + 1}', group, 'community_mapping', note, REFERENCE)
        gpu = (('Tg0G', 'Tg0H') if chip == 'Apple M4' else ('Tg1U', 'Tg1k')) + (
            'Tg0K', 'Tg0L', 'Tg0d', 'Tg0e', 'Tg0j', 'Tg0k')
        if key in gpu:
            label(f'GPU · 测点 {gpu.index(key) + 1}', 'gpu', 'community_mapping', note, REFERENCE)
    if row['source'] == 'smc' and chip.startswith('Apple M'):
        common = {'TH0x': ('SSD 闪存', 'storage'), 'TW0P': ('Wi-Fi 无线模块', 'wireless'),
                  'TB1T': ('电池 · 测点 1', 'battery'), 'TB2T': ('电池 · 测点 2', 'battery')}
        if key in common:
            name, group = common[key]
            label(name, group, 'community_mapping', '依据公开 Apple Silicon 命名映射，未独立确认物理安装位置。', REFERENCE)
    if row['source'] == 'iohid':
        name = row['name']
        nand = re.fullmatch(r'NAND CH(\d+) temp', name)
        pmu = re.fullmatch(r'(PMU\d*) (tdie|tdev)(\d+)', name)
        if nand:
            label('SSD 闪存 · 通道 ' + nand[1], 'storage', 'system_name',
                  '翻译系统返回的 NAND 通道名称，不等同于整盘 SMART 温度。', 'IOHID Product: ' + name)
        elif pmu:
            area = '芯片内部' if pmu[2] == 'tdie' else '设备测点'
            label(f'电源管理 {pmu[1]} · {area} {pmu[3]}', 'power', 'system_name',
                  'PMU 为电源管理单元；保留系统编号，未确认板上位置。', 'IOHID Product: ' + name)
        elif re.fullmatch(r'PMU\d* tcal', name):
            label('电源管理 ' + name.split()[0] + ' · 参考读数', 'power', 'system_name',
                  '系统返回 tcal；用途未独立验证，不纳入温度概览。', 'IOHID Product: ' + name)
            result['exclude_from_overview'] = True
        else:
            patterns = (
                (r'pACC MTR Temp Sensor(\d+)', 'CPU 性能区', 'cpu'),
                (r'eACC MTR Temp Sensor(\d+)', 'CPU 能效区', 'cpu'),
                (r'GPU MTR Temp Sensor(\d+)', 'GPU', 'gpu'),
                (r'ANE MTR Temp Sensor(\d+)', '神经网络引擎', 'neural'),
                (r'SOC MTR Temp Sensor(\d+)', 'SoC 芯片', 'system'),
                (r'ISP MTR Temp Sensor(\d+)', '图像处理器', 'system'),
                (r'PMGR SOC Die Temp Sensor(\d+)', '芯片电源管理', 'power'),
            )
            for pattern, title, group in patterns:
                match = re.fullmatch(pattern, name)
                if match:
                    label(title + ' · 测点 ' + match[1], group, 'system_name',
                          '翻译系统报告的部件名称；测点编号不是物理核心编号。', 'IOHID Product: ' + name)
            if name == 'gas gauge battery':
                label('电池', 'battery', 'system_name', '系统电池测点名称。', 'IOHID Product: ' + name)
    if chip == 'Demo':
        fixtures = {'TCDX': ('CPU 演示测点', 'cpu'), 'Tg0D': ('GPU 演示测点', 'gpu'),
                    'TB0T': ('电池演示测点', 'battery'), 'Tp00': ('CPU 待确认演示测点', 'cpu')}
        if key in fixtures:
            name, group = fixtures[key]
            label(name, group, 'simulated', '明确模拟数据，不用于真实硬件映射。', 'AgenticFan demo fixture')
    result['group_name'] = GROUPS[result['group']]
    return result


def annotate(rows, device):
    return [{**row, **describe(row, device)} for row in rows]


def overview(rows):
    groups = []
    for group, name in GROUPS.items():
        members = [row for row in rows if row.get('group', 'unknown') == group]
        if not members or group == 'unknown':
            continue
        valid = [row for row in members if row['status'] == 'ok' and row['value'] is not None
                 and not row.get('exclude_from_overview')]
        hottest = max(valid, key=lambda row: row['value']) if valid else None
        groups.append({'group': group, 'name': name, 'value': hottest['value'] if hottest else None,
                       'unit': 'celsius', 'aggregation': 'maximum_of_available_identified_points',
                       'sensor_count': len(members), 'valid_count': len(valid),
                       'sensor_ids': [row['id'] for row in members],
                       'value_sensor_id': hottest['id'] if hottest else None,
                       'label_verified': False})
    return groups
