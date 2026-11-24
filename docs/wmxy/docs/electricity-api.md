# 完美校园用电支出只读接口

2026 年 11 月 18 日，用户切换网络后，在 `wmxy30` 中通过「全部应用 → 缴费 → 用电支出」查询账号历史记录。金明同一房间有独立的空调表和照明表。回到[交接首页](../README.md)可查看工作树及验收状态。

## 请求和响应

| 项目 | 实测 |
|---|---|
| 用电页面 | `GET https://h5cloud.17wanxiao.com:18443/CloudPayment/bill/selectPayProject.do` |
| 电量查询 | `GET https://h5cloud.17wanxiao.com:18443/CloudPayment/user/getRoomState.do` |
| 查询参数 | `businesstype`、`payProId`、`roomverify`、`schoolcode` |
| 请求头 | `Accept`、`Cookie`、`Referer`、`X-Requested-With` |
| 成功响应 | HTTP 200，JSON `returncode` 为字符串 `"100"` |
| 剩余电量 | `quantity` 为数值字符串，`quantityunit` 为单位字符串 |

`roomverify` 是所选表项的官方目录 `id`。金明东苑 26 号公寓双表取值已用 APP 目录实测。[取值与 Bot 解析](roomverify-resolution.md) 响应另含 `returnmsg`、`subCode`、`subMsg`、`canbuy`、`description`、`custparams`。单位样本为 `"度"`，`canbuy` 为 `"true"`。页面「缴费金额」是用户输入项，响应没有金额余额字段，原型将 `currency_balance` 表达为 `null`。

## 实测对照

| 表型 | APP | 宿主机 Python |
|---|---|---|
| 空调表 | 成功 | 两组读数与单位一致 |
| 照明表 | 两组成功 | 其中一组读数与单位一致 |

APP 三个条目均由 `GET getRoomState.do` 返回 HTTP 200 和 `returncode="100"`。金明空调表重新选择同一历史条目时会再次发送查询。其两次 Python 请求发送前已移除本次 WebView 调试转发，AVD 当时仍运行。

- [金明空调表脱敏 HAR](../captures/electricity-room-query.sanitized.har) · [APP 和 Python 对照](../evidence/electricity-webview-requests.jsonl)
- [金明照明表、明伦照明表及新版 CLI 对照](../evidence/electricity-other-rooms.jsonl)

## Python 用法

用工作目录内的 Python 3.13 环境。程序从标准输入接收 JSON，输出读数。输入包含 `room_ref`、`cookie`、`referer`。`room_ref` 这四个参数来自该账户在官方缴费页面对授权房间的请求，`cookie` 是该请求携带的有效云缴费会话，`referer` 是同源用电页面地址。真实会话数据仅通过内存交给进程。本轮交付文件只包含脱敏信息。

```bash
.venv/bin/python -B electricity_client.py < /path/to/private-input.json
.venv/bin/python -B tests/test_electricity_client.py
```

电量使用 `Decimal`。CLI 出错时退出码为 `1`，只在标准错误输出简短原因。调用 `query_balance()` 时可按 `ElectricityError` 子类处理输入无效、认证授权、连接超时或断连、协议异常。HTTP 401/403 提示重新认证或授权。带跳转地址的重定向会提示核对会话。非 JSON 或业务失败归为协议错误。以上错误分支用合成响应验证。

过期后通过官方流程重新认证。目录层级已实测。全量目录接入、自动续期和完全关停 AVD 后的实际查询仍待验证。
