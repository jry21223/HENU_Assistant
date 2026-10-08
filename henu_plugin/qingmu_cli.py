"""Space CLI and public help for the Qingmu platform integration."""
from __future__ import annotations

import shlex

from henu_plugin.cli import CliCommandSpec

PLATFORMS = {'ucampus': 'U校园', 'ucampus_ai': 'U校园 AI 版', 'chaoxing': '学习通'}
ALIASES = {'u校园': 'ucampus', '优校园': 'ucampus', 'u校园ai版': 'ucampus_ai',
           'u校园ai': 'ucampus_ai', '学习通': 'chaoxing'}


def is_qingmu_command(text):
    """Use the CLI's quoting rules, even when a later credential argument is malformed."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        head = next(lexer, '').lower()
    except ValueError:
        head = text.split(maxsplit=1)[0].replace("'", '').replace('"', '').replace('\\', '').lower() if text else ''
    return head in {'qingmu', '青木'}


def help_payload():
    return {'topic': 'qingmu', 'summary': 'U校园、U校园 AI 版、学习通账号与课程订单。',
            'commands': ['qingmu status', 'qingmu platforms',
                "qingmu account set --platform <平台> --account <账号> --password '<密码>' --school '<学校>'",
                'qingmu account clear --platform <平台>', 'qingmu courses --platform <平台>',
                'qingmu order add --platform <平台> --course <查课序号>', 'qingmu orders',
                'qingmu order status --id <本地订单号>', 'qingmu order retry --id <本地订单号>'],
            'examples': ['qingmu courses --platform ucampus', 'qingmu courses --platform chaoxing'],
            'tips': ['平台：ucampus / ucampus_ai / chaoxing；青木为命令别名。',
                     '绑定必须本人私聊直发；密码不会交给模型。绑定、下单、补刷均需下一条私聊确认。',
                     '查课序号仅对本人的最近查询有效；下单为付费操作，使用部署方核实的计费说明。',
                     'orders 查看本地回执；order status 查询上游进度。结果未知时不可重复提交。']}


def parse_command(raw, argv):
    def result(tool=None, params=None, error='', help=False):
        return CliCommandSpec(raw, argv, tool, params or {}, tool or 'help qingmu', False,
                              is_help=help, help_topic='qingmu', error=error)
    if len(argv) == 1:
        return result(help=True)
    route, offset = argv[1], 2
    if route in {'account', 'order'}:
        if len(argv) < 3:
            return result(error='请先执行 help qingmu 查看用法。')
        route += '_' + argv[2]
        offset = 3
    required = {'status': (), 'platforms': (), 'orders': (),
                'account_set': ('platform', 'account', 'password', 'school'),
                'account_clear': ('platform',), 'courses': ('platform',),
                'order_add': ('platform', 'course'), 'order_status': ('id',), 'order_retry': ('id',)}
    if route not in required:
        return result(error='未知青木命令；改密尚未支持，请执行 help qingmu。')
    tokens = argv[offset:]
    options = {}
    index = 0
    while index < len(tokens):
        name, sep, value = tokens[index].partition('=')
        if not name.startswith('--'):
            return result(error='参数格式无效，请执行 help qingmu。')
        key = name[2:].replace('-', '_')
        if not sep:
            index += 1
            if index >= len(tokens) or tokens[index].startswith('--'):
                return result(error='参数缺少值，请执行 help qingmu。')
            value = tokens[index]
        if key in options or key not in required[route] + (('platform',) if route == 'orders' else ()):
            return result(error='未知或重复参数，请执行 help qingmu。')
        if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
            return result(error='参数为空、过长或包含控制字符。')
        options[key] = value
        index += 1
    if any(k not in options for k in required[route]):
        return result(error='缺少必填参数，请执行 help qingmu。')
    if 'platform' in options:
        value = ''.join(options['platform'].lower().split())
        options['platform'] = ALIASES.get(value, value)
        if options['platform'] not in PLATFORMS:
            return result(error='平台应为 ucampus、ucampus_ai 或 chaoxing。')
    if 'course' in options and (not options['course'].isdigit() or not 1 <= int(options['course']) <= 200):
        return result(error='课程序号应为最近查课返回的 1–200。')
    return result('qingmu_' + route, options)
