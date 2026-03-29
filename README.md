# 河大校园助手（Langbot 插件版）

把原来仓库里的 `mcp-server` 分支能力改造成了 Langbot 插件，保留课表、图书馆、研讨室、节次校准和系统状态能力，并新增了按 QQ 账号隔离保存账号配置的存储层。

## 功能

- 统一 CLI 风格入口：`henu_cli`
- 内部仍复用原有账号、课表、图书馆、研讨室、节次校准和系统状态逻辑
- 帮助采用渐进式披露：先 `help`，再 `help <topic>`，再执行精确命令

常用命令示例：

- `help`
- `account status`
- `account set --student-id 20230001 --password 'secret'`
- `schedule now`
- `schedule day --date 2026-03-30`
- `library current`
- `seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4`

## 账号隔离

插件会按当前发消息的 QQ 账号隔离保存数据：

- 群聊优先使用 `sender_id`
- 私聊回退到 `launcher_id`
- 每个 QQ 账号都有独立目录：`data/users/<qq>/`

每个账号目录下会保存：

- `profile.json`：学号、密码、默认图书馆参数、研讨室分组等
- `xk_cookies.json`：教务系统 Cookie
- `library_cookies.json`：图书馆 Cookie
- `seminar_signin_tasks.json`：研讨室签到任务
- `output/`：该账号自己的课表抓取结果

共享数据放在 `data/shared/`：

- `period_time_config.json`
- `period_time_calibration_state.json`
- `xiqueer_period_time_request.json`

## 安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 本地调试

```bash
cp .env.example .env
.venv/bin/lbp run
```

## 构建插件包

```bash
.venv/bin/lbp build
```

## 目录说明

- `manifest.yaml`：Langbot 插件清单
- `main.py`：插件入口
- `components/cli_tools/`：对 LLM 暴露的统一 CLI Tool
- `components/tools/`：内部复用的旧 Tool 包装层，不再直接暴露
- `henu_plugin/service.py`：工具分发和按 QQ 隔离的存储上下文
- `henu_plugin/cli.py`：CLI 命令解析、帮助与下一步建议
- `mcp_server.py` / `course_schedule.py` / `library_core/`：复用的原始业务逻辑

## 说明

- 账号和 Cookie 仍然只保存在本地
- 研讨室预约成功后会保存签到任务，但插件版不会启动后台自动签到线程
- 需要签到时，请再次调用 `seminar_signin(auto_scan=true)` 或 `seminar_signin(record_id=...)`
- 如果需要查看当前账号绑定和路径信息，调用 `system_status`
