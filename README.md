<p align="center">
  <img src="./assets/readme/hero.png" width="100%" alt="河大校园助手 Langbot 插件：QQ 接入，按发送者隔离账号">
</p>

Langbot 插件形态。对外主要是统一 CLI 工具 `henu_cli`，并按 **QQ 发送者** 隔离账号数据。

版本见 `manifest.yaml`。其它形态：[`main`](https://github.com/jry21223/HENU_Assistant) · [`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server) · [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)

---

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/lbp run
```

生产环境必须在 `.env` 配置稳定的 **`HENU_MASTER_KEY`**。容器重建 / 迁移要继续用同一密钥，否则已有密码密文无法解密。

```bash
.venv/bin/lbp build
```

## CI 发布

`.github/workflows/release-lbp.yaml`：面向 `langbot-plugin` 的 PR 会编译、完整测试并 `lbp build`。  
推送与 `manifest.yaml` 版本一致的 `v<version>` tag 时，产物上传到 GitHub Release。

```bash
git tag v2.0.2
git push origin v2.0.2
```

---

## 常用命令

```text
help
account status
schedule now
schedule day --date YYYY-MM-DD
course status
course plan --excel ./courses.xlsx --class 25软工1
empty_classroom query --week 1 --day-of-week 1 --period 1 --building-text 十号楼
resource search 十号楼101
library current
library locations --date YYYY-MM-DD
library seats --location "<区域>" --date YYYY-MM-DD --time 08:00
seminar rooms --date YYYY-MM-DD --start 14:00 --end 16:00 --members 4
```

### 敏感命令（仅私聊直发）

账号绑定与校准必须在**私聊中直接发送**：

```text
account set --student-id <学号> --password '<密码>'
calibration set --data '<请求体>' --cookie '<Cookie>'
```

事件监听器会在进模型前拦截这两类命令；密码、Cookie、校准请求体不会进入模型消息或历史。**群聊中的敏感命令会被拒绝。**

### 外部写操作：两阶段确认

图书馆 / 研讨室的预约、签到、取消：

1. 首次调用只生成预览 + 短期确认令牌，**不**提交校园系统。  
2. 核对后，下一条消息发送工具返回的 `confirm <token>`。  
3. 同一轮自动确认、令牌过期、参数变化 → 拒绝。

若 `external_committed=true` 且 `storage_persisted=false`：校园已提交、本地 Storage 失败——**不得重试**，应先查询当前预约/记录反查。

---

## 时间语义

每个请求的临时 system prompt 注入 `[HENU_RUNTIME_CONTEXT_V2]`：

- 校园时间默认 `Asia/Shanghai`。  
- 时间快照每请求重新生成，不用五分钟缓存。  
- `schedule now/current` 不缓存。  
- 时间 / QQ / 学号状态不写进用户消息历史。  
- 非法时区会标明请求时区与实际生效时区。

`week_filter_applied=false` 时，不要把结果说成已按教学周精确过滤。

## 数据隔离

| 规则 | 行为 |
| --- | --- |
| 群聊 | 优先 `sender_id` |
| 私聊 | 可用 `launcher_id` |
| 缺发送者 | fail closed，不回退群号 / `unknown` |

Storage Adapter：

- 每请求独立 staging；同一用户 load/execute/save 由 per-user lock 串行。  
- 只保存实际变化的 JSON；读取异常不当作空数据。  
- 保存前乐观冲突检查。  
- xiqueer Cookie 为用户私有；共享区只放无个人凭据的节次时间与校准状态。

用户目录：账号、IDS/CAS Cookie、业务 Cookie/Token、课表、研讨室签到任务、选课监控。  
插件版**不**启动后台研讨室自动签到线程；需要时 `seminar signin --auto-scan` 并完成二次确认。

## CLI 输出与安全

- QQ 载荷约 2200 字符预算；长列表生成机器可读摘要。  
- 缓存深复制，截断不影响后续结果。  
- `source=live_empty` 明确失败，禁止猜开放时间/区域。  
- 密码、Cookie、Ticket、Token、确认令牌、校准 data 不进普通日志。

## 登录

IDS 优先；失败时只一次 Kingo。Kingo 主要保 xk 能力，不生成/覆盖其他服务 CAS Cookie。验证码 → `captcha_required`。

## 目录

```text
manifest.yaml
main.py
components/event_listener/   # 身份、时间注入、敏感命令
components/cli_tools/        # CLI、写确认、QQ 安全输出
henu_plugin/hardened_service.py
henu_plugin/storage_adapter.py
henu_plugin/confirmation.py
henu_mcp/  campus_core/
tests/
```

## 边界

- 需要河大学生账号与可访问校园系统的网络。  
- 仅供学习与个人使用。  
- 正式认证 / 成绩 / 选课 / 财务以官方渠道为准。

## License

[MIT](LICENSE)
