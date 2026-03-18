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

### macOS / Linux
```bash
source venv/bin/activate
python3 diagnose_mcp.py
```

### Windows
```cmd
venv\Scripts\activate
python diagnose_mcp.py
```

## MCP 客户端配置

### Cherry Studio

#### macOS / Linux
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

#### Windows
```json
{
  "mcpServers": {
    "henu-campus": {
      "command": "cmd",
      "args": [
        "/c",
        "cd /d \"<YOUR_PROJECT_PATH>\" && venv\\Scripts\\activate && python mcp_server.py --transport stdio"
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

课表查询补充说明：
- `schedule_query(view="current")` 只查“当前正在上的课 + 下一节课”
- `schedule_query(view="day", target_date="2026-03-19")` 查某一天课表
- `schedule_query(view="week")` 为兼容旧客户端，返回未按教学周过滤的完整周课表

## 说明

- OpenClaw Skill 版本在 `openclaw-skill` 分支
- 如果无法连接，先运行 `python3 diagnose_mcp.py`
- 账号与 Cookie 只保存在本地
