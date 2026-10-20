"""Offline comparison of the exact shared card output; no model or school I/O."""
from pathlib import Path
import runpy
import sys
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from henu_plugin.presentation import Presentations
from henu_plugin.qq_cards import result_card
from ops.evaluate_qq_agent import synthetic_tool


def main():
    renderer = runpy.run_path(str(ROOT / 'ops/render-qq-cards-preview.py'))
    def render(md):
        return re.sub(r'<div class="line">## (.*?)</div>', r'<h3>\1</h3>', renderer['render'](md))
    store, owner = Presentations(), ('preview', 'person', 'synthetic', 'synthetic')
    commands = [('后天课表', 'schedule day --date 2026-10-22'),
                ('图书馆余座', 'library locations --date 2026-10-21'),
                ('空教室', 'empty classroom query --campus-text 金明 --building-text 十号楼 --week 8 --day-of-week 3 --period 3 --view free'), ('本周课表', 'schedule week')]
    sections, refs = [], []
    for index, (label, command) in enumerate(commands, 1):
        _, result = synthetic_tool(command, prepare=False)
        if command == 'schedule week':
            result = {'success': True, 'week_filter_applied': True, 'teaching_week': {'week': 8, 'week_start_date': '2026-10-19'}, 'schedule': {'星期一': [{'course': '数据结构', 'period': '第9-10节', 'location': '金明综合楼2402'}]}, 'cli': {'resolved_tool': 'schedule_query'}}
        direct = result_card(result).pages()[0]
        ref = store.capture(owner, 1, result)
        selected = store.render(owner, 1, {'cards': [{'ref': ref}]})['pages'][0]
        assert direct == selected
        refs.append(ref)
        sections.append(f'<section><h2>{label}</h2><div class="pair"><div><p>菜单／直接查询</p>{render(direct)}</div><div><p>自然语言 · Agent选择结果</p>{render(selected)}</div></div></section>')
    combined = store.render(owner, 1, {'cards': [{'ref': refs[0]}, {'ref': refs[1], 'campus': '金明校区'}]})
    sections.append('<section><h2>组合查询：后天课表＋金明余座</h2>' + ''.join(render(p) for p in combined['pages']) + '</section>')
    css = renderer['STYLE'] + 'body{max-width:1180px;margin:auto}main{display:block;padding-top:0}section{margin-bottom:32px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.pair p{color:#69778c}.card{margin-bottom:14px}section>h2{font-size:20px} @media(max-width:650px){.pair{grid-template-columns:1fr}}'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QQ 统一业务卡片对照</title><style>'+css+'</style><header><h1>同一份结果，同一种业务卡片</h1><p>由实际渲染器生成 · 合成数据 · 本地对照预览，不代表 QQ 实收</p></header><main>'+''.join(sections)+'</main></html>'
    output = ROOT / 'docs/previews/unified-business-cards.html'
    output.write_text(page)
    print(output)


if __name__ == '__main__':
    main()
