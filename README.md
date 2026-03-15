# 河大校园助手（MCP 服务器版）

河南大学校园助手的 MCP 实现，集成了课表查询、图书馆预约、研讨室预约、节次校准和系统状态能力。

## 功能概览

- 课表：同步课表、查询当前课程/下一节课、查看本周有效课表
- 图书馆：查询区域、预约座位、查询当前预约、自动签到、查询记录、取消预约
- 研讨室：保存 group、查询筛选项/房间/详情、预约、取消、手动签到、自动签到任务
- 运维：账号本地保存、登录会话复用、节次时间自动校准

## 分支说明

- 当前分支：`mcp-server`
- OpenClaw Skill 版本：<https://github.com/jry21223/HENU_MCP/tree/openclaw-skill>
- 项目主页：<https://github.com/jry21223/HENU_MCP>

## 快速开始

### 1. 安装

```bash
git clone https://github.com/jry21223/HENU_MCP.git
cd HENU_MCP
git checkout mcp-server
chmod +x install.sh
./install.sh
```

### 2. 启动

```bash
./run.sh
```

等价手动方式：

```bash
source venv/bin/activate
python3 mcp_server.py --transport stdio
```

### 3. 诊断

```bash
source venv/bin/activate
python3 diagnose_mcp.py
```

## MCP 客户端配置

### Cherry Studio

```json
{
  "mcpServers": {
    "henu-campus": {
      "command": "bash",
      "args": [
        "-lc",
        "cd \"<YOUR_PROJECT_PATH>\" && source venv/bin/activate && python3 mcp_server.py --transport stdio"
      ]
    }
  }
}
```

### LangBot

新增一个 `stdio` 服务，命令与参数可直接复用上面的 `bash -lc ...`。

## 常用工具

| 类别 | 工具 |
| --- | --- |
| 账号与系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 节次校准 | `set_calibration_source` |

## 推荐流程

1. 首次使用先调用 `setup_account` 保存账号并验证登录。
2. 调用 `sync_schedule` 拉取最新课表。
3. 涉及“现在/今天/明天/当前预约/待签到”等相对时间时，先调用 `system_status` 确认当前时间。
4. 图书馆流程通常是：`library_query(view="locations")` -> `library_reserve` -> `system_status` -> `library_query(view="current")` / `library_auto_signin` -> `library_query(view="records")` / `library_cancel`。
5. 研讨室流程通常是：`seminar_group(action="save")` -> `seminar_query(view="filters")` / `seminar_query(view="rooms")` -> `seminar_query(view="detail")` -> `seminar_reserve` -> `system_status` -> `seminar_query(view="records")` / `seminar_cancel`。
6. 研讨室预约成功后会自动登记“开始前 10 分钟签到”任务；常驻 `mcp_server.py` 进程会后台扫描，也可以手动调用 `seminar_signin(auto_scan=true)` 补扫。

## 关键文件

- 服务入口：`mcp_server.py`
- 图书馆/研讨室核心：`library_core/henu_core.py`
- 安装脚本：`install.sh`
- 启动脚本：`run.sh`
- 依赖清单：`requirements.txt`

## 常见问题

- 虚拟环境不存在：先执行 `./install.sh`
- MCP 无法连接：执行 `python3 diagnose_mcp.py` 检查路径和 Python 环境
- 图书馆或研讨室功能不可用：确认 `library_core/henu_core.py` 存在且可导入
- 若出现登录失败，现在会返回更具体的原因，例如 CAS 页面异常、ticket/token 换取失败、账号或密码错误

## 数据与安全

- 账号与 Cookie 默认保存在本地，如 `henu_profile.json`、`henu_cookies.json`、`henu_library_cookies.json`
- 研讨室自动签到任务保存在本地 `seminar_signin_tasks.json`
- 研讨室 group 保存的是同行成员学号，不含自己；建议保存 3-9 个学号，这样总人数通常会落在 4-10 人范围内
- 研讨室申请内容必须多于 10 个字
- 项目不会主动上传你的账号数据到第三方服务器
