from henu_plugin.cache import TTLCache


def test_cache_write_and_read_are_mutation_isolated() -> None:
    cache = TTLCache(default_ttl=60)
    original = {"items": [{"id": 1}, {"id": 2}]}
    cache.set("key", original)

    original["items"].clear()
    first = cache.get("key")
    assert first == {"items": [{"id": 1}, {"id": 2}]}

    first["items"][0]["id"] = 99
    second = cache.get("key")
    assert second == {"items": [{"id": 1}, {"id": 2}]}
