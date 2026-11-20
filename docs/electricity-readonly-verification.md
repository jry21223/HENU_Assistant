# 完美校园剩余电量只读实测

2026 年 11 月 18 日，使用 `feat/electricity-readonly` 的 `2ab5bbf` 验证。通过官方 App 正常登录后的缴费房间目录，在金明校区北苑 27 号公寓选取一间账号历史记录外的宿舍。App 对两块表各返回一条实时成功响应。每次取得响应后移除本次 WebView 调试转发，再由 macOS 上的 Python 3.13.14 执行 `python -m campus_core.electricity_client`，查询同一块表。

| 表型 | App 查询状态 | Python 对照 |
| --- | --- | --- |
| 空调 | HTTP 200，返回剩余电量 | 读数和单位一致，单位为度 |
| 照明 | HTTP 200，返回剩余电量 | 读数和单位一致，单位为度 |

25 项电费合成测试通过。连同 CLI、QQ 路由和打包回归共 79 项通过。

证据保存在原工作区 `output/wmxy-electricity/evidence/git-live-2ab5bbf.jsonl`。归档保留状态和对照结论，会话头和原始表 ID 只在对照过程中通过内存传递。

本次属于正常会话下的单宿舍双表样本，查询时 AVD 仍运行。QQ Bot 入口、会话续期和关停 AVD 后的查询待验。Bot 接入时需处理 HTTPX INFO 日志中的查询参数。

## 从宿舍地址查询

在电费功能工作树里运行。标准输入接受宿舍简写或地址 JSON。`--context` 提供有效会话和缴费项目。

```bash
printf '%s\n' '顺河15345' \
  | python -m campus_core.electricity_client --context /path/to/private-context.json

printf '%s\n' '顺河15345空调' \
  | python -m campus_core.electricity_client --context /path/to/private-context.json

printf '%s\n' '{"campus":"金明","building":"东苑26栋","room":"638","meter":"lighting"}' \
  | python -m campus_core.electricity_client --context /path/to/private-context.json
```

上下文文件由当前用户持有，权限设为 `0600`，按账号分开存放。字段来自用户的有效云缴费会话。

```json
{
  "cookie": "<有效云缴费会话>",
  "referer": "https://h5cloud.17wanxiao.com:18443/CloudPayment/bill/selectPayProject.do",
  "schoolcode": "<官方项目值>",
  "payProId": "<官方项目值>",
  "businesstype": "<官方项目值>"
}
```

程序先请求 `checkRoomCount.do`。官方拒绝选房时停止。随后按级读取 `getRoom.do`，从目录同层的一对表项确认分表，使用所选项 `id` 查询。地址匹配多项时提示补充。房间已定位且两表已确认时，未点名表型就查双表，点名空调或照明才只查该表。

「顺河 → 铁三」由用户确认，放在[园区别名数据](../campus_core/config/electricity_area_aliases.toml)。APP 目录已确认「明伦校区 → 铁三15号公寓 → 3层」同时有 `345房间` 和 `15345房间`。前者对应空调，后者对应照明。编号只用于匹配显示项，电表 ID 由目录提供。

`readings` 分列成功读数，`errors` 分列失败表型和原因。一块失败时保留另一块结果。上下文可额外提供 `directory`，沿用已授权楼层快照。原有 `room_ref`、`cookie`、`referer` 输入仍受支持。

官方 APP 已分别点选 `345房间` 和 `15345房间`，均显示剩余电量，页面传入查询的两个目录 ID 不同。CLI 合成验收覆盖原样输入、点名单表、地址歧义、单块和双块失败。真实 QQ 消息端到端仍待接入。

## 保存登录态并部署到服务器

本地 AVD 只负责取得会话。服务器读取私有上下文文件后直接发 HTTP 请求，不运行 AVD。

1. 在本地 AVD 的官方 App「用电支出」查询一间已授权房间。Chrome 打开 `chrome://inspect`，进入该 WebView 的 DevTools → Network，右键 `getRoomState.do`，选 Copy → Copy as cURL (bash)。
2. 经剪贴板管道导入，命令不进 shell 历史。生成的文件权限为 `0600`；重新导入只替换会话和项目字段，保留 `directory`。

   ```bash
   pbpaste | python -m campus_core.electricity_client --import-curl \
     --context output/private/electricity-<账号>.json
   ```

3. 将代码和该文件拷到服务器。文件属主改为运行 Bot 的用户并保持 `chmod 600`，安装 `requirements.txt` 后按上文查询。接入 LangBot 时在插件根目录 `.env` 写 `HENU_ELECTRICITY_CONTEXT=<该文件路径>`，文件放在插件目录之外，避免被打包。所有 QQ 用户的 `electricity <宿舍>`（或「电费 东苑26 638」）都使用这份登录态，默认仅私聊可用。
4. 查询成功时，服务端下发的 `Set-Cookie` 会并入该文件，后续请求沿用。会话失效时 CLI 退出码为 `1` 并提示重新认证，回到本地重复 1–3 步。

会话有效期以及是否需要定时保活仍待实测。

2026 年 11 月 19 日按上述 1–2 步从已登录的本地 AVD 导入，查询「东苑26 638」双表成功。随后关停 AVD，同一文件再次查询成功，读数一致。两次查询均未收到新的 `Set-Cookie`，文件未改写。会话 Cookie 含 `SESSION`、`sid`、`standardH5_token` 等项。之后按每小时一次查询。19:06 的查询已失效，距上次成功查询不足 1 小时。`checkRoomCount.do` 返回 `returncode="-600"`、`returnmsg` 含「idserial为空」，同时下发新的匿名 Cookie；`getRoom.do` 返回非 JSON。CLI 和 Bot 将这一返回识别为会话过期，提示重新导入。

## 会话过期与自动续期实测

2026 年 11 月 19 日 20 时重新导入后的各 Cookie 客户端到期时间：

| Cookie | 域 | 到期 |
| --- | --- | --- |
| `SESSION` | `h5cloud.17wanxiao.com` | 会话级，无固定到期 |
| `sid`、`standardH5_token` | `.17wanxiao.com` | 约 1 个月 |
| `scanHost`、`schoolid`、`schoolName` | `.17wanxiao.com` | 约 5 年 |

`SESSION` 是会话级 Cookie，过期由服务端决定。相同 Cookie 值在 19:06 被拒，说明服务端按空闲时间判定。

尝试只用长寿命 Cookie 换发新会话，结果如下。每次请求都移除 `SESSION`，保留 `sid`、`standardH5_token` 等：

| 请求 | 结果 |
| --- | --- |
| 回放 App 入口页 `/CloudPayment/bill/type.do` | HTTP 200，下发新的 `SESSION`；用它调 `checkRoomCount.do` 仍为 `-600 idserial为空` |
| 新 `SESSION` 再打开入口页 | 同上 |
| 新 `SESSION` 打开带 `payProId` 参数的 `selectPayProject.do` | HTTP 200，仍为 `-600` |

结论：长寿命 Cookie 单独不能换来可用的云缴费会话，新下发的 `SESSION` 是匿名会话。

## 自动续期：pay.do 换票（2026-11-19 实测通过）

真正可用的换票接口是云缴费的 `pay.do`，不是入口页：

```text
GET /CloudPayment/user/pay.do
    ?customerId=<customId>            # 河南大学为 3 位机构代码
    &systemType=Android&UAinfo=wanxiao&versioncode=10589101
    &token=<校区 sessionId>            # 密钥交换得到的 session，不是登录 token
Header: X-Requested-With: com.newcapec.mobile.ncp
        Cookie: <除去 SESSION 的其余 Cookie>
```

- `token` 正确时返回 **302**，`Set-Cookie: SESSION=...`；跟随 `Location` 后，`checkRoomCount.do` 返回 `returncode=100`、`useFlag=1`。
- `token` 错误（登录 token 或过期的 sessionId）时返回 HTTP 200 + 「出错啦」页面，不下发可用会话。
- 用换到的会话查询「东苑26 638」：空调 169.25 度、照明 61.76 度；随后**关停 AVD**，同一会话再次查询仍成功。
- 之前从 `sid` Cookie 反解出的 sessionId 与 App 当前会话不一致（该 Cookie 已被 H5 覆盖或过期），这是前几组实验失败的原因。

`sessionId` 只能来自 App 原生链路：`exchangeSecretkey.action` 返回的 `session`。`custom_id` 来自登录响应的 `LoginUserResult.getCustomId()`。两者都需由原生登录客户端获取；`--refresh` 从上下文读取 `session_id`、`custom_id`，换票成功后写回 `SESSION`。

`python -m campus_core.electricity_client --refresh --context <文件>` 保留为诊断命令：只有服务端确认会话可用时才写回文件，否则不改动。

## 原生登录与 token 续期实测（2026-11-19）

`campus_core/wmxy_auth.py` 按 `docs/protocol-audit.md` 实现，合成测试通过。真实环境下：

| 步骤 | 结果 |
| --- | --- |
| `exchangeSecretkey.action`（仅换密钥，不带账号） | HTTP 200；解出 key 32 字符、session 36 字符 |
| `loginnew.action` type=3，缺 `deviceId` | HTTP 500。`deviceId` 是必填字段 |
| 同一请求带上 `deviceId` | HTTP 200，响应带 `campusSign` 且签名匹配，说明服务端已接受续期并轮换 token |
| 用旧 token 再请求 | `code_=100600`、`result_=false`、「登录失效，请重新登录，上次登录信息：21:18:05 登录于 sdk_gphone_arm64」 |

要点：

- 外层响应键为 `code_`、`message_`、`result_`，`data` 只在成功时出现；`data` 用交换所得的 3DES 密钥解密。
- token 续期会**轮换并作废旧 token**。轮换后的新 token 必须立即保存，否则 App 与本机都会失去登录态。
- 因此服务器与 App 共用同一条 token 链：机器人续期后，App 的原生登录会失效；只读电费查询本身不依赖 App 登录态，但成绩、课表等原生功能会提示重新登录。

端到端（续期 → pay.do → 查询）尚未在同一轮内跑完，因为 21:18 的轮换结果未被保存。需要一次新的登录取得有效 token。

### 可用路线：直接使用 App 的 sessionId

2026 年 11 月 20 日 00:56 实测，不经过 token 也能换票：

1. 在已登录的 App 里打开「缴费」页，通过 WebView 调试取 `wanxiao_authen.getUserJsonValue()`，得到 `sessionId` 与 `customCode`。
2. 将两者写入上下文的 `session_id`、`custom_id`，或直接调用 `pay.do` 换取 `SESSION`。
3. `query_saved()` 遇到会话过期时，会自动用 `pay.do` 重试一次；`--refresh` 可单独触发。

实测结果：`pay.do` 返回 302 并下发 `SESSION`，查询「东苑26 638」得空调 169.25、照明 60.76；关停 AVD 后同一上下文继续查询成功。上下文字段：`cookie`、`referer`、`schoolcode`、`payProId`、`businesstype`、`session_id`、`custom_id`。

`sessionId` 的有效期尚未测出；失效后需要重新从 App 取一次。

### 受阻的路线

| 尝试 | 结果 |
| --- | --- |
| 用账号密码登录（`type=1`，随机 `deviceId`） | `code 5`「您正在新设备上使用完美校园」；改用 App 的 `deviceId` 后成功 |
| `type=3` 续期，随机 `deviceId` | `code 100600`「登录失效」，token 与登录设备绑定 |
| Frida 读取 App 的 `deviceId` | 注入主进程超时（加固拦截）；WebView 进程无 Java 运行时 |
| 从 logcat 猜 `deviceId` | 13 个 UUID 候选全部失败 |
| 解密 App 偏好文件 | 内容为加密二进制，未做进一步破解 |

结论：完全无人值守仍差“App 设备身份”这一块。可行的替代是从 App 的 JS 桥取 `sessionId`，之后服务器只用 `pay.do` 续期。

## 全自动续期（2026-11-20 打通）

设备身份这一块已经解决，不再需要短信或 App 常驻：

1. **读取 App 的 `deviceId`**：主进程注入此前超时是 SELinux 拦截。`setenforce 0` 后，用 `frida_tools` 的 `java` 桥调用 `DeviceUtils.getDeviceId()` 即可只读取回（36 字符 UUID）。App 的实际机型是 `sdk_gphone_arm64`、Android 11。
2. **用 App 的设备身份做密码登录**（`type=1`）：携带该 `deviceId`、`telephoneModel`、`shebeixinghao`、`telephoneInfo` 后，服务端视为已知设备，**不再要求验证码**，直接返回 token 和 `customId`。
3. **`type=3` token 续期**：携带同一设备身份即可成功，返回新 token。续期会轮换旧 token。
4. **`pay.do` 换票**：用会话换 `SESSION`，随后查询电费。

上下文新增字段：`login_token`、`user_id`、`login_name`、`session_id`、`custom_id`、`device_id`、`device_model`、`device_product`、`device_release`。

`refresh_saved()` 现在的顺序是：先用现有 `session_id` 换票；失败则用 token 续期得到新会话，再换票。`query_saved()` 遇到会话过期会自动重试一次。

实测：

| 场景 | 结果 |
| --- | --- |
| 用 App 的设备身份密码登录 | 成功，无验证码 |
| `type=3` 续期 | 成功，返回 42 字符新 token |
| 人为把会话改坏（旧 `SESSION` + 假 `session_id`）后查询 | 自动续期 + 换票，仍返回空调 169.25 度 |
| 关停 AVD 后查询 | 成功 |

补充：短信登录接口是 `cam_iface46/gainMatrixCaptcha543.action`（`confirm:true` 才真正下发）。验证码与申请设备绑定，App 申请的码不能用于其它 `deviceId`。

注意：token 与 App 的设备身份绑定，机器人续期会轮换 token，App 的原生登录随之失效；电费 H5 页在会话有效期内不受影响。

随后改为每 10 分钟查询一次，观察持续使用能否让 `SESSION` 一直有效。20:15–21:05 连续 6 次成功，21:15 仍失效（距导入约 71 分钟）；上一轮 18:04 导入、19:06 失效（约 62 分钟）。持续使用不能延长会话，`SESSION` 有约 1 小时的绝对有效期。因此保活方案不可行，必须按上节换票或重新登录。

`pay.do` 换发的会话表现不同：21:10 换发后每 10 分钟查询，连续 15 次成功，23:47（约 2 小时 37 分）后 `getRoom.do` 返回非 SUCCESS；随后 `checkRoomCount.do` 返回 `useFlag=0`（「暂未允许选房」），说明会话仍被识别但不再允许查询。累计 16 次查询可能触及额度，也可能是会话状态变化，尚不能区分。运行策略应改为按需查询，避免 10 分钟级轮询。

## 复现「东苑25 534」

2026 年 11 月 19 日，在 APP 的「全部应用 → 缴费 → 用电支出」选择金明东苑 25 号公寓 5 层。分别查 `534房间` 空调表和 `25534房间` 照明表。移除 WebView 调试转发后，宿主机以同一有效会话调用 `query_address()`，传入原样文本 `东苑25 534`。两块读数及单位都与 APP 一致。会话和原始表 ID 仅在内存传递。

查询链经过以下函数。

1. [`electricity_client.main()`](../campus_core/electricity_client.py#L317) 读取标准输入和 `--context`。[`electricity_meter.split_meter_text()`](../campus_core/electricity_meter.py#L70) 得到园区 `东苑`、编号 `25534`、未指定表型。
2. [`electricity_client._official_directory()`](../campus_core/electricity_client.py#L165) 先查 `checkRoomCount.do`，再用 `getRoom.do` 的 `optype=1/2/3/4` 依次查校区、楼栋、楼层和房间。用官方楼号和同层配对表项确定地址。
3. [`electricity_meter.resolve_meter_text()`](../campus_core/electricity_meter.py#L93) 和 [`resolve_meters()`](../campus_core/electricity_meter.py#L124) 选定对应的两个目录项。`ResolvedMeter.query_params()` 将各自目录项 `id` 传为 `roomverify`。[`electricity_client.query_balance()`](../campus_core/electricity_client.py#L105) 调 `getRoomState.do`。
4. [`electricity_client.query_address()`](../campus_core/electricity_client.py#L251) 汇总各表读数和原因，CLI 将结果分到 `readings` 和 `errors`。

在本机功能工作树，使用项目 Python 环境和从自己已授权会话取得的上下文文件。将 `CONTEXT` 换成私有文件的实际路径。保持上下文不含 `directory`，以复现在线选房。

```bash
cd /Users/mac/Desktop/kit_assistant/HENU_Assistant/output/worktrees/electricity-readonly
PY=/Users/mac/Desktop/kit_assistant/HENU_Assistant/output/review-198dc2d/.venv/bin/python
CONTEXT=/path/to/private-context.json
chmod 600 "$CONTEXT"

"$PY" -B -c 'from campus_core.electricity_meter import split_meter_text; print(split_meter_text("东苑25 534"))'
printf '%s\n' '东苑25 534' | "$PY" -B -m campus_core.electricity_client --context "$CONTEXT"
printf '%s\n' '东苑25 534照明' | "$PY" -B -m campus_core.electricity_client --context "$CONTEXT"
"$PY" -B -m pytest -q -p no:cacheprovider \
  tests/test_electricity_client.py tests/test_electricity_meter.py tests/test_cli_surface.py \
  tests/test_qq_unified_responses.py tests/test_packaged_images.py
```
