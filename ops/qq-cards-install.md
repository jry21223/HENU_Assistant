# QQ 卡片宿主桥（发布阶段）

插件发送标准 SDK `Plain`，专属前缀 `# 河大助手 · ` 标记由插件渲染的卡片。宿主桥在 QQ Official Client 的单聊/群聊文字方法中转换为 `msg_type=2` 与 `markdown.content`。其他文字、富媒体、引用来源、发送回执与频道路径保留。

本任务只准备和验证文件，没有在生产运行以下步骤。发布前必须重新读取实际宿主版本与配置；不能拿本机历史副本的 hash 当线上身份。

1. 精确定位线上 `langbot/libs/qq_official_api/api.py`、`langbot/pkg/platform/sources/`。读取原文件 hash、发送方法和当前 seq/receipt 补丁，备份原文件与部署插件。
2. 把 `qq_cards_transport.py` 作为 `henu_qq_cards.py` 放到宿主 `langbot/pkg/platform/sources/`；模块依赖宿主既有 httpx。
3. 在宿主 `langbot/` 根目录对 `qq-cards-host.patch` 先执行 `patch --dry-run -p1`，检查两处目标恰好为 private/group text 方法。不得强行套用有差异的代码。
4. 应用后运行 `python ops/verify-qq-cards-host.py <宿主api.py>`、Python 编译检查和本地出站 mock 测试；确认模块可从 `langbot.pkg.platform.sources.henu_qq_cards` 导入。
5. 生产已安装较新的 2.1.0 与独立业务热修复时，只部署经精确 before/after 哈希和保护文件校验的呈现增量，不以本 PR 的 2.0.5 包覆盖线上。SDK 0.4.13 的 `reply_message_chain` 与公开 SDK 的 `reply` 由共享 helper 适配；正常响应覆盖回复链，不调用 `prevent_default`。确认接收人/测试场景后再验收真实 QQ；审批和验收由根线程完成。

回滚：恢复备份的宿主 API 文件和原插件包。保留/移除未引用的 helper 均不影响原来的文字发送。原有业务 Storage 不变。

HTTP 400/403 明确拒绝时回退一次可读文字，新 `msg_seq` 保留原被动来源；429/5xx/网络超时不重复发送。未配置被动来源时维持原宿主文字行为，不新增主动 Markdown 消息。桥永不发送模板或 keyboard 字段，不上传图片。
