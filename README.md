# 河大校园助手（MCP 服务器版）

河南大学校园助手的 MCP 实现，集成了课表查询、图书馆预约、研讨室预约、节次校准和系统状态能力。

## 安装

```bash
git clone https://github.com/jry21223/HENU_MCP.git
cd HENU_MCP
git checkout mcp-server
chmod +x install.sh
./install.sh
```

## 启动

```bash
./run.sh
```

## 诊断

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

其他支持 `stdio` 的客户端也可以复用同一条启动命令。

## 常用工具

| 类别 | 工具 |
| --- | --- |
| 账号与系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 节次校准 | `set_calibration_source` |

## 最短流程

1. 先调用 `setup_account` 保存账号。
2. 需要课表时，先 `sync_schedule`，再用 `schedule_query`。
3. 涉及“现在/今天/明天/当前预约”时，先用 `system_status`。
4. 图书馆通常按 `library_query(view="locations")` -> `library_reserve` -> `library_query(view="current"|"records")`。
5. 研讨室通常按 `seminar_group(action="save")` -> `seminar_query(view="filters"|"rooms"|"detail")` -> `seminar_reserve` -> `seminar_query(view="records")`。
6. 研讨室签到可用 `seminar_signin`，补扫可用 `seminar_signin(auto_scan=true)`。

## 说明

- OpenClaw Skill 版本在 `openclaw-skill` 分支
- 如果无法连接，先运行 `python3 diagnose_mcp.py`
- 账号与 Cookie 只保存在本地
