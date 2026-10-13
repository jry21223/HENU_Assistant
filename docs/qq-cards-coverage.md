# QQ 卡片覆盖矩阵

基线：LangBot 远端 `1f13d98`，2026-10-13 新鲜 clone；原本机 dirty checkout 未修改。领域依据 `henu_plugin/service.py` 的实际 dispatch、`henu_plugin/cli.py`、两项已注册 Tool 和三个既有直接回复流程。

本次新增统一呈现、分页与宿主传输桥，不新增校园业务。所有 service dispatch 路由都登记卡片标题（测试强制集合相等）；展示采用现有摘要和经过白名单选择的结构字段，不输出原始 JSON。

| 实际路由 | 卡片 | 群聊输出策略 |
| --- | --- | --- |
| `henu_cli` | 使用帮助 | 仅帮助；个人结果转私聊 |
| `setup_account` | 校园账号绑定 | 仅帮助；个人结果转私聊 |
| `sync_schedule` | 课表导入 | 仅帮助；个人结果转私聊 |
| `schedule_query` | 我的课表 | 仅帮助；个人结果转私聊 |
| `smart_course_selection` | 选课方案 | 仅帮助；个人结果转私聊 |
| `smart_course_select` | 选课提交 | 仅帮助；个人结果转私聊 |
| `course_selection_query` | 教务选课 | 仅帮助；个人结果转私聊 |
| `course_selection_plan` | 选课方案 | 仅帮助；个人结果转私聊 |
| `course_selection_submit` | 选课提交 | 仅帮助；个人结果转私聊 |
| `course_monitor_config` | 选课监控设置 | 仅帮助；个人结果转私聊 |
| `course_monitor_once` | 选课监控 | 仅帮助；个人结果转私聊 |
| `course_monitor_run` | 选课监控 | 仅帮助；个人结果转私聊 |
| `course_monitor_notify_test` | 监控通知测试 | 仅帮助；个人结果转私聊 |
| `library_query` | 图书馆座位 | 仅区域/座位公开；当前预约/记录转私聊 |
| `library_reserve` | 座位预约 | 仅帮助；个人结果转私聊 |
| `library_auto_signin` | 座位签到 | 仅帮助；个人结果转私聊 |
| `library_cancel` | 取消座位预约 | 仅帮助；个人结果转私聊 |
| `seminar_group` | 研讨室成员组 | 仅帮助；个人结果转私聊 |
| `seminar_query` | 研讨室 | 仅房间/筛选/详情公开；记录/任务转私聊 |
| `seminar_reserve` | 研讨室预约 | 仅帮助；个人结果转私聊 |
| `seminar_signin` | 研讨室签到 | 仅帮助；个人结果转私聊 |
| `seminar_cancel` | 取消研讨室预约 | 仅帮助；个人结果转私聊 |
| `set_calibration_source` | 节次校准 | 仅帮助；个人结果转私聊 |
| `system_status` | 账号与服务状态 | 仅帮助；个人结果转私聊 |
| `yunfz_leave_query` | 河宝请假 | 仅帮助；个人结果转私聊 |
| `yunfz_signin_query` | 河宝签到 | 仅帮助；个人结果转私聊 |
| `yunfz_checksleep_query` | 河宝查寝 | 仅帮助；个人结果转私聊 |
| `yunfz_activity_query` | 河宝活动 | 仅帮助；个人结果转私聊 |
| `yunfz_collection_query` | 河宝信息收集 | 仅帮助；个人结果转私聊 |
| `empty_classroom_query` | 空教室 | 公开查询/同步摘要 |
| `empty_classroom_sync` | 空教室数据同步 | 公开查询/同步摘要 |
| `resource_registry_query` | 校园资源 | 公开查询/同步摘要 |
| `resource_registry_sync` | 校园资源同步 | 公开查询/同步摘要 |
| `yuketang_status` | 雨课堂状态 | 仅帮助；个人结果转私聊 |
| `yuketang_courses` | 雨课堂课程 | 仅帮助；个人结果转私聊 |
| `yuketang_exam_list` | 雨课堂考试列表 | 仅帮助；个人结果转私聊 |
| `yuketang_config_show` | 雨课堂配置 | 仅帮助；个人结果转私聊 |
| `yuketang_set_enabled` | 雨课堂开关 | 仅帮助；个人结果转私聊 |
| `yuketang_domain_set` | 雨课堂域名 | 仅帮助；个人结果转私聊 |
| `yuketang_lesson_set` | 雨课堂课堂设置 | 仅帮助；个人结果转私聊 |
| `yuketang_exam_set` | 雨课堂考试设置 | 仅帮助；个人结果转私聊 |
| `yuketang_list_update` | 雨课堂课程范围 | 仅帮助；个人结果转私聊 |
| `yuketang_start_time` | 雨课堂进班时间 | 仅帮助；个人结果转私聊 |
| `yuketang_set_token` | 雨课堂令牌绑定 | 仅帮助；个人结果转私聊 |
| `yuketang_account_set` | 雨课堂账号绑定 | 仅帮助；个人结果转私聊 |
| `yuketang_login` | 雨课堂登录 | 仅帮助；个人结果转私聊 |
| `yuketang_logout` | 雨课堂退出登录 | 仅帮助；个人结果转私聊 |

| 其他真实入口 | 卡片 | 说明 |
| --- | --- | --- |
| `help` / `help <topic>` | 渐进式帮助 | 命令、示例、提示与下一步，长帮助可翻页 |
| 私聊校园绑定与校准拦截 | 校园账号与校准回执 | 不回显密码/Cookie；异常不暴露异常正文 |
| HENU KIT 绑定/换绑/解绑 | HENU KIT 绑定 | 保留已验证授权 URL 的完整 fragment；原有引用确认和发送回执语义保留 |
| 自然语言账号状态群询问 | 请转到私聊 | 不读取/展示个人 Storage |
| 雨课堂登录/确认/重新登录/退出 | 雨课堂登录回执 | 原有后台任务、候选凭据、确认和结果不明保护保留 |
| 下一条本人“确认” | 操作确认 | 原有会话、来源、TTL、幂等保护继续生效 |
| 美食 create / 图片补充 / 确认发布 / 取消 | 草稿/投稿回执 | 私聊；长草稿全部页送达后才标记完整预览；不新增图床 |
| 美食 mine | 美食查询 | 私聊；只提取可展示字段，不输出身份元数据 |
| 美食 list / detail / venues | 美食查询 | 公开结果摘要，五种实际 action 均接入 |
| 下一页/上一页/卡片第 N 页 | 原卡片分页 | 机器人+会话类型+会话+发送者隔离，600秒过期，最多20页/128会话；单消息单页 |

通用状态：成功、未绑定/需要登录、空结果、实时接口空返回、静态参考、缓存过期、错误、确认未提交、确认过期、外部已提交但本地保存失败。异常或 Query API 不可用时保留原有文字回退。原始结果不因卡片分页而修改；卡片在模型载荷截断前捕获。同请求的不同工具/命令合并分页，下一请求覆盖旧卡片；同一命令的持久化告警会替换原成功页。

## 官方场景与传输

- [Markdown 文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)：2026/04/23 已开放单聊/群聊自定义 Markdown；使用 `markdown.content`，无需 `custom_template_id`。频道仍单独受能力限制，本补丁保持频道文字路径。
- [消息按钮](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html)：按钮模板和自定义按钮分别有能力条件。旧问卷已被用户取消，本任务不访问/提交旧问卷，不申请后台、不认定企业账号已具备按钮能力。传输字段完全不包含 `keyboard`。
- [群聊发送](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)被动窗口5分钟/每条最多5次；[单聊发送](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)60分钟/每条最多4次。本任务不批量发送分页、不新增定时/主动推送。明确400/403拒绝时只文字回退一次、使用新序号；超时和5xx不自动重复发送。

## 真实缺项与验收边界

- 此 LangBot 分支只有 `schedule sync/now/day/week/full`。没有课表定时推送路由、调度器或 QQ 推送接收人设置，未虚构新增。
- 雨课堂 PDF/PPT/试卷文件推送在原有 README/CLI 明确停用，未恢复。
- 同步工具不是后台任务；研讨室后台自动签到在插件版原本关闭，本改造不改变。
- SDK 没有原生 Markdown 组件。插件继续使用可序列化 Plain；`ops/qq_cards_transport.py` 与 `ops/qq-cards-host.patch` 提供真实 msg_type=2 桥。补丁在本机既有 `qq-quote-live-20260927/verified-candidate` 的源文件副本上 dry-run、应用、AST/载荷验证；这不是当前线上宿主已适配的证据。
- **尚需发布阶段**：对线上宿主精确版本重新核验并安装桥、部署插件；之后由根线程确认真实接收人再进行 QQ 手机/桌面与频道/拒绝回退验收。本任务未部署、未发送真实 QQ 消息、未变更账号权限。
- 预览为 Safari 打开的离线合成数据，不等同 QQ 客户端截图。见 `docs/previews/queries.html`、`states.html` 和 `representative.md`。


## 针对性验证

- 最终相关测试：283 passed（12 条原 SDK 弃用警告），未运行全仓重测。包含真实帮助payload、真实选课规划器、真实Freshness.to_dict、矩阵/占用/美食详情白名单、群聊私密拦截、SDK序列化、多工具/美食查询聚合、消息超时、附图分页、完整美食预览后确认。
- `lbp build` 成功；Python编译与 `git diff --check` 通过。包构建只证明可打包，不证明线上已安装。
- 宿主副本补丁 dry-run、应用与 `verify-qq-cards-host.py` 通过。mock HTTP验证 msg_type=2/content、键盘/模板字段缺席、被动来源和新seq、400/403单次文字回退、超时/5xx不重试。
- Safari真实打开离线合成HTML，截图已裁掉浏览器栏；[查询预览](previews/queries-safari.png)、[状态预览](previews/states-safari.png)。未使用Chrome/Chromium，包括headless。
- 两轴独立评审发现的问题均补针对性回归并修复；最终复核通过，见 [验证记录](qq-cards-validation.md)。
