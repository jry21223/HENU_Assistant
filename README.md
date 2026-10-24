<p align="center">
  <img src="./assets/readme/hero.png" width="100%" alt="河大校园助手 Langbot 插件：QQ 接入，按发送者隔离账号">
</p>

Langbot 插件形态。对外主要是统一 CLI 工具 `henu_cli`，并按 **QQ 发送者** 隔离账号数据。

版本见 `manifest.yaml`（当前 **2.0.4**）。其它形态：[`main`](https://github.com/jry21223/HENU_Assistant) · [`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server) · [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)

> **命令方言**：本插件使用空格风格 CLI（`schedule now`、`library seats`、`--student-id`）。  
> Agent Skill / MCP 使用下划线子命令（`schedule_query`、`library_query`、`--student_id`）。**不要互相粘贴。**

---

## 安装

### A. 终端用户（LangBot 装插件包）

前置：已部署可用的 [LangBot](https://docs.langbot.app)，并接好消息适配器（如 NapCat / OneBot）。本仓库**不是**独立 QQ 机器人。

1. 打开 [Releases](https://github.com/jry21223/HENU_Assistant/releases)，下载与版本号一致的 `*.lbpkg`（由 `langbot-plugin` 分支 CI 构建）。  
2. 在 LangBot 中安装该插件包（插件管理 / 本地导入，以你使用的 LangBot 版本文档为准）。  
3. 为运行环境配置稳定的 **`HENU_MASTER_KEY`**（长随机串；容器重建、迁移必须沿用同一密钥，否则已有密码密文无法解密）。  
4. 私聊机器人绑定账号（见下方「敏感命令」），再查询课表 / 图书馆等。

官方插件开发与调试说明可参考：<https://docs.langbot.app/en/plugin/dev/tutor.html>

### B. 开发者（本仓库调试 / 打包）

```bash
git clone -b langbot-plugin https://github.com/jry21223/HENU_Assistant.git
cd HENU_Assistant

# 推荐一键安装（venv + 依赖 + lbp + .env）
chmod +x install.sh && ./install.sh

# 或手动：
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install lbp          # 不在 requirements.txt：开发/打包 CLI
cp -n .env.example .env            # 已有 .env 时请勿覆盖
# 编辑 .env：生产必须设置 HENU_MASTER_KEY=

.venv/bin/lbp run                  # 连接本机 LangBot 调试 runtime
.venv/bin/lbp build                # 产出 dist/*.lbpkg
```

`.env` 关键字段：

| 变量 | 说明 |
| --- | --- |
| `DEBUG_RUNTIME_WS_URL` | 本地调试 WebSocket（默认 `ws://127.0.0.1:5401/debug/ws`） |
| `PLUGIN_DEBUG_KEY` | 调试鉴权；空表示关闭 |
| **`HENU_MASTER_KEY`** | 凭据加密主密钥；**生产必填且保持稳定** |

## CI 发布

`.github/workflows/release-lbp.yaml`：对 `langbot-plugin` 的 tag 会安装依赖与 `lbp`、执行 `lbp build`，并把 `dist/*.lbpkg` 上传到 GitHub Release。

```bash
# tag 必须与 manifest.yaml 的 version 一致
git tag v2.0.4
git push origin v2.0.4
```

---

## 常用命令

工具只收一条字符串 `command`。不确定时先 `help` 或 `help <topic>`。

```text
help
help library
account status
schedule now
schedule day --date YYYY-MM-DD
course status
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
course monitor config --config-json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
course monitor once
empty_classroom query --week 1 --day-of-week 1 --period 1 --building-text 十号楼
empty_classroom query --view occupancy --classroom-text 十号楼101
resource search 十号楼101
library current
library locations --date YYYY-MM-DD
library seats --location "<区域>" --date YYYY-MM-DD --time 08:00
library reserve --location "<区域>" --seat-no "<座位号>" --date YYYY-MM-DD --time 08:00 --end-time 12:00
seminar rooms --date YYYY-MM-DD --start 14:00 --end 16:00 --members 4
seminar signin --auto-scan
yuketang status
yuketang courses
yuketang exam list --course 数据结构
yuketang exam whitelist add 数据结构
yuketang lesson set --auto-enter on --auto-answer on --llm on --subjective off --enter-delay 30
yuketang lesson whitelist add <课程名>
yuketang exam set --master on
确认
```

### 敏感命令（仅私聊直发）

账号绑定与校准必须在**私聊中由用户直接发送**（不要让模型用 Tool 拼密码）：

```text
account set --student-id <学号> --password '<密码>'
calibration set --data '<请求体>' --cookie '<Cookie>'
yuketang exam set --x-access-token '<考试系统令牌>'
yuketang account set --account <手机号> --password '<密码>'
确认
yuketang login
yuketang logout
```

事件监听器会在进模型前拦截这些命令；密码、Cookie、校准请求体、考试令牌与登录动作不会进入模型消息或历史。**群聊中的敏感命令会被拒绝。**此外私聊中的自然语言短语“登录雨课堂 / 帮我重新登录一下雨课堂 / 退出登录雨课堂”等（整句、指名雨课堂）会在进模型前直接执行对应登录/退出动作。

### 雨课堂（yuketang）配置

绑定前请先在雨课堂设置密码，再使用雨课堂绑定手机号和该密码；不要使用学校 IDS 密码。私聊发送绑定命令并按提示确认后，插件会后台验证并回复结果。

按 QQ 隔离的雨课堂监听配置。开关是**总闸**，黑白名单决定**作用范围**（黑名单优先）；exam 白名单为空等于考试功能整体关闭。课堂侧自动进班、自动答题、大模型**默认开**（`--auto-enter off` / `--auto-answer off` / `--llm off` 关闭；`--auto-enter off` 后开课不进班不答题，考试监听不受影响）。课件 PDF / PPT 进度 / 试卷文件推送（ppt/si/paper）已整体停用，不可配置。`--enter-delay` 为开班后进班延时（0-600 秒，默认 0），与 `start-time`（钟表几点前不进班）相互独立；主观题默认不作答，`--subjective on` 开启。`yuketang logout` 退出登录：清除守护进程侧 cookie 并停用监听（绑定与配置保留，自动续期不会把登录续回来；恢复用 `yuketang enable` + `yuketang login`）。数据存于该 QQ 的个人 Storage，守护进程每扫描周期重读，无需重启。

考试白名单后台匹配的是**班级标签**（课程列表的 `name`），不是课程名（`course.name`）。新增时输入课程名或班级标签，插件在真实用户确认后用当前登录态核验并保存唯一班级标签，例如“数据结构”解析为“010 班”。先执行 `yuketang courses` 查看对应关系；含空格的班级标签用引号：`yuketang exam whitelist add "010 班"`。同名多班、重复标签、未登录或查询失败均不新增。移除使用配置中已保存的标签（如 `yuketang exam whitelist remove "010 班"`），移除/清空在桥不可达时仍可操作。现有错误条目不会自动迁移或启用，需要用户重新新增正确范围并移除旧条目。

`yuketang exam list --course 数据结构` 只读查询本班级的测验与考试，并分别展示类型，包括尚未开始作答的活动。测验使用自己的编号与班级活动元数据，不会套用考试封面；考试封面的班级、标题或资源编号冲突时停止查询。封面未返回资源编号时，显示“封面关联未核实”，仅使用活动列表的时间，不用封面覆盖。无需创建考试令牌，不开始考试、不读取题目、不提交答案。空列表与查询错误分别报告。查询结果只证明当前元数据可读，不能替代远端自动通知验收；桥的 `recent_events` 为空也不能证明没有考试。

考试使用流程：私聊完成绑定、登录、考试白名单及答题设置并确认启用，再由本人在雨课堂官方页面开始考试。后台接管考试会话时，原页面可能显示“其他设备登录”；作答期间避免反复重登。后台保存答案后，本人重新进入检查已答数量并正式交卷。插件没有自动开始考试或正式交卷命令，答案保存、预计耗时和 Bridge 成功码均不能替代正式交卷结果；答题正确率不保证满分。

同一雨课堂账号请只启用一个聊天身份，避免多身份重复作答和答案覆盖。现有 Bridge 会自动处理登录、配置接收和执行器注册，日常使用不需要直接操作后台服务器；当前接口没有提供已核验的账号排他协议或题级完成/正式交卷回执，不能声称已经全局防重。

`yuketang status` 分别报告本地监听配置、Bridge 配置一致性、当前身份注册和执行器心跳。只有配置一致、执行器已接入、身份注册符合开关且心跳在 75 秒内时确认注册状态；缺字段、过期心跳或注册不符明确报告无法确认。它不核验本场考试是否答完或交卷。雨课堂命令拒绝未知参数、重复参数、缺少值和多余参数；含空格的参数值请加引号。

#### 守护进程桥（可选）

雨课堂守护进程可部署在另一台服务器（B）。在插件根目录 `.env` 配置 `HENU_BRIDGE_URL` / `HENU_BRIDGE_SECRET`；支持环境变量覆盖，但不要假定插件子进程继承容器环境。仅允许 HTTPS，额外使用 AES-256-GCM 信封，禁止重定向，客户端拒绝窗口内重复响应。桥端是可信的凭据接收方，不是对其隐藏密码的存储服务。

账号密码与 Token 在 Storage 中加密保存；敏感绑定先生成加密的待确认操作，用户在下一条私聊直接回复 `确认` 后才保存并传递凭据，账号绑定确认后启动密码登录。登录先回执、后台等待并回复最终结果，不在用户 Storage 事务内长轮询。没有证据时不会声称自动续期成功；停用不是解绑或凭据删除。

启用、域名、课堂/考试设置及课程范围修改由用户在下一条消息直接回复 `确认`，整体 `yuketang disable` 直接处理。功能面向所有用户开放，总开关仍默认关闭；课堂自动进班、自动答题、大模型子开关默认开启，存量显式 off 保持不变。先提交 LangBot Storage，再读取已提交的最新配置推桥；分别报告本地保存、等待同步和桥端接收，桥端接收不等于任务已经执行。断线后下一次 `yuketang status` 重试同步；关闭未同步时明确提示远端可能仍在运行。`daemon_wired=false` 时提示桥端执行器未接入。

私聊可直接说“登录雨课堂”“帮我重新登录一下雨课堂”“退出登录雨课堂”；重新登录会强制验证，普通登录在有效 Cookie 剩余超过一小时的时候复用。账号密码变更确认后强制验证新凭据，不复用旧账号 Cookie。退出不删除绑定，并先持久化停用后通知桥端；存在进行中的登录时等待，结果不明时不宣称已完成退出。请求前的加密在途记录在重启后仍有效；若网络丢失响应而无法确认会话，需要维护者核实桥端后处理，不能盲目重试。

正式验收需要真实用户私聊绑定、收到最终结果、状态查询、配置确认及停止同步。仅 HTTP 200 或桥鉴权通过不构成完整验收。请求级响应关联与跨重启重放保护需桥端配套，当前不单方面更改 v1 协议。

### 外部写操作：两阶段确认

确认只接受用户真实消息，模型不得代确认。待确认操作按用户和聊天绑定、5 分钟过期；若同时有雨课堂凭据与其他操作，插件会要求回复“确认绑定”或“确认操作”消歧。旧的 `confirm <token>` / `yuketang confirm <token>` 仅保留兼容，不再作为默认提示。升级前创建的旧记录可重新发起后使用快捷确认。

图书馆 / 研讨室的预约、签到、取消：

1. 首次调用只生成预览 + 短期确认令牌，**不**提交校园系统。  
2. 用户核对后，下一条消息直接回复 `确认`，无需复制令牌。
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

QQ 功能结果采用统一 Markdown 卡片：状态、时间位置、关键条目和下一步。长列表在截断模型载荷前保存为当前会话的分页卡片，回复“下一页”“上一页”或“卡片第 N 页”查看，十分钟过期。个人课表、绑定、预约记录、河宝、雨课堂和美食草稿仅私聊展示。长美食草稿必须全部预览页成功送达，原确认发布流程才接受确认。

原生 QQ `markdown.content` 的宿主适配、文字回退和发布边界见 [安装说明](ops/qq-cards-install.md)；准确功能/状态覆盖及未实现项见 [覆盖矩阵](docs/qq-cards-coverage.md)。不依赖旧问卷，不启用未核验能力的按钮。频道或未适配宿主仍使用可读文字。

- QQ 载荷约 2200 字符预算；长列表生成机器可读摘要。  
- 缓存深复制，截断不影响后续结果。  
- `source=live_empty` 明确失败，禁止猜开放时间/区域。  
- 密码、Cookie、Ticket、Token、确认令牌、校准 data 不进普通日志。  
- 工具返回 `reply_text` 时，最终回复优先复述/压缩 `reply_text`，不要整段 JSON 甩给用户。

## 登录

IDS 优先；仅在提交凭据前 IDS 不可用时降级一次 Kingo。验证码、锁号、密码错误、限流或提交结果未知时停止重试。Kingo 主要保 xk 能力，不生成/覆盖其他服务 CAS Cookie。

## 目录

```text
manifest.yaml
main.py
install.sh                  # 开发环境：venv + 依赖 + lbp
components/event_listener/  # 身份、时间注入、敏感命令
components/cli_tools/       # CLI、写确认、QQ 安全输出
henu_plugin/hardened_service.py
henu_plugin/storage_adapter.py
henu_plugin/confirmation.py
henu_mcp/  campus_core/
tests/
```

## 测试

```bash
.venv/bin/pytest
```

## 边界

- 需要河大学生账号与可访问校园系统的网络。  
- 仅供学习与个人使用。  
- 正式认证 / 成绩 / 选课 / 财务以官方渠道为准。

## License

[MIT](LICENSE)

### 美食投稿的自动 Kit 身份

美食统一通过插件 `henu_food`，支持投稿、我的投稿、公开列表、详情和场所查询。
用户只提供投稿内容，系统每次优先读取当前 QQ 的 Kit 绑定；未绑定或暂时无法
读取时仍可用独立游客身份投稿，并提示私聊发送“绑定 HENU KIT”。学校 IDS
登录不等于 Kit 绑定。游客记录不会自动转移到后来绑定的账号。

配置 `.env` 的 `HENU_FOOD_MCP_ACCESS_TOKEN` 和独立、稳定的
`HENU_FOOD_CONTEXT_SECRET`（至少 32 字节），后者须与 Kit
`FOOD_MCP_ACTOR_SECRET` 匹配。共享 QQ 部署启用 Kit
`FOOD_MCP_REQUIRE_ACTOR_CONTEXT=true`。在 QQ Pipeline 排除原独立 `food`
MCP 连接器，保留其他 MCP，启用插件 Tool；共享 MCP Session 不能用于用户身份。
账号不进入模型参数。`create` 只生成或更新未发布草稿，用户可继续补充价格、营业时间、推荐菜和聊天图片。
核对最新完整预览后，在后续独立消息单独发送“确认发布”才提交；发布后公开且暂不支持修改。
重复确认不会再次投稿；结果不确定时先查我的投稿。发送“取消美食草稿”可放弃未发布草稿。
询问“你能收到图片吗？”会从实际草稿回复图片数量，不会让已有预览失效；确认消息本身也不会被当作内容补充。

图片从原始聊天消息读取，无需用户或模型提供 base64。支持 JPG/PNG/WebP，最多 6 张、每张不超过 2MiB；
QQ 官方适配器单条消息当前只提供第一张附件，多张可分次发送。无法读取或不合规的图片会阻止发布并提示处理。
草稿有效期 10 分钟，按 Bot、发送者与会话隔离，存储在插件目录的私有 `.food-drafts/drafts.sqlite3`，
单文件权限为 600；可用 `HENU_FOOD_DRAFT_DB` 指定持久化路径。该目录不可打包或发布，备份需按私有数据处理。

## 图书馆区域与座位偏好

`preferences get`、`preferences set --library-location "第一自习室" --library-seat-no "001"`、`preferences clear --library-seat-no`。偏好不需要重新提供密码，仅保存区域和座位；清空必须明确字段。换区域会清除旧座位，除非同时提供新座位。偏好不会发起或授权预约，日期、时间及确认要求仍然适用。

保存账号时会通过全新会话核对学号。验证失败不替换旧绑定；不验证保存会标记 `verified=false` 并跳过节次校准。成功换绑后旧会话和课表缓存失效。

### 私聊账号绑定保护

直接私聊发送 `account set --student-id <学号> --password '<密码>'`；请替换示例占位符。凭据命令会在模型处理前拦截，先确认接收，再单独回报结果；群聊、身份缺失或格式错误不执行学校登录。`--no-verify-login` 仅保存配置，不能证明学校认证成功。

同一 QQ 或学号在 24 小时内累计 3 次高风险/结果未知的绑定尝试后，插件会返回剩余等待时间。登录保护依赖稳定的 `HENU_MASTER_KEY` 与可写的 LangBot Storage；只支持当前单插件进程的串行保护。账号状态卡片显示最近一次绑定尝试，不能据此认定当前学校会话仍有效。

### QQ 菜单与使用入口

私聊菜单分为「我的校园」「找自习室」「账号与帮助」，同时提供私聊和群聊指令面板。
菜单点击会把需求填入输入框，由用户发送；个人状态和课表仍在私聊查询。
可直接发送「绑定账号」「使用帮助」「找自习室」「课表推送」查看简短引导。
校园账号与 HENU KIT 网站账号分别绑定。私聊发送「开启课前提醒」可在上课前 15 分钟收到提醒，连堂课只提醒一次；发送「关闭课前提醒」可关闭。

提醒检查只读取个人保存的课表，按校历教学周和保存的节次时间判断。学校课表每个校园账号最多每 24 小时自动同步一次，同一账号绑定多个 QQ 身份共用该限制；成功后保存课表与 Cookie。认证失败、冻结、响应结果未知或保存失败时暂停自动同步，防止后台重复登录。手动成功同步一份更新的课表后恢复自动同步；暂停期间仍可根据已保存课表提醒。可发送「测试课前提醒」检查 QQ 通知通道。

操作者可用 `ops/configure-qq-navigation.py --backup-dir <新目录>` 读取并保存预览，
确认后用新的备份目录并加 `--apply` 更新。服务端环境需要 `QQ_APP_ID`、`QQ_CLIENT_SECRET`，
不得将凭据放入菜单或提交 Git。脚本备份、检测并发变更、幂等更新并回读，保留其他面板。
详见 [范围与发布保护](docs/qq-ux-panels-spec.md) 和 [本地卡片预览](docs/previews/navigation.html)。
