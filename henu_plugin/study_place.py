"""Short-lived, owner-scoped public classroom locations in native Storage."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextvars import ContextVar
from functools import wraps

STORAGE_KEY = 'shared:self_study_places_v1'
PLACE_TTL = 86400
_QUERY_STARTED = ContextVar('classroom_place_query_started', default=None)


def remember_context(call):
    @wraps(call)
    async def wrapped(*args, **kwargs):
        token = _QUERY_STARTED.set(time.time())
        try:
            return await call(*args, **kwargs)
        finally:
            _QUERY_STARTED.reset(token)
    return wrapped


def _owner_id(owner):
    return hashlib.sha256(json.dumps(owner, ensure_ascii=False).encode()).hexdigest()


def _lock(plugin):
    lock = getattr(plugin, '_study_place_lock', None)
    if lock is None:
        lock = plugin._study_place_lock = asyncio.Lock()
    return lock


async def _read(plugin):
    # Native LangBot raises on GET of an absent key.
    if STORAGE_KEY not in await plugin.get_plugin_storage_keys():
        return {}
    raw = await plugin.get_plugin_storage(STORAGE_KEY)
    if not raw or len(raw) > 256_000:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        return {}
    now = time.time()
    return {key: value for key, value in data.items()
            if isinstance(value, dict) and type(value.get('at')) in (int, float)
            and 0 <= now - value['at'] < PLACE_TTL}


def _record(kind, code, campus=''):
    from campus_core.resource_registry.registry import get_resource
    rid = f'henu:{kind}:' + (f'{campus}:' if campus else '') + str(code)
    return get_resource(rid)


def _matches(text, kind, expected_id, campus=''):
    from campus_core.resource_registry.alias import normalize
    from campus_core.resource_registry.registry import search_resources
    exact = {row.resource_id for row in search_resources(resource_type=kind, campus_code=campus, limit=100)
             if normalize(text).casefold() in {normalize(name).casefold()
                 for name in [row.canonical_name, row.display_name, *row.aliases]}}
    return exact == {expected_id}


def _validated_place(result):
    from campus_core.resource_registry import preload_seed_if_needed
    if result.get('success') is not True or result.get('partial'):
        return None
    if (result.get('cli') or {}).get('resolved_tool') != 'empty_classroom_query':
        return None
    params = result.get('_effective_params')
    if not isinstance(params, dict):
        return None
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    if 'periods' in data:
        slots = data['periods']
        if (not isinstance(slots, list) or len(slots) != 5
                or {row.get('period') for row in slots if isinstance(row, dict)} != {1, 2, 3, 4, 5}
                or not all(_verified(row) for row in slots)):
            return None
    elif not _verified(result):
        return None
    query = result.get('query') if isinstance(result.get('query'), dict) else data
    campus, building = query.get('campusCode'), query.get('buildingCode')
    if not campus or not building:
        return None
    preload_seed_if_needed()
    campus_record, building_record = _record('campus', campus), _record('building', building, campus)
    if not campus_record or not building_record or building_record.location.get('campusCode') != campus:
        return None
    for field, record, kind, constraint in (
            ('campus', campus_record, 'campus', ''), ('building', building_record, 'building', campus)):
        code, text = params.get(field + '_code'), params.get(field + '_text')
        if not code and not text:
            return None
        if code and str(code) != str(campus if field == 'campus' else building):
            return None
        if text and not _matches(text, kind, record.resource_id, constraint):
            return None
    scopes = [result, query, data] + list(data.get('periods') or [])
    for slot in data.get('periods') or []:
        if not isinstance(slot, dict) or slot.get('success') is False:
            return None
        scopes.append(slot.get('query') or {})
        scopes.extend(slot.get('rooms') or [])
    scopes += list(result.get('rooms') or [])
    for row in scopes:
        if not isinstance(row, dict) or row.get('partial') or (row.get('freshness') or {}).get('partial'):
            return None
        if row.get('campusCode', campus) != campus or row.get('buildingCode', building) != building:
            return None
    return {'campus': campus_record.canonical_name.removesuffix('校区'),
            'building': building_record.location.get('buildingName') or building_record.display_name,
            'campus_code': campus, 'building_code': building}


def _verified(row):
    proof = row.get('freshness') or {}
    if (row.get('stale') or row.get('is_stale') or proof.get('stale')
            or proof.get('cacheHit') is False or proof.get('partial')):
        return False
    if proof.get('liveChecked') is True:
        return True
    age, ttl = proof.get('ageSeconds'), proof.get('ttlSeconds')
    return (bool(proof.get('fetchedAt')) and type(age) in (int, float)
            and type(ttl) in (int, float) and 0 <= age <= ttl)


async def remember_query_place(plugin, owner, result):
    """Only a successful matching registry scope can become a future default."""
    started = _QUERY_STARTED.get()
    if not owner or started is None:
        return
    try:
        place = _validated_place(result)
        if not place:
            return
        async with _lock(plugin):
            entries = await _read(plugin)
            key = _owner_id(owner)
            if entries.get(key, {}).get('at', 0) > started:
                return
            entries[key] = {**place, 'at': started}
            entries = dict(sorted(entries.items(), key=lambda pair: pair[1]['at'])[-256:])
            await plugin.set_plugin_storage(STORAGE_KEY, json.dumps(entries, ensure_ascii=False).encode())
    except Exception:
        pass  # Remembering a public location must never invalidate the query itself.


async def known_place(plugin, owner):
    try:
        async with _lock(plugin):
            place = (await _read(plugin)).get(_owner_id(owner), {})
        from campus_core.resource_registry import preload_seed_if_needed
        preload_seed_if_needed()
        campus = _record('campus', place.get('campus_code'))
        building = _record('building', place.get('building_code'), place.get('campus_code'))
        if not campus or not building or building.location.get('campusCode') != place.get('campus_code'):
            return {}
        return {'campus': campus.canonical_name.removesuffix('校区'),
                'building': building.location.get('buildingName') or building.display_name,
                'campus_code': place['campus_code'], 'building_code': place['building_code']}
    except Exception:
        return {}
