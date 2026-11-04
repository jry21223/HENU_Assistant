"""School print cells may abbreviate courses while retaining full tooltips."""
from html import escape
from types import SimpleNamespace

import pytest

from campus_core.empty_classroom.parser import parse_schedule_html
from campus_core.empty_classroom import query
from henu_plugin.classroom_map import _room_states


def report(detail):
    title = f' title="{escape(detail, quote=True)}"' if detail is not None else ''
    return f'''<html><p>教室：综合教学楼103(67)</p>
    <table id="mytable0"><tr><td{title}><div class="div1" id="075">
    指导课 老师 [9]周 11-12节 001 班级 52 学院<br>
    文化课...<font>详细内容见备注2</font></div></td></tr></table>
    <p>教室：综合教学楼116(140)</p><table id="mytable1"><tr><td>
    <div class="div1" id="133">原理课 老师 [1-9]周 6-8节 002 班级 73 学院</div>
    </td></tr></table></html>'''


DETAIL = ('指导课 老师 [9]周 11-12节 001 班级 52 学院\r'
          '文化课 老师 [10-18]周 11-12节 001 班级 15 学院\r')


def test_overflow_restores_all_courses_and_keeps_room_scope(tmp_path, monkeypatch):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    client = SimpleNamespace(fetch_schedule_html=lambda **kw: report(DETAIL),
        fetch_classrooms=lambda **kw: [], get_campus_map=lambda: {'01': '明伦校区'},
        get_building_map=lambda code: {'0003': '综合教学楼'})
    parsed = parse_schedule_html(report(DETAIL), '2026,0', '01', '0003')
    assert parsed['parse_errors'] == []
    assert [(c['roomName'], c['dayOfWeek'], c['period']) for c in parsed['cells']] == [
        ('综合教学楼103', 7, 5), ('综合教学楼103', 7, 5), ('综合教学楼116', 3, 3)]
    for week, day, period, expected in [(9, 3, 3, 'occupied'), (10, 3, 3, 'free'),
                                        (10, 7, 5, 'free')]:
        result = query.query_free_classrooms(client, '2026,0', week, day, period,
            '01', '0003', freshness='live').to_dict()
        result['success'] = True
        states = _room_states(result, '01', '0003')
        assert states['综合教学楼116'] == expected
        if day == 7:
            assert states['综合教学楼103'] == 'occupied'
        assert result['freshness']['roomStates']['complete'] is True


@pytest.mark.parametrize('detail', [None, '', '文化课...详细内容见备注2', '未识别课程内容'])
def test_missing_or_unparseable_full_detail_stays_incomplete(detail):
    parsed = parse_schedule_html(report(detail), '2026,0', '01', '0003')
    assert parsed['parse_errors']


def test_other_room_tooltip_cannot_supply_missing_detail():
    source = report(None).replace('<td>\n    <div', f'<td title="{escape(DETAIL)}">\n    <div')
    parsed = parse_schedule_html(source, '2026,0', '01', '0003')
    assert parsed['parse_errors']
    assert not any(c['roomName'] == '综合教学楼103' and 10 in c['weeks'] for c in parsed['cells'])
