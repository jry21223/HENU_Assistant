"""Private CLI membership gates; synthetic signed Core and supplier HTTP seams."""
import asyncio
import json
from unittest.mock import Mock

from test_qingmu_flow import Plugin, BIND
from henu_plugin.qingmu_flow import QingmuCoordinator
from henu_plugin.qingmu_client import Settings, QingmuClient


def test_member_courses_fee_included_and_revoked_confirmation_does_not_submit(core, monkeypatch):
    data,calls,_ = core
    settings = Settings('https://supplier.test.invalid','vendor','secret','form',
        {'ucampus':{'id':'service','name':'英语课程服务','billing':'采购价99元/门'}},
        {'get_success_code':'1','course_id_field':'id','course_name_field':'name',
         'order_id_field':'id','retry_success_code':'1'},True)
    monkeypatch.setattr('henu_plugin.qingmu_flow.settings_from_env',lambda:settings)
    get = Mock(return_value=[{'kcid':'eng','name':'大学英语'}])
    add = Mock(return_value='upstream-order')
    monkeypatch.setattr(QingmuClient,'courses',get)
    monkeypatch.setattr(QingmuClient,'place_order',add)
    async def run():
        flow = QingmuCoordinator(Plugin())
        conv = {'launcher_type':'person','launcher_id':'alice'}
        async def confirm(query):
            pending = await flow.valid_pending('alice',conv,query)
            return await flow.execute('qingmu confirm','alice',conv,query,confirmation_token=pending['token'])
        await flow.execute(BIND,'alice',conv,1)
        await confirm(2)
        data.update(plan='free',lifetime=False,allowed=False,benefits=[])
        denied = await flow.execute('qingmu courses --platform ucampus','alice',conv,3)
        assert denied['error_code'] == 'LIFETIME_REQUIRED'
        assert not get.called
        data.update(plan='lifetime',lifetime=True,allowed=True,benefits=['yuketang','ucampus','chaoxing','library_schedule'])
        assert (await flow.execute('qingmu courses --platform ucampus','alice',conv,4))['success']
        preview = await flow.execute('qingmu order add --platform ucampus --course 1','alice',conv,5)
        assert '费用已包含' in preview['reply_text'] and '无需另付费' in preview['reply_text']
        assert '99' not in str(preview) and '采购' not in str(preview)
        data.update(plan='free',lifetime=False,allowed=False,benefits=[])
        assert (await confirm(6))['error_code'] == 'LIFETIME_REQUIRED'
        assert not add.called
        assert all(json.loads(call[1]['data']) == {'subject':'alice'} for call in calls)
        assert (await flow.execute('qingmu orders','alice',conv,7))['success']
    asyncio.run(run())


def test_supplier_retry_rechecks_membership_and_old_results_stay_readable(core, monkeypatch):
    from henu_plugin.membership import BENEFITS
    data,_,response = core
    settings = Settings('https://supplier.test.invalid','vendor','secret','form',
        {'chaoxing':{'id':'cx','name':'学习通课程服务','billing':'采购价88元/门'}},
        {'get_success_code':'1','course_id_field':'id','course_name_field':'name',
         'order_id_field':'id','retry_success_code':'1'},True)
    monkeypatch.setattr('henu_plugin.qingmu_flow.settings_from_env',lambda:settings)
    monkeypatch.setattr(QingmuClient,'courses',lambda *_:[{'kcid':'course','name':'课程'}])
    monkeypatch.setattr(QingmuClient,'place_order',lambda *_:'existing-order')
    progress = Mock(return_value={'status':'进行中','process':'50%'})
    retry = Mock()
    monkeypatch.setattr(QingmuClient,'progress',progress)
    monkeypatch.setattr(QingmuClient,'retry',retry)
    async def run():
        flow = QingmuCoordinator(Plugin())
        conv = {'launcher_type':'person','launcher_id':'alice'}
        async def confirm(q):
            pending = await flow.valid_pending('alice',conv,q)
            return await flow.execute('qingmu confirm','alice',conv,q,confirmation_token=pending['token'])
        await flow.execute(BIND.replace('ucampus','chaoxing'),'alice',conv,1)
        await confirm(2)
        await flow.execute('qingmu courses --platform chaoxing','alice',conv,3)
        await flow.execute('qingmu order add --platform chaoxing --course 1','alice',conv,4)
        order = await confirm(5)
        command = 'qingmu order retry --id '+order['local_order_id']
        assert (await flow.execute(command,'alice',conv,6))['confirmation_required']
        response.status_code = 503
        assert (await confirm(7))['error_code'] == 'MEMBERSHIP_UNAVAILABLE'
        assert not retry.called
        assert (await flow.execute('qingmu order status --id '+order['local_order_id'],'alice',conv,8))['success']
        response.status_code = 200
        data.update(plan='free',lifetime=False,allowed=False,benefits=[])
        assert (await confirm(9))['error_code'] == 'LIFETIME_REQUIRED'
        assert not retry.called
        # Local removal remains a later private confirmation, even when free.
        await flow.execute('qingmu account clear --platform chaoxing','alice',conv,10)
        assert (await confirm(11))['success']
        data.update(plan='lifetime',lifetime=True,allowed=True,benefits=list(BENEFITS))
    asyncio.run(run())


def test_slow_drip_core_has_overall_deadline_and_never_calls_supplier(core, monkeypatch):
    import time
    data,_,_ = core
    settings = Settings('https://supplier.test.invalid','vendor','secret','form',
        {'ucampus':{'id':'service','name':'英语课程服务','billing':'采购价99元/门'}},
        {'get_success_code':'1','course_id_field':'id','course_name_field':'name',
         'order_id_field':'id','retry_success_code':'1'},True)
    monkeypatch.setattr('henu_plugin.qingmu_flow.settings_from_env',lambda:settings)
    get = Mock(return_value=[{'kcid':'eng','name':'大学英语'}])
    monkeypatch.setattr(QingmuClient,'courses',get)
    monkeypatch.setattr('henu_plugin.kit_client.REQUEST_DEADLINE_SECONDS',.04,raising=False)
    class SlowCore:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_content(self,chunk_size):
            for byte in json.dumps({'data':data}).encode():
                time.sleep(.002)
                yield bytes([byte])
    monkeypatch.setattr('requests.post',lambda *a,**kw:SlowCore())
    async def run():
        flow = QingmuCoordinator(Plugin())
        conv = {'launcher_type':'person','launcher_id':'alice'}
        await flow.execute(BIND,'alice',conv,1)
        p = await flow.valid_pending('alice',conv,2)
        await flow.execute('qingmu confirm','alice',conv,2,confirmation_token=p['token'])
        started = time.monotonic()
        result = await flow.execute('qingmu courses --platform ucampus','alice',conv,3)
        assert result['error_code'] == 'MEMBERSHIP_UNAVAILABLE'
        assert time.monotonic() - started < .2
        assert not get.called
    asyncio.run(run())
