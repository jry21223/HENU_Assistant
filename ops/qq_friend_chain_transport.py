"""Exact FriendMessage validator overlay for both LangBot SDK transport hops."""
import pydantic


@pydantic.field_validator('message_chain', mode='before')
@classmethod
def validate_message_chain(cls, value):
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain
    from copy import deepcopy
    return MessageChain.model_validate(deepcopy(value))
