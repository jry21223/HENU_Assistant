"""GroupMessage validator overlay for host and runtime SDK transport hops.

Install this method on GroupMessage, retaining all other SDK event methods.
An independent copy is intentional: deployment embeds the method into the SDK.
"""
import pydantic


@pydantic.field_validator('message_chain', mode='before')
@classmethod
def validate_message_chain(cls, value):
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain
    from copy import deepcopy
    return MessageChain.model_validate(deepcopy(value))
