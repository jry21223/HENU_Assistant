"""Exact method overlay for LangBot QQ Official's previously empty send_message.

Deployment inserts only this function into the existing QQOfficialAdapter class.
"""


async def send_message(self, target_type, target_id, message):
    from langbot_plugin.api.entities.builtin.platform.message import Plain

    target = {'person': 'c2c'}.get(target_type)
    parts = getattr(message, 'root', None)
    if not target or not target_id or not parts or any(not isinstance(part, Plain) for part in parts):
        raise ValueError('QQ active messages require a private target and plain text')
    content = '\n'.join(part.text for part in parts)
    if not content.strip():
        raise ValueError('QQ active message cannot be empty')
    result = await self._send_c2c_or_group_text_reply(target, target_id, content)
    if not isinstance(result, dict) or not result.get('id'):
        raise RuntimeError('QQ active message receipt was not confirmed')
    return result
