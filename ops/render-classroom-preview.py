#!/usr/bin/env python3
"""Render offline DEMO replies with the production classroom image builder."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import platform
import sys

from PIL import Image, ImageChops, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from henu_plugin.classroom_floorplans import period_label, plans
from henu_plugin.classroom_map import LAYOUTS, build_classroom_image

OUT = ROOT / 'output' / 'classroom-preview'


def demo_result(campus, building, campus_name, building_name, names, free, unknown=()):
    """Synthetic evidence exercises the same scope checks as a real query."""
    scope = dict(termCode='2026,1', week=10, dayOfWeek=5, period=2,
                 campusCode=campus, buildingCode=building)
    return {
        '_demo': 'SIMULATED DATA; no school API request; not current availability',
        'success': True,
        '_resolved_tool_name': 'empty_classroom_query',
        'query': {**scope, 'campusName': campus_name, 'buildingName': building_name,
                  'periodName': '第2大节'},
        'rooms': [{'roomName': name, 'campusCode': campus, 'buildingCode': building}
                  for name in free],
        'total': len(free),
        'freshness': {
            # These are simulated values, never evidence of a live school query.
            'liveChecked': True, 'stale': False,
            'fetchedAt': '2026-11-13T10:00:00+08:00',
            'ageSeconds': 0, 'ttlSeconds': 300,
            'roomStates': {
                'complete': True, 'scope': dict(scope), 'free': free,
                'occupied': [name for name in names if name not in free and name not in unknown],
            },
        },
    }


def samples():
    entry = plans()['01-0013']
    names = sorted(room['name'] for room in entry['rooms'])
    free = [name for i, name in enumerate(names)
            if (i % 3 == 0 or name == '十号楼200') and name != '十号楼201']
    yield 'minglun-10', demo_result('01', '0013', entry['campus'], entry['name'], names, free)

    names = sorted('金明综合楼' + number
                   for rows in LAYOUTS['18']['floors'].values()
                   for row in rows for number in row if number)
    yield 'jinming-3', demo_result(
        '02', '18', '金明校区', '金明综合楼3', names, names[::3], ['金明综合楼3103'])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    readme = [
        '# 空教室机器人回复示例', '',
        '**全部为模拟数据；未连接教务接口、未发送 QQ。图片中的 DEMO 表示演示。**', '',
        '使用 `build_classroom_image()` 生成 Base64，再解码为 PNG。', '',
        '绿色＝模拟课程未占用；灰色＝模拟课程占用或未核验。日期、周次、时效字段均为测试输入，不能用于找教室。', '',
        '复现：在仓库根目录，使用已安装项目依赖的 Python 执行 `python ops/render-classroom-preview.py`。', '',
        f'本次环境：Python {platform.python_version()} / Pillow {pillow_version}。', '',
        '仓库 CI 使用 Python 3.13；本地示例不替代该环境的完整测试。', '',
    ]
    if int(pillow_version.split('.')[0]) >= 13:
        readme += ['**环境差异：本次 Pillow 不满足项目 `>=10.4,<13` 约束；仅验证此环境的示例渲染，不代表项目依赖兼容测试通过。**', '']

    for stem, result in samples():
        encoded = build_classroom_image(result)
        if not encoded:
            raise RuntimeError(f'{stem}: production image builder returned no image')
        png = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            left, top, right, bottom = ImageChops.difference(
                image, Image.new('RGB', image.size, '#f6f8fa')).getbbox()
            assert (left, top, image.width - right, image.height - bottom) == (48, 48, 48, 48)
            record = dict(name=stem, format=image.format, width=image.width, height=image.height,
                          png_bytes=len(png), base64_chars=len(encoded), free_rooms=result['total'],
                          metadata=image.info)
        if record['format'] != 'PNG' or len(png) > 2 * 1024 * 1024:
            raise RuntimeError(f'{stem}: invalid PNG or oversized image')
        (OUT / f'{stem}.png').write_bytes(png)
        (OUT / f'{stem}.base64.txt').write_text(encoded + '\n', encoding='utf-8')
        (OUT / f'{stem}.result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        reply = ('> 模拟机器人回复；合成数据，未发送 QQ。文字为本地示例，未经过 LangBot 消息封装。\n\n'
                 f'{record["metadata"]["Title"]} · 第10周 · 星期五 · {period_label(2)}\n\n'
                 f'模拟课程未占用 {result["total"]} 间；绿色表示课程未占用，灰色表示占用或未核验。\n\n'
                 f'![空教室 DEMO]({stem}.png)\n\n'
                 '仅演示图片效果，不代表实时空闲；现场是否开放以实际为准。\n')
        (OUT / f'{stem}.reply.md').write_text(reply, encoding='utf-8')
        records.append(record)
        readme += [f'## {record["metadata"]["Title"]}', '',
                   f'![空教室 DEMO]({stem}.png)', '',
                   f'{record["width"]} × {record["height"]}；PNG {len(png):,} 字节；'
                   f'模拟课程未占用 {result["total"]} 间。', '',
                   f'配套：`{stem}.result.json`、`{stem}.base64.txt`、`{stem}.reply.md`。', '']
    (OUT / 'README.md').write_text('\n'.join(readme), encoding='utf-8')
    (OUT / 'manifest.json').write_text(json.dumps({
        'synthetic': True, 'python': platform.python_version(), 'pillow': pillow_version,
        'school_api_called': False, 'qq_sent': False, 'images': records,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(records, ensure_ascii=False, indent=2))
    print(f'Output: {OUT}')


if __name__ == '__main__':
    main()
