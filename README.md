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

## 常用入口

- `setup_account`：保存账号并验证登录
- `sync_schedule`：同步最新课表
- `schedule_query --view current|full`：查当前课程或完整课表
- `library_query --view locations|current|records`：查图书馆区域、当前预约或历史记录
- `library_reserve` / `library_auto_signin` / `library_cancel`：图书馆写操作
- `seminar_group --action list|save|delete`：管理研讨室 group
- `seminar_query --view filters|rooms|detail|records|signin_tasks`：查研讨室筛选项、房间、详情、记录和签到任务
- `seminar_reserve` / `seminar_signin` / `seminar_cancel`：研讨室写操作
- `system_status`：查当前时间和系统状态
- `set_calibration_source`：更新节次校准源

## 最短流程

图书馆：
- 先执行 `system_status`
- 再执行 `library_query --view locations`
- 然后 `library_reserve`
- 后续用 `library_query --view current` / `library_query --view records`

研讨室：
- 先执行 `system_status`
- 可先用 `seminar_group --action save` 保存成员
- 按 `seminar_query --view filters` -> `seminar_query --view rooms` -> `seminar_query --view detail` 查询
- 然后 `seminar_reserve`
- 后续用 `seminar_query --view records`、`seminar_signin`、`seminar_cancel`

## 说明

- Skill CLI 统一为 14 个入口，避免重复命令
- 账号与 Cookie 仅本地保存
- 研讨室 `group` 不含自己，建议保存 3-9 个同行成员
- 研讨室申请内容必须多于 10 个字
- Ubuntu/Debian 不要直接执行系统级 `pip3 install -r requirements.txt`
