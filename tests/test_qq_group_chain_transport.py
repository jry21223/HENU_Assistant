"""Exercise the SDK event transport seam used before group response rules."""
import importlib.util
from pathlib import Path

from langbot_plugin.api.entities.builtin.platform import entities, events, message


def test_group_transport_preserves_at_text_and_sender_across_repeated_hops():
    spec = importlib.util.spec_from_file_location(
        'group_transport', Path(__file__).parents[1] / 'ops/qq_group_chain_transport.py')
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)

    class PreservedGroup(events.GroupMessage):
        validate_message_chain = overlay.validate_message_chain

    raw = {
        'sender': {'id': 'synthetic-member', 'member_name': 'test',
                   'permission': 'MEMBER', 'special_title': '',
                   'group': {'id': 'synthetic-group', 'name': 'test', 'permission': 'MEMBER'}},
        'message_chain': [{'type': 'At', 'target': 'justbot'},
                          {'type': 'Plain', 'text': '使用帮助'},
                          {'type': 'Source', 'id': 'synthetic-source', 'timestamp': 1792424400},
                          {'type': 'Image', 'url': 'https://example.com/synthetic.png'}],
        'time': 1792424400,
    }
    from copy import deepcopy
    original = deepcopy(raw)
    for _ in range(3):
        unchanged = deepcopy(raw)
        event = PreservedGroup.model_validate(raw)
        PreservedGroup.model_validate(raw)
        assert raw == unchanged
        assert isinstance(event.message_chain.root[0], message.At)
        assert event.message_chain.root[0].target == 'justbot'
        assert isinstance(event.message_chain.root[1], message.Plain)
        assert event.message_chain.root[1].text == '使用帮助'
        assert isinstance(event.message_chain.root[2], message.Source)
        assert event.message_chain.root[2].id == 'synthetic-source'
        assert event.message_chain.root[2].time.timestamp() == 1792424400
        assert isinstance(event.message_chain.root[3], message.Image)
        assert event.message_chain.root[3].url == 'https://example.com/synthetic.png'
        assert event.sender.id == 'synthetic-member'
        assert event.group.id == 'synthetic-group'
        raw = event.model_dump(mode='json')
    assert original['message_chain'][2]['timestamp'] == 1792424400
