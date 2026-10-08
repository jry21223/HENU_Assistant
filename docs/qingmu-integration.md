# 青木平台接入（LangBot 2.0.5）

本功能支持 U校园、U校园 AI 版、学习通账号与课程订单，沿用雨课堂的私聊、QQ 隔离、确认和状态回执。新协议是供应商 `api.php?act=...`，不复用雨课堂的 AES-GCM 守护进程协议，也不自动复制到 MCP / Agent Skill。

## 用户使用

1. 私聊发送 `help qingmu` / `qingmu platforms`，选择 `ucampus`、`ucampus_ai` 或 `chaoxing`。
2. 私聊直发 `qingmu account set --platform ucampus --account <本人账号> --password '<平台密码>' --school '<学校>'`。不要把密码交给模型 Tool。收到预览后下一条回复“确认”或“确认青木绑定”。账号加密保存不代表已验证登录。
3. `qingmu courses --platform ucampus` 查课，得到本 QQ 的课程列表和序号。五分钟内用 `qingmu order add --platform ucampus --course 1` 预览订单。
4. 核对平台、课程和部署方核实的计费说明；下一条私聊回复“确认”或“确认青木操作”才请求付费下单。价格资料只是快照，不是现行结算承诺。
5. `qingmu orders` 查看本地回执。`qingmu order status --id <本地订单号>` 查询该订单的上游进度；不接受其他账号或任意上游订单号。受理不等于课程完成。
6. `qingmu order retry --id <本地订单号>` 预览补刷，再确认。每个订单的已受理/未知补刷均禁止自动重复申请。
7. `qingmu account clear --platform ucampus` 预览删除绑定，再确认。删除绑定会移除账号密码和查课缓存，但保留订单回执及重复提交屏障。

中文“青木”是 `qingmu` 的别名；平台参数也支持“优校园”“U校园”“U校园 AI 版”“学习通”。群聊拒绝全部青木账号/订单命令。遇到多个待办，分别使用“确认绑定”（雨课堂）、“确认操作”（校园）、“确认青木绑定”或“确认青木操作”。KIT 的引用确认机制保留。

## 部署配置

配置仅由运营者写入插件根目录 `.env` 或环境变量，环境变量优先。不会把供应商 UID/KEY 或附件原始秘密写入代码。生产必须使用稳定的 `HENU_MASTER_KEY`；不要运行多个同时写同一插件 Storage 的实例，Storage API 不提供跨进程事务/CAS。

| 配置 | 含义 |
| --- | --- |
| `QINGMU_API_URL` | 经核验的 HTTPS origin，不能含路径、用户信息、查询或片段 |
| `QINGMU_UID` / `QINGMU_KEY` | 供应商对接凭据，与学生账号密码分开 |
| `QINGMU_CA_BUNDLE` | 可选、经独立核验的 CA 文件绝对路径；默认使用系统信任链，不能关闭校验 |
| `QINGMU_REQUEST_ENCODING` | 供应商确认的 `form` 或 `json`，不设置则停止请求 |
| `QINGMU_PLATFORM_MAP` | JSON：平台别名到核实的服务 `id` 和 `billing` 说明；计费说明最多 160 字，不得包含控制字符 |
| `QINGMU_CONTRACT_JSON` | JSON：供应商确认的查课成功码、课程字段、订单回执字段、补刷成功码 |
| `QINGMU_ENABLE_ORDERS` | 默认 `0`；核实契约、服务和计费后才设为 `1` |

以下是结构模板，空值故意不能工作；必须由供应商核实后填入，不能直接把附件里的商品 ID 复制成服务 `platform`：

```dotenv
QINGMU_API_URL=
QINGMU_UID=
QINGMU_KEY=
QINGMU_CA_BUNDLE=
QINGMU_REQUEST_ENCODING=
QINGMU_PLATFORM_MAP='{"ucampus":{"id":"","billing":""},"ucampus_ai":{"id":"","billing":""},"chaoxing":{"id":"","billing":""}}'
QINGMU_CONTRACT_JSON='{"get_success_code":"","course_id_field":"","course_name_field":"","order_id_field":"","retry_success_code":""}'
QINGMU_ENABLE_ORDERS=0
```

当前适配器要求查课成功后顶层 `data` 是数组；课程 ID/名称字段可配置。下单成功码按文档为 `0`，回执在 `data` 对象或顶层的配置字段中；同步成功码按文档为 `1`，`data` 必须恰好包含所查询的一个 `id`。查课成功码和补刷成功码没有文档证明，必须配置。非法响应、重复课程 ID、其他订单或无订单 ID 均不能视作可追踪的成功订单。

进度只返回受限的状态和 0–100 数值/百分比，不转发原始 remarks、密码、账号字段或任意上游错误文本。未识别状态/进度明确显示“未识别的上游状态”/“未知进度”；不能据此推断完成。真实响应不符合当前结构时，应先更新并测试适配契约，不能修改成功码绕过结构校验。

## 结果未知与重复提交

每个 QQ 的账号、查课缓存、待确认操作和订单放在独立的加密 Storage 记录里。确认与“提交中”屏障在同一次保存中落盘；保存失败不会请求供应商。相同账号、服务与课程的已受理/提交中/未知订单阻止重单；明确拒绝的订单可以重新发起预览。

HTTP 不重试、不重定向，限制响应字节数、读取时间与用户等待时间。超时不能证明供应商没有受理，后台工作线程也可能稍后得到结果；未知记录必须由维护者通过供应商记录核对，不能删除屏障后自动重试。上游受理但回执保存失败时返回 `external_committed=true,storage_persisted=false`；持久记录仍保留“提交中”屏障。

账号或供应商配置在预览后变更会使确认失效。订单查询必须匹配保存时的供应商身份。修改 KEY 可以继续读取同一供应商旧订单，但修改 origin、UID 或服务 ID 后不会拿旧 ID 查询新供应商。

## 验证与交付边界

本地测试使用模拟供应商、合成凭据；没有使用真实学生账号、供应商余额、下单或补刷。真实部署仍需：核验 TLS 身份（归档地址曾出现自签名证书）、确认三平台服务映射和响应样例、先做本人授权账号的只读查课，再由人逐项验收付费预览/回执/进度。当前实现不提供改密，因为原文的 `yid/oid` 相互冲突；也不提供考试或人脸处理能力。

原始依据：[2026-10-09 脱敏归档和调查](https://github.com/jry21223/HENU_Assistant/tree/a4f287d19911169183dca917e50df3e575953629/docs/integrations/qingmu/2026-10-09)。
