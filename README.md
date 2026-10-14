# 河大校园助手 · MCP 服务器版

把河南大学校园能力暴露为 **MCP tools**（stdio），适合 Cherry Studio 等支持 MCP 的客户端。

> 同能力的其他形态：[`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)（CLI / Skill）、[`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin)（QQ 插件）。

## 安装与诊断

```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git henu-mcp
cd henu-mcp

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 diagnose_mcp.py
```

也可用：

```bash
./install.sh
```

## 启动

```bash
python3 mcp_server.py --transport stdio
# 或
./run.sh
```

### Cherry Studio 配置示例

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

## 工具一览

| 类别 | 工具 |
| --- | --- |
| 账号 / 系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 选课 | `smart_course_selection`, `smart_course_select`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 空教室 | `empty_classroom_query`, `empty_classroom_sync` |
| 资源映射 | `resource_registry_query`, `resource_registry_sync` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 节次校准 | `set_calibration_source` |

## 常用流程

1. `setup_account` 绑定学号、密码（可保存默认图书馆区域/座位）。  
2. 涉及「今天 / 现在」时先 `system_status` 对齐服务器时间。  
3. **图书馆**：`locations` → `seats` → `library_reserve` → `current`。  
4. **空教室**：`empty_classroom_query(view="free")`；不确定校区/楼房时先 `campuses` / `buildings`。  
5. **研讨室**：`filters` → `rooms` → `detail` → `seminar_reserve`。  
6. **选课规划**：用 Excel/JSON + 偏好参数调用 `smart_course_selection`；监控用 `course_monitor_*`（只读）。

## 给 MCP 客户端 / LLM 的提示

- 用户问空教室、自习室时，**不要**回复「系统不支持」，优先调用 `empty_classroom_query`。  
- 相对时间（今天/当前）先 `system_status`，再把具体日期/周次/节次传给查询工具。  
- 工具返回大 JSON 时，对用户概括 `msg` / `rooms` / `data`，不要整段粘贴。

## 目录结构

```text
mcp_server.py          # 启动入口 + 公开工具门面
diagnose_mcp.py        # 环境诊断
henu_mcp/
  core/                # 课表抓取、清洗、加密存储、选课监控等
  tools/               # MCP 工具实现
  runtime.py           # 本地路径 / per-user 路径切换
campus_core/           # 图书馆、研讨室、河宝、空教室、资源映射、智能选课
tests/                 # pytest
```

## 认证与数据

- 账号、Cookie、抓取结果**仅本地保存**。  
- 登录固定双通道：  
  1. 优先 IDS CAS（`CASTGC` / `TGC`），可跨服务 SSO；  
  2. IDS / Service 跳转 / 网络 / 验证码风控失败时，**只自动尝试一次** xk Kingo；  
  3. 不识别验证码、不绕过风控、不循环重试。  
- **IDS**：课表、选课、空教室、图书馆、研讨室、河宝可复用统一登录态。  
- **Kingo（降级）**：主要保证 xk 相关能力；不生成/覆盖 CAS Cookie；其他服务仍需 IDS。  
- 账号初始化、登录检查、课表同步、系统状态会返回 `auth`：`mode`（`ids_cas` / `xk_kingo`）、`degraded`、`error_code`、`warning`。

## 智能选课

模块：`campus_core.smart_course_selector`（与其他交付形态共用逻辑）。

- 从教务导出的 Excel 或清洗后的 JSON 读取课程选项。  
- 按班级筛选专业课 / 专业公共课 / 全年级公共课。  
- 偏好规划：早八、集中上课天数、避免晚课、是否允许未排时间。  
- 输出结构：`henu.smart_course_selection.v1`；`plans[].selection_actions` 可作为后续提交器的 dry-run 输入。  
- `course_selection_query`：只读状态，不提交。  
- `course_monitor_*`：只读余量监控，可飞书提醒；**不会**点击选课/提交/退选。

## 测试

```bash
source venv/bin/activate
pytest
# 或环境诊断
python3 diagnose_mcp.py
```
