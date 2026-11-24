# 完美校园 5.8.19 登录排障旧计划

本文保留切网前的登录排障方案。切网后，官方 APP 已进入用电支出，Python 原型完成只读对照。当前用法与验收状态见[电费接口](electricity-api.md)和[交接首页](../README.md)。

## 已确认的失败点

- 点击空密码框即可触发「安全环境未配置成功，请稍后再试」。`LoginAndRegistActivity.java:611` 检查 `getChangeKeyStatus()`。`LoginAndRegistBusiness.java:447` 在状态未就绪时发起密钥交换，按确定也可能再触发，见同文件第 88 行。检查时不要反复点弹窗。
- `AbstractNetRequestProcess.java:311` 进入密钥交换，TCP 连接超时 4 秒，读取超时 6 秒。失败后可调用 `switchDomain()` 再试一次。该方法还会查询备用域名，属于另一路请求。
- 日志样本落在 `changeKey → DefaultHttpsNetRequest.request → Conscrypt.startHandshake → SSLInputStream.readFromSocket`。`DefaultHttpsNetRequest.java:335` 的 `connect()` 尚未返回，之后才写 POST 正文。日志中的 9 次异常记录可能含重复打印，不能直接用作失败连接数。现有记录没有实际目标 IP、SNI 或停顿在握手哪一条消息。[异常栈](../evidence/login-init-failure.json)
- 原 APP 使用两个内置证书条目形成独立 TrustManager，并配置 HostnameVerifier。`getHostName()` 返回的 `*.域名` 是给本地校验器的参数，不能当作 SNI。既有 Android 探针用系统 TrustManager、显式直连、15 秒超时，因此只能证明两域曾可达。计划不把证书错误、Root 检测或服务端拒绝当作已证实原因。[既有分析](analysis.md)
- 本轮宿主机重新测试两个域名的公开 `/campus/`，无凭据。主域 3 次均完成 TLS，约 1.24–1.45 秒。备用域 2 次完成，约 2.20–2.37 秒，另 1 次在 4 秒连接预算内 TLS 超时。两个 DNS 结果 `198.18.1.225` 和 `198.18.1.231` 在宿主机都经 `utun7` 路由。该段属于非全局可达的测试保留地址。现有 DNS/TUN 路径是优先排查对象，仍未证实 APP 失败连接用了同一路径。[宿主机实测](../evidence/host-tls-baseline-2026-11-17.csv) · [IANA 地址登记](https://www.iana.org/assignments/iana-ipv4-special-registry/iana-ipv4-special-registry.xhtml)
- 后续记录包括带 4 秒预算的探针和一次 APP 重试。探针接近预算时有成功也有超时。APP 重试原始事件中的错误标签为 `Not Allowed` 和 `Not Found`，数字状态字段为空。矩阵的部分状态和原因是根据标签推断，切网奏效也仅能确认此次恢复。 [复测矩阵](../evidence/auth-tls-matrix.csv) · [APP 重试脱敏事件](../evidence/login-retry-request.json)

## 一、原定 TLS 复测步骤

1. 在本计划所在目录的上一级执行 `python3 scripts/vm.py start`、`python3 scripts/vm.py wait`。使用 `wmxy30` / `emulator-5580`、已有原始 APK、既有 API 30 镜像。这个对照只启动 VM，`scripts/start.sh` 会额外启动代理，因此留给后续 TLS 探针。检查 Android `http_proxy` 为 `:0` 或未设置。Frida 和任务代理保持关闭。
2. 冷开原 APP，记录 UTC 时间。各取一次自动初始化和一次空密码框焦点造成的失败。按目标进程 PID 和本次时间窗筛选 logcat，只输出时间、异常类和调用栈函数名。源码的调试日志可能包含登录正文、密码或密钥，避免保存或显示完整日志。记录 APP 请求实际域名、DNS 结果、连接 IP 和 SNI（可见时）。如需抓包，只针对这些连接读取握手元数据，输出 TCP 建连、ClientHello、ServerHello、Alert 及各阶段时间。TLS 1.3 的后续握手可能加密，证书错误优先以客户端异常鉴别。
3. 在同一 AVD、同一时间窗对两个认证域各做三次无凭据对照。复用 `scripts/TlsProbe.java`，增设连接和读取超时参数，采用 4 秒 / 6 秒、`direct`、系统正常证书校验。访问公开的 `/campus/`。每次写一行 `时间、客户端、目标域、解析 IP、连接 IP、路由、TCP/TLS 阶段耗时、HTTP 状态或异常类` 至 `evidence/auth-tls-matrix.csv`。阶段耗时从握手元数据算，无法观测的字段留空。宿主机以验证证书的 `curl -I` 同时记录路由和时间。4/6 秒配置和宿主机 `curl` 的总连接预算分别标注。
4. 如主机路由显示 TUN 或备用域仍超时，再由操作者选择可用的另一条正常网络复测。同一 APP 版本、时间窗和步骤，与现有网络一项一项比较。可以用真机同版本做独立对照。无需改系统级代理、DNS 或防护设置。

按证据决定下一步：

| 现象 | 下一步 |
|---|---|
| APP 与无凭据探针在同路由、同目标均超时，另一条正常网络成功 | 记录网络差异，用成功路径重新验证原 APP 密钥交换 |
| 建连后发出 ClientHello，客户端等待握手消息超时 | 对照实际 SNI、目标 IP、同网段真机及网络路径，确认哪一步开始分叉 |
| 探针通过，APP 收到明确的证书链或主机名错误 | 保留错误类型。检查原 APK 内置公开证书及官方版本兼容性，使用正常信任校验 |
| TLS 已完成，控制台出现 HTTP 或业务失败 | 记录实际状态码、阶段与业务错误类型，再核对 APP 原生过程 |
| 尚无足够证据区分 | 记录可重现条件与缺失字段，使用能在正常网络登录的官方客户端验证用电入口 |

阶段通过条件是同一官方 APP 在明确记录的环境中完成密钥交换并进入可提交登录的状态，或在实际 TLS 阶段得到可以复现的故障证据。后者只允许继续做离线协议对照和官方入口查证。

## 二、原定登录源码与实测对照

源码根目录是 `analysis/business/sources/`。本阶段先写结构和证据表，再决定实现范围。无需重复脱壳。

| 阶段 | 源码入口 | 要核对的行为 |
|---|---|---|
| 密钥交换 | `AbstractNetRequestProcess.java:311`、`ChangeKeyReqData.java:26`、`ChangeKeyEncrypt.java:17` | `exchangeSecretkey.action`。客户端临时生成 RSA 密钥对。源码显示请求是包含公钥的 JSON，`encrypt()` 原样返还字节。响应才通过该次私钥解开。HTTP 200、业务成功、合法 session 和 key 都成立后才进入下一步。 |
| 密码字段 | `PwdInputBusiness.java:85`、`PwdEncryptUtil.java:16`、`LoginAndRegistBusiness.java:183` | 输入事件构造密码字符串列表，源码显示逐字符处理。对照实际登录表单及回调。先用合成密码核验长度、顺序、字符编码和 Base64 行尾。 |
| 请求封装 | `LoginReqData.java`、`TripleDESEncry.java:37`、`LoginBeforeNetRequestImpl.java:92` | `loginnew.action`。确认内层 JSON 的字段与真实请求一致。外层 `session`/`data` 使用协商所得密钥，`campusSign` 针对实际发送的字节。验证响应完整性、解码和业务成功后才接受登录状态。 |
| 续期 | `AbstractNetRequestProcess.java:168`、`LoginAndRegistBusiness.java:390` | 这里存在 token 类型的自动登录分支，代码成功时保存服务端新 token。至少一次真实续期才标记可用，否则原型只提示重新认证。 |
| 电费 | 官方 APP 的「金明 → 缴费 → 用电支出」 | 同一个授权房间操作一次并刷新。用请求时间和页面结果核对实际域、接口、方法、认证形式、房间/电表映射及单位。先确认电费页面是否依赖单独授权或 WebView 会话。 |

与用户协调一次正常登录。验证码或交互验证交给账号持有人，凭据仅由其在正式客户端输入或经授权安全配置提供。优先使用官方支持的 API 或调试渠道获取请求结构。APP 原生认证域保持服务端证书校验。只记录协议字段名称、类型、状态和合成样本。若正常客户端提示风控、绑定或授权要求，按平台正常流程处理，交接记录保留尚缺的证据。

## 三、当时的原型验收计划

1. 先把实测 API 契约写在 `docs/electricity-api.md`。给 `list_rooms()` 和 `query_balance(room_ref)` 各列一对经过核验的请求/响应结构，注明只读含义及来自哪个页面动作。实际只观察到单表查询时，不猜测房间列表接口。若没有成功会话或真实响应，交付目前的证据矩阵并停在该门槛。
2. 合法授权会话的取得方式及请求结构确认后，在任务目录用现有 Python 3.13 + httpx 实现最小 client。启用 HTTPS 校验，限制到实测业务域和只读接口，凭据从本地受限配置取，保持服务端原有会话和身份校验。先实现一次有效会话的只读访问，再决定是否需自动续期。无需改 Bot 或增加服务。
3. 返回授权房间引用、余额、余量、单位、查询时间。金额和余量用 `Decimal`，缺失为 `None`。用 `httpx.MockTransport` 和合成数据覆盖零值、缺值、非 JSON、业务失败、403/会话失效、超时及单位差异。验证敏感数据不会出现在报告或日志。使用真实账号仅做用户授权的 read-only 核验。
4. 同一房间完成两组 APP 与 Python 对照，核对时间和数值。关闭 VM 与任务代理后再跑一次独立查询。只有服务端实际返回并通过对照才算完成。记录真实核验次数、上游未提供字段、续期状态及当前硬限制。

原计划要求关停 AVD 后再查询一次。已记录的宿主机直连对照均在 AVD 仍运行时完成，关停验证仍待实测。[当前对照](electricity-api.md)
