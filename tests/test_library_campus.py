from campus_core.bot import HenuCampusBot


def test_location_query_retains_confirmed_campus_for_ambiguous_school_names(monkeypatch):
    bot = HenuCampusBot('fixture-student', '', saved_cookies={'_v4_token': 'fixture'})
    monkeypatch.setattr(bot, '_is_token_valid', lambda: True)
    rows = [
        {'id': '8', 'name': '二楼南附楼走廊'},
        {'id': '9', 'name': '二楼北附楼走廊'},
        {'id': '10', 'name': '二楼大厅走廊'},
        {'id': '18', 'name': '读书室'},
        {'id': '67', 'name': '第一借书处'},
        {'id': '888', 'name': '待核实区域'},
    ]
    monkeypatch.setattr(bot, '_post_json', lambda *a, **k: {'code': 0, 'data': {'area': rows}})
    result = bot.list_locations(target_date='2026-10-10')
    assert result['success'] is True
    locations = {item['area_id']: item for item in result['locations']}
    assert [locations[key].get('campus') for key in ('8', '9', '10', '18', '67')] == [
        '金明', '金明', '金明', '金明', '明伦',
    ]
    assert 'campus' not in locations['888']
    fallback = {item['area_id']: item for item in bot.static_locations()}
    assert fallback['67']['campus'] == '明伦'
    assert fallback['18']['campus'] == '金明'
