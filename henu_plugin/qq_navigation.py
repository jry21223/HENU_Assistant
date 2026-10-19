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
    '找自习室': ('找自习室', '空教室：发送“查金明校区十号楼的空教室”，补充教学周、星期和节次；结果反映课程占用，不能保证现场空闲。\n图书馆：发送“图书馆座位”，先选区域，再给出日期和开始、结束时间。\n预约会先展示待确认信息，由你下一条确认后执行。'),
    '空教室查询': ('空教室查询', '请告诉我校区和教学周；可补充教学楼、星期与节次缩小范围。不指定节次可查询全天。\n例如：查金明校区十号楼第8周星期二第2大节的空教室。\n这里只查询课程占用，不保证现场无人使用。'),
    '课表推送': ('课表推送', '目前支持手动查询与同步课表，尚未提供定时课表推送。\n可发送“今天课表”“明天课表”或“下一节课”。'),
}


async def handle_navigation(plugin, ctx, *, listener=None) -> bool:
    from henu_plugin.qq_cards import reply_card
    from langbot_plugin.api.entities.builtin.platform.message import At, Plain, Source

    chain = getattr(getattr(ctx.event, 'message_chain', None), 'root', [])
    if not chain or any(not isinstance(item, (Plain, At, Source)) for item in chain):
        return False
    text = ''.join(item.text for item in chain if isinstance(item, Plain)).strip()
    # Existing production credential intake must own binding intent and arm its guard.
    if text == '绑定账号' and callable(getattr(listener, '_arm_credential_intake', None)):
        return False
    guide = GUIDES.get(text)
    if guide is None:
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    # Prevent duplicated handling at the normal-message stage of the same query.
    await reply_card(ctx, *guide, private=False, plugin=plugin)
    return True
