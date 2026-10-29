"""Bounded, page-scoped leave status summaries without personal reasons."""
from collections import Counter


def leave_summary_lines(result: dict) -> list[str]:
    records = result.get('records')
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        return ['请假记录格式无法识别，未将其当成没有记录。']
    page = result.get('page', 1)
    page = page if type(page) is int and page > 0 else 1
    marker = result.get('records_truncated', 0)
    omitted = marker if type(marker) is int and marker >= 0 else 0
    if marker is True:
        # Safe CLI compaction uses a boolean. Earlier normalization may also
        # have trimmed rows, so records_total need not be the original page.
        lines = [f'第 {page} 页 · 当前摘要展示 {len(records)} 条请假记录。',
                 '摘要已裁剪，未展示记录未纳入以下统计。']
    else:
        lines = [f'第 {page} 页 · 本页返回 {len(records) + omitted} 条请假记录。']
    if omitted:
        lines.append(f'当前摘要保留 {len(records)} 条，另有 {omitted} 条未纳入以下统计。')
    total = result.get('total')
    if type(total) is int and total >= len(records):
        lines.append(f'系统返回总数：{total} 条。')
    counts: Counter[str] = Counter()
    for row in records:
        label = row.get('status_name')
        if not isinstance(label, str) or not label.strip():
            status = row.get('status')
            label = f'状态码 {status}' if type(status) in (int, str) and str(status).strip() else '状态未返回'
        counts[label.strip()[:80]] += 1
    for label, count in counts.items():
        lines.append(f'{label}：{count} 条。')
    if not records:
        lines.append('本次查询未返回请假记录。')
    lines.append('状态统计仅涵盖当前展示记录；不展示请假事由、姓名等个人信息。')
    return lines
