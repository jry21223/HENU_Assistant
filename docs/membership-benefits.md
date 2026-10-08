# KIT 终身会员校园权益

QQ 用户绑定 KIT 后使用 Account Portfolio 的同一份终身会员，无机器人本地会员副本。雨课堂、U校园（含 AI 版）、学习通和图书馆定时预约是四项权益。平台服务费用由站方承担，会员无需另付费或扣积分；供应商采购价、计费和余额属于运营配置，不向用户展示。

## 资格契约与发布

必须配套发布 HENU-Kit-DEV #562 / PR #564 的 Core 和 Portfolio，只升级插件不会获得权限。插件继续使用自己的 `HENU_KIT_*` 配置和现有独立签名 credential，不持有 Portfolio / Portal / Console owner credential，不把任何 owner credential 传给雨课堂桥。

`POST /api/v1/qq-bindings/benefits` 只发送 `{subject:<当前可信 QQ sender>}`，签名覆盖 POST、Core canonical path、timestamp、nonce 和正文 SHA256 五行。服务端解析 app-scoped binding 和 active/verified 身份再查询会员 owner，不接受 UUID 或本地 `member` 字段。

200 envelope 的 data 必须有 `bound=true`、`plan=free|lifetime`、bool `lifetime`/`allowed`、`benefits` 和 RFC3339 UTC `checked_at`。终身会员的权益列表恰为 `yuketang,ucampus,chaoxing,library_schedule`；免费会员为空列表且 allowed=false。插件要求响应检查时间不超过30秒且不在未来，每次检查新发请求、无缓存。KIT HTTP 按单字节检查10秒总期限，拒绝压缩响应，socket空闲读取期限1秒；异步入口另设12秒期限。超时不会授权外部动作。`ucampus_ai` 使用 `ucampus` 权益。404 NOT_BOUND 引导私聊发送“绑定 HENU KIT”；免费会员引导开通；503、网络失败、过期或不一致契约都拒绝新操作。

公共同步 gate 是 `henu_plugin.membership.require_benefit(subject, benefit)`，异步 message/worker gate 是 `require_benefit_async`，失败抛 `MembershipError`，其 `public_result()` 返回不含原始接口异常的安全提示。subject 仅来自运行时身份，不能来自用户参数。

青木查课、订单/补刷预览核验资格；确认提交先查会员再持久记录屏障，在工作线程真正调用供应商前再次查资格。资格拒绝发生在 supplier HTTP 前；既有结果、删除绑定继续允许。真实订单仍遵循私聊后续确认、单 QQ 加密 Storage、防重和未知结果禁止自动重试。

## 雨课堂执行端边界

本仓库仅有服务器 A 的 LangBot 客户端，旧桥 v1 没有逐动作会员检查能力。没有取得 B 的桥和 daemon canonical source，不能证明会员撤销会停止已排队或存量任务。`daemon_wired=true`、接口 ok 或客户端一次检查均不能证明执行器受约束。

本版本明确失败关闭：拒绝新的启用、配置修改、凭据绑定登录和 enabled 快照同步；桥客户端直接调用 config/start/password 也不能绕过。状态读取、disabled 配置同步和 logout 保留。status 自动重同步不会把旧的 enabled=true 重新推给桥。存量 B 作业是否停止尚未核实，必须通过 B 停止确认和真实执行器状态另行验收，不将本地 disabled 当停止证明。

恢复开放需取得 B 源码并配套升级：真实课堂动作、延迟任务、恢复/session、派生 worker 的每次外部动作之前，按同一 subject 权威资格门校验，或使用身份、config hash、audience 绑定且有明确到期/撤销上限的服务端授权。旧 session/config 缺授权、过期、身份/hash 不匹配或 owner 不可用均失败关闭。新能力证明必须绑定请求、当前身份和配置并经执行器边界测试；不能仅接受桥自报 boolean。雨课堂公开 Tool 和私聊入口先在全局运行时锁外查询资格；同步 service 内仅进行纯本地旧执行器拒绝，资格网络等待不阻塞其他 QQ 的读取/停用。当前版本不附加未经验证的 lease 字段（旧 sanitize 会丢弃），也不以环境变量打开此门。

此处未实现 B 逐动作适配，未停止生产存量执行。必须完成 B 配套验收后再恢复本地新操作入口，不能以本版本的合成桥测试宣称真实 daemon 已集成。

## 验证范围

测试公开 signed HTTP、私聊/Tool 和实际 service/bridge 边界，使用合成账号、Core 和供应商响应。覆盖会员撤销/故障不提交、按 subject 隔离、费用包含、只读旧结果、旧桥新执行拒绝和停用退出回归。本次未部署生产、未发真实订单或学校预约。图书馆一次性调度属于后续 #34，不把立即预约的使用日期/时间当执行时间。
