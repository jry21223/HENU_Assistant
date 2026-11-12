from copy import deepcopy
import pytest

from henu_plugin.qq_cards import result_card


def activity(**changes):
    return dict(dict(course_name='概率论', classroom_label='005班', classroom_id='123',
        assessment_type='quiz', assessment_id='9001', quiz_id='9001',
        title='章节测验 第二章', status=0, deadline_china='2030-11-12T14:30:00+08:00',
        question_count=12, limit_seconds=1800), **changes)


def test_public_card_renders_assessment_facts_without_internal_identifiers():
    payload = dict(success=True, activities=[activity()], page=1, total=1, has_more=False,
        coverage=dict(total=24, scanned=12, failed=1, complete=False),
        cli=dict(resolved_tool='yuketang_browse'))
    original = deepcopy(payload)
    card = result_card(payload)
    text = '\n'.join(card.pages())
    for fact in ['雨课堂', '概率论', '005班', '测验', 'quiz', '章节测验 第二章',
                 '未开始', '2030-11-12 14:30', '12 题', '30 分钟', '部分']:
        assert fact in text
    assert '9001' not in text and '123' not in text
    assert payload == original


def render(tool, **result):
    return '\n'.join(result_card(dict(result, cli=dict(resolved_tool=tool))).pages())


def test_monitoring_receipt_never_claims_target_answers_saved():
    text = render('yuketang_status', success=True,
        yuketang=dict(enabled=True, exam=dict(an=True, llm=True, classroomWhiteList=['005班'])),
        bridge=dict(status='ok', monitoring_state='running', config_hash_matches=True, heartbeat_fresh=True))
    assert '监听运行：已运行' in text and '桥端配置：已接收' in text
    assert '目标发现／模型处理／答案保存／正式交卷：本次未核验' in text
    assert '已答完' not in text.replace('不代表已答完或已交卷', '')


def test_quiz_start_preview_retains_all_facts_and_separate_submit_boundary():
    text = render('yuketang_quiz_start', success=False, confirmation_required=True, quiz=activity())
    for fact in ['概率论', '005班', 'quiz', '章节测验 第二章', '2030-11-12 14:30', '12 题', '30 分钟', '待本人确认', '5分钟', '不代表同意交卷']:
        assert fact in text


def test_already_submitted_details_do_not_offer_start_preview():
    text = render('yuketang_activity_show', success=True, activity=activity(status=2))
    assert '已交卷' in text and '只读' in text
    assert '选择“开始预览”' not in text
    assert '正式交卷：已由官方状态核验' in text


def test_exam_details_explicitly_require_official_entry_and_personal_steps():
    text = render('yuketang_activity_show', success=True,
        activity=activity(assessment_type='exam', status=0, limit_seconds=None, question_count=None))
    assert '考试 · exam' in text and '题数：未提供' in text and '限时：未提供' in text
    assert '本人完成协议、必要验证及开始步骤' in text


def test_unknown_write_receipt_does_not_recommend_retrying_save_or_submit():
    text = render('yuketang_quiz_save', success=False, external_attempted=True,
        retry_safe=False, error_code='quiz_write_unknown', quiz=activity(status=1))
    assert '不要重复提交' in text and '结果待核验' in text
    assert '已完成' not in text


def test_unpublished_grade_is_never_exposed():
    text = render('yuketang_quiz_result', success=True, quiz=activity(status=2),
        grade=dict(visible=False, score=15, total_score=15))
    assert '尚未公布' in text
    assert '15' not in text


def test_final_grade_does_not_imply_backend_authorship():
    text = render('yuketang_quiz_result', success=True, quiz=activity(status=2),
        grade=dict(visible=True, score=15, total_score=15, incorrect_numbers=[], unfinished_numbers=[]))
    assert '成绩：15 / 15' in text and '已交卷' in text
    assert '接管成功' not in text and '后台答题完成' not in text


def test_legacy_exams_receive_same_card_without_duplicate_generic_rows():
    text = render('yuketang_exam_list', success=True, classroom=dict(course_name='概率论', classroom_label='005班'),
        exams=[dict(title='第二章', assessment_type='quiz', quiz_id='9001', status=0)])
    assert text.count('第二章') == 1
    for fact in ['题数：未提供', '限时：未提供', '截止：未提供']:
        assert fact in text


def test_untrusted_title_is_escaped_and_structured_data_preserved():
    value = activity(title='<@everyone> **注入**')
    text = render('yuketang_activity_show', success=True, activity=value)
    assert '<@everyone>' not in text and '\\*\\*注入' in text
    assert value['title'] == '<@everyone> **注入**'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, None])
def test_invalid_limits_render_unknown_without_crashing(value):
    text = render('yuketang_activity_show', success=True, activity=activity(limit_seconds=value))
    assert '限时：未提供' in text


def test_rain_help_does_not_request_manual_ids_or_chat_agent_solving():
    text = '\n'.join(result_card(dict(success=True, cli=dict(mode='help', topic='yuketang'))).pages())
    assert '无需寻找编号' in text and '手工作答替代' in text


def test_local_config_save_pending_bridge_is_not_shown_as_running():
    text = render('yuketang_set_enabled', success=True, bridge='pending', yuketang=dict(enabled=True))
    assert '配置保存：已保存' in text and '待同步' in text
    assert '监听运行：未核验' in text


def test_verified_save_and_formal_submit_have_distinct_receipt_titles():
    saved = render('yuketang_quiz_save', success=True, quiz=activity(status=1, saved_count=12),
        submission_state='not_submitted')
    submitted = render('yuketang_quiz_submit', success=True, quiz=activity(status=2),
        submission_state='submitted')
    assert '答案保存' in saved and '已保存 · 未交卷' in saved
    assert '提交结果' in submitted and '已交卷' in submitted
    assert '后台接管成功' not in saved
