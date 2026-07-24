# 河大校园助手 Langbot 插件版

将校园助手封装为 Langbot 插件，对外主要暴露统一 CLI 工具 `henu_cli`，并按当前 QQ 发送者隔离账号数据。

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/lbp run
```

生产环境必须在 `.env` 中配置稳定的 `HENU_MASTER_KEY`。容器重建或迁移时应继续使用同一密钥，否则已有密码密文无法解密。

构建插件包：

```bash
.venv/bin/lbp build
```

## CI 自动发布

`.github/workflows/release-lbp.yaml` 会在面向 `langbot-plugin` 的 PR 上执行编译、完整测试和 `lbp build`。推送与 `manifest.yaml` 版本一致的 `v<version>` tag 时，构建产物会上传到对应 GitHub Release。

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

### 敏感命令

账号绑定和校准请求必须在**私聊中直接发送**：

```text
account set --student-id <学号> --password '<密码>'
calibration set --data '<请求体>' --cookie '<Cookie>'
```

事件监听器会在调用模型前拦截这两类命令；密码、Cookie 和校准请求体不会进入模型消息或对话历史。群聊中的敏感命令会被拒绝。

### 外部写操作确认

图书馆或研讨室的预约、签到、取消使用两阶段确认：

1. 首次调用只生成预览和短期确认令牌，不会提交校园系统。
2. 核对日期、时间、地点和对象后，在下一条消息中发送工具返回的 `confirm <token>`。
3. 同一轮自动确认、令牌过期或参数变化都会被拒绝。

如果返回 `external_committed=true` 且 `storage_persisted=false`，说明校园系统已经提交成功、但本地 Storage 保存失败。此时不得重试，应先查询当前预约或记录进行反查。

## 时间语义

插件在每个请求的临时 system prompt 中注入 `[HENU_RUNTIME_CONTEXT_V2]`：

- 校园时间默认使用 `Asia/Shanghai`。
- 时间快照每请求重新生成，不使用五分钟运行时缓存。
- `schedule now/current` 不缓存；不会复用上一轮的“当前课程”。
- 时间、QQ、学号状态不再写入用户消息历史。
- 非法时区会明确标记请求时区和实际生效时区。

当前课程仍依赖课表中的周次数据质量；工具返回 `week_filter_applied=false` 时，不应把结果表述为已按教学周精确过滤。

## 数据隔离与持久化

插件优先使用群聊 `sender_id`，私聊可使用 `launcher_id`。群聊缺少发送者身份时会 fail closed，不会回退到群号或 `unknown` 用户。

Storage Adapter 使用以下策略：

- 每请求独立 staging 目录；
- 同一用户的 load/execute/save 由 per-user lock 串行化；
- 只保存实际变化的 JSON；
- Storage 读取异常不会被当作空数据；
- 保存前执行乐观冲突检查，拒绝覆盖其他请求的新版本；
- xiqueer Cookie 改为用户私有 Storage，不再写入 `shared:xiqueer`；
- 共享区只保存无个人凭据的节次时间和校准状态。

用户目录包含账号配置、IDS/CAS Cookie、业务 Cookie/Token、课表、研讨室签到任务和选课监控状态。插件版不会启动后台研讨室自动签到线程，需要时调用 `seminar signin --auto-scan` 并完成二次确认。

## CLI 结果与安全回显

QQ 载荷保持约 2200 字符预算。`reply_text`、计数和稳定 ID 优先保留，长列表会生成机器可读摘要。缓存读写使用深复制，投递层的截断和字段删除不会污染后续缓存结果。

图书馆实时区域接口返回 `source=live_empty` 时，工具会明确失败；不得据此猜测开放时间、区域或替代方案。密码、Cookie、Ticket、Token、确认令牌和校准 data 不应出现在普通日志中。

## 目录

- `manifest.yaml`：插件清单
- `main.py`：插件入口，加载 Hardened service
- `components/event_listener/`：身份捕获、时间注入、敏感命令直处理
- `components/cli_tools/`：统一 CLI、写操作确认和 QQ 安全输出
- `henu_plugin/hardened_service.py`：动态时间、缓存键和运行上限
- `henu_plugin/storage_adapter.py`：Storage 事务边界与用户隔离
- `henu_plugin/confirmation.py`：两阶段确认协议
- `henu_mcp/`、`campus_core/`：校园业务逻辑

## 登录策略

教务登录优先复用或登录 IDS 统一认证；仅在 IDS、Service 跳转、网络或验证码风控失败时尝试一次 xk Kingo 独立登录。Kingo 仅保证课表、选课状态和空教室等 xk 能力，不生成或覆盖其他服务使用的 CAS Cookie。验证码会返回 `captcha_required`，不会识别、绕过或循环重试。

## 说明

- 需要河南大学学生账号和可访问校园相关系统的网络环境。
- 项目仅供学习和个人使用，请遵守学校规定和各系统服务条款。
- 正式身份认证、成绩、选课、财务和审批事项以官方渠道为准。

## 许可证

MIT License，见 `LICENSE`。
