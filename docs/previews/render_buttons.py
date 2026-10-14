"""Render synthetic card payloads without a browser or desktop capture."""
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT = '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
image = Image.new('RGB', (2020, 1570), '#edf3f8')
draw = ImageDraw.Draw(image)


def font(size):
    return ImageFont.truetype(FONT, size)


def label(position, text, size=28, fill='#152f48'):
    draw.text(position, text, font=font(size), fill=fill)


label((65, 48), '河大助手 · 回复中的原生按钮', 51)
label((68, 126), '合成预览｜仅示例数据｜未发送 QQ 消息｜生产按钮开关保持关闭', 27, '#5a7187')
payloads = json.loads((ROOT / 'buttons-payloads.json').read_text())
for index, card in enumerate(payloads['cards']):
    x, y = 60 + index % 2 * 1000, 210 + index // 2 * 630
    width, height = 940, 580
    draw.rounded_rectangle((x, y, x + width, y + height), radius=24, fill='white', outline='#d5e1eb', width=2)
    label((x + 32, y + 22), card['name'], 24, '#687f91')
    rows = [re.sub(r'\\([\\`*_{}\[\]()#!|~])', r'\1', line).replace('**', '').removeprefix('# ')
            for line in card['markdown'].splitlines() if line.strip()]
    label((x + 32, y + 72), rows[0], 31)
    label((x + 32, y + 120), rows[1], 27, '#26708f' if index < 2 else '#617c8c')
    line_y = y + 178
    body = rows[2:]
    for paragraph in body:
        paragraph = paragraph.removeprefix('- ')
        lines, current = [], ''
        for character in paragraph:
            if draw.textlength(current + character, font=font(25)) > width - 70:
                lines.append(current)
                current = ''
            current += character
        if current:
            lines.append(current)
        for line in lines:
            if line_y > y + height - 148:
                break
            label((x + 34, line_y), line, 25, '#435d73')
            line_y += 36
    buttons = card['keyboard']['content']['rows'][0]['buttons']
    button_width = (width - 80 - 18 * (len(buttons) - 1)) / len(buttons)
    for number, button in enumerate(buttons):
        left = x + 40 + number * (button_width + 18)
        top = y + height - 102
        disabled = card.get('disabled')
        blue = button['render_data'].get('style') == 1 and not disabled
        fill = '#ecf5ff' if blue else '#f5f7f9'
        draw.rounded_rectangle((left, top, left + button_width, top + 62), radius=12,
                               fill=fill, outline='#418ed5' if blue else '#d0dce5', width=2)
        text = '已取消' if disabled and number == 1 else button['render_data']['label']
        tx = left + (button_width - draw.textlength(text, font=font(28))) / 2
        label((tx, top + 12), text, 28, '#9aaebb' if disabled else '#236da9' if blue else '#56738b')
label((70, 1494), '确认/取消：仅处理原待确认操作；过期、跨人、跨会话及重复选择由服务器拒绝。', 27, '#5a7187')
image.save(ROOT / 'buttons-synthetic.png')
print('Rendered', ROOT / 'buttons-synthetic.png')
