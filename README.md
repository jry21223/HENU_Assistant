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

## 常用命令

```text
help
account status
account set --student-id 20230001 --password 'secret'
schedule now
schedule day --date 2026-03-30
course status
course plan --excel ./courses.xlsx --class 25软工1
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

用户目录会保存账号配置、教务 Cookie、图书馆 Cookie、研讨室签到任务和课表抓取结果。

## 目录

- `manifest.yaml`：插件清单
- `main.py`：插件入口
- `components/cli_tools/`：对 LLM 暴露的统一 CLI Tool
- `components/tools/`：内部复用的旧 Tool 包装层
- `henu_plugin/service.py`：工具分发和存储上下文
- `henu_plugin/cli.py`：CLI 命令解析和帮助
- `mcp_server.py` / `course_schedule.py` / `campus_core/`：复用的业务逻辑

账号与 Cookie 只保存在本地。插件版不会启动后台研讨室自动签到线程，需要时调用 `seminar signin --auto-scan` 或指定 `record_id`。

## 智能选课

本版本包含通用智能选课模块 `campus_core.smart_course_selector`，三种接入形态复用同一套逻辑；同时保留 xk 只读状态查询入口：

- 从教务导出的 Excel 或清洗后的 JSON 读取课程选项。
- 按班级筛选班级对应专业课、专业选课班 / 专业公共课、全年级公共课。
- 根据偏好规划无冲突课表：早八偏好、集中上课天数、避免晚课、是否允许未排时间。
- 输出统一结构 `henu.smart_course_selection.v1`，其中 `plans[].selection_actions` 可作为后续自动选课提交器的 dry-run 输入。
- `course status` 会先走统一认证和 xk frame 菜单入口，只查询选课状态，不提交教务系统。
- `course monitor` 只读监控指定教学班余量，检测到余量变化时可发飞书提醒；不会点击选课、提交或退选。

MCP / OpenClaw 示例：

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
