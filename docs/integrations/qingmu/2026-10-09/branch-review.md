# HENU Assistant 分支核查（2026-10-09）

本记录依据本轮成功 `git fetch`、GitHub 分支列表/PR API、固定提交源码。日期按 Asia/Shanghai 表示。分支尖端提交时间不能当作分支创建时间；合并记录不证明生产部署。

## 交付分支快照

| 远端分支 | 提交 | 最近提交日期 | 本地状态 |
| --- | --- | --- | --- |
| `main` | `4b494d23afbe3725c78e0f4996902d0419d771e1` | 2026-07-25 | 文档首页；本次归档基点 |
| `mcp-server` | `20431d6b1c0f4dfff0186ea50a9db89e2e350ade` | 2026-07-25 | 脏工作区，仅落后 4 提交 |
| `agent-skill` | `51352daa4e3d55186d98dc0d1daa1de3de4c1e38` | 2026-07-25 | 脏工作区，仅落后 5 提交，无本地 upstream 配置，以同名远端比较 |
| `langbot-plugin` | `b3fcd88aaffa5f26c1f87641dd1fe9945410c611` | 2026-09-27 | 脏工作区，仅落后 38 提交 |

来源：[main](https://github.com/jry21223/HENU_Assistant/commit/4b494d23afbe3725c78e0f4996902d0419d771e1)、[MCP](https://github.com/jry21223/HENU_Assistant/commit/20431d6b1c0f4dfff0186ea50a9db89e2e350ade)、[Agent Skill](https://github.com/jry21223/HENU_Assistant/commit/51352daa4e3d55186d98dc0d1daa1de3de4c1e38)、[LangBot](https://github.com/jry21223/HENU_Assistant/commit/b3fcd88aaffa5f26c1f87641dd1fe9945410c611)。

历史 `repo/` 的 `openclaw-skill` 上游已经不存在；不能以其本地旧快照判断当前发布状态。四个原检出的未提交文件保留，本次文档使用基于 `origin/main` 的独立 worktree。

## 近期已合入的两组能力

| 日期 | 分支 / PR | 改动 |
| --- | --- | --- |
| 2026-09-23 | `release/yuketang-bridge-secure`；[#25](https://github.com/jry21223/HENU_Assistant/pull/25)、[#26](https://github.com/jry21223/HENU_Assistant/pull/26) | 雨课堂桥、持久登录/退出、直接回复“确认” |
| 2026-09-26 | `feature/qq-kit-binding`；[#27](https://github.com/jry21223/HENU_Assistant/pull/27) | HENU KIT 网页授权与原 QQ 引用确认绑定；主功能提交发生于 9 月 25 日 |
| 2026-09-26 | [#28](https://github.com/jry21223/HENU_Assistant/pull/28)、[#29](https://github.com/jry21223/HENU_Assistant/pull/29)、[#30](https://github.com/jry21223/HENU_Assistant/pull/30) | Core 代理路径、Storage 缺失记录与转发错误处理 |
| 2026-09-27 | `codex/qq-binding-short-command-quote`；[#31](https://github.com/jry21223/HENU_Assistant/pull/31) | 简化绑定命令并区分引用目标 |

`release/yuketang-bridge-secure` 尖端是 `cd9e4f0238c6d71785ebe8b28698c9eaccfd0af2`，当前 `langbot-plugin` 比它多 15 个提交、没有反向独有提交，说明其变更已被包含；保留的功能分支不意味着还未合入。比较来源：[固定提交比较](https://github.com/jry21223/HENU_Assistant/compare/cd9e4f0238c6d71785ebe8b28698c9eaccfd0af2...b3fcd88aaffa5f26c1f87641dd1fe9945410c611)。

本轮分支清单中还保留了三个 `codex/mcp2-2.1.0-*` 工作分支；只确认分支存在，没有把它们当作已发布 2.1.0 的证据。当前 LangBot 固定提交的 `manifest.yaml` 仍写 2.0.4。[manifest 源码](https://github.com/jry21223/HENU_Assistant/blob/b3fcd88aaffa5f26c1f87641dd1fe9945410c611/manifest.yaml)。

## 与附件的关系

- 雨课堂现有客户端：`HENU_BRIDGE_URL`/`HENU_BRIDGE_SECRET`，AES-GCM 信封，`/v1/config`、`/v1/status`、`/v1/login/*`、`/v1/logout`。[bridge_client.py](https://github.com/jry21223/HENU_Assistant/blob/b3fcd88aaffa5f26c1f87641dd1fe9945410c611/henu_plugin/bridge_client.py)
- HENU KIT 现有客户端：服务签名与 `/api/v1/qq-bindings/{action}`。[kit_client.py](https://github.com/jry21223/HENU_Assistant/blob/b3fcd88aaffa5f26c1f87641dd1fe9945410c611/henu_plugin/kit_client.py)
- 新收到附件：青木 `api.php?act=get/add/budan/chadanoid/xgmm`，代理 UID/KEY + 学生凭据。[归档原文](source.sanitized.md)

对当前三条交付分支的固定快照搜索 `qingmu`、`103.52.155.128`、`chadanoid`、`api.php?act=` 未发现匹配。结合上述源码，未发现它已被接入的证据；这不排除别名、动态配置或其他仓库中的实现。此次只归档与调查，没有实现新接入。
