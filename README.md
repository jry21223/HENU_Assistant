# 河大校园助手 · OpenClaw Skill（历史快照）

> **注意**：本目录是早期 umbrella / OpenClaw Skill 布局的快照，**不是当前推荐入口**。  
> 新开发与部署请使用：
>
> | 形态 | 分支 / 目录 |
> | --- | --- |
> | MCP Server | `mcp-server` |
> | Agent Skill + CLI | `agent-skill` |
> | Langbot 插件 | `langbot-plugin` |

能力与 MCP 版大体一致：课表、图书馆、研讨室、河宝社区、节次校准、系统状态，并包含共享智能选课模块。

教务登录优先 IDS 统一认证；仅在 IDS / Service 跳转 / 网络 / 验证码风控失败时，自动尝试 **一次** xk Kingo 独立登录。Kingo 为降级通道，主要保证课表 / 选课状态 / 空教室等 xk 能力，不提供图书馆、研讨室、河宝的跨服务 SSO，也不会生成或覆盖 CAS Cookie。遇到验证码返回 `captcha_required`，不识别、不绕过、不循环重试。

## 安装（仅作对照）

```bash
git clone -b openclaw-skill https://github.com/jry21223/HENU_Assistant.git henu_campus_assistant
cp -r henu_campus_assistant ~/.openclaw/workspace/skills/
cd ~/.openclaw/workspace/skills/henu_campus_assistant

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Ubuntu/Debian 若遇到 *externally managed environment*：

```bash
sudo apt install -y python3-venv
```

## CLI 入口（本快照）

| 场景 | 命令 |
| --- | --- |
| 账号 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query --view current\|day\|week\|full` |
| 图书馆 | `library_query --view locations\|seats\|current\|records`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |
| 选课规划 | `smart_course_selection` |

完整能力（空教室、资源映射、选课监控等）以 **`agent-skill` / `mcp-server` 分支** 为准。

## 最短流程

### 图书馆

```bash
python3 henu_cli.py system_status
python3 henu_cli.py library_query --view locations
python3 henu_cli.py library_query --view seats --location "<区域>"
python3 henu_cli.py library_reserve --location "<区域>" --seat_no "<座位号>"
```

### 研讨室

```bash
python3 henu_cli.py seminar_query --view filters
python3 henu_cli.py seminar_query --view rooms \
  --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --members 4
python3 henu_cli.py seminar_reserve \
  --area_id <ID> --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM \
  --content "<用途说明，须多于10个字>"
```

## 说明

- 账号与 Cookie 仅本地保存。  
- 研讨室 `group` 不包含自己，建议 3–9 名同行成员；申请内容须 >10 字。  
- 不要使用系统级 `pip3 install -r requirements.txt`。  
- 布局上本快照将部分模块平铺在仓库根（如 `course_schedule.py`、`secure_storage.py`）；新变体已收拢到 `henu_mcp/` + `campus_core/`。

## 智能选课

模块：`campus_core.smart_course_selector`。

```bash
python3 henu_cli.py smart_course_selection \
  --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
```

输出结构：`henu.smart_course_selection.v1`。
