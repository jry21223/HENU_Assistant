# 电费只读功能协作说明

适用于 `feat/electricity-readonly`，目标交付形态为 LangBot 插件。以下进度核对于 2026 年 11 月 19 日，代码基线为 `415f041`。

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
| 登录态保存与服务器部署 | 已实现 cURL 导入为私有上下文、有读数时合并服务端新发的 Cookie。服务器可在 AVD 关停后查询，限于导入会话仍有效时 |
| 自动续期 | `pay.do` 换票已实测通过；原生 `type=3` 续期已被服务端接受（带 `deviceId`），但会轮换并作废旧 token。需要一次新登录取得有效 token 后完成端到端 |
| LangBot CLI 与 QQ 入口 | 已接入 `electricity <宿舍>`，使用 `HENU_ELECTRICITY_CONTEXT` 指向的运营者登录态，默认仅私聊；真实 QQ 消息端到端待验收 |
| 脱离 AVD 的持续使用 | 关停 AVD 后查询成功，但 `SESSION` 约有 1 小时绝对有效期（每 10 分钟使用也不能延长）。长期运行依赖 `pay.do` 换票，需约每小时一次 |

历史测试数量属于对应记录的基线；修改后按本次代码重新验证，并记录实际结果。

## 电费登录态为什么会失效

11 月 19 日的轮询日志记下了 18:04:22 导入并查询成功、`18:0x` 关停 AVD 后仍成功、19:06:17 首次记录失败。复核失败响应时，选房接口 `checkRoomCount.do` 返回业务码 `-600` 和「idserial为空」，并下发匿名 Cookie。两次成功查询均未收到新的 `Set-Cookie`。这将问题定位到云缴费在该次请求中已无法识别用户。最后一次成功的分钟不完整，原会话的签发时间也未知，无法从这组记录推算固定有效期。[实测记录](electricity-readonly-verification.md#保存登录态并部署到服务器)

| 所在层 | 完美校园 5.8.19 反编译代码与插件源码所见 |
|---|---|
| App 原生登录 | `AbstractNetRequestProcess.autoLogin()` 在原生登录标志失效时读取已保存的 Token。服务端接受后，App 保存返回的 Token 并恢复登录标志。服务端明确返回业务失败时，App 清空 Token。 |
| App 打开网页 | `WXWebView.loadUrl()` 在加载网页前调用 `setCookie()`，取当前原生会话 ID，将日期加一个日历月后写入 CookieManager。该日期描述客户端写入的这枚域 Cookie。云缴费服务端仍独立判断请求能否识别用户。另有通用页面凭据桥，其数据模型含 `buildTimes` 和 `deadTimes`。现有电费请求记录从缴费项目页开始，尚未观察到电费入口调用该桥。 |
| Bot 发起查询 | `query_saved()` 发送导入的 Cookie，有读数且服务端发来变更时才回写。代码里没有按登录时长丢弃 Cookie 的逻辑，也没有调用 App 的原生自动登录流程。[查询客户端](../campus_core/electricity_client.py) |

下一次失效时，用同一账号在官方 App 重新打开「缴费 → 用电支出」，查询同一间已授权宿舍。记录是否需要重新登录或授权，以及首次成功查询前的页面跳转、业务码和新发 Cookie 的名称及到期属性，只保留脱敏信息。如果旧会话失败而 App 直接恢复查询，就有证据表明官方入口能在当时的 App 登录状态下恢复电费访问。

服务端并不因 Cookie 值不变而延长会话：11 月 19 日 20:15–21:05 每 10 分钟查询全部成功，21:15 仍失效，说明 `SESSION` 有约 1 小时的绝对有效期。保活不可行，必须每小时内通过 `pay.do` 换票（见下）或重新登录。

11 月 19 日 20–21 时的续期实验：长寿命 Cookie 不能换发可用会话；真正的换票接口是 `GET /CloudPayment/user/pay.do?token=<校区 sessionId>&customerId=<customId>`，`token` 正确时 302 下发可用 `SESSION`。已用该会话查询成功，并在关停 AVD 后重复查询。[实测记录](electricity-readonly-verification.md#自动续期paydo-换票2026-11-19-实测通过)

因此下一步只剩获取 `sessionId`（`exchangeSecretkey.action`）与 `custom_id`（登录响应），即原生登录客户端；拿到后 `--refresh` 即可全自动续期。

## 修改入口

| 范围 | 文件 |
| --- | --- |
| HTTP 查询、上下文校验、CLI 汇总 | [campus_core/electricity_client.py](../campus_core/electricity_client.py) |
| 地址解析、目录匹配与表型解析 | [campus_core/electricity_meter.py](../campus_core/electricity_meter.py) |
| 已确认的园区别名 | [campus_core/config/electricity_area_aliases.toml](../campus_core/config/electricity_area_aliases.toml) |
| 合成查询与解析测试 | [test_electricity_client.py](../tests/test_electricity_client.py)、[test_electricity_meter.py](../tests/test_electricity_meter.py) |
| Bot 命令与回复 | [henu_plugin/cli.py](../henu_plugin/cli.py) 的 `electricity` 分支、[henu_plugin/service.py](../henu_plugin/service.py) 的 `_electricity_query` |
| 原生登录与续期 | [campus_core/wmxy_auth.py](../campus_core/wmxy_auth.py)、[test_wmxy_auth.py](../tests/test_wmxy_auth.py) |

后续接入需要补齐：真实 QQ 消息端到端验收、会话有效期实测及是否需要保活。HTTPX 请求日志已过滤云缴费 URL；认证失效时 Bot 提示联系管理员重新导入。

## 验收与交接

- 修改查询或解析后，使用项目 Python 环境运行合成测试：

  ```bash
  python -m pytest -q tests/test_electricity_client.py tests/test_electricity_meter.py
  ```

- 接入 LangBot CLI、QQ 路由或打包后，补跑实测文档列出的对应回归；环境准备和完整检查沿用 `AGENTS.md`。
- 真实查询使用本人获授权的房间和有效官方会话，保持只读。上下文与会话按账号隔离，存放在本工作树 `output/` 下的私有文件中；凭据、Cookie、原始表 ID 和个人响应不进入 Git 或公开日志。电表 ID 使用官方目录返回值。
- 纯文档修改运行 `git diff --check`。交接时列明修改范围、已运行检查、未验证项及下一步；代码进度变化时更新上表，新增实测结果写入实测文档，脱敏证据放在 `output/`。
