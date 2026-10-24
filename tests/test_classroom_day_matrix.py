import pytest

from campus_core.empty_classroom.query import query_free_classrooms
from campus_core.empty_classroom.storage import compute_query_hash, save_query_cache


@pytest.mark.parametrize('cached', [False, True], ids=['missing', 'expired'])
def test_read_only_query_distinguishes_missing_and_expired_records(tmp_path, monkeypatch, cached):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    params = dict(term_code='2026,0', week=8, day_of_week=6, period=1,
                  campus_code='02', building_code='18')
    if cached:
        key = compute_query_hash(**params, type_code='', min_capacity=0, keyword='')
        save_query_cache(key, {
            'query': {'termCode': '2026,0', 'week': 8, 'dayOfWeek': 6, 'period': 1,
                      'campusCode': '02', 'buildingCode': '18'},
            'rooms': [{'roomName': '金明综合楼3-101'}], 'total': 1,
            'freshness': {'fetchedAt': '2020-01-01T00:00:00+00:00', 'ageSeconds': 0, 'stale': False},
        })
    result = query_free_classrooms(None, **params, freshness='cache_only')
    assert result.freshness['liveChecked'] is False
    assert result.freshness['cacheHit'] is cached
    if cached:
        assert result.freshness['stale'] is True
        assert result.freshness['ageSeconds'] > 300
        assert result.freshness['fetchedAt'] == '2020-01-01T00:00:00+00:00'
    else:
        assert not result.freshness['fetchedAt']
        assert result.freshness['note']


def test_all_day_api_keeps_location_date_and_each_slots_evidence(tmp_path, monkeypatch):
    from campus_core import storage_paths
    from henu_mcp.tools import server_impl as api
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: pytest.fail('school login'))
    from campus_core.resource_registry import preload_seed_if_needed
    preload_seed_if_needed()
    params = dict(term_code='2026,0', week=8, day_of_week=6,
                  campus_code='02', building_code='18')
    key = compute_query_hash(**params, period=1, type_code='', min_capacity=0, keyword='')
    save_query_cache(key, {
        'query': {'termCode': '2026,0', 'week': 8, 'dayOfWeek': 6, 'period': 1,
                  'campusCode': '02', 'buildingCode': '18'},
        'rooms': [{'roomName': '金明综合楼3-101', 'campusCode': '02', 'buildingCode': '18'}],
        'total': 1, 'freshness': {'fetchedAt': '2020-01-01T00:00:00+00:00'},
    })
    result = api.empty_classroom_query(view='day_matrix', **params,
                                      freshness='cache_only')
    assert result['success'] is True
    data = result['data']
    assert data['date'] == ''  # No trusted term label in a query-result cache.
    assert data['campusCode'] == '02' and data['buildingCode'] == '18'
    assert data['campusName'] == '金明校区' and data['buildingName'] == '金明综合楼3'
    assert len(data['periods']) == 5
    first, second = data['periods'][:2]
    assert first['freshness']['stale'] is True
    assert first['rooms'][0]['campusCode'] == '02'
    assert second['freshness']['cacheHit'] is False


def test_all_day_saved_session_does_not_retry_login_when_cookie_expired(tmp_path, monkeypatch):
    from henu_mcp.tools import server_impl as api

    class ExpiredSession:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch_user_context(self):
            return {'authenticated': False}

        def login(self):
            pytest.fail('must not submit credentials')

    monkeypatch.setattr(api, 'HenuXkClient', ExpiredSession)
    monkeypatch.setattr(api, '_resolve_account', lambda *args, **kwargs: ('synthetic-user', 'synthetic-password'))
    monkeypatch.setattr(api, '_load_xk_cookies', lambda: {'synthetic-cookie': 'not-real'})
    result = api.empty_classroom_query(view='day_matrix', campus_code='02', building_code='18',
                                      saved_session_only=True, target_date='2026-10-24')
    assert result['success'] is False
    assert result['error_code'] == 'session_unavailable'
    assert '未重新登录' in result['msg']


def test_read_only_query_never_relabels_other_campus_cache(tmp_path, monkeypatch):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    params = dict(term_code='2026,0', week=8, day_of_week=6, period=1,
                  campus_code='02', building_code='18')
    key = compute_query_hash(**params, type_code='', min_capacity=0, keyword='')
    save_query_cache(key, {
        'query': {'termCode': '2026,0', 'week': 8, 'dayOfWeek': 6, 'period': 1,
                  'campusCode': '03', 'buildingCode': '23'},
        'rooms': [{'roomName': '友兰学堂南楼C座301', 'campusCode': '03', 'buildingCode': '23'}],
        'total': 1, 'freshness': {'fetchedAt': '2026-10-24T05:00:00+00:00'},
    })
    result = query_free_classrooms(None, **params, freshness='cache_only')
    assert not result.rooms
    assert result.freshness['partial'] is True


def test_failed_upstream_is_not_reported_as_zero_free_rooms(tmp_path, monkeypatch):
    from campus_core import storage_paths
    from henu_plugin.qq_cards import result_card
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)

    class OfflineSchool:
        def fetch_schedule_html(self, **kwargs):
            raise ConnectionError('offline')

        def get_campus_map(self):
            return {'02': '金明校区'}

        def get_building_map(self, _campus):
            return {'18': '金明综合楼3'}

    result = query_free_classrooms(OfflineSchool(), '2026,0', 8, 6, 1,
                                  campus_code='02', building_code='18', freshness='live')
    page = '\n'.join(result_card({'success': True, 'data': {'periods': [result.to_dict()]}},
                                 'empty_classroom_query').pages())
    assert '待核验' in page
    assert '课程未占用 0 间' not in page


def test_date_cannot_relabel_unverified_old_term_cache(tmp_path, monkeypatch):
    from campus_core import storage_paths
    from henu_mcp.tools import server_impl as api
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: pytest.fail('school login'))
    result = api.empty_classroom_query(view='day_matrix', term_code='2025,0', week=8,
                                      day_of_week=6, target_date='2026-10-24', freshness='cache_only')
    assert result['success'] is False
    assert result['error_code'] == 'calendar_term_unavailable'


def test_explicit_teaching_week_gets_date_from_school_term_and_reviewed_calendar(tmp_path, monkeypatch):
    from campus_core import storage_paths
    from campus_core.empty_classroom.models import Term
    from henu_mcp.tools import server_impl as api
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)

    class SchoolTerms:
        def fetch_terms(self):
            return [Term('school-opaque-term', '2026-2027学年第一学期', '2026', '0')]

        def resolve_term(self, _term):
            return {'success': True, 'term_code': 'school-opaque-term'}

    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: SchoolTerms())
    # Result cache is the external data fixture, not a mocked query engine.
    from datetime import datetime
    for period in range(1, 6):
        key = compute_query_hash(term_code='school-opaque-term', week=8, day_of_week=6,
                                 period=period, campus_code='', building_code='',
                                 type_code='', min_capacity=0, keyword='')
        save_query_cache(key, {'query': {'termCode': 'school-opaque-term', 'week': 8, 'dayOfWeek': 6,
                                         'period': period, 'campusCode': '', 'buildingCode': ''},
                               'rooms': [], 'total': 0, 'freshness': {'fetchedAt': datetime.now().isoformat()}})
    result = api.empty_classroom_query(view='day_matrix', term_code='school-opaque-term', week=8,
                                      day_of_week=6)
    assert result['success'] is True
    assert result['data']['date'] == '2026-10-24'
