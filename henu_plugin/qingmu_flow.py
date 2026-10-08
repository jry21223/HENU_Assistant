"""Per-QQ encrypted platform state, private intake and durable order coordination."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shlex
import time

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain

from henu_mcp.core.secure_storage import decrypt_value, encrypt_value
from henu_plugin.cli import inspect_cli_command
from henu_plugin.confirmation import conversation_context, create_pending_operation, validate_pending_operation
from henu_plugin.qingmu_cli import PLATFORMS, help_payload, is_qingmu_command
from henu_plugin.storage_reads import read_optional_storage
from henu_plugin.qingmu_client import QingmuClient, QingmuError, settings_from_env


STATE_SCHEMA = 'henu.qingmu-state.v1'


def reply_result(message, *, success=True, **values):
    return {'success': success, 'msg': message, 'reply_text': message, **values}


def state_key(sender):
    return 'user:' + sender + ':qingmu_state'


def get_coordinator(plugin):
    flow = getattr(plugin, '_henu_qingmu', None)
    if flow is None:
        flow = QingmuCoordinator(plugin)
        plugin._henu_qingmu = flow
    return flow


class QingmuCoordinator:
    def __init__(self, plugin):
        self.plugin = plugin
        self.tasks = {}
        self.users = {}
        self.seen = set()
        self.locks = getattr(plugin, '_qingmu_locks', None)
        if self.locks is None:
            self.locks = {}
            plugin._qingmu_locks = self.locks

    def _lock(self, sender):
        # Include loop to keep sequential test/runtime event-loop lifetimes independent.
        return self.locks.setdefault((asyncio.get_running_loop(), sender), asyncio.Lock())

    @staticmethod
    def _identity(sender, conversation):
        return (isinstance(sender, str) and sender not in {'', '0', 'None', 'none'}
                and bool(re.fullmatch(r'[0-9A-Za-z._-]{1,128}', sender))
                and conversation.get('launcher_type') == 'person'
                and bool(conversation.get('launcher_id')))

    async def _load(self, sender):
        key = state_key(sender)
        raw = await read_optional_storage(self.plugin, key)
        if not raw:
            return {'schema': STATE_SCHEMA, 'accounts': {}, 'courses': {}, 'orders': {}, 'pending': None}
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(decrypt_value(raw))
        if not isinstance(data, dict) or data.get('schema') != STATE_SCHEMA:
            raise QingmuError('invalid_storage')
        for field in ('accounts', 'courses', 'orders'):
            if not isinstance(data.get(field), dict):
                raise QingmuError('invalid_storage')
        return data

    async def _save(self, sender, data):
        raw = encrypt_value(json.dumps(data, ensure_ascii=False)).encode()
        await self.plugin.set_plugin_storage(state_key(sender), raw)

    async def valid_pending(self, sender, conversation, query_id):
        if not self._identity(sender, conversation):
            return None
        async with self._lock(sender):
            pending = (await self._load(sender)).get('pending')
            if not isinstance(pending, dict) or pending.get('conversation') != conversation:
                return None
            check = validate_pending_operation(pending, token=pending.get('token', ''), storage_key=sender,
                canonical_command=pending.get('command', ''), query_id=query_id)
            return pending if check.ok else None

    async def handle(self, ctx, *, is_group, command=None):
        text = command if command is not None else str(getattr(ctx.event, 'text_message', '') or getattr(ctx.event, 'message_chain', '')).strip()
        if not is_qingmu_command(text):
            return False
        # Intercept the entire namespace, including malformed password commands, before LLM/history.
        ctx.prevent_default()
        ctx.prevent_postorder()
        sender = str(getattr(ctx.event, 'sender_id', '') or '').strip()
        conversation = conversation_context(ctx.event)
        if is_group or not self._identity(sender, conversation):
            await self.reply(ctx, '青木平台账号和订单仅允许本人私聊操作；本条消息未交给模型。')
            return True
        identity = (sender, conversation['launcher_id'], ctx.query_id)
        if identity in self.seen:
            return True
        self.seen.add(identity)
        if len(self.seen) > 1024:
            self.seen = {identity}
        if sender in self.users and not self.users[sender].done():
            await self.reply(ctx, '青木操作正在处理，请等待结果；不要重复提交。')
            return True
        start = asyncio.Event()
        task = asyncio.create_task(self._run(ctx, text, sender, conversation, start))
        self.tasks[ctx.query_id] = task
        self.users[sender] = task
        try:
            await self.reply(ctx, '已安全接收青木操作，请等待结果。')
        except Exception:
            task.cancel()
            return True
        start.set()
        return True

    async def _run(self, ctx, text, sender, conversation, start):
        try:
            await start.wait()
            try:
                parts = shlex.split(text) if len(text) <= 4096 else []
            except ValueError:
                parts = []
            token = parts[2] if len(parts) == 3 and parts[1] == 'confirm' else None
            result = await self.execute(text, sender, conversation, ctx.query_id, confirmation_token=token)
            await self.reply(ctx, result['reply_text'])
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.reply(ctx, '青木操作结果未确认，请查询 qingmu status；不要重复提交。')
        finally:
            if self.users.get(sender) is asyncio.current_task():
                self.users.pop(sender, None)
            if len(self.tasks) > 512:
                self.tasks = {key: value for key, value in self.tasks.items() if not value.done()}

    @staticmethod
    async def reply(ctx, text):
        await ctx.reply(MessageChain([Plain(text=text[:1200])]))

    async def execute(self, command, sender, conversation, query_id, *, confirmation_token=None):
        if not self._identity(sender, conversation):
            return reply_result('青木仅允许身份明确的本人私聊操作。', success=False, error_code='private_identity_required')
        try:
            async with self._lock(sender):
                state = await self._load(sender)
                if confirmation_token:
                    return await self._confirm(state, sender, conversation, query_id, confirmation_token)
                spec = inspect_cli_command(command)
                if spec.error:
                    return reply_result('青木命令格式无效，请执行 help qingmu。', success=False)
                if spec.is_help:
                    return reply_result('\n'.join(help_payload()['commands']), help=help_payload())
                if spec.resolved_tool in {'qingmu_status', 'qingmu_platforms'}:
                    try:
                        settings = settings_from_env()
                    except QingmuError:
                        settings = None
                    platforms = [{'platform': name, 'name': label, 'bound': name in state['accounts'],
                        'configured': bool(settings and self._configured(settings, name))} for name, label in PLATFORMS.items()]
                    text = '\n'.join(f"{p['name']} ({p['platform']})：{'已绑定' if p['bound'] else '未绑定'}，{'服务已配置' if p['configured'] else '服务未配置'}" for p in platforms)
                    return reply_result(text + '\n绑定保存不等于上游登录验证成功。', platforms=platforms,
                        orders_enabled=bool(settings and settings.orders_enabled))
                if spec.resolved_tool in {'qingmu_account_set', 'qingmu_account_clear'}:
                    pending = create_pending_operation(storage_key=sender, canonical_command=command, query_id=query_id)
                    pending.update(conversation=conversation, kind='binding', tool=spec.resolved_tool, params=spec.params)
                    state['pending'] = pending
                    await self._save(sender, state)
                    verb = '保存绑定' if spec.resolved_tool.endswith('_set') else '删除绑定（保留订单回执）'
                    return reply_result(f"将为 {PLATFORMS[spec.params['platform']]} {verb}。账号密码加密保存；后续查课/下单会发送给青木供应商，不发送给模型。保存不代表登录验证完成。请在下一条私聊回复“确认”（五分钟内有效）。", success=False, confirmation_required=True)
                return await self._operation(spec, state, sender, conversation, query_id)
        except QingmuError as exc:
            return reply_result('青木配置、接口或状态暂不可用；本次结果未确认。', success=False, error_code=exc.code)
        except Exception:
            return reply_result('青木状态读取或保存失败，本次结果未确认。', success=False, error_code='storage_error')

    async def _confirm(self, state, sender, conversation, query_id, token):
        pending = state.get('pending')
        if not isinstance(pending, dict) or pending.get('conversation') != conversation:
            return reply_result('当前私聊没有有效的青木待确认操作。', success=False)
        check = validate_pending_operation(pending, token=token, storage_key=sender,
            canonical_command=pending.get('command', ''), query_id=query_id)
        if not check.ok:
            return reply_result(check.message, success=False)
        if pending['tool'] not in {'qingmu_account_set', 'qingmu_account_clear'}:
            return await self._commit_order(state, sender, pending)
        params = pending['params']
        platform = params['platform']
        if pending['tool'] == 'qingmu_account_set':
            state['accounts'][platform] = {**params, 'revision': secrets.token_hex(16)}
        else:
            state['accounts'].pop(platform, None)
        state['courses'].pop(platform, None)
        state['pending'] = None
        await self._save(sender, state)
        return reply_result('青木平台绑定已保存。请执行 qingmu courses --platform ' + platform + ' 验证查课；尚未验证上游账号。')

    @staticmethod
    def _configured(settings, platform):
        try:
            settings.service(platform)
            return True
        except QingmuError:
            return False

    @staticmethod
    async def _network(function, *args):
        # A timeout cannot prove that the supplier did not accept an order.
        # The durable fence is retained even if the worker thread completes later.
        return await asyncio.wait_for(asyncio.to_thread(function, *args), timeout=25)

    @staticmethod
    def _provider_identity(settings, platform):
        return hashlib.sha256(json.dumps([settings.url, settings.uid, settings.service(platform)['id']]).encode()).hexdigest()

    @staticmethod
    def _public_order(local_id, order):
        return {'id': local_id, 'platform': order['platform'], 'course': order['course']['name'],
                'outcome': order['outcome'], 'retry_outcome': order.get('retry_outcome', '')}

    async def _operation(self, spec, state, sender, conversation, query_id):
        params, tool = spec.params, spec.resolved_tool
        if tool == 'qingmu_orders':
            orders = [self._public_order(k, v) for k, v in state['orders'].items()
                      if not params.get('platform') or v['platform'] == params['platform']]
            return reply_result('本地订单回执：' + ('无记录。' if not orders else '\n' + '\n'.join(
                f"{v['id']}：{v['course']}，{v['outcome']}" for v in orders[-20:])), orders=orders[-20:], total=len(orders))
        settings = settings_from_env()
        client = QingmuClient(settings)
        if tool == 'qingmu_courses':
            platform = params['platform']
            account = state['accounts'].get(platform)
            if not account:
                return reply_result('请先私聊绑定该平台账号。', success=False)
            settings.service(platform)
            state['courses'].pop(platform, None)
            await self._save(sender, state)
            values = await self._network(client.courses, platform, account)
            state['courses'][platform] = {'values': values, 'revision': account['revision'],
                'provider': settings.fingerprint, 'queried_at': time.time()}
            await self._save(sender, state)
            courses = [{'no': i, 'name': v['name']} for i, v in enumerate(values, 1)]
            return reply_result('查课完成，共 ' + str(len(courses)) + ' 门。\n' + '\n'.join(
                f"{v['no']}：{v['name']}" for v in courses[:20]) + '\n下单使用上面序号，五分钟内有效。',
                courses=courses, total=len(courses))
        if tool == 'qingmu_order_add':
            platform = params['platform']
            settings.require_writes(platform)
            account = state['accounts'].get(platform)
            cache = state['courses'].get(platform)
            if (not account or not cache or cache['revision'] != account['revision']
                    or cache['provider'] != settings.fingerprint or time.time() - cache['queried_at'] > 300):
                return reply_result('请先重新查课；账号、配置已变更或查课结果已过期。', success=False)
            ordinal = int(params['course'])
            if ordinal > len(cache['values']):
                return reply_result('课程序号不在最近查课结果中。', success=False)
            course = cache['values'][ordinal - 1]
            fence = hashlib.sha256(json.dumps([settings.url, settings.service(platform)['id'],
                account['account'], course['kcid']]).encode()).hexdigest()
            if any(v['fence'] == fence and v['outcome'] != 'rejected' for v in state['orders'].values()):
                return reply_result('该账号课程已有订单或提交结果未知，已阻止重复下单。请查询 qingmu orders。', success=False)
            job = {'platform': platform, 'course': course, 'fence': fence,
                'revision': account['revision'], 'provider': settings.fingerprint,
                'provider_identity': self._provider_identity(settings, platform)}
        elif tool in {'qingmu_order_status', 'qingmu_order_retry'}:
            order = state['orders'].get(params['id'])
            if not order:
                return reply_result('本 QQ 没有该本地订单记录，不能查询其他用户订单。', success=False)
            platform = order['platform']
            if order['provider_identity'] != self._provider_identity(settings, platform):
                return reply_result('供应商或服务已变更，无法使用当前配置处理旧订单。', success=False)
            if order['outcome'] != 'accepted' or not order.get('supplier_id'):
                return reply_result('订单提交结果未知或未受理，请维护者先核对上游记录；不能重新下单。', success=False)
            if tool == 'qingmu_order_status':
                progress = await self._network(client.progress, order['supplier_id'])
                order['progress'] = progress
                await self._save(sender, state)
                return reply_result(f"订单 {params['id']} 上游状态：{progress.get('status', '未知')}，进度 {progress.get('process', '未知')}。上游受理不等于课程完成。",
                    local_order_id=params['id'], progress=progress)
            settings.require_writes(platform, retry=True)
            if order.get('retry_outcome') in {'submitting', 'unknown', 'accepted'}:
                return reply_result('该订单已申请补刷或补刷结果未知，禁止重复提交。', success=False)
            job = {'platform': platform, 'local_id': params['id'], 'course': order['course'],
                   'provider': settings.fingerprint}
        else:
            return reply_result('未知青木操作，请执行 help qingmu。', success=False)
        pending = create_pending_operation(storage_key=sender, canonical_command=spec.raw, query_id=query_id)
        pending.update(conversation=conversation, kind='order', tool=tool, params=job)
        state['pending'] = pending
        await self._save(sender, state)
        billing = client.safe_text(settings.service(platform)['billing'])
        service_name = client.safe_text(settings.service(platform)['name'])
        label = '付费下单' if tool == 'qingmu_order_add' else '申请补刷'
        school = '\n学校：' + client.safe_text(account['school'], account) if tool == 'qingmu_order_add' else ''
        return reply_result(f"将为 {PLATFORMS[platform]} 的课程“{job['course']['name']}”{label}。{school}\n服务：{service_name}\n计费说明：{billing}\n请核对后在下一条私聊回复“确认”（五分钟内有效）。",
            success=False, confirmation_required=True)

    async def _commit_order(self, state, sender, pending):
        job = pending['params']
        settings = settings_from_env()
        platform = job['platform']
        retry = pending['tool'] == 'qingmu_order_retry'
        settings.require_writes(platform, retry=retry)
        if settings.fingerprint != job['provider']:
            return reply_result('服务配置已变更，请重新发起预览。', success=False)
        account = state['accounts'].get(platform)
        if not retry and (not account or account['revision'] != job['revision']):
            return reply_result('账号已变更，请重新查课和预览。', success=False)
        if retry:
            local_id = job['local_id']
            order = state['orders'][local_id]
            if order.get('retry_outcome') in {'submitting', 'unknown', 'accepted'}:
                return reply_result('补刷已提交或结果未知，不可重复。', success=False)
        else:
            if any(v['fence'] == job['fence'] and v['outcome'] != 'rejected' for v in state['orders'].values()):
                return reply_result('已阻止重复下单，请查询订单。', success=False)
            if len(state['orders']) >= 500:
                return reply_result('本 QQ 订单记录已达上限，请维护者处理；不会自动删除未知订单。', success=False)
            local_id = secrets.token_hex(8)
            order = {k: job[k] for k in ('platform', 'course', 'fence', 'provider_identity')}
            order.update(outcome='submitting', created_at=time.time())
            state['orders'][local_id] = order
        outcome_key = 'retry_outcome' if retry else 'outcome'
        order[outcome_key] = 'submitting'
        state['pending'] = None
        # Durable fence and consumed confirmation are one encrypted Storage value.
        # No campus/global Storage transaction or runtime lock is held during HTTP.
        await self._save(sender, state)
        client = QingmuClient(settings)
        try:
            if retry:
                await self._network(client.retry, platform, order['supplier_id'])
            else:
                supplier_id = await self._network(client.place_order, platform, account, job['course'])
                order['supplier_id'] = supplier_id
            order[outcome_key] = 'accepted'
        except QingmuError as exc:
            order[outcome_key] = 'rejected' if exc.code == 'supplier_rejected' else 'unknown'
        except Exception:
            order[outcome_key] = 'unknown'
        try:
            await self._save(sender, state)
        except Exception:
            return reply_result('上游操作结果无法持久保存，请维护者核对；禁止重试。', success=False,
                local_order_id=local_id, outcome='unknown', external_committed=order[outcome_key] == 'accepted', storage_persisted=False)
        outcome = order[outcome_key]
        message = ('上游已受理补刷。' if retry else '上游已受理订单；这不代表课程已完成。') if outcome == 'accepted' else (
            '上游拒绝本次请求。' if outcome == 'rejected' else '请求结果未知，请维护者核对上游；禁止重试。')
        return reply_result(f"本地订单号 {local_id}：{message}", success=outcome == 'accepted',
            local_order_id=local_id, outcome=outcome, external_committed=outcome == 'accepted', storage_persisted=True)
