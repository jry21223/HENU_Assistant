from pathlib import Path
import runpy
from types import SimpleNamespace


def test_legacy_overlay_keeps_valid_building_but_rejects_other_campus_and_types():
    patch = runpy.run_path(str(Path(__file__).parents[1] / 'ops/qq_classroom_scope.py'))['patch_legacy_resolver']
    source = '''
def unchanged():
    return "unchanged"
def resolve_resource(resource_type="", campus_code=""):
    candidates = rows.copy()
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
'''
    rows = [SimpleNamespace(resource_id='wrong-campus', resource_type='building', score=1),
            SimpleNamespace(resource_id='wrong-type', resource_type='classroom', score=.9),
            SimpleNamespace(resource_id='correct', resource_type='building', score=.8)]
    ns = {'rows': rows, 'get_resource': lambda rid: SimpleNamespace(location={
        'campusCode': '01' if rid == 'wrong-campus' else '02'})}
    exec(patch(source), ns)
    assert [c.resource_id for c in ns['resolve_resource']('building', '02')] == ['correct']
    assert len(ns['resolve_resource']()) == 3
    assert ns['unchanged']() == 'unchanged'


def test_legacy_exact_aliases_obey_type_campus_and_building_filters():
    patch = runpy.run_path(str(Path(__file__).parents[1] / 'ops/qq_classroom_scope.py'))['patch_legacy_registry']
    source = '''
def search_resources(resource_type="", campus_code="", building_code=""):
    results = []
    seen_ids = set()
    if True:
        for rid in aliases:
            record = get_resource(rid)
            if record and rid not in seen_ids:
                results.append(record)
                seen_ids.add(rid)
    return results
'''
    records = {
        'minglun': SimpleNamespace(resource_type='building', location={'campusCode': '01', 'buildingCode': '0003'}),
        'classroom': SimpleNamespace(resource_type='classroom', location={'campusCode': '02', 'buildingCode': '0006'}),
        'jinming': SimpleNamespace(resource_type='building', location={'campusCode': '02', 'buildingCode': '0006'}),
    }
    ns = {'aliases': list(records), 'get_resource': records.get}
    exec(patch(source), ns)
    assert ns['search_resources']('building', '02', '0006') == [records['jinming']]
    assert len(ns['search_resources']()) == 3
