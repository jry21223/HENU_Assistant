# 青木平台接入实现规格（用户已确认推荐方案）

基点：langbot-plugin b3fcd88；独立 worktree qingmu-platform-integration。只修改 LangBot 接入层，本轮不复制到 MCP/Agent Skill、不部署生产、不使用真实学生账号消费。

## 用户可见行为

沿用雨课堂的空间 CLI、私聊直接绑定、按 QQ 隔离的加密存储、直接回复“确认”和明确状态回执。平台先覆盖 U校园、U校园AI版、学习通，展示已选择的具体服务类型。

建议命令：`qingmu status`、`qingmu platforms`、`qingmu account set --platform ucampus --account <账号> --password <密码> --school <学校>`、`qingmu courses`、`qingmu order add --platform <平台> --course <查课结果序号>`、`qingmu orders`（本地回执）、`qingmu order status --id <本地订单号>`（上游进度）、`qingmu order retry --id <自己的订单>`。中文“青木”可作为入口别名；密码只能通过用户私聊直接发送，不进入模型工具参数或历史。

下单先显示平台、课程、学校、服务类型、部署方核实的计费说明和付费性质，收到同一 QQ 在同一私聊的确认后才提交。没有现行结算价格接口，不能把附件静态商品价说成实际扣费；部署方必须提供核实的计费说明，用户确认该说明后才提交。补刷也需确认。改密因 `yid/oid` 冲突暂缓。

## 适配约束

供应商 uid/key 仅由部署环境注入。平台服务编号配置与学生课程 kcid、订单 id 分开；目录商品 id 不能未经核实当作 platform。HTTP 客户端只允许运营者配置的 HTTPS 端点，校验 TLS（可配置供应商 CA），禁用重定向、请求自动重试和外部 URL 参数，限制响应大小和网络耗时。返回数据按允许字段净化，不回传凭据、供应商原始错误或订单中的密码字段。

get/add/budan/chadanoid 分 action 处理返回码。未知响应/订单回执缺失时明示“结果未确认”，提交前持久化在途记录防止并发或重启重复付费，不把 HTTP 200 当成订单成功。查询仅限本 QQ 已绑定账号及已保存订单，不能按任意 username/id 查询供应商其他用户。

## 需要实测前确认的供应商契约

无凭据/脱敏真实响应：查课列表的字段、下单订单ID、补刷成功码、platform 与商品ID映射；可信HTTPS地址/CA和测试环境。未确认部分保持配置门控，不默认开通外部订单。

## 建议测试边界

公开CLI/工具和真实消息入口：私聊绑定拦截、凭据脱敏、跨QQ和群聊拒绝、plain确认与雨课堂/KIT消歧、有效期、并发/重放、重启在途恢复。HTTP客户端：各action请求契约、响应类型/大小、返回码、TLS错误、重定向拒绝、读取超时、未知付费结果不重试。请求用模拟第三方边界，不用真实学生密码或真实付费。

## 工程技能约定草案

问题追踪：建议本仓库GitHub Issues。文档单一上下文：根GLOSSARY.md + docs/adr/。默认triage标签：needs-triage、needs-info、ready-for-agent、ready-for-human、wontfix。约定写 docs/agents/issue-tracker.md、domain.md、triage-labels.md，优先编辑既有CLAUDE.md/AGENTS.md；本分支无这两份文件，创建哪份需按setup技能确认。

## 审查基点与交付

基点 b3fcd88aaffa5f26c1f87641dd1fe9945410c611。实现使用独立加密 Storage 状态和异步协调器；供应商网络请求在短 Storage 事务外执行，避免阻塞其他校园命令。Tool 只可发起预览或只读请求，确认只能来自真实消息。使用原生 CLI 的平台别名、帮助及工具交付返回，保持已存在 KIT/雨课堂确认行为。供应商契约参数为部署配置，缺失时停止调用；原文没有证明的映射、查课响应和补刷返回码不写为默认事实。交付本地通过的代码与 lbpkg；不部署、不调用真实付费接口。
