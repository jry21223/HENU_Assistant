# 河大校园助手（OpenClaw Skill）

`openclaw-skill` 分支，能力与 `mcp-server` 对齐：课表、图书馆预约、研讨室预约、节次校准、系统状态。

## 快速安装

```bash
git clone -b openclaw-skill https://github.com/jry21223/HENU_MCP.git henu_campus_assistant
cp -r henu_campus_assistant ~/.openclaw/workspace/skills/
cd ~/.openclaw/workspace/skills/henu_campus_assistant

# Ubuntu/Debian: 避免 "externally managed environment" 报错
sudo apt update
sudo apt install -y python3-venv

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 可选：激活后可直接使用 python/pip
source .venv/bin/activate
```

## 核心命令

```bash
.venv/bin/python henu_cli.py setup_account --student_id "<学号>" --password "<密码>"
.venv/bin/python henu_cli.py sync_schedule
.venv/bin/python henu_cli.py schedule_query --view week
.venv/bin/python henu_cli.py schedule_query --view current
.venv/bin/python henu_cli.py library_query --view locations
.venv/bin/python henu_cli.py library_reserve --location "<区域>" --seat_no "<座位号>" --preferred_time "10:30"
.venv/bin/python henu_cli.py library_query --view current
.venv/bin/python henu_cli.py library_auto_signin
.venv/bin/python henu_cli.py library_query --view records
.venv/bin/python henu_cli.py library_cancel --record_id "<记录ID>"
.venv/bin/python henu_cli.py seminar_group --action save --group_name "<组名>" --member_ids "<学号1,学号2,学号3>"
.venv/bin/python henu_cli.py seminar_group --action list
.venv/bin/python henu_cli.py seminar_query --view filters
.venv/bin/python henu_cli.py seminar_query --view records --record_type 1 --mode books
.venv/bin/python henu_cli.py seminar_query --view rooms --target_date "2026-03-14" --members 0 --library_names "<馆舍名>"
.venv/bin/python henu_cli.py seminar_query --view detail --area_id "<房间ID>" --target_date "2026-03-14"
.venv/bin/python henu_cli.py seminar_reserve --area_id "<房间ID>" --target_date "2026-03-14" --start_time "14:00" --end_time "16:00" --group_name "<组名>" --title "<主题>" --content "<超过10字的申请说明>" --mobile "<手机号>"
.venv/bin/python henu_cli.py seminar_signin --auto_scan
.venv/bin/python henu_cli.py seminar_cancel --record_id "<记录ID>"
.venv/bin/python henu_cli.py set_calibration_source --data "<DATA>" --cookie "<COOKIE>"
.venv/bin/python henu_cli.py system_status
```

## 关键文件

- `henu_cli.py`：Skill CLI 入口
- `scripts/henu_campus_mcp.py`：Skill 调用包装层
- `mcp_server.py`：核心能力实现
- `course_schedule.py` / `schedule_cleaner.py`：课表抓取与清洗
- `library_core/henu_core.py`：图书馆/研讨室核心（内置）

## 说明

- Skill CLI 已统一为 14 个入口：`setup_account`、`sync_schedule`、`schedule_query`、`library_query`、`library_reserve`、`library_auto_signin`、`library_cancel`、`seminar_group`、`seminar_query`、`seminar_signin`、`seminar_reserve`、`seminar_cancel`、`set_calibration_source`、`system_status`
- 不依赖外部 `../图书馆自动预约/web`
- 账号与 Cookie 仅本地保存
- 涉及“现在/今天/明天/当前预约/待签到”等相对时间时，先调用 `system_status`
- 研讨室 `group` 保存的是同行成员学号，不含自己；建议保存 3-9 个学号，预约时会自动去重并排除当前登录账号
- 研讨室预约会按房间限制校验总人数，通常为 4-5 人起、最多 10 人
- 研讨室申请内容会强制校验为多于 10 个字
- 研讨室取消通过 `/v4/seminar/cancel`，可先用 `seminar_query --view records` 查到记录 `id`
- 相关分支：`mcp-server`、`main`
- Ubuntu/Debian 不要直接执行系统级 `pip3 install -r requirements.txt`
