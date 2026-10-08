# 图书馆一次性定时预约

该功能仅 LangBot 分支提供，使用 KIT 权威终身会员权益 `library_schedule`。会员费用已包含，由站方承担。MCP/Agent Skill 的即时预约接口保持原有语义；本次未给它们增加定时 worker。

## 使用

先完成本人 KIT 绑定和学校账号绑定，然后在本人私聊执行：

```text
library schedule add --run-at 'YYYY-MM-DDT07:00:00+08:00' --location 金明馆北区 --seat-no 201 --date YYYY-MM-DD --time 08:00 --end-time 10:00
确认定时预约
library schedule list
library schedule status --id <任务号>
library schedule cancel --id <任务号>
```

示例日期为占位，须替换。`--run-at` 为**提交预约的执行时刻**，严格带 `+08:00` 的 ISO 秒级格式，Asia/Shanghai，需晚于当前时刻且在未来 30 天内。`--date`、`--time`、`--end-time` 为**座位使用日期和时段**，结束晚于开始，执行时刻不得晚于使用开始。区域与座位明确指定，不读取默认座位。不是每日重复任务。

创建先返回具体预览，五分钟内下一条本人原私聊回复“确认定时预约”才持久创建；模型 Tool 可预览/查询/取消，不能提交确认。群聊、其他用户、其他私聊和同轮确认拒绝。多个校园/雨课堂/青木/KIT 待办并存时，普通“确认”不随意选择。

确认和实际到期提交都重新向 signed subject-only KIT benefits 服务核实会员。会员撤销、绑定失效、资格服务故障或学校账号配置变化时不调用学校。账号密码不写入任务，只记录账号身份摘要及原配置指纹；提交时重新从本人 PluginStorageAdapter 加载当前账号和 Cookie。相同学校账号、区域座位、使用日期时段的 pending/running/succeeded/unknown 任务禁止再次安排，改变执行时刻或重新绑定相同账号不能绕过该限制。

## 状态与取消

- `pending`：已确认，等待执行，可取消。
- `running`：已持久占用本次执行，不能取消或重复发起。
- `succeeded`：学校入口返回明确 success=true；这不保证实际就座或签到。
- `cancelled`：未执行前取消。
- `missed`：超过执行时刻 90 秒窗口，未补发。
- `blocked`：执行前的资格、账号或依赖失败，未发预约。
- `unknown`：学校响应为不明确/失败（包括可能已受理的超时）、提交或回执保存异常，或重启发现 running。**禁止自动重试**，先查学校实际预约记录。

本人查询和取消不因会员失效被锁住。`library schedule cancel --id awaiting` 清除本人未确认预览。取消插件任务不撤销学校已存在的预约；学校记录需另用原即时取消流程处理。

## 持久化与部署

稳定 `HENU_MASTER_KEY` 是重启/迁移后解密的必要部署配置。全局 Storage 键 `library-schedule:ledger:v1` 为一个加密记录，内含有界 subject 索引和各用户独立加密的预览/任务；索引与任务在一次 Storage 写入中一起提交，不会执行孤立任务。最多 512 用户、2048 总任务、每人 20 记录，密文上限 4 MB；达到上限拒绝新增，需运营者审计归档，不通过删除 unknown 回执绕开重单保护。

假定 SDK `set_plugin_storage` 对单记录原子替换。SDK 无 CAS，因此**同一 Storage 命名空间只能有一个活跃插件实例**，不支持多进程副本并行 worker。插件 initialize 恢复记录再启动 worker，读取/解密/恢复写失败时初始化失败关闭，不用空队列覆盖记录。每次 poll 最多处理一条到期任务，先保存 running 再释放全局 ledger 锁，之后进行资格 HTTP 和学校调用；不在全局锁内等待网络。学校参数强制 max_attempts=1、retry_until 空，未知回执不会二次提交。提交线程真正开始后先同步重新核实会员（资格 HTTP 仍在锁外，不复用线程池排队前的旧资格），然后仅非阻塞取得全局 runtime RLock；忙时明确标记 blocked/executor_busy，学校未调用，本任务不自动再试，不在旧资格检查后长时间排队；取得后本地重检资格回执仍在30秒内、90秒执行窗口及停机状态，会员 HTTP 保持锁外。后台故障只输出固定脱敏 warning 错误码。

worker 属于插件实例，异步 `shutdown()` 停止 poll 并等待已开始的提交及回执保存；热重载必须先等待它完成。同步 SDK 析构只取消 poll，持久 running fence 保留。worker 对插件使用弱引用，避免长期 task 强引用使析构无法触发。SDK 不提供独立异步卸载钩子：部署应完整停止旧进程后再启动新进程，或显式等待 shutdown；不能在旧执行仍运行时并行启动替代实例。已 claim 的任务若进程终止，重启转换为 unknown，绝不恢复成 pending。

启动、创建确认、取消、claim 和恢复的 SDK 异常不会触发学校预约。结果不输出原始上游错误、密码、Cookie、SDK 路径或完整回执。用户看到的成功、GitHub 测试、安装包和生产/学校实单验收分别判断。

## 验证范围

使用合成 signed membership HTTP 回应和公开学校 service 边界，无真实学校或供应商请求。回归包含私聊确认、并发确认和 claim、撤销/故障/账号改变、取消/错过窗口、未知防重、文件 Storage 真子进程恢复及学校已接收后进程退出。雨课堂 B 逐动作会员门仍是独立外部依赖；本功能不声称该执行端已升级。
