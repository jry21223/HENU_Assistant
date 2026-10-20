from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import pydantic
from langbot_plugin.api.entities.builtin.platform import events, entities, message

spec = importlib.util.spec_from_file_location('friend_transport', Path(__file__).parents[1] / 'ops/qq_friend_chain_transport.py')
overlay = importlib.util.module_from_spec(spec);spec.loader.exec_module(overlay)


class PreservedFriend(events.FriendMessage):
    validate_message_chain = overlay.validate_message_chain


def test_friend_message_roundtrip_retains_source_identity_and_time():
    epoch = 1792424400
    chain = message.MessageChain([message.Source(id='synthetic-source', time=datetime.fromtimestamp(epoch, timezone.utc)), message.Plain(text='确认')])
    friend = PreservedFriend(sender=entities.Friend(id='synthetic-user', nickname='C2C_MESSAGE_CREATE', remark=''), message_chain=chain, time=float(epoch))
    for _ in range(3):
        friend = PreservedFriend.model_validate(friend.model_dump())
        source = friend.message_chain.root[0]
        assert isinstance(source, message.Source)
        assert source.id == 'synthetic-source' and source.time.timestamp() == epoch
        assert friend.message_chain.root[1].text == '确认'


def test_nested_friend_message_preserves_component_types():
    class Envelope(pydantic.BaseModel):
        message_event: PreservedFriend
    raw = {'sender': {'id': 'synthetic-user', 'nickname': 'C2C_MESSAGE_CREATE', 'remark': ''}, 'message_chain': [{'type': 'Source', 'id': 'synthetic-source', 'timestamp': 1792424400}, {'type': 'Plain', 'text': 'HENU KIT 状态'}], 'time': 1792424400.0}
    envelope = Envelope.model_validate({'message_event': raw})
    assert isinstance(envelope.message_event.message_chain.root[0], message.Source)
    assert envelope.message_event.message_chain.root[0].id == 'synthetic-source'


def test_same_raw_friend_message_can_be_validated_twice_without_mutation():
    from copy import deepcopy
    raw = {'sender': {'id': 'synthetic-user', 'nickname': 'C2C_MESSAGE_CREATE', 'remark': ''}, 'message_chain': [{'type': 'Source', 'id': 'synthetic-source', 'timestamp': 1792424400}, {'type': 'Plain', 'text': '确认'}], 'time': 1792424400.0}
    original = deepcopy(raw)
    for _ in range(2):
        friend = PreservedFriend.model_validate(raw)
        assert friend.message_chain.root[0].id == 'synthetic-source'
        assert friend.message_chain.root[0].time.timestamp() == 1792424400
    assert raw == original
