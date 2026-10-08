"""Authoritative signed HTTP benefit boundary, with synthetic Core responses."""
import asyncio
import json

import pytest

from henu_plugin.membership import MembershipError, require_benefit, require_benefit_async

BENEFITS = ['yuketang', 'ucampus', 'chaoxing', 'library_schedule']



def test_signed_subject_only_fresh_benefits_on_every_call(core):
    data,calls,_ = core
    assert require_benefit('alice','ucampus_ai')['allowed'] is True
    assert asyncio.run(require_benefit_async('bob','library_schedule'))['allowed'] is True
    assert [json.loads(call[1]['data']) for call in calls] == [{'subject':'alice'}, {'subject':'bob'}]
    assert all(call[0].endswith('/qq-bindings/benefits') for call in calls)
    assert calls[0][1]['headers']['X-Nonce'] != calls[1][1]['headers']['X-Nonce']
    data.update(plan='free', lifetime=False, allowed=False, benefits=[])
    with pytest.raises(MembershipError, match='LIFETIME_REQUIRED'):
        require_benefit('alice','ucampus')
    assert len(calls) == 3

@pytest.mark.parametrize('bad', [
    {'allowed':True,'lifetime':False,'plan':'free','benefits':[]},
    {'benefits':['ucampus']}, {'lifetime':1}, {'bound':False},
    {'checked_at':'2020-01-01T00:00:00Z'}, {'checked_at':'2999-01-01T00:00:00Z'},
    {'checked_at':'2026-10-09T00:00:00+00:00'}, {'benefits':BENEFITS+['ucampus']},
])
def test_broken_or_stale_core_contract_never_authorizes(core, bad):
    data,_,_ = core
    data.update(bad)
    with pytest.raises(MembershipError, match='MEMBERSHIP_UNAVAILABLE'):
        require_benefit('alice','ucampus')


def test_not_bound_outage_and_invalid_subject_fail_closed(core):
    _,calls,response = core
    response.status_code = 503
    with pytest.raises(MembershipError, match='MEMBERSHIP_UNAVAILABLE'):
        require_benefit('alice','ucampus')
    with pytest.raises(MembershipError, match='NOT_BOUND'):
        require_benefit('','ucampus')
    assert len(calls) == 1


def test_unbound_and_transport_failure_are_safe_retry_guidance(core, monkeypatch):
    import requests
    from henu_plugin.membership import MembershipError
    class Unbound:
        status_code = 404
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_content(self,_): yield b'{"error":{"code":"NOT_BOUND"}}'
    monkeypatch.setattr('requests.post',lambda *a,**kw:Unbound())
    with pytest.raises(MembershipError) as denied:
        require_benefit('alice','ucampus')
    assert denied.value.code == 'NOT_BOUND' and '绑定' in denied.value.public_result()['reply_text']
    def offline(*a,**kw): raise requests.Timeout('synthetic secret data should not appear')
    monkeypatch.setattr('requests.post',offline)
    with pytest.raises(MembershipError) as denied:
        require_benefit('alice','ucampus')
    assert 'synthetic' not in str(denied.value.public_result())


def test_current_timestamp_must_be_rfc3339_utc(core):
    data,_,_ = core
    data['checked_at'] = data['checked_at'].replace('T',' ')
    with pytest.raises(MembershipError, match='MEMBERSHIP_UNAVAILABLE'):
        require_benefit('alice','ucampus')


def test_async_qualification_has_deadline_even_before_a_response_byte(core, monkeypatch):
    import time
    data,_,_ = core
    monkeypatch.setattr('henu_plugin.membership.ASYNC_DEADLINE_SECONDS',.02)
    monkeypatch.setattr('henu_plugin.kit_client.REQUEST_DEADLINE_SECONDS',.03)
    class WaitingCore:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_content(self,_):
            time.sleep(.1)
            yield json.dumps({'data':data}).encode()
    monkeypatch.setattr('requests.post',lambda *a,**kw:WaitingCore())
    async def run():
        started = time.monotonic()
        with pytest.raises(MembershipError, match='MEMBERSHIP_UNAVAILABLE'):
            await require_benefit_async('alice','library_schedule')
        assert time.monotonic() - started < .07
    asyncio.run(run())
