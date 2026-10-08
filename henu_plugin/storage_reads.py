"""Read an optional Storage record without treating outages as missing data."""
from langbot_plugin.entities.io.errors import ActionCallError


async def read_optional_storage(plugin, key):
    try:
        return await plugin.get_plugin_storage(key)
    except KeyError as exc:
        if exc.args != (key,):
            raise
        return None
    except ActionCallError as exc:
        # Known SDK forwarding layers; additional text or other errors fail closed.
        missing = f'Storage with key {key} not found'
        if str(exc) not in {'ActionCallError: ' * n + missing for n in (0, 1, 3)}:
            raise
        return None
