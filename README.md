# 河大校园助手 Langbot 插件版

将校园助手封装为 Langbot 插件，对外主要暴露统一 CLI 工具 `henu_cli`，并按 QQ 账号隔离保存数据。

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
.venv/bin/lbp run
```

构建插件包：

```bash
.venv/bin/lbp build
```

## CI 自动发布

当前仓库已配置 GitHub Actions（`release-lbp.yaml`）：

- 当你推送形如 `v<version>` 的 tag 时，Actions 会自动执行 `lbp build`。
- 打包后的 `dist/*.lbpkg` 会自动上传到对应 tag 的 GitHub Release。

发布示例：

```bash
git tag v2.0.3
git push origin v2.0.3
```

## 常用命令

```text
help
account status
account set --student-id 20230001 --password 'secret'
schedule now
schedule day --date 2026-03-30
course status
course plan --excel ./courses.xlsx --class 25软工1
empty_classroom query --week 1 --day-of-week 1 --period 1 --building-text 十号楼
empty_classroom query --view occupancy --classroom-text 十号楼101
resource search 十号楼101
library current
library seats --location "<区域>" --date 2026-03-30 --time 08:00
library reserve --location "<区域>" --seat-no "<座位号>"
seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4
seminar signin --auto-scan
```

## 数据隔离

插件按当前发消息的 QQ 身份选择存储目录：

- 群聊优先使用 `sender_id`
- 私聊回退到 `launcher_id`
- 用户数据位于 `data/users/<qq>/`
- 共享节次校准文件位于 `data/shared/`

用户目录会保存账号配置、该 QQ 用户的 IDS CAS Cookie jar（`CASTGC`/`TGC` 等）、业务 Cookie/Token、研讨室签到任务、选课监控配置和课表抓取结果。教务登录优先复用或登录 IDS；IDS、Service 跳转、网络或验证码风控失败后，仅自动尝试一次 xk Kingo 独立登录，不识别验证码或循环重试。

IDS 模式可供课表、选课、空教室、图书馆、研讨室和河宝复用。Kingo 降级模式只保证课表、选课状态和空教室等 xk 能力，不生成或覆盖 CAS Jar；其他服务仍需完成 IDS 登录。账号初始化、登录检查、课表同步和系统状态通过 `auth` 返回认证模式、降级状态、错误码和能力警告。

共享目录只保存公共校园配置和缓存，例如节次时间、节次校准状态和空教室公共请求参数；`shared:*` Storage key 不保存密码、`CASTGC`、业务 Token 或个人 Cookie。

## CLI 结果与安全回显

图书馆先调用 `library locations`，后续只使用返回的 `location` 或 `area_id`；座位预约只使用返回的座位号。实时区域结果保留 `source`、`is_live`、`total`、`returned_count`、`truncated` 和完整的紧凑 `location_options`；座位结果保留 `area`、`target_date`、`time_window`、`total_count`、`available_count`、`status_counts` 和有限的 `seat_options`。QQ 载荷仍保持约 2200 字符安全上限，但 `reply_text`、`llm_hint`、计数和关键 ID 优先保留。

当区域接口返回 `source=live_empty` 时，工具会返回 `success=false` 的明确实时空数据；不得据此猜测图书馆开放时间、区域或推荐空教室/现场方案。静态映射若返回，只标记为非实时参考。`account set`、校准等命令的密码、Cookie、Ticket、Token 和 `--data` 值会统一显示为 `<redacted>`；公开结果不会包含 `_effective_params`。真实写操作仍只以 `success=true` 为准。

## 目录

- `manifest.yaml`：插件清单
- `main.py`：插件入口
- `components/cli_tools/`：对 LLM 暴露的统一 CLI Tool
- `henu_plugin/service.py`：工具分发和存储上下文
- `henu_plugin/cli.py`：CLI 命令解析和帮助
- `mcp_server.py`：兼容 MCP/Agent 公共工具函数的薄门面
- `henu_mcp/`：复用的校园业务逻辑和运行时路径切换
- `campus_core/`：底层校园集成模块

账号与 Cookie 只保存在本地。插件版不会启动后台研讨室自动签到线程，需要时调用 `seminar signin --auto-scan` 或指定 `record_id`。

## 智能选课

本版本包含通用智能选课模块 `campus_core.smart_course_selector`，三种接入形态复用同一套逻辑；同时保留 xk 只读状态查询入口：

- 从教务导出的 Excel 或清洗后的 JSON 读取课程选项。
- 按班级筛选班级对应专业课、专业选课班 / 专业公共课、全年级公共课。
- 根据偏好规划无冲突课表：早八偏好、集中上课天数、避免晚课、是否允许未排时间。
- 输出统一结构 `henu.smart_course_selection.v1`，其中 `plans[].selection_actions` 可作为后续自动选课提交器的 dry-run 输入。
- `course status` 会先走统一认证和 xk frame 菜单入口，只查询选课状态，不提交教务系统。
- `course monitor` 只读监控指定教学班余量，检测到余量变化时可发飞书提醒；不会点击选课、提交或退选。

MCP / Agent Skill 示例：

```bash
python3 henu_cli.py smart_course_selection --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
```

Langbot CLI 示例：

```text
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
course status
course monitor config --config-json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
course monitor once
```
