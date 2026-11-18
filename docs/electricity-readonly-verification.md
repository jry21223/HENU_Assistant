# 完美校园剩余电量只读实测

2026 年 11 月 18 日，使用 `feat/electricity-readonly` 的 `2ab5bbf` 验证。通过官方 App 正常登录后的缴费房间目录，在金明校区北苑 27 号公寓选取一间账号历史记录外的宿舍。App 对两块表各返回一条实时成功响应。每次取得响应后移除本次 WebView 调试转发，再由 macOS 上的 Python 3.13.14 执行 `python -m campus_core.electricity_client`，查询同一块表。

| 表型 | App 查询状态 | Python 对照 |
| --- | --- | --- |
| 空调 | HTTP 200，返回剩余电量 | 读数和单位一致，单位为度 |
| 照明 | HTTP 200，返回剩余电量 | 读数和单位一致，单位为度 |

25 项电费合成测试通过。连同 CLI、QQ 路由和打包回归共 79 项通过。

证据保存在原工作区 `output/wmxy-electricity/evidence/git-live-2ab5bbf.jsonl`。归档保留状态和对照结论，会话头和原始表 ID 只在对照过程中通过内存传递。

本次属于正常会话下的单宿舍双表样本，查询时 AVD 仍运行。QQ Bot 入口、会话续期和关停 AVD 后的查询待验。Bot 接入时需处理 HTTPX INFO 日志中的查询参数。
