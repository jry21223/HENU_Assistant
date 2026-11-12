# 雨课堂入口交付任务图

规格：yuketang-entry-ui-spec.md；用户已给出详细实现/验收范围并要求沿用测试边界、不重复确认。

1. **可继续操作的真实测评目录**。Blocked by: 无。公共CLI→目录→结构化工具输出完整保留复合身份和分页完整性；支持无ID浏览及精确详情、旧quiz操作可附精确班级ID。通过真实HTTP隔离测试。
2. **统一雨课堂事实卡片**。Blocked by: 无。现有各雨课堂结果及新目录/详情均采用同一信息层级和时间格式；不把配置/心跳当完成，错误/超时/确认/成绩有准确说明。QQ Card公共输出回归。
3. **无编号QQ导航与验收**。Blocked by: 1、2。用户文本/菜单/按钮→列表分页→序号/标题/课程消歧→精确详情→quiz确认或exam官方入口。作用域/主体/TTL/列表修订保护、全量测试、两路审查、受控生产合并及真实QQ验收。

## 模块间契约（供并行实现）

- CLI `yuketang browse [--course <名称>] [--page N] [--view available|all]` 返回 `activities` 当前页（每页5），`page/page_size/total/has_more`、`coverage`（课程total/scanned/failed、complete布尔与原因）、`account_revision`（官方user_id+domain摘要）、`source=live_rain_api`。每项包含course_name/classroom_label/classroom_id/assessment_type/assessment_id/quiz_id或exam_id/title/status/status_label/deadline_china/question_count/limit_seconds/official_url，以及可执行`detail_command`。
- CLI `yuketang activity show --classroom-id ID --type quiz|exam --id ID` 重新验证当前账号课程及确切复合身份。返回 `activity`（同形状，尽可能补全限制）、`account_revision`、`start_command`（quiz且未开始/未截止时）、`official_url`（exam）、`backend`（target/model/saved/submission各自证据状态，未知明确unverified），`success/error_code`。已交卷不提供start_command。
- quiz CLI兼容旧参数，新增可选`--classroom-id`。内部精确班级选择不依赖全局班级标签唯一，但白名单配置原有消歧不变。
- 工具的Agent输出保留上述结构化字段，页长受控，不被通用2200字符裁剪移除；旧exams/courses查询也保留完整身份或明确可导航分页，不能只剩不可操作摘要。
- 通用QQ Card入口`result_card(result)`仍返回Card；雨课堂专用`rain_ui.result_card(result, tool)`可返回Card/None，供现有入口优先使用。协调器可用该渲染器呈现新活动事实。
- 新协调器在qq_navigation.handle_navigation及handle_menu_query中统一挂载，使用existing qq_buttons.actions/handle及kind=`rain_*`路由。按钮必须携带经owner/TTL/本次列表版本校验的服务端选择令牌，不接受裸用户payload直接写。直接quiz预览必须沿用HenuCliSafe确认机制。
