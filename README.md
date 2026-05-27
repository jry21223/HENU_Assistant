# 河大校园助手 OpenClaw Skill

OpenClaw 版校园助手，能力与 MCP 服务器版保持一致：课表、图书馆、研讨室、河宝社区、节次校准和系统状态。

## 安装

```bash
git clone -b openclaw-skill https://github.com/jry21223/HENU_Assistant.git henu_campus_assistant
cp -r henu_campus_assistant ~/.openclaw/workspace/skills/
cd ~/.openclaw/workspace/skills/henu_campus_assistant

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Ubuntu/Debian 如遇到 externally managed environment，先安装：

```bash
sudo apt install -y python3-venv
```

## CLI 入口

| 场景 | 命令 |
| --- | --- |
| 账号 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query --view current|day|week|full` |
| 图书馆 | `library_query --view locations|current|records`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |

## 最短流程

图书馆：

```bash
python3 henu_cli.py system_status
python3 henu_cli.py library_query --view locations
python3 henu_cli.py library_reserve --location "<区域>" --seat_no "<座位号>"
```

研讨室：

```bash
python3 henu_cli.py seminar_query --view filters
python3 henu_cli.py seminar_query --view rooms --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --members 4
python3 henu_cli.py seminar_reserve --area_id <ID> --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --content "<用途说明>"
```

## 说明

- 账号与 Cookie 仅本地保存。
- 研讨室 `group` 不包含自己，建议保存 3-9 个同行成员。
- 研讨室申请内容必须多于 10 个字。
- 不建议直接执行系统级 `pip3 install -r requirements.txt`。
