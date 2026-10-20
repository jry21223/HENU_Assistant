# QQ 群消息与快捷入口修复

范围：保留现有校园功能，给底部快捷入口增加语义图标，修复 QQ 群 @ 消息链在 SDK 传输时丢失类型的问题；核验 LangBot 新版本兼容性。

## 已复现

生产 LangBot 4.10.11、SDK 0.5.8。原版 `GroupMessage.model_validate(event.model_dump())` 将 `At` 和 `Plain` 降为 `MessageComponent`，丢失 target/text，影响 @ 响应规则和插件指令识别。私聊已有同类 validator，群消息没有。

`ops/qq_group_chain_transport.py` 提供群消息 validator。只将输入深复制后交给现有 `MessageChain.model_validate`，保留原 SDK 的 model_dump、执行作用域和 sender/group 字段，不添加来源证明或放松个人信息私聊规则。必须同时安装到主程序与插件 runtime 的 SDK `GroupMessage`，仅修改仓库文件不会使线上 SDK 生效。

## 快捷入口

菜单显示名增加图标，发送内容不变。顶层菜单限制 10 字符，子菜单限制 14 字符。指令面板的 name 会成为输入内容，因此保持 name 原样，在 desc 中加图标。官方接口没有可写的原生 icon 字段。

接口依据：[菜单修改](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_menu.put.html)、[面板创建](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html)。

## 升级兼容性

2026-10-20 实时查询官方发布 API，最新稳定版本为 [v4.11.0](https://github.com/langbot-app/LangBot/releases/tag/v4.11.0)，固定依赖 SDK 0.7.11，并要求迁移内置运行器。

在生产 runtime 的独立 `/tmp/henu-sdk0711-probe` 安装该 SDK，仅执行无真实账号、无网络调用的后台存储探针：在 invocation 内创建任务，退出 invocation 后放行并调用 `LangBotAPIProxy.get_plugin_storage`，复现 `RuntimeError: Runner invocation has ended`。因此保留 4.10.11；这不是完整新版本演练，也不能据此声称已升级成功。后续升级需要另行适配后台任务生命周期及新运行器，再验证提醒、绑定、引用确认和存储。

## 发布与验收

服务器独立备份：`/root/LangBot/backups/henu-qq-group-icons-20261020/`，包含在线 SQLite 备份、原 Compose、两端原 SDK 文件、候选文件及 SHA256、实际 SDK 三次序列化验证记录。

仅替换两端 SDK 文件并生成固定镜像，更新实际 release Compose 的两个 image，保留 Box 镜像和数据挂载；回退使用原镜像与原 Compose，不恢复旧用户数据库。菜单另行备份、应用及回读。

本地全量 560 项通过；补充输入不变性与 Source/附件后，相关 4 项通过。两端实际 SDK 候选各完成三次序列化验证；激活后实际 SDK 与宿主原 `AtBotRule.match` 联测，@justbot 响应、@其他对象不响应。测试问题经独立评审修复。

2026-10-20 12:08 上线，两端 SDK 文件 SHA256 均为 `19f3f87112b18ac57a00daf8f68e69d7ad3546c6d1dc922a9fd31109d98758e3`。主程序固定镜像 `236de7a6e3e5eff847d3deb0413814067811e45a5a500742d4aff09577a35a8f`，runtime 固定镜像 `090ace38345b078945c3d490deb1c5cea57300518143df7beee79d6c5dfdbd7b`，五插件正常初始化。菜单及单聊/群聊面板应用成功并回读一致。

Mac QQ 指令面板实际显示各项图标；点击“今天课表”实际填入 `/今天课表`，12:10 发出后正常收到第 8 教学周的 4 项课程，证明私聊快捷入口未被图标改动破坏。手机底部菜单视觉、真实群消息仍待指定测试群后确认，不能以 API 回读或容器健康代替。未向任意群发送测试。
