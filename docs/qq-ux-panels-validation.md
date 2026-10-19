# QQ 使用体验验证与发布记录

线上：langbot.metaview.top；仅 HENU 插件与 QQ 菜单/面板，不升级主程序。

- 本地完整测试：462 passed（含上游账号保护与 QQ 响应规范化回归）。
- 包构建通过：2.0.5 lbpkg。生产采用增量 overlay，不整包覆盖。
- 真实 SDK 0.5.8 隔离回归：41 passed，包括原账号拦截、卡片投递与新导航/API。
- 两轴审查初轮发现面板预检顺序问题，补红绿回归后修复；最终 Standards/Spec 复核均无剩余激活阻塞；发布脚本5项成功/失败恢复测试通过。
- 备份目录：`/root/LangBot/backups/henu-qq-ux-20261019/`。插件归档、SQLite 一致性备份、允许文件清单及145个保护源码哈希；用户数据只备份，不回滚。

## 最终 overlay 哈希

- `components/event_listener/identity_capture_safe.py`：`055d557209255f05df94fd596ae7e593c04a01132c832cad535531ccd06f2b81`
- `components/cli_tools/henu_cli.yaml`：`e9befab54e82010f625a1cce1a149728122f6434b1e55ef7dca40401c1b99b61`
- `henu_plugin/qq_cards.py`：`2cb2745b050c6a8b6d7aee491eb0e0aa721da56e8daa3c3e09b55055ea909a2c`
- `henu_plugin/qq_navigation.py`：`d3e0d93d1883c9d5b4a89a88917124a460efc2adb72f9295dcc0e12bd2d0ac6a`
- `henu_plugin/qq_navigation_api.py`：`222bf4fce670ce51a6972a523aef48407ece8ba5d613e8b81a13f6f3d4b4fd08`

## 验收边界

菜单主要查询继续使用既有模型与工具路由，新增提示词明确入口映射；未把静态映射测试当作真实模型端到端验收。真实 QQ 菜单展示、私聊绑定/状态/课表和群聊隐私仍待客户端验收。

## 线上结果

- 增量 overlay 已激活，仅改5个允许文件；145个保护源码hash一致，所有Bot适配器/启用/路由配置与部署前一致。
- 主程序镜像未替换；仅停止并启动插件 runtime。5个插件均 enabled=true、status=initialized，实际HENU子进程已存在。
- 实际安装路径回归17项通过；后续 operator helper 的响应规范化回归纳入完整462项。
- QQ：3组菜单、私聊12项、群聊5项；再次API同步 `verified=true, changes=[]`，没有重复创建。
- QQ回读会自动补菜单icon并省略false，首轮严格JSON对比误报；写入已完成，未盲目重试。补规范化测试后只读确认并验证幂等，详见私有 `qq-applied`、`qq-verified` 证据。
- Operator API helper 做了一次独立规范化修正，未改变运行中的业务模块；前后hash保存在 `operator-normalization.json`。

客户端尚未验收：打开机器人私聊检查菜单，发送“使用帮助”“绑定账号”“今天课表”“找自习室”；已有账号不必重新绑定。群聊只测试公开自习查询与引导。不要发送真实密码作展示或测试素材。
