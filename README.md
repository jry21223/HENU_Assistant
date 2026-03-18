# 河大校园助手（Langbot 插件版）

把原来的 `HENU_MCP` `mcp-server` 分支能力改造成了 Langbot 插件，保留课表、图书馆、研讨室、节次校准和系统状态能力，并新增了按 QQ 账号隔离保存账号配置的存储层。

## 功能

- 当前 QQ 账号绑定河大学号和密码：`setup_account`
- 同步并查询课表：`sync_schedule`、`schedule_query`
- 图书馆查询 / 预约 / 签到 / 取消：`library_query`、`library_reserve`、`library_auto_signin`、`library_cancel`
- 研讨室分组 / 查询 / 预约 / 签到 / 取消：`seminar_group`、`seminar_query`、`seminar_reserve`、`seminar_signin`、`seminar_cancel`
- 节次校准源：`set_calibration_source`
- 系统状态：`system_status`

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
- `components/tools/`：Langbot Tool 组件
- `henu_plugin/service.py`：工具分发和按 QQ 隔离的存储上下文
- `mcp_server.py` / `course_schedule.py` / `library_core/`：复用的原始业务逻辑

## 说明

- 账号和 Cookie 仍然只保存在本地
- 研讨室预约成功后会保存签到任务，但插件版不会启动后台自动签到线程
- 需要签到时，请再次调用 `seminar_signin(auto_scan=true)` 或 `seminar_signin(record_id=...)`
- 如果需要查看当前账号绑定和路径信息，调用 `system_status`
