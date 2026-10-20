"""Seat lookup and resource resolution at the public tool seam, without school I/O."""
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest


@pytest.mark.parametrize('seat_count', [1, 40])
def test_seat_lookup_does_not_serialize_the_entire_index_for_every_seat(tmp_path, monkeypatch, seat_count):
    import requests
    from campus_core import storage_paths
    from henu_mcp.tools import server_impl as api

    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    for attr, name in [('PROFILE_FILE','profile.json'), ('LIBRARY_COOKIE_FILE','library.json'), ('CAS_COOKIE_FILE','cas.json'), ('COOKIE_FILE','cookies.json')]:
        monkeypatch.setattr(api, attr, tmp_path/name)
    (tmp_path/'profile.json').write_text(json.dumps({'student_id':'synthetic', 'password':'synthetic'}))
    (tmp_path/'library.json').write_text(json.dumps({'_v4_token':'synthetic','_subject_student_id':'synthetic'}))
    directory = tmp_path/'data/shared/resource_registry';directory.mkdir(parents=True)
    # Comparable public metadata volume to production; no credentials or real users.
    seed = {f'old-{i}': {'resourceId':f'old-{i}', 'resourceType':'campus', 'displayName':'旧资源', 'notes':'x'*700} for i in range(2600)}
    (directory/'resources.json').write_text(json.dumps(seed))
    (directory/'sync_state.json').write_text(json.dumps({'seed_classrooms':{'status':'loaded'}}))

    seats = [{'id':str(i), 'no':f'{i:03}', 'name':f'{i:03}', 'status':1 if i<=seat_count else 2,
              'status_name':'空闲' if i<=seat_count else '已预约',
              'point_x':i, 'point_y':10, 'width':1, 'height':1} for i in range(1,seat_count+2)]
    def school(_session, method, url, **kwargs):
        assert method.upper() == 'POST'
        path = urlsplit(url).path
        if path == '/v4/space/pick':
            data = [{'id':'30', 'name':'701 七层南自习室'}]
        elif path == '/v4/Space/map':
            data = {'type':'1','date':{'reserveType':'1','list':[{'day':'2026-10-21','times':[{'id':'slot','start':'08:00','end':'23:00','status':'1'}]}]}}
        elif path == '/v4/Space/seat':
            data = {'list':seats}
        else:
            pytest.fail('Unexpected school operation: '+path)
        return SimpleNamespace(json=lambda:{'code':0,'data':data})
    monkeypatch.setattr(requests.Session, 'request', school)

    # Observe the real JSON codec's work, not the registry's private call count.
    # This budget catches repeated whole-index encoding regardless of file layout.
    encode = json.dumps
    encoded_bytes = [0]
    def measured_json(*args, **kwargs):
        text = encode(*args, **kwargs)
        encoded_bytes[0] += len(text.encode())
        return text
    monkeypatch.setattr(json, 'dumps', measured_json)
    result = api.library_query(view='seats', area_id='30', target_date='2026-10-21', preferred_time='08:00', preferred_end_time='23:00')
    assert result['success'], result
    assert result['available_count']==seat_count and result['total_count']==seat_count+1
    assert result['seats'][-1]['no']==f'{seat_count:03}'
    assert len(result['_qq_seat_map'])==seat_count+1
    assert encoded_bytes[0] < 8_000_000, f"Index serialization exceeded budget: {encoded_bytes[0]} bytes"
    resolved=api.resource_registry_query(view='search',query=f'座位{seat_count:03}',resource_type='library_seat')
    assert resolved['success'] and f'{seat_count:03}' in str(resolved)
