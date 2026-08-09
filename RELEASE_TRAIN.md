# HENU Assistant 2.1.0 发布列车

2.1.0 只允许按 `mcp-server → agent-skill → langbot-plugin` 的顺序发布。Tag 推送不会自动创建 Release；最后一步必须从 `v2.1.0` tag 手动调度工作流，并通过 `henu-production-release` 受保护环境审批。

## 候选版本与证据

发起审批前固定三个 40 位提交 SHA，且它们必须分别等于远端 `mcp-server`、`agent-skill`、`langbot-plugin` 分支头。三份 `henu_mcp.version`、LangBot `manifest.yaml` 的 `metadata.version`、MCP `serverInfo.version` 都必须是 `2.1.0`。

每个候选 SHA 均须通过 Python 3.10–3.14 的 frozen-lock 安装、`pip check`、`compileall`、完整 pytest 与各自 E2E。MCP 还须通过真实 MCP 1.29 client 与 MCP 2.0 modern client；LangBot 还须通过 `lbp build → ZIP/入口/敏感文件校验 → 解包导入 → 两份资源加载`。

发布审批必须附三端各自成功的 GitHub Actions run URL。工作流通过 GitHub API验证 workflow 名称、成功结论与候选 SHA 精确一致，其中 LangBot 证据必须来自 POSIX/Linux Python 3.10–3.14 矩阵工作流。Windows 不在 2.1.0 的发布与验收范围内。另须附一条不含 userinfo、query 或 fragment 的 HTTPS 证据链接，记录使用真实账号、隔离数据目录完成的只读 smoke：登录状态、课表、图书馆、研讨室和空教室查询。记录不得包含凭据、Cookie、token 或个人敏感响应。校园系统写操作只验证确认链和模拟 HTTP，不在 smoke 中提交。

LangBot 候选还必须安装到实际目标宿主，核验宿主插件运行时支持三参数 Tool 调用，并由真实 `PluginRuntimeHandler → HenuCliSafe` 链路传入可信的 `session/query_id`。2.1.0 的冻结开发运行时为 `langbot-plugin==0.5.0`；宿主 smoke 必须确认不存在旧版单参数 Handler 的 `TypeError`，且两个发送者的身份与 Storage 不串用。

## 发布顺序

1. 发布并验证 `mcp-server`，记录不可变 SHA。
2. 发布并验证 `agent-skill`，记录不可变 SHA。
3. 在 `v2.1.0` tag 上手动运行 LangBot Release，输入上述两个 SHA、当前 LangBot SHA、三端成功的 CI run URL、只读 smoke 证据，并确认前两步和实际 LangBot 宿主可信上下文 smoke 均已完成。

工作流会重新读取三个远端分支头、三个版本文件和 `manifest.yaml` 的 `metadata.version`，并通过 GitHub API 核验三端 CI；任何漂移都会拒绝发布。最终发布 job 按固定 tag 串行，后到的任务会重新验证并拒绝覆盖既有 Release。Release 正文同时记录三个 SHA、CI run 与 smoke 证据。`mcp_server_released`、`agent_skill_released`、`langbot_runtime_context_verified` 和真实账号 smoke 内容仍是受保护环境审批者的人工 attest，不被描述为自动发布证明。

## 运行限制

2.1.0 的 LangBot Storage 冲突锁是进程内锁，后端暂不提供跨 worker 的原子 CAS。因此生产部署限定为单进程 LangBot worker；不得用多个 worker 共享同一 Storage 后端。

用户账号、Cookie、研讨室任务与监控状态在同一个用户权威快照域内原子发布；共享节次/校准是另一个独立快照域。两域同时变化时不宣称跨域原子，发布和运行文案不得扩大这一保证。

研讨室自动签到会在外部调用前持久化 claim，以阻止并发或进程重启后的重复提交。若进程在 claim 与校园系统最终结果之间中断，任务进入 `uncertain`，不会自动重试，必须人工核验。这是崩溃场景的“至多一次”保护，不宣称上游没有幂等能力时可实现严格 exactly-once。

## 停止与回滚

任一远端 smoke 或下游验证失败都立即停止后续发布。对对应分支使用可审查的 `revert commit` 回滚，重新运行全部门禁；禁止强推、禁止移动既有 tag。保留上一版 `v2.0.4` LangBot 包作为可恢复资产。

LangBot 降级前先停止新请求，并用当前 2.1.0 包完成一次成功初始化。初始化会枚举所有 `snapshot_v2`，修复因进程中断而未完成的 `v2.0.4` individual-key 降级镜像；任一读取、解码或写入失败都会阻止初始化，此时禁止切换旧包。只有初始化成功且无未完成镜像后，才可换回 `v2.0.4`，随后重新运行只读 smoke。不得在仍有写请求或镜像修复失败时直接替换包。

2.1.0 的 snapshot 先标记 mirror pending，完成全部 legacy 写后才标记 complete。再次从 v2.0.4 升级前，必须先停止旧 worker、确认没有中断中的 Storage 写并备份后端，然后仅在首次 2.1.0 启动时设置 `HENU_IMPORT_V204_ROLLBACK=1`。该显式 handoff 会把完整 legacy 新代导入 snapshot；验证账号状态与只读 smoke 后必须移除变量并重启。默认启动遇到 complete snapshot 与 legacy 漂移会 fail closed，不会猜测数据代次；legacy 缺键、损坏或不可读同样必须人工核验。

本仓库中的工作流与文档不构成发布授权；未获得单独授权时不得 push、打 tag、上传 Release 或部署。
