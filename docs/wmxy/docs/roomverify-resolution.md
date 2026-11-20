# 电表定位与 Bot 输入

2026-11-18 通过官方「用电支出 → 缴费房间」实测金明东苑 26 号公寓 6 楼。楼号与房号用于定位页面目录项。剩余电量请求的 `roomverify` 是目录项的 `id`。

以下使用合成房号演示分表规则。

| 用户意图 | 目录显示名 |
|---|---|
| 101 空调电 | `101房间` |
| 101 照明电 | `26101房间` |

两次从目录选中后，`GET getRoomState.do` 的 `roomverify` 都与选中项的 `id` 完全相同。APP 描述的另一组示例是北苑 27 号公寓 107，空调选 `107房间`，照明选 `27107房间`。北苑示例用于规则测试，未查该房电量。[脱敏目录证据](../evidence/electricity-room-catalog.jsonl)

## 官方目录

打开选房时可见 `GET /CloudPayment/user/checkRoomCount.do`。随后 `GET /CloudPayment/user/getRoom.do` 按下表读取。每级响应为 HTTP 200、`code="SUCCESS"`、`roomlist: [{id, name, factorycode}]`。请求沿用页面的 `schoolcode`、`payProId`、`businesstype`。

| `optype` | 返回 | 已选上级 |
|---|---|---|
| `1` | 校区 | 全部为 `0` |
| `2` | 楼栋 | `areaid` |
| `3` | 楼层 | `areaid`、`buildid` |
| `4` | 房间与电表选项 | `areaid`、`buildid`、`levelid` |

实测路线的 `unitid` 始终为 `0`。目录读取保留官方页面校验和当前会话的访问边界。

## Bot 怎么传参

「查金明东苑 26 栋 638 的灯还剩多少电」提取为：

```json
{"campus":"金明","building":"东苑26栋","room":"638","meter":"lighting"}
```

- 自然语言层填写 `campus`、`building`、实际 `room`、可选 `floor`、`meter`。表型为 `aircon`、`lighting`、`both` 或 `null`。用户表达「只查照明」就保留该限制。
- 目录层按校区、楼栋、楼层取匹配项。省略楼层时，需要目录内同一地址的楼层候选唯一。多层同号补问楼层，缺校区或楼栋时补地址。
- 已确认分表的楼栋使用 APP 规则。空调找房号，照明找楼号拼房号。未指定表型时一起查空调和照明，分开返回。
- 解析器取匹配目录项的 `id`。`query_params()` 将它放入 `roomverify`，其余三个字段沿用缴费项目。把结果交给 `electricity_client.query_balance()`。
- 多楼号、合表和未知楼栋需要独立核对账表规则。当前原型只接已确认分表的楼栋目录。

[解析原型](../../campus_core/electricity_meter.py)接受官方楼层目录，含校区、楼栋、楼层和 `roomlist`。已用真实 6 楼目录核验两种表型均传回各自原始 `id`。Bot 按官方目录查询，消歧候选带 `--floor`。

```bash
.venv/bin/python -B -m pytest -q tests/test_electricity_meter.py
```
