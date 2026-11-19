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

官方 APP 已分别点选 `345房间` 和 `15345房间`，均显示剩余电量，页面传入查询的两个目录 ID 不同。CLI 合成验收覆盖原样输入、点名单表、地址歧义、单块和双块失败。服务端登录与真实 QQ 消息端到端仍待接入。

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
