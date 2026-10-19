"""Public navigation copy shared by QQ menus, panels and chat guidance."""
from __future__ import annotations



TASKS = (
    ('我的校园', (('我的状态', '查看绑定与服务状态'), ('今天课表', '查看今天的课程安排'), ('明天课表', '提前查看明天课程'), ('下一节课', '查看接下来上的课'), ('同步课表', '从教务更新课表'))),
    ('找自习室', (('找自习室', '选择空教室或图书馆'), ('空教室查询', '按校区楼栋时段查询'), ('图书馆座位', '查看区域与可用座位'))),
    ('账号与帮助', (('绑定账号', '校园账号与网站绑定'), ('绑定Kit', '关联 HENU KIT 网站'), ('使用帮助', '查看常用说法与流程'), ('课表推送', '查看课表提醒支持情况'))),
)


def navigation_config() -> dict:
    menu = {'items': [
        {'name': name, 'type': 'menu', 'sub_menu_items': [
            {'name': label, 'type': 'send_message', 'send_message': label}
            for label, _ in tasks
        ]} for name, tasks in TASKS
    ]}
    panels = {}
    for scope in ('c2c', 'group'):
        tasks = [task for _, group in TASKS for task in group]
        if scope == 'group':
            tasks = [task for task in tasks if task[0] in {'找自习室', '空教室查询', '图书馆座位', '使用帮助', '绑定账号'}]
        panels[scope] = {'scope': scope, 'target_type': 'all', 'panel': {
            'remark': 'henu-navigation-v1-' + scope,
            'items': [{'type': 'command', 'name': label, 'desc': desc, 'only_admin': False} for label, desc in tasks],
        }}
    return {'menu': menu, 'panels': panels}


GUIDES = {
    '绑定账号': ('绑定账号', '校园账号：用于查询个人课表和校园服务。请在私聊发送 help account，按提示直接提交绑定命令。\nHENU KIT 网站账号：私聊发送“绑定Kit”，打开授权链接；核对目标账号后按原提示引用确认。\n两种绑定独立，网站绑定不会自动绑定校园账号。不要在群聊发送学号、密码或授权链接。'),
    '使用帮助': ('从这里开始', '1. 首次使用：私聊发送“绑定账号”。\n2. 看个人状态：发送“我的状态”。\n3. 看课程：发送“今天课表”“明天课表”或“下一节课”；数据有变化时发送“同步课表”。\n4. 找地方自习：发送“找自习室”。\n5. 关联网站：私聊发送“绑定Kit”。\n个人信息仅私聊展示；预约需核对地点、日期、完整时段，再由本人确认。'),
    '找自习室': ('找自习室', '空教室：发送“查金明校区十号楼的空教室”，按提示补充教学周，可查指定节次或全天；结果反映课程占用，不能保证现场空闲。\n图书馆：发送“图书馆座位”，先选区域，再给出日期和开始、结束时间。\n预约会先展示完整方案，由你下一条回复“确认”后执行。'),
    '空教室查询': ('空教室查询', '请告诉我校区和教学周；可补充教学楼、星期与节次缩小范围。不指定节次可查询全天。\n例如：查金明校区十号楼第8周星期二第2大节的空教室。\n这里只查询课程占用，不保证现场无人使用。'),
    '课表推送': ('课表推送', '目前支持手动查询与同步课表，尚未提供定时课表推送。\n可发送“今天课表”“明天课表”或“下一节课”。'),
}


async def handle_navigation(plugin, ctx, *, listener=None, defer_binding=False) -> bool:
    from henu_plugin.qq_cards import reply_card
    from langbot_plugin.api.entities.builtin.platform.message import At, Plain, Source

    chain_object = getattr(ctx.event, 'message_chain', None)
    if chain_object is None:
        chain_object = getattr(getattr(ctx.event, 'query', None), 'message_chain', None)
    chain = getattr(chain_object, 'root', None)
    # Text-only stages must retain the original chain; never hide attachments.
    if not chain or any(not isinstance(item, (Plain, At, Source)) for item in chain):
        return False
    text = ''.join(item.text for item in chain if isinstance(item, Plain)).strip()
    reminders = getattr(plugin, 'schedule_reminders', None)
    if reminders is not None:
        if getattr(ctx, 'is_prevent_postorder', False):
            return True
        if await reminders.handle(ctx, text):
            return True
    # Existing production credential intake must own binding intent and arm its guard.
    if text == '绑定账号' and (defer_binding or callable(getattr(listener, '_arm_credential_intake', None))):
        return False
    if text == '课表推送' and getattr(plugin, 'schedule_reminders', None) is not None:
        return False
    guide = GUIDES.get(text)
    if guide is None:
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    if text == '绑定账号':
        from henu_plugin.qq_cards import card_chain
        await ctx.reply(card_chain(account_binding_card()[0]))
        return True
    # Prevent duplicated handling at the normal-message stage of the same query.
    await reply_card(ctx, *guide, private=False, plugin=plugin)
    return True


def account_binding_card() -> list[str]:
    """Render a static input template; no user-supplied values can enter it."""
    from henu_plugin.qq_cards import Card, escape
    return Card('绑定校园账号', '请在私聊中填写', (
        escape('校园账号用于个人课表和校园服务，与 HENU KIT 网站账号分别绑定。'),
        "account set --student-id 学号 --password '密码'",
        escape('请把上面的学号和密码换成自己的内容，在同一条私聊中发送；不要保留占位文字，也不要单独发送密码。'),
        escape('不想绑定时发送“取消”；关联网站账号请先取消，再发送“绑定Kit”。'),
    )).pages()


def menu_command(text: str, now=None) -> str:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    if text in {'今天课表', '明天课表'}:
        day = now.date() + timedelta(days=int(text == '明天课表'))
        return 'schedule day --date ' + day.isoformat()
    return {'我的状态': 'account status', '下一节课': 'schedule now', '本周课表': 'schedule week',
            '同步课表': 'schedule sync', '图书馆座位': 'library locations'}.get(text, '')


async def handle_menu_query(plugin, ctx) -> bool:
    """Run exact menu choices through the existing isolated CLI, without the LLM."""
    from langbot_plugin.api.entities.builtin.platform.message import At, Plain, Source
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.qq_cards import result_card, card_chain, PRIVATE_NOTICE, text_card
    chain_object = getattr(ctx.event, 'message_chain', None)
    if chain_object is None:
        chain_object = getattr(getattr(ctx.event, 'query', None), 'message_chain', None)
    chain = getattr(chain_object, 'root', None)
    if not chain or any(not isinstance(item, (Plain, At, Source)) for item in chain):
        return False
    text = ''.join(item.text for item in chain if isinstance(item, Plain)).strip()
    reminders = getattr(plugin, "schedule_reminders", None)
    if reminders is not None and await reminders.handle(ctx, text):
        return True
    command = menu_command(text)
    if not command:
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    kind = str(getattr(getattr(ctx.event, 'launcher_type', ''), 'value', getattr(ctx.event, 'launcher_type', '')))
    if kind != 'person':
        await ctx.reply(card_chain(text_card('请转到私聊', PRIVATE_NOTICE)[0]))
        return True
    sender = str(getattr(ctx.event, 'sender_id', '') or '').strip()
    launcher = str(getattr(ctx.event, 'launcher_id', '') or '').strip()
    if not sender or not launcher:
        await ctx.reply(card_chain(text_card('无法查询', '无法确认当前私聊身份，请重新进入机器人私聊。')[0]))
        return True
    try:
        session = Session(launcher_type=LauncherTypes.PERSON, launcher_id=launcher, sender_id=sender)
        tool = HenuCliSafe()
        tool.plugin = plugin
        result = await tool.call({'command': command}, session, ctx.query_id)
        variables = await ctx.get_query_vars()
        staged = variables.get('_henu_card') if isinstance(variables, dict) else None
        page = staged.get('page') if isinstance(staged, dict) else None
        page = page or result_card(result).pages()[0]
        await ctx.set_query_var('_henu_card', None)
    except Exception:
        page = text_card('查询未完成', '查询暂时无法完成，请稍后重试；本次没有提交预约或修改账号。')[0]
    await ctx.reply(card_chain(page))
    return True
