# 2026-10-09 会员权益与一次性定时预约验证

本记录区分源码/测试、安装包和真实服务验收。费用口径：会员服务费用已包含，由站方承担，无需另付费或扣积分。雨课堂 B 的逐动作会员门仍未实现，当前新绑定、Token、登录、启用和配置修改失败关闭；不能将会员页或客户端测试当作 B 执行端完成。

## LangBot 2.0.6

调度源码提交 `c8de8a0adc36d200e6c31e27180181822e8b1eca` 的独立环境全量 pytest **293 passed**（12项 SDK 弃用警告）；新增28项调度测试。新调度模块/tests/main Ruff、两个模块约束 mypy、diff 检查通过；最终 Python Standards 和 Spec 复审均 approve、0发现。

覆盖公开 CLI/私聊/Tool、signed membership HTTP、公开学校 service 和 worker 持久接缝，均为合成响应：

- 本人原私聊后续确认、模型不能确认、同轮/跨用户/跨聊天/群聊拒绝、确认过期和多待办消歧。
- 执行时刻与使用日期/时段独立，东八区明确、未来30天有界、过去/错误时区拒绝。
- 加密任务与原账号指纹、并发确认/claim、防重、取消、会员撤销/故障、账号变更及错过窗口。
- 实际提交线程重新查资格；全局运行时锁忙时明确不调用学校，撤销/过期/停机竞争回归通过。
- 文件 Storage 的实际子进程创建、重新打开执行、学校入口已接收后进程退出，重启将 running 转 unknown，学校调用次数保持1。
- 负向或不明确学校回执、回执存储故障的持久 fence；不输出原始密码、Cookie、异常或 SDK 路径。
- initialize/shutdown/reinitialize、停止期间资格等待不得保存新任务、SDK 故障启动关闭。

父集成验收对上述源码包完成 pip check、lbp build，解包核对4个静态 JSON 及新增源码字节，并在解包产物运行 **55项目标测试通过**。该安装包验证对应 c8de8a0 的源码；之后的文案/Tool提示修订需要最终包重建或重新核对，不能把旧包字节核验当作新包验证。

## HENU-Kit

配套会员服务与页面修改在 Kit [`f8f5803e1c662abaa26f74ef54291b4229f6c3a9`](https://github.com/jry21223/HENU-Kit-DEV/commit/f8f5803e1c662abaa26f74ef54291b4229f6c3a9)（[PR #564](https://github.com/jry21223/HENU-Kit-DEV/pull/564)）已完成 **286项测试、3项页面测试**以及 Go 测试/vet、静态检查、race 验证；配套 GitHub CI 在该 SHA 全部通过：包括 [Core](https://github.com/jry21223/HENU-Kit-DEV/actions/runs/37829940880)、[Owner](https://github.com/jry21223/HENU-Kit-DEV/actions/runs/37829940983)、[Console](https://github.com/jry21223/HENU-Kit-DEV/actions/runs/37829940871)、[Release](https://github.com/jry21223/HENU-Kit-DEV/actions/runs/37829941056) 和 [Governance](https://github.com/jry21223/HENU-Kit-DEV/actions/runs/37829944212)。权威会员事实仍由 Account Portfolio 持有，Core 仅按签名QQ subject解析绑定并查询 owner，Bot不接受自报UUID或本地member开关。这里记录配套实现的验证结果，不代表两个仓库已部署到生产。

## 尚未执行的验收

未发真实供应商订单、未调用真实学校预约、未部署或停止生产执行。供应商真实 TLS/契约/服务映射、学校实际受理与就座、B逐动作会员约束与存量停止均须各自验收。源码、安装包和 GitHub CI通过只证明对应层，不作为真实履约或远端停止证明。

功能和部署边界见 [会员权益说明](../membership-benefits.md)、[定时预约说明](../library-schedule.md)。
