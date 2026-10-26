"""Public navigation copy shared by QQ menus, panels and chat guidance."""
from __future__ import annotations



TASKS = (
    ('我的校园', (('我的状态', '查看绑定与服务状态'), ('今天课表', '查看今天的课程安排'), ('明天课表', '提前查看明天课程'), ('下一节课', '查看接下来上的课'), ('同步课表', '从教务更新课表'))),
    ('找自习室', (('找自习室', '查询当前时段空教室'), ('空教室查询', '按校区楼栋时段查询'), ('图书馆座位', '查看各区域剩余座位'), ('自动预约帮助', '设置终身会员定时预约'))),
    ('账号与帮助', (('绑定账号', '校园账号与网站绑定'), ('绑定Kit', '关联 HENU KIT 网站'), ('使用帮助', '查看常用说法与流程'), ('课表推送', '查看课表提醒支持情况'), ('河宝社区', '查询状态和个人记录'))),
)

MENU_LABELS = {'我的校园': '🎓我的校园', '找自习室': '📍找自习室', '账号与帮助': '⚙️账号帮助'}
TASK_ICONS = {
    '我的状态': '👤', '今天课表': '📅', '明天课表': '🗓️', '下一节课': '⏰',
    '同步课表': '🔄', '找自习室': '📍', '空教室查询': '🏫', '图书馆座位': '📚',
    '自动预约帮助': '⏱️', '河宝社区': '🧾', '绑定账号': '🔐', '绑定Kit': '🔗', '使用帮助': '❓', '课表推送': '🔔',
}


def navigation_config() -> dict:
    menu = {'items': [
        {'name': MENU_LABELS[name], 'type': 'menu', 'sub_menu_items': [
            {'name': TASK_ICONS[label] + label, 'type': 'send_message', 'send_message': label}
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
            # Command names become input text; put panel icons in descriptions.
            'items': [{'type': 'command', 'name': label, 'desc': TASK_ICONS[label] + desc, 'only_admin': False} for label, desc in tasks],
        }}
    return {'menu': menu, 'panels': panels}


GUIDES = {
    '河宝社区': ('河宝社区', '使用校园账号查询，请先绑定校园账号。\n发送“河宝状态”查看任务统计；还可发送“河宝请假记录”“河宝签到任务”“河宝查寝任务”“河宝活动”“河宝信息收集”。\n个人记录仅私聊展示；当前仅提供查询，不会办理请假、签到或查寝。'),
    '绑定账号': ('绑定账号', '校园账号：用于查询个人课表和校园服务。请在私聊填写机器人提供的绑定模板。\nHENU KIT 网站账号：私聊发送“绑定Kit”，打开授权链接；核对目标账号后按原提示引用确认。\n两种绑定独立，网站绑定不会自动绑定校园账号。不要在群聊发送学号、密码或授权链接。'),
    '使用帮助': ('从这里开始', '1. 首次使用：私聊发送“绑定账号”。\n2. 看个人状态：发送“我的状态”。\n3. 看课程：发送“今天课表”“明天课表”或“下一节课”；数据有变化时发送“同步课表”。\n4. 找地方自习：发送“找自习室”。\n5. 关联网站：私聊发送“绑定Kit”。\n6. 河宝查询：私聊发送“河宝社区”。\n7. 定时预约：私聊发送“自动预约帮助”。\n个人信息仅私聊展示；预约需核对地点、日期、完整时段，再由本人确认。'),
    '课表推送': ('课表推送', '提醒服务暂未就绪，请稍后重试；已保存的提醒设置不会因此更改。'),
}


async def handle_navigation(plugin, ctx, *, listener=None, defer_binding=False) -> bool:
    from henu_plugin.qq_buttons import handle
    if await handle(plugin, ctx):
        return True
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
    from henu_plugin.library_seat_map import handle_library_text
    if await handle_library_text(plugin, ctx, text):
        return True
    from henu_plugin.library_availability import handle_library_availability
    if await handle_library_availability(plugin, ctx, text):
        return True
    from henu_plugin.self_study import handle_self_study
    if await handle_self_study(plugin, ctx, text):
        return True
    reminders = getattr(plugin, 'schedule_reminders', None)
    if reminders is not None:
        if getattr(ctx, 'is_prevent_postorder', False):
            return True
        if await reminders.handle(ctx, text):
            return True
    # Existing production credential intake must own binding intent and arm its guard.
    if text == '绑定账号' and (defer_binding or callable(getattr(listener, '_arm_credential_intake', None))):
        return False
    automation = getattr(plugin, 'library_automation', None)
    if automation is not None:
        kind = getattr(getattr(ctx.event, 'launcher_type', ''), 'value', getattr(ctx.event, 'launcher_type', ''))
        if await automation.handle(ctx, is_group=kind != 'person'):
            return True
    if text == '课表推送' and getattr(plugin, 'schedule_reminders', None) is not None:
        return False
    if classroom_command(text):
        return await handle_menu_query(plugin, ctx)
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
    return Card('绑定校园账号', '', (
        escape('查课表、图书馆等服务用校园账号。它和 HENU KIT 网站账号分别绑定。'),
        escape('把下面的学号、密码换成你的，整条在私聊发给我：'),
        "account set --student-id 学号 --password '密码'",
        escape('不要保留“学号”“密码”这两个占位词，也别单独发密码。'),
        escape('不绑定了就发“取消”。绑定网站请先取消，再发“绑定Kit”。'),
    )).pages()


def classroom_command(text: str) -> str:
    """Translate the bounded classroom guide grammar into an existing read-only query."""
    import re
    match = re.fullmatch(r'查(金明|明伦|龙子湖)校区([\u4e00-\u9fffA-Za-z0-9]{1,30}楼)(?:第([1-9]\d?)周)?(?:星期([一二三四五六日天]))?(?:第([1-5])大节)?的空教室', text)
    if not match:
        return ''
    campus, building, week, day, period = match.groups()
    if week and int(week) > 60:
        return ''
    command = f'empty classroom query --campus-text {campus} --building-text {building}'
    if week:
        command += f' --week {week}'
    if day:
        weekday = '一二三四五六日'.find('日' if day == '天' else day) + 1
        command += f' --day-of-week {weekday}'
    if period:
        command += f' --period {period}'
    else:
        command += ' --view day_matrix'
    return command


def menu_command(text: str, now=None) -> str:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    if text in {'今天课表', '明天课表'}:
        day = now.date() + timedelta(days=int(text == '明天课表'))
        return 'schedule day --date ' + day.isoformat()
    if classroom_command(text):
        return classroom_command(text)
    return {'河宝状态': 'yunfz signin statistics', '河宝请假记录': 'yunfz leave list',
            '河宝签到任务': 'yunfz signin list', '河宝查寝任务': 'yunfz checksleep list',
            '河宝活动': 'yunfz activity list', '河宝信息收集': 'yunfz collection list', '我的状态': 'account status', '下一节课': 'schedule now', '本周课表': 'schedule week',
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
    from henu_plugin.qq_buttons import handle
    if await handle(plugin, ctx):
        return True
    from henu_plugin.library_seat_map import handle_library_text
    if await handle_library_text(plugin, ctx, text):
        return True
    from henu_plugin.library_availability import handle_library_availability
    if await handle_library_availability(plugin, ctx, text):
        return True
    from henu_plugin.self_study import handle_self_study
    if await handle_self_study(plugin, ctx, text):
        return True
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
    from henu_plugin.cli import inspect_cli_command
    from henu_plugin.qq_cards import public_spec
    public_group = kind == 'group' and public_spec(inspect_cli_command(command))
    if kind != 'person' and not public_group:
        await ctx.reply(card_chain(text_card('请转到私聊', PRIVATE_NOTICE)[0]))
        return True
    sender = str(getattr(ctx.event, 'sender_id', '') or '').strip()
    launcher = str(getattr(ctx.event, 'launcher_id', '') or '').strip()
    if not sender or not launcher:
        await ctx.reply(card_chain(text_card('无法查询', '无法确认当前私聊身份，请重新进入机器人私聊。')[0]))
        return True
    if command.startswith(('library ', 'empty classroom ')):
        await ctx.reply(card_chain(text_card('正在查询', '正在读取校园服务数据，请稍等；本次仅查询，不会预约或签到。')[0]))
    try:
        session = Session(launcher_type=LauncherTypes.GROUP if public_group else LauncherTypes.PERSON, launcher_id=launcher, sender_id=sender)
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
