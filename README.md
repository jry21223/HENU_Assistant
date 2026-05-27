# 河大校园助手 MCP 服务器版

把河大校园能力暴露为 MCP tools，适合 Cherry Studio 等支持 stdio MCP 的客户端。

## 安装与诊断

```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git henu-mcp
cd henu-mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 diagnose_mcp.py
```

Windows 将激活命令换成：

```cmd
venv\Scripts\activate
```

## 启动

```bash
python3 mcp_server.py --transport stdio
# 或
./run.sh
```

Cherry Studio 示例：

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

## 工具

| 类别 | 工具 |
| --- | --- |
| 账号/系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 节次校准 | `set_calibration_source` |

## 常用流程

1. `setup_account` 绑定学号、密码，可保存默认图书馆区域和座位。
2. 查询前先用 `system_status` 确认服务器时间。
3. 图书馆预约前先查 `library_query(view="locations")`，预约后用 `library_query(view="current")` 核对。
4. 研讨室按 `filters -> rooms -> detail -> reserve` 查询和预约。

账号、Cookie 和抓取结果都保存在本地。
