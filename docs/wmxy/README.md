# 完美校园电费研究资料

这里的文档、脱敏抓包和证据原先只存在于本机 `output/wmxy-electricity/`（该目录被 `.gitignore` 排除）。为了让换机器或换人时能继续工作，把**脱敏后**的部分收录进仓库。

## 内容

- `docs/`：协议审计、电费接口、环境与认证排障、电表定位、登录排障、代码评审记录。
- `captures/`：两份 `.sanitized.har`，请求头与参数值已替换为 `<redacted>`。
- `evidence/`：脱敏的请求/响应形态、TLS 对照、VM 状态等记录。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [electricity-api.md](docs/electricity-api.md) | 云缴费只读接口契约、请求参数、APP/Python 对照样本 |
| [roomverify-resolution.md](docs/roomverify-resolution.md) | 空调表与照明表的目录匹配规则、`roomverify` 取值 |
| [protocol-audit.md](docs/protocol-audit.md) | 原生登录协议（密钥交换、报文封装、签名、续期）静态审计 |
| [analysis.md](docs/analysis.md) | 环境、证书信任、早期登录排障记录 |
| [auth-handoff-plan.md](docs/auth-handoff-plan.md) | 早期 TLS 排障计划与当时的结论，仅作历史参考 |
| [astra-review-feat-electricity.md](docs/astra-review-feat-electricity.md) | 功能分支的一次代码评审记录 |

当前实现与验收状态以仓库内 [电费实测说明](../electricity-readonly-verification.md) 和 [协作说明](../electricity-readonly-collaboration.md) 为准；本目录保留原始研究证据。

## 未收录

以下仍只在本机 `output/wmxy-electricity/`，不进入 Git：

- `private/`（本地 CA 私钥）、`avd/`（模拟器数据）、`.venv/`、原始 APK 与反编译源码 `analysis/`、原始抓包。
- 截图 `phase1-app-launch.png`（可能包含账号信息）。
- `runtime/`、`cache/`、`run/` 等运行时产物。

因此 `protocol-audit.md` 里指向 `analysis/business/sources/...` 的源码链接已改为纯文本；需要核对时在本机原目录查看。

## 复制回本机工作目录

需要重新使用这些资料做实测时，把本目录同步回 `output/wmxy-electricity/` 对应子目录即可，脚本按相对路径引用。

## 敏感信息约定

- 不写入 Cookie、Token、密码、原始表 ID、个人响应和账号截图。
- 如新增证据，先脱敏再放到这里；原始文件留在 `output/private/`（权限 0600）。
