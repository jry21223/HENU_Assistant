<p align="center">
  <img src="./assets/readme/hero.png" width="100%" alt="河大校园助手 MCP 服务器：stdio MCP tools，本地课表 / 空教室 / 图书馆 / 研讨室">
</p>

把河大校园能力暴露为 **MCP tools**（stdio）。适合 Cherry Studio 等支持 MCP 的客户端。

首页与其它形态：[`main`](https://github.com/jry21223/HENU_Assistant) · [`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin) · [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)

---

## 安装

```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git henu-mcp
cd henu-mcp
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 diagnose_mcp.py           # 可选
```

也可：`./install.sh`

## 启动

```bash
python3 mcp_server.py --transport stdio
# 或
./run.sh
```

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

---

## 工具

| 类别 | 工具 |
| --- | --- |
| 账号 / 系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 选课 | `smart_course_selection`, `smart_course_select`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 空教室 | `empty_classroom_query`, `empty_classroom_sync` |
| 资源映射 | `resource_registry_query`, `resource_registry_sync` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |

## 常用流程

1. `setup_account` 绑定学号、密码（可存默认图书馆区域/座位）。  
2. 相对时间先 `system_status`。  
3. 图书馆：`locations` → `seats` → `library_reserve` → `current`。  
4. 空教室：`empty_classroom_query(view="free")`；不确定校区/楼房时先 `campuses` / `buildings`。  
5. 研讨室：`filters` → `rooms` → `detail` → `seminar_reserve`。

### 图书馆结果契约

- 先 `library_query(view="locations")`，后续只用返回的 `locations[].location` / `area_id`。  
- 座位预约只用 `seats[].seat_no`。  
- `source="live_empty"` 表示实时接口明确空列表（`success=false`），**不要**推测开放时间或替代方案。  
- 写操作只以 `success=true` 为准。

## 给 MCP 客户端 / LLM

- 空教室 / 自习室：**不要**说「不支持」，优先 `empty_classroom_query`。  
- 「今天 / 现在」先 `system_status`，再传具体日期/周次/节次。  
- 大 JSON 只概括 `msg` / `rooms` / `data`，不要整段粘贴。

---

## 登录与数据

- 账号、Cookie、抓取结果**仅本地保存**。  
- **IDS CAS 优先**（`CASTGC` / `TGC`）；失败时只自动 **一次** xk Kingo。  
- 不识别验证码、不绕过风控、不循环重试。  
- Kingo 主要保课表 / 选课状态 / 空教室；图书馆、研讨室、河宝仍需 IDS。  
- `auth` 返回 `mode`（`ids_cas` / `xk_kingo`）、`degraded`、`error_code`、`warning`。

## 智能选课

模块：`campus_core.smart_course_selector`。

- Excel / JSON 读选项；按班级筛选。  
- 偏好：早八、集中天数、避免晚课、未排时间。  
- 输出：`henu.smart_course_selection.v1`。  
- `course_selection_query` / `course_monitor_*` **只读**；监控可飞书提醒，不自动提交。

```bash
python3 henu_cli.py smart_course_selection \
  --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
```

## 目录

```text
mcp_server.py       # 入口 + 工具门面
diagnose_mcp.py
henu_mcp/core/      # 课表、存储、选课监控
henu_mcp/tools/     # MCP 工具实现
henu_mcp/runtime.py
campus_core/        # 图书馆 / 研讨室 / 河宝 / 空教室 / 智能选课
tests/
```

## 测试

```bash
source venv/bin/activate
pytest
# 或
python3 diagnose_mcp.py
```

## License

[MIT](LICENSE)
