"""Rain Classroom fact cards. Rendering never mutates a result or advances a task."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any
from zoneinfo import ZoneInfo

from henu_plugin.qq_cards import Card, escape


UNKNOWN = '未提供'
CHINA = ZoneInfo('Asia/Shanghai')


def _china_moment(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            moment = datetime.fromtimestamp(value / 1000 if value > 1e12 else value, CHINA)
        elif isinstance(value, str) and value.strip():
            moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
            moment = moment.replace(tzinfo=CHINA) if moment.tzinfo is None else moment.astimezone(CHINA)
        else:
            return None
        return moment
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def china_time(value: Any) -> str:
    moment = _china_moment(value)
    return moment.strftime('%Y-%m-%d %H:%M') if moment is not None else UNKNOWN


def duration(value: Any) -> str:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return UNKNOWN
    if value == 0:
        return '不限时'
    minutes, seconds = divmod(int(value), 60)
    return (f'{minutes} 分钟' if minutes else '') + (f' {seconds} 秒' if seconds else '')


def _value(data: dict, *keys: str, default: Any = UNKNOWN) -> Any:
    return next((data[key] for key in keys if data.get(key) is not None and data[key] != ''), default)


def assessment_state(row: dict) -> str:
    state, kind = row.get('status'), row.get('assessment_type', 'quiz')
    labels = {0: '未开始', 1: '进行中', 2: '已交卷', 3: '已截止'} if kind == 'quiz' else {
        0: '未开始', 1: '进行中', 2: '已截止', 3: '已交卷'}
    if state in (0, 1):
        deadline = _china_moment(_value(row, 'deadline', 'deadline_china', default=None))
        if row.get('expired') is True or deadline is not None and datetime.now(CHINA) >= deadline:
            return '已截止'
    return labels.get(state if type(state) is int else -1, str(_value(row, 'status_label', default='状态未核验')))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _activity(result: dict) -> dict | None:
    row = result.get('activity') or result.get('quiz')
    if not isinstance(row, dict):
        return None
    classroom = _dict(row.get('classroom'))
    return {**classroom, **row, 'assessment_type': row.get('assessment_type', 'quiz')}


def activity_lines(row: dict, *, number: int | None = None) -> tuple[str, ...]:
    kind = row.get('assessment_type')
    type_text = {'quiz': '测验 · quiz', 'exam': '考试 · exam'}.get(str(kind), '类型未核验')
    heading = (f'{number}. ' if number is not None else '') + str(_value(row, 'title'))
    count = _value(row, 'question_count', 'problem_count', default=None)
    count_text = f'{count} 题' if type(count) is int and count >= 0 else UNKNOWN
    return (
        '**' + escape(heading, 300) + '**',
        '课程：' + escape(_value(row, 'course_name')) + '　班级：' + escape(_value(row, 'classroom_label', 'name')),
        escape(type_text) + '　' + escape(assessment_state(row)),
        '截止：' + china_time(_value(row, 'deadline_china', 'deadline', default=None)),
        '题数：' + count_text + '　限时：' + duration(_value(row, 'limit_seconds', 'limit', default=None)),
    )


def _backend_lines(result: dict) -> list[str]:
    backend = _dict(result.get('backend'))
    labels = (
        ('target', '目标发现', {'found': '已发现', 'discovered': '已发现'}),
        ('model', '模型处理', {'processing': '处理中', 'started': '处理中', 'returned': '已返回结果'}),
        ('saved', '答案保存', {'verified': '已由官方缓存核验', 'partial': '尚未全部保存'}),
        ('submission', '正式交卷', {'submitted': '已由官方状态核验', 'not_submitted': '未交卷'}),
    )
    lines = []
    for key, label, known in labels:
        state = backend.get(key)
        if isinstance(state, dict):
            state = state.get('status') or state.get('state')
        row = _activity(result)
        if key == 'submission' and row and assessment_state(row) == '已交卷':
            state = 'submitted'
        if key == 'submission' and result.get('submission_state') == 'not_submitted':
            state = 'not_submitted'
        if key == 'saved' and result.get('submission_state') == 'not_submitted' and row and type(row.get('saved_count')) is int:
            count = row.get('question_count')
            state = 'verified' if type(count) is int and count > 0 and row['saved_count'] == count else 'partial'
        lines.append(label + '：' + known.get(str(state), '未核验'))
    return lines


def _configuration(result: dict, tool: str) -> Card:
    cfg = _dict(result.get('yuketang'))
    bridge = result.get('bridge')
    bridge_data = bridge if isinstance(bridge, dict) else {}
    exam = _dict(cfg.get('exam'))
    lesson = _dict(cfg.get('lesson'))
    def on_off(value: Any) -> str:
        return '开' if value is True else '关' if value is False else '未核验'
    lines = []
    mutation = tool not in {'yuketang_status', 'yuketang_config_show'}
    if mutation:
        lines.append('配置保存：已保存')
    if 'enabled' in cfg:
        lines.append('监听设置：' + ('已启用' if cfg['enabled'] is True else '未启用'))
    monitor = {'running': '已运行', 'stopped': '已停用'}.get(str(bridge_data.get('monitoring_state')), '未核验')
    lines.append('监听运行：' + monitor)
    accepted = bridge == 'synced' or bridge_data.get('config_hash_matches') is True
    lines.append('桥端配置：' + ('已接收' if accepted else '待同步' if bridge == 'pending' else '未核验'))
    if exam:
        lines.append('考试设置：后台答题 ' + on_off(exam.get('an')) + ' · 模型 ' + on_off(exam.get('llm')))
        whitelist = exam.get('classroomWhiteList')
        lines.append('考试班级：' + (escape('、'.join(map(str, whitelist)), 600) if isinstance(whitelist, list) and whitelist else '未设置'))
    if lesson:
        lines.append('课堂设置：自动进入 ' + on_off(lesson.get('autoEnter')) + ' · 自动答题 ' + on_off(lesson.get('an')))
    if tool == 'yuketang_status':
        reply = str(result.get('reply_text') or '')
        login_line = next((line for line in reply.splitlines() if line.startswith('登录状态：')), '')
        lines.insert(0, '登录状态：' + ('已登录' if '已登录' in login_line else '未登录' if '未登录' in login_line else '未核验'))
    lines += ['目标发现／模型处理／答案保存／正式交卷：本次未核验。', '配置已保存或桥已接收，不代表已答完或已交卷。']
    title = '状态' if tool == 'yuketang_status' else '配置'
    return Card('雨课堂 · ' + title, '已保存' if mutation else '当前设置', tuple(lines), '发送“开始考试”选测评；发送“雨课堂帮助”查看说明。')


def result_card(result: dict, tool: str = 'henu_cli') -> Card | None:
    cli = _dict(result.get('cli'))
    rain_help = cli.get('mode') == 'help' and str(cli.get('topic', '')).startswith('yuketang')
    if not tool.startswith('yuketang') and not rain_help:
        return None
    if rain_help:
        return Card('雨课堂 · 帮助', '', (
            '1. 发送“雨课堂”，查看登录、状态及常用入口。',
            '2. 发送“开始考试”，按课程、标题、序号或按钮选择测评，无需寻找编号。',
            '3. 核对课程、班级、类型、截止时间、题数和限时，再进入开始预览。',
            '测验 quiz：本人独立确认后开始；考试 exam：按官方入口完成协议、验证和开始。',
            '默认仅保存答案；正式交卷需要另一次明确授权和确认。',
            '后台是否接管、是否保存，以目标事件和官方缓存为准；不会由聊天 Agent 手工作答替代。',
        ), '个人信息及测评操作仅在本人私聊中进行。')
    if tool == 'yuketang_home':
        return Card('雨课堂', '从这里开始', ('开始考试：查看当前测评，选中后核对限制。',
            '登录／退出：管理雨课堂登录。', '状态／配置：查看监听及班级范围。',
            '默认仅保存；交卷需要独立授权。'), '发送“开始考试”“雨课堂状态”或“雨课堂帮助”。')
    row = _activity(result)
    lines = list(activity_lines(row)) if row else []
    if result.get('confirmation_required'):
        if not row and tool.startswith('yuketang_quiz'):
            lines = list(activity_lines({}))
        action = str(cli.get('action') or tool)
        submit = 'submit' in action or '交卷' in action
        lines += ['请核对测评与限制，本次尚未执行。',
                  '正式交卷后答案不可修改。' if submit else '本次确认不代表同意交卷。']
        return Card('雨课堂 · ' + ('交卷确认' if submit else '操作确认'), '待本人确认', tuple(lines),
            '请在下一条私聊回复“确认”或使用确认按钮，5分钟内有效；取消请回复“取消”。')
    if result.get('success') is False:
        code = str(result.get('error_code') or '')
        unknown = result.get('external_attempted') is True or result.get('retry_safe') is False or any(w in code for w in ('unknown', 'unverified', 'timeout'))
        lines += ['操作结果尚未确认，请先只读查询状态，不要重复提交。' if unknown else escape(result.get('msg') or '查询未完成，请重新核对。', 700)]
        return Card('雨课堂 · ' + ('结果待核验' if unknown else '暂未完成'), '未核验' if unknown else '未完成', tuple(lines),
            '发送“开始考试”重新查询；需要登录时发送“登录雨课堂”。')
    if tool in {'yuketang_status', 'yuketang_config_show', 'yuketang_set_enabled', 'yuketang_lesson_set',
                'yuketang_exam_set', 'yuketang_list_update', 'yuketang_start_time', 'yuketang_domain_set'}:
        return _configuration(result, tool)
    rows = result.get('activities') if isinstance(result.get('activities'), list) else result.get('exams')
    if isinstance(rows, list):
        lines = []
        page, size = result.get('page', 1), result.get('page_size', len(rows) or 5)
        offset = (page - 1) * size if type(page) is int and type(size) is int else 0
        classroom = _dict(result.get('classroom'))
        for index, item in enumerate(rows, 1):
            if isinstance(item, dict):
                lines.extend((*activity_lines({**classroom, **item}, number=offset + index), ''))
        coverage = result.get('coverage')
        if isinstance(coverage, dict):
            total = _value(coverage, 'total', 'courses_total')
            scanned = _value(coverage, 'scanned', 'courses_scanned')
            lines.append('查询范围：' + ('已核对完整范围' if coverage.get('complete') is True else '部分结果，尚未完整核验') + f'（课程 {scanned}/{total}）。')
        else:
            lines.append('查询范围：当前课程；未核验是否覆盖全部活动。')
        if not rows:
            lines.insert(0, '当前查询范围内没有符合条件的测评。')
        return Card('雨课堂 · 测评列表', f'第 {page} 页', tuple(lines),
            f'发送“选择第{offset + 1}项”等本页序号、标题或课程；翻页用“测评下一页”“测评上一页”，无需输入编号。')
    if tool == 'yuketang_courses':
        courses = _list(result.get('courses'))
        lines = [escape(str(c.get('course_name') or UNKNOWN)) + ' · ' + escape(str(c.get('classroom_label') or UNKNOWN)) for c in courses if isinstance(c, dict)]
        lines.append('本次显示 ' + str(len(courses)) + ' 个课程班级；列表是否完整以查询回执为准。')
        return Card('雨课堂 · 课程班级', '', tuple(lines), '发送“开始考试”进入测评列表，或回复课程名称。')
    if row:
        grade = result.get('grade')
        if isinstance(grade, dict):
            if grade.get('visible') is not True:
                lines.append('成绩：尚未公布。')
            elif grade.get('grading_pending'):
                lines.append('成绩：等待批改。')
            else:
                lines.append('成绩：' + escape(grade.get('score')) + ' / ' + escape(grade.get('total_score')))
                lines.append('错题：' + str(len(grade.get('incorrect_numbers') or [])) + ' 题　漏答：' + str(len(grade.get('unfinished_numbers') or [])) + ' 题')
            return Card('雨课堂 · 成绩', assessment_state(row), tuple(lines), '已交卷测评只做只读查询，不会重新开始。')
        saved = row.get('saved_count')
        if type(saved) is int and saved >= 0 and row.get('status') == 1:
            lines.append('官方草稿：已保存 ' + str(saved) + ' / ' + str(row.get('question_count', UNKNOWN)) + ' 题。')
        lines.extend(_backend_lines(result))
        state = assessment_state(row)
        if state in {'已交卷', '已截止'}:
            next_step = '本测评仅可只读查看状态或已公布成绩，不会重新开始。'
        elif row.get('assessment_type') == 'exam':
            lines.append('考试须在雨课堂官方页面，由本人完成协议、必要验证及开始步骤。')
            next_step = '使用“进入官方页面”入口；后台保存与正式交卷分别核验。'
        else:
            next_step = '未开始时选择“开始预览”；已开始时先核对后台与官方草稿。'
        title = {'yuketang_quiz_start': '开始结果', 'yuketang_quiz_save': '答案保存',
                 'yuketang_quiz_submit': '提交结果'}.get(tool, '测评详情')
        receipt = state
        if tool == 'yuketang_quiz_save' and result.get('submission_state') == 'not_submitted':
            count, total = row.get('saved_count'), row.get('question_count')
            complete = type(count) is int and type(total) is int and total > 0 and count == total
            receipt = '已保存 · 未交卷' if complete else '尚未全部保存 · 未交卷'
        return Card('雨课堂 · ' + title, receipt, tuple(lines), next_step)
    if tool in {'yuketang_login', 'yuketang_logout', 'yuketang_account_set', 'yuketang_set_token'}:
        return Card('雨课堂 · 登录' if tool != 'yuketang_logout' else '雨课堂 · 退出登录', '',
            (escape(result.get('reply_text') or result.get('msg') or '结果未核验。', 900),), '发送“开始考试”选测评；登录状态不代表答题完成。')
    return Card('雨课堂 · 状态', '本次回执', (escape(result.get('reply_text') or result.get('msg') or '未提供可核验结果。', 900),),
        '配置、接管、保存和交卷需分别核验。')
