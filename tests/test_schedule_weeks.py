import json
from datetime import datetime
from zoneinfo import ZoneInfo

from henu_mcp.tools import server_impl as api


def schedule_fixture(tmp_path,monkeypatch,rows):
    (tmp_path/'schedule_clean_latest.json').write_text(json.dumps({'schedule':{'星期二':rows}},ensure_ascii=False))
    monkeypatch.setattr(api,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(api,'_now_dt',lambda timezone:datetime(2026,10,19,12,tzinfo=ZoneInfo('Asia/Shanghai')))


def course(name,weeks):
    return {'course':name,'period':'第1-2节','time':weeks+' 二[1-2]','location':'十号楼101'}


def test_day_filters_each_week_variant_before_returning_courses(tmp_path,monkeypatch):
    rows=[course('计算机网络-旧教室','1,3周'),course('计算机网络-本周','1-18周'),course('计算机网络-单周','5,7,9,11,13,15,17周'),course('概率论','1-18周（双）')]
    schedule_fixture(tmp_path,monkeypatch,rows)
    result=api.schedule_query(view='day',target_date='2026-10-20')
    assert result['week_filter_applied'] is True
    assert result['teaching_week']['week']==8
    assert [row['course'] for row in result['schedule']]==['计算机网络-本周','概率论']
    assert result['schedule_count']==2


def test_next_week_changes_enumeration_range_and_parity_matches(tmp_path,monkeypatch):
    schedule_fixture(tmp_path,monkeypatch,[course('旧周','1,3周'),course('单周','5,7,9,11,13,15,17周'),course('双周','1-18周（双）'),course('后半学期','9-18周')])
    result=api.schedule_query(view='day',target_date='2026-10-27')
    assert [row['course'] for row in result['schedule']]==['单周','后半学期']
    assert result['teaching_week']['week']==9


def test_unknown_week_expression_is_not_treated_as_every_week(tmp_path,monkeypatch):
    schedule_fixture(tmp_path,monkeypatch,[course('已核验','1-18周'),course('待核验','未知周次')])
    result=api.schedule_query(view='day',target_date='2026-10-20')
    assert [row['course'] for row in result['schedule']]==['已核验']
    assert result['unknown_course_count']==1


def test_date_outside_verified_calendar_does_not_return_full_day(tmp_path,monkeypatch):
    schedule_fixture(tmp_path,monkeypatch,[course('不能当作当天课','1-18周')])
    result=api.schedule_query(view='day',target_date='2028-10-17')
    assert result['success'] is False
    assert not result.get('schedule')
    assert result['error_code']=='teaching_week_unavailable'


def test_current_next_course_is_selected_after_week_filter(tmp_path,monkeypatch):
    schedule_fixture(tmp_path,monkeypatch,[course('旧教室','1,3周'),course('本周课','1-18周')])
    monkeypatch.setattr(api,'_now_dt',lambda timezone:datetime(2026,10,20,7,tzinfo=ZoneInfo('Asia/Shanghai')))
    monkeypatch.setattr(api,'_load_period_times',lambda:{'1':{'start':'08:00','end':'08:45'},'2':{'start':'08:55','end':'09:40'}})
    result=api.schedule_query(view='current',auto_calibrate=False)
    assert result['next_course']['course']=='本周课'
    assert len(result['day_schedule'])==1 and result['week_filter_applied']


def test_week_view_filters_variants_instead_of_returning_full_term(tmp_path,monkeypatch):
    schedule_fixture(tmp_path,monkeypatch,[course('旧周','1,3周'),course('本周','1-18周')])
    result=api.schedule_query(view='week')
    assert [row['course'] for row in result['schedule']['星期二']]==['本周']
    assert result['teaching_week']['week']==8


def test_unrecognized_week_qualifiers_are_omitted_not_partially_matched(tmp_path, monkeypatch):
    rows = [course('正常', '1-18周'), course('前置单周', '单周1-18周'),
            course('待定', '1-18周（待定）'), course('除外', '1-18周（双周除外）')]
    schedule_fixture(tmp_path, monkeypatch, rows)
    result = api.schedule_query(view='day', target_date='2026-10-20')
    assert [row['course'] for row in result['schedule']] == ['正常']
    assert result['unknown_course_count'] == 3
    assert result['week_filter_complete'] is False
