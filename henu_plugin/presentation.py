"""Request-scoped references to rendered facts, selected by the campus agent."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import secrets
import time
import re
import base64
import asyncio
from weakref import WeakValueDictionary
from typing import Any

from henu_plugin.qq_cards import Card, MAX_BYTES, MAX_PAGES, PAGE_TTL, escape, result_card


@dataclass
class ResultCard:
    card: Card
    campuses: dict[str, Card]
    image: str | None = None
    selection: dict | None = None
    fallback: Card | None = None
    image_only: bool = False


@dataclass
class ResultCards:
    expires: float
    cards: dict[str, ResultCard]
    regions: dict[str, dict] = field(default_factory=dict)
    begun: bool = False
    started: float = 0
    guard: tuple = (None, None)


def build_seat_image(result: dict) -> tuple[str | None, int | None]:
    rows = result.get('_qq_seat_map')
    area = result.get('area')
    day = str(result.get('target_date') or result.get('date') or '')
    clock = str(result.get('time_window') or '')
    if not result.get('success') or not isinstance(rows, list) or not rows or not isinstance(area, dict) or not day or not clock:
        return None, None
    from henu_plugin.seat_map_image import render_seat_map, seat_state
    from henu_plugin.library_availability import campus_now
    reserved = sum(seat_state(row) == 'reserved' for row in rows if isinstance(row, dict))
    try:
        title = ' · '.join(str(v) for v in (area.get('campus'), area.get('location') or area.get('name') or '图书馆座位') if v)
        data = render_seat_map(title, day + ' ' + clock, rows, campus_now().strftime('%H:%M:%S'))
        if len(data) <= 2 * 1024 * 1024:
            return base64.b64encode(data).decode(), reserved
    except (ValueError, KeyError, TypeError, OSError):
        pass
    return None, reserved


class Presentations:
    def __init__(self):
        self.requests: OrderedDict[tuple, ResultCards] = OrderedDict()
        self.latest: OrderedDict[tuple, tuple[float, int]] = OrderedDict()

    def begin(self, owner: tuple, query_id: int, guard: tuple, started: float) -> None:
        now = time.monotonic()
        key = (*owner, query_id)
        entry = self.requests.get(key)
        if entry is None or entry.expires <= now:
            entry = ResultCards(now + PAGE_TTL, {})
            self.requests[key] = entry
        if not entry.begun:
            entry.begun, entry.started, entry.guard = True, started, guard
        previous = self.latest.get(owner)
        if previous is None or entry.started > previous[0]:
            self.latest[owner] = (entry.started, query_id)
            self.latest.move_to_end(owner)
        while len(self.latest) > 128:
            self.latest.popitem(last=False)
        while len(self.requests) > 128:
            self.requests.popitem(last=False)

    def is_latest(self, owner: tuple, query_id: int) -> bool:
        entry = self.requests.get((*owner, query_id))
        return bool(entry and entry.begun and self.latest.get(owner) == (entry.started, query_id))

    def guard(self, owner: tuple, query_id: int) -> tuple:
        return self.requests[(*owner, query_id)].guard

    def with_region(self, owner: tuple, query_id: int, result: dict) -> dict:
        entry = self.requests.get((*owner, query_id))
        area = result.get('area')
        if entry is None or entry.expires <= time.monotonic() or not isinstance(area, dict):
            return result
        region = entry.regions.get(str(area.get('id') or ''))
        return {**result, 'area': {**area, **region}} if region else result

    def capture(self, owner: tuple, query_id: int, result: dict, *, image: str | None = None, reserved: int | None = None) -> str:
        now = time.monotonic()
        for key in list(self.requests):
            if self.requests[key].expires <= now:
                del self.requests[key]
        key = (*owner, query_id)
        entry = self.requests.setdefault(key, ResultCards(now + PAGE_TTL, {}))
        ref = secrets.token_urlsafe(12)
        variants = {}
        cli = result.get('cli') if isinstance(result.get('cli'), dict) else {}
        if ((cli.get('resolved_tool') or result.get('_resolved_tool_name')) == 'library_query'
                and result.get('success') and isinstance(result.get('locations'), list)):
            entry.regions = {} if result.get('source') in {'static_fallback', 'live_empty'} else {
                str(row.get('area_id') or ''): {'campus': row['campus'], 'location': str(row.get('location') or '')}
                for row in result['locations'][:64] if isinstance(row, dict) and row.get('area_id')
                and row.get('campus') in {'金明校区', '明伦校区', '龙子湖校区', '郑州校区'}}
        rendered = dict(result)
        selection = None
        if image:
            rendered['_qq_seat_image'] = True
        if reserved is not None:
            rendered['reserved_count'] = reserved
        if ((cli.get('resolved_tool') or result.get('_resolved_tool_name')) == 'library_query'
                and result.get('success') and isinstance(result.get('seats'), list) and isinstance(result.get('area'), dict)):
            day = str(result.get('target_date') or result.get('date') or '')
            clock = str(result.get('time_window') or (str(result.get('start') or '') + '–' + str(result.get('end') or '')))
            window = re.search(r'(\d{2}:\d{2}).*?(\d{2}:\d{2})', clock)
            area = result['area']
            if day and window and re.fullmatch(r'\d{1,12}', str(area.get('id') or '')):
                selection = {'area': {'area_id': str(area['id']), 'location': str(area.get('location') or area.get('name') or ''), 'campus': str(area.get('campus') or '')},
                    'window': (day, *window.groups()),
                    'seats': [str(row.get('no') or row.get('seat_no') or '') if isinstance(row, dict) else str(row) for row in result['seats']]}
        card = result_card(rendered)
        if card.title == '图书馆余座':
            from henu_plugin.library_availability import availability_card
            for campus in {'金明校区', '明伦校区', '龙子湖校区'}:
                variants[campus] = availability_card(result, campus=campus)
        entry.cards[ref] = ResultCard(card, variants, image, selection, result_card({**rendered, '_qq_seat_image': False}) if image else card,
                                    image_only=bool(image and (cli.get('resolved_tool') or result.get('_resolved_tool_name')) == 'library_query'))
        self.requests.move_to_end(key)
        while len(entry.cards) > 32:
            del entry.cards[next(iter(entry.cards))]
        while len(self.requests) > 128:
            self.requests.popitem(last=False)
        return ref

    def render(self, owner: tuple, query_id: int, params: dict, *, failed_images: set[str] | None = None) -> dict:
        if (not isinstance(params, dict) or set(params) - {'cards', 'note'}
                or not isinstance(params.get('cards'), list) or len(params['cards']) > 4
                or not isinstance(params.get('note', ''), str) or len(params.get('note', '')) > 1200
                or not params['cards'] and not params.get('note', '').strip()):
            raise ValueError('invalid_presentation')
        selections, note = params['cards'], params.get('note', '').strip()
        entry = self.requests.get((*owner, query_id))
        if selections and (entry is None or entry.expires <= time.monotonic()):
            raise ValueError('results_expired')
        chosen, remaining, images, seats = [], [], [], []
        continuation_at = None
        seen = set()
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) - {'ref', 'campus'}:
                raise ValueError('invalid_selection')
            ref = selection.get('ref')
            if not isinstance(ref, str) or ref in seen or ref not in entry.cards:
                raise ValueError('invalid_reference')
            seen.add(ref)
            asset = entry.cards[ref]
            if asset.selection and not self.is_latest(owner, query_id):
                raise ValueError('request_superseded')
            card, campuses = (asset.fallback if failed_images and ref in failed_images else asset.card), asset.campuses
            campus = selection.get('campus', '')
            if campus:
                if campus not in campuses:
                    raise ValueError('invalid_campus')
                card = campuses[campus]
            if asset.image and not (failed_images and ref in failed_images):
                images.append(ref)
            if asset.selection:
                seats.append(ref)
            if asset.image_only and asset.image and not (failed_images and ref in failed_images):
                continue
            parts = card.pages()
            if len(parts) > 1 and continuation_at is None:
                continuation_at = len(chosen)
            chosen.append(parts[0])
            remaining.extend(parts[1:])
        if note:
            parts = Card('补充说明', '', (escape(note, len(note) * 2 + 100),)).pages()
            if len(parts) > 1 and continuation_at is None:
                continuation_at = len(chosen)
            chosen.append(parts[0])
            remaining.extend(parts[1:])
        # First screens of all selected results arrive in this turn. Longer
        # continuations stay available without using more passive reply slots.
        strip_paging = lambda part: re.sub(r'\n第 \d+/\d+ 页 · [^\n]+', '', part)
        chosen = [strip_paging(p) for p in chosen]
        remaining = [strip_paging(p) for p in remaining]
        more_hint = '\n\n还有较长结果，回复“下一页”继续查看。' if remaining else ''
        if continuation_at is not None:
            # Replace that card's longer original paging footer before packing,
            # so even a full note page never gets an out-of-budget suffix.
            chosen[continuation_at] += more_hint
        pages: list[str] = []
        for part in chosen:
            if pages and len((pages[-1] + '\n\n' + part).encode()) <= MAX_BYTES:
                pages[-1] += '\n\n' + part
            else:
                pages.append(part)
        if len(images) + len(pages) > 5:
            raise ValueError('too_many_messages')
        if len(pages) + len(remaining) > MAX_PAGES:
            raise ValueError('too_many_pages')
        return {'pages': pages, 'more': remaining, 'images': images,
                'seat_selection': seats[0] if len(seats) == 1 else None,
                'expires': entry.expires if entry is not None else time.monotonic() + PAGE_TTL}

    def asset(self, owner: tuple, query_id: int, ref: str) -> ResultCard:
        entry = self.requests.get((*owner, query_id))
        if entry is None or entry.expires <= time.monotonic() or ref not in entry.cards:
            raise ValueError('results_expired')
        return entry.cards[ref]


def presentations(plugin: Any) -> Presentations:
    if not isinstance(getattr(plugin, '_henu_presentations', None), Presentations):
        plugin._henu_presentations = Presentations()
    return plugin._henu_presentations


def publication_lock(plugin: Any, owner: tuple, query_id: int) -> asyncio.Lock:
    if plugin is None:
        return asyncio.Lock()
    if not isinstance(getattr(plugin, '_henu_publication_locks', None), WeakValueDictionary):
        plugin._henu_publication_locks = WeakValueDictionary()
    key = (*owner, query_id)
    lock = plugin._henu_publication_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        plugin._henu_publication_locks[key] = lock
    return lock
