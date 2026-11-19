# 电费只读功能协作说明

适用于 `feat/electricity-readonly`，目标交付形态为 LangBot 插件。以下进度核对于 2026 年 11 月 19 日，代码基线为 `ca06206`；接手时以当前源码和 Git 状态为准。

## 接手入口

1. 阅读本工作树的 [AGENTS.md](../AGENTS.md)，核对分支、未提交改动和工作树位置：

   ```bash
   git branch --show-current
   git status --short --branch
   git worktree list
   ```

   本机功能工作树位于主仓库的 `output/worktrees/electricity-readonly/`，主工作树当前承载 `langbot-plugin`。电费改动在功能工作树内完成。
2. 阅读[实测与使用说明](electricity-readonly-verification.md)，区分已有对照记录与尚待验收的能力。
3. 开工时在协作消息中写清本次目标、预计修改文件和验收方式。多人或多个 Agent 并行时，各用独立工作树和任务分支；修改范围重叠时先协调归属，再集成提交。

## 当前进度

| 工作项 | 当前状态 |
| --- | --- |
| 只读查询核心 | 已实现有效云缴费会话下的目录查询、选表与剩余电量查询 |
| 宿舍地址解析 | 已实现简写和地址 JSON；默认查询双表，指定表型时查询单表；保留部分成功结果 |
| App 与 Python 对照 | 已有北苑、东苑宿舍双表对照记录；样本范围和复现方法见实测文档 |
| 登录态保存与服务器部署 | 已实现 cURL 导入为私有上下文、查询成功后回写 `Set-Cookie`；服务器只需文件和 Python，不运行 AVD。会话有效期与保活需求待实测 |
| LangBot CLI 与 QQ 入口 | 已接入 `electricity <宿舍>`，使用 `HENU_ELECTRICITY_CONTEXT` 指向的运营者登录态，默认仅私聊；服务入口已用真实登录态冒烟，真实 QQ 消息端到端待验收 |
| 脱离 AVD 的持续使用 | 2026 年 11 月 19 日导入后关停 AVD，查询成功；空闲不足 1 小时会话即过期（`idserial为空`），保活间隔与绝对时长待测 |

历史测试数量属于对应记录的基线；修改后按本次代码重新验证，并记录实际结果。

## 修改入口

| 范围 | 文件 |
| --- | --- |
| HTTP 查询、上下文校验、CLI 汇总 | [campus_core/electricity_client.py](../campus_core/electricity_client.py) |
| 地址解析、目录匹配与表型解析 | [campus_core/electricity_meter.py](../campus_core/electricity_meter.py) |
| 已确认的园区别名 | [campus_core/config/electricity_area_aliases.toml](../campus_core/config/electricity_area_aliases.toml) |
| 合成查询与解析测试 | [test_electricity_client.py](../tests/test_electricity_client.py)、[test_electricity_meter.py](../tests/test_electricity_meter.py) |
| Bot 命令与回复 | [henu_plugin/cli.py](../henu_plugin/cli.py) 的 `electricity` 分支、[henu_plugin/service.py](../henu_plugin/service.py) 的 `_electricity_query` |

后续接入需要补齐：真实 QQ 消息端到端验收、会话有效期实测及是否需要保活。HTTPX 请求日志已过滤云缴费 URL；认证失效时 Bot 提示联系管理员重新导入。

## 验收与交接

- 修改查询或解析后，使用项目 Python 环境运行合成测试：

  ```bash
  python -m pytest -q tests/test_electricity_client.py tests/test_electricity_meter.py
  ```

- 接入 LangBot CLI、QQ 路由或打包后，补跑实测文档列出的对应回归；环境准备和完整检查沿用 `AGENTS.md`。
- 真实查询使用本人获授权的房间和有效官方会话，保持只读。上下文与会话按账号隔离，存放在本工作树 `output/` 下的私有文件中；凭据、Cookie、原始表 ID 和个人响应不进入 Git 或公开日志。电表 ID 使用官方目录返回值。
- 纯文档修改运行 `git diff --check`。交接时列明修改范围、已运行检查、未验证项及下一步；代码进度变化时更新上表，新增实测结果写入实测文档，脱敏证据放在 `output/`。
