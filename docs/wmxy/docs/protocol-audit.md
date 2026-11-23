# 完美校园 5.8.19 认证链路与用电查询协议审计

基于 `analysis/business/sources/` 的静态审计（DEX SHA-256 前缀 `d9a08cee`）。第 1–4 节描述反编译代码分支。原生认证成功报文、Token 续期仍待实测。已实测的 H5 请求以[电费接口](electricity-api.md)为准。

---

## 1. 密钥交换协议 (`exchangeSecretkey.action`)

### 1.1 触发与生命周期
- **入口**: [`AbstractNetRequestProcess.java:311`] `changeKey()`。
- **并发控制**: 独占锁 `LOCK_CHANGE_KEY`；若 `mApplicationPreference.getChangeKeyStatus() == true` 直接跳过。
- **网络预算**: `setTimeout(4000, 6000)`（TCP 连接 4 秒，Socket 读取 6 秒）。失败时调用 `switchDomain()` 在主域 `app.17wanxiao.com` 与备用域 `59wanmei.com` 之间切换，重试 1 次后抛出异常。
- **目标路径**: `getServerRootUrl() + "cam_iface46/exchangeSecretkey.action"`。

### 1.2 客户端密钥对生成
- **实现**: [`RSAUtil.java:147-161`]。
- **算法**: RSA 1024-bit (`KeyPairGenerator.getInstance("RSA")`, `SecureRandom`).
- **编码格式**:
  - 公钥 (`publicKey`): X.509 `SubjectPublicKeyInfo` 结构经标准 Base64 编码字符串。
  - 私钥 (`privateKey`): PKCS#8 `EncodedKeySpec` 结构经标准 Base64 编码字符串。

### 1.3 请求报文结构
- **类定义**: [`ChangeKeyReqData.java:26`]。
- **明文 JSON 结构**:
  ```json
  {
    "key": "<Base64 编码的 RSA 1024-bit X.509 公钥>"
  }
  ```
- **加密行为**: [`ChangeKeyEncrypt.java:28`] 中 `encrypt(byte[] sourceBytes)` 实现为 `return sourceBytes;`。客户端公钥请求正文以**明文 JSON 字符串**发出，未作对称加密。

### 1.4 响应解密与密钥提取
- **传输层响应**: HTTP 200，正文为 Base64 编码的 RSA 分段密文。
- **解密处理**: [`ChangeKeyEncrypt.java:18-25`] 调用 `RSAUtil.decryptByPrivateKey(Base64.decode(encodeBytes, 0), this.decryptKey)`。
- **解密算法**: `Cipher.getInstance("RSA")`（默认 `RSA/ECB/PKCS1Padding`）。密文按 `MAX_DECRYPT_BLOCK = 128` 字节分块解密拼接。
- **解密后 JSON 结构**:
  ```json
  {
    "key": "<3DES 对称密钥字符串，24 字节>",
    "session": "<会话 SessionId 字符串>"
  }
  ```
- **状态持久化**:
  - `mApplicationPreference.setSessionId(resultData.getSession())`
  - `mApplicationPreference.setKen(resultData.getKey())`（源码方法名笔误为 `setKen`，实际存入 key）
  - `mApplicationPreference.setChangeKeyStatus(true)`
  - `mApplicationPreference.setLoginStatus(false)`

---

## 2. 密码字段逐字符加密机制

### 2.1 UI 拦截与事件监听
- **核心类**: [`PwdInputBusiness.java:37-178`] `EditChangedListener`。
- **防拷贝控制**:
  - `disableCopyAndPaste`: 拦截长按 `setOnLongClickListener`，禁用长按菜单。
  - `setInsertionDisabled`: 反射修改 `TextView` 的 `mEditor.mInsertionControllerEnabled` 为 `false`。
  - `setCustomSelectionActionModeCallback`: 屏蔽复制、剪切、全选等操作。
- **掩码逻辑**:
  - 监听 `onTextChanged(s, start, before, count)` 计算增量。
  - `afterTextChanged`: UI 文本立即被替换为等长星号 `*`（`inputStarForEdit`），光标重置。

### 2.2 逐字符加密流程
- **加密类**: [`PwdEncryptUtil.java:16-53`]。
- **存储容器**: `List<String> passWords = new ArrayList<>()`。
- **逐字处理**:
  - 当输入发生时，每输入 1 个字符：
    ```java
    String str2 = new String(this.tripleDESEncry.encryptPwd(password.getBytes()));
    this.passWords.add(index, str2);
    ```
  - 当退格删除时：`this.passWords.remove(index)`。
- **3DES 加密算法参数**:
  - 算法: `DESede/CBC/PKCS5Padding`。
  - 密钥: `SecretKeyFactory.getInstance("DESede").generateSecret(new DESedeKeySpec(app.getKey().getBytes()))`。
  - 静态 IV: 常量 `StubApp.getString2("39173")` = `"66666666"`（UTF-8 字节：`[0x36, 0x36, 0x36, 0x36, 0x36, 0x36, 0x36, 0x36]`）。
  - Base64: `Base64.encode(cipher.doFinal(sourceBytes), 0)`（Android `Base64.DEFAULT`，含尾部 `\n`）。

### 2.3 密码字段的合成结构示意
若明文密码为 `"secret123"`（长度 9），`password` 字段在 JSON 序列化时为 9 个独立 Base64 字符串构成的 JSON 数组：
```json
"password": [
  "U2FsdGVkX18=\n",
  "R0FNQkxF...=\n",
  "..."
]
```
每个元素为对应单字符 UTF-8 字节经 3DES-CBC（IV `"66666666"`）加密后的 Base64 串。

---

## 3. 请求封装与数字签名 (`loginnew.action`)

### 3.1 接口与通信规范
- **目标路径**: `getServerRootUrl() + "cam_iface46/loginnew.action"`。
- **协议入口**: [`LoginBeforeNetRequestImpl.java:89-123`] `postProcess()`。

### 3.2 内层业务 JSON Schema (`LoginReqData`)
由 [`LoginReqData.java`] 经 FastJSON 序列化：
```json
{
  "appCode": "wanxiao",
  "deviceId": "c88f17a9-...",
  "netWork": "WIFI",
  "password": [
    "<3DES密文_字符1>\n",
    "<3DES密文_字符2>\n"
  ],
  "qudao": "official",
  "shebeixinghao": "sdk_gphone64_arm64",
  "sms": "",
  "systemType": "android",
  "telephoneInfo": "11",
  "telephoneModel": "sdk_gphone64_arm64",
  "token": "",
  "type": "1",
  "unionid": "",
  "userId": "",
  "userName": "13800000000",
  "wanxiaoVersion": 10581901,
  "yunyingshang": "中国移动"
}
```
*注：`type` 字段枚举：`"1"` 密码登录、`"2"` 短信登录、`"3"` Token 登录/续期、`"4"` 微信、`"5"` QQ。*

### 3.3 外层 3DES 封装与签名头
1. **对称加密**:
   - [`TripleDESEncry.java:37-48`] `encrypt()`：
     内层 JSON 字符串的 UTF-8 字节经 `DESede/CBC/PKCS5Padding`（Key = `app.getKey()`, IV = `"66666666"`）加密并转 Base64。
2. **外层传输 JSON**:
   ```json
   {
     "session": "<协商获取的 SessionId>",
     "data": "<内层 JSON 3DES 加密后的 Base64 字符串>"
   }
   ```
3. **签名 Header (`campusSign`)**:
   - 类: [`SHA256Util.java:22-26`]。
   - 计算: 对外层 JSON 字符串求 SHA-256 哈希，格式化为 64 字符全小写十六进制字符串。
   - 请求头: `campusSign: <64-char hex>`。

### 3.4 响应完整性校验与解密
1. 校验响应头 `campusSign`：必须与服务端响应体原始字符串的 SHA-256 完全一致，不一致则直接抛弃并报错。
2. 解密 `data` 字段：取响应 JSON 的 `data` 字段，Base64 解码后使用协商的 3DES 密钥与静态 IV 解密，恢复为业务结果 JSON。

---

## 4. Token 续期与自动登录机制

### 4.1 自动登录入口
- **源码入口**: [`AbstractNetRequestProcess.java:168-276`] `autoLogin()`。
- **触发条件**:
  - `LoginAfterNetRequestImpl` 在每次发起业务请求前自动执行：
    ```java
    changeKey();
    autoLogin();
    ```
  - 检查 `mApplicationPreference.getLoginStatus()`：若已登录则直接退出。
  - 检查 `mApplicationPreference.getToken()`：若 token 为空，直接抛出异常，要求手动登录。

### 4.2 续期报文与响应处理
- **请求配置**:
  - `LoginReqData.type` = `"3"`（`login_type_token`）。
  - `userId` = `mApplicationPreference.getLoginUserInfo().getId()`。
  - `userName` = `mApplicationPreference.getLoginName()`。
  - `token` = `mApplicationPreference.getToken()`。
  - 同样通过 `TripleDESEncry` 与 `campusSign` 封装发送至 `cam_iface46/loginnew.action`。
- **状态流转与 Token 滚动更新**:
  - **成功分支 (`resultStatus == true`)**:
    - 获取新 `LoginUserResult`。
    - 保存响应中的 Token，调用 `mApplicationPreference.setToken(resultData.getToken())`。实际服务端的续期行为仍待验证。
    - 更新用户信息: `mApplicationPreference.setLoginUserInfo(resultData)`。
    - 标记登录就绪: `mApplicationPreference.setLoginStatus(true)`。
    - 初始化支付 SDK 域名: `NewcapecPay.setPaymentDomain(resultData.getAppPayPlatFormUrl())`。
  - **失败分支 (`resultStatus == false`)**:
    - 置空登录状态: `setLoginStatus(false)`。
    - 清除持久化 Token: `setToken("")`。
    - 发送广播 `wanxiao.security.relogin` 通知 UI 弹出重新登录提示。

---

## 5. 电费业务链路追溯 ("金明 → 缴费 → 用电支出")

### 5.1 Hybrid H5 子应用
- 已恢复的源码中，未找到负责用电房间或电表查询的原生类。子应用由 `SubApp` 表示。
  - [`SubAppManager.java:337-410`]: 当子应用类型为 Web/H5（类型代码 `3020` / `3022` / `3026` / 默认）时，统一由 [`WXWebViewActivity.open(context, app.getName(), app.getPath())`] 承载。

### 5.2 认证信息向 H5 容器的注入链路
原生容器代码中定义了三种给子应用传递身份的机制。静态代码只能确认这些入口存在：

1. **URL 参数拼装 (`WXWebView.appUrlArg`)**:
   - `customId`: 河南大学机构代码（源自 `LoginUserResult.getCustomId()`）。
   - `sessionId`: 密钥交换所得有效会话。
   - 客户端版本与平台标记。
2. **Cookie 动态注入 (`WXWebView.setCookie`)**:
   - 对 `sessionId` 执行自定义字符置换表映射（[`WXWebView.java:31-98`] `encodeMap`，如 `'a'->'C', 'b'->'A', 'c'->'6'`）。
   - 置换后的字符串经 Base64 编码，格式化后写入 `CookieManager`（作用域 `.17wanxiao.com` / `.59wanmei.com`）。
3. **JSBridge 原生接口绑定 (`JsApiHelper.java:80-144`)**:
   - `wanxiao` 命名空间注入 [`AuthenticateJsExecutor`]：提供 `getToken()`（返回 sessionId）、`getUserJsonValue()`（返回包含 userId, studentId, customId, token 的 JSON）。
   - `GetPageCredentialsJsExecutor`: 提供临时票据获取接口。
   - `EcardPayJsMethod` / `PayJsMethod`: 提供新开普支付平台交互。

### 5.3 后续实测

WebView 中观察到云缴费用电页面和携带 Cookie 的只读查询。同一账号历史记录的空调、照明表分别显示剩余电量。实际接口与 Python 样本见[电费接口](electricity-api.md)。进入具体 H5 时是否调用对应 JSBridge、云缴费如何创建和续期会话，以及房间目录完整结构仍待实测。
