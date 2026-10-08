# 青木对接文档调查笔记

调查日期：2026-10-09。调查对象：微信附件《对接(1).md》。本报告仅审读附件静态内容，未向其接口发送任何请求，未验证登录、余额、下单、补刷、改密或实际课程执行效果。

## 结论

这份附件展示的是一个以“青木”作为接入标识、使用 `api.php?act=...` 的网课服务订购及订单管理接口，并附带大规模商品与分类价格快照。它提供给“29系统”的 PHP 接入片段，业务围绕学生账号、课程下单、补刷、订单进度和更新密码字段。结合“整本”“时长”“考试”“作业”“慢刷”等商品名称，可以判断它更接近网课代刷/代做服务的商业渠道接入资料；文档没有证明它是河南大学、学习通或其他平台的官方开放 API，也没有证明供应商实际履约能力。其法律主体、所属企业、授权关系和数据处理约定均无法从附件确认。[接入说明与查课代码](source.sanitized.md?plain=1#L1-L107)、[下单与补刷](source.sanitized.md?plain=1#L110-L237)、[首批商品示例](source.sanitized.md?plain=1#L401-L579)

附件列出的服务器基址为 `https://103.52.155.128`。推荐项目包括学习通/智慧树使用青木，U校园/Welearn/FIF 使用 PUP，中国大学 MOOC 使用龙龙；推荐文字和目录商品并不是服务可用性或兼容性的实测证据。[推荐项目](source.sanitized.md?plain=1#L1)、[接口地址](source.sanitized.md?plain=1#L6-L8)

## 五类接口契约及缺口

下面“必传/可选”只复述参数表；“示例差异”来自附件 PHP 片段。所有接口均列为 POST，鉴权均使用供应商对接账号 `uid` 与 `key`。文档没有给出 Content-Type、字符编码、HTTP 状态码、限流或完整请求/响应样例；PHP 的 `get_url()` 实现没有包含在附件中，不能据此确认请求是表单还是 JSON。[查课参数和代码](source.sanitized.md?plain=1#L8-L107)、[其他接口](source.sanitized.md?plain=1#L110-L368)

| 接口 | 路径 act | 参数表 | PHP 示例差异与响应信息 | 来源 |
| --- | --- | --- | --- | --- |
| 查课 | `get` | 必传 `uid,key,platform,user,pass,school` | 参数表重复一遍；代码还传 `kcid`。直接返回 JSON 解码结果，未给成功码或课程列表结构。 | [L8-L107](source.sanitized.md?plain=1#L8-L107) |
| 下单 | `add` | 必传 `uid,key,platform,user,pass,kcname,kcid` | 代码额外传 `school`；将上游 `code == "0"` 转为本地 `code=1,msg=下单成功`，失败转 `code=-1`。未展示上游订单 ID 或完整成功响应。 | [L112-L185](source.sanitized.md?plain=1#L112-L185) |
| 补刷 | `budan` | 必传 `uid,key,id`，`id` 描述为“订单账号” | 代码将订单变量 `$yid` 赋给 `id`，其描述与订单 ID 含义可能冲突。直接返回上游结果，没有成功码或响应结构。 | [L191-L235](source.sanitized.md?plain=1#L191-L235) |
| 同步/进度查询 | `chadanoid` | 必传 `uid,key`；可选 `yid,username,school` | 代码同时传 `username,yid`，不传 `school`；未说明同时提供时优先级、全部可选缺省时行为。`code == "1"` 成功，遍历 `data`。 | [L241-L312](source.sanitized.md?plain=1#L241-L312) |
| 改密 | `xgmm` | 必传 `uid,key,yid,pwd` | 代码发送 `oid` 而不是表中的 `yid`，直接返回上游结果，没有响应或成功码说明。 | [L318-L368](source.sanitized.md?plain=1#L318-L368) |

### 对接前必须核实的契约问题

1. **`xgmm` 的 `yid/oid` 冲突。** 参数表明确写 `yid`，示例明确写 `oid`。没有服务实现或供应商确认，不能任选其一做生产调用。该请求只有订单标识与新密码，没有学校/平台/学生账号字段；这既可能更新订单保存的登录密码，也可能触发其他操作，文档不足以判断。接口标题“改密”不证明它真正修改学生在校园/学习平台上的密码。[参数表](source.sanitized.md?plain=1#L338-L348)、[代码](source.sanitized.md?plain=1#L359-L368)
2. **返回码不是统一的。** `add` 使用上游 `0` 成功，`chadanoid` 使用上游 `1` 成功；查课、补刷、改密没有成功码。不能在统一封装中写一个适用于全部 action 的“code=1 即成功”判断。还需确认返回类型是数值还是字符串，以及业务失败和 HTTP 失败的边界。[下单代码](source.sanitized.md?plain=1#L177-L185)、[同步代码](source.sanitized.md?plain=1#L293-L311)
3. **下单没有可保存的订单回执。** 示例仅返回本地成功提示，不保留上游订单 ID；同步和改密随后却需要订单标识。需明确 `add` 完整响应是否有 ID、字段名是什么，查不到回执时怎样避免重试重复扣费或重复订单。文档未描述幂等键、退款、取消、余额、订单重复检测。[下单](source.sanitized.md?plain=1#L175-L185)、[同步参数](source.sanitized.md?plain=1#L261-L277)、[改密参数](source.sanitized.md?plain=1#L338-L348)
4. **进度字段只有读取代码，没有真实样例。** 示例读取 `data[].id/kcname/status/process/remarks/courseStartTime/courseEndTime/examStartTime/examEndTime`。没有字段类型、空值规则、状态枚举、进度单位或时间格式；读取的课程起止时间也没有进入最终返回数组。同步失败示例把整个 `$result` 放入 `msg`，与下单失败传字符串 `$result["msg"]` 的类型不一致。需要独立响应模型与供应商样例。[进度处理](source.sanitized.md?plain=1#L296-L311)
5. **鉴权与学生凭据是两组数据。** `uid/key` 属于对接鉴权；`user/pass` 属于学生账号与密码。示例兼容“29系统”的配置字段，把 `$a["user"]/$a["pass"]` 映射成上游 `uid/key`；`token：key` 是该系统配置提示，不能当作独立 OAuth token 机制。进度示例还把 `$user/$pass` 放入返回数组，照搬会扩大密码暴露范围。[配置提示和查课请求](source.sanitized.md?plain=1#L88-L106)、[进度返回](source.sanitized.md?plain=1#L307)
6. **参数范围缺失。** 查课额外传 `kcid`，下单额外传 `school`；表与代码没有说明这些字段是否可省略。同步的 `yid/username/school` 又全部标为可选，组合检索规则与授权隔离规则无法确定。[查课](source.sanitized.md?plain=1#L16-L101)、[下单](source.sanitized.md?plain=1#L120-L175)、[同步](source.sanitized.md?plain=1#L261-L291)

## 商品、分类与数字标识

### 商品数字不是学生课程 `kcid`

商品表头第一列为“对接ID”，第二列为“分类id”，第三列为“商品名称”。首条商品是对接 ID `24857`、分类 `109`、名称“PUP-U校园 整本”；这是售卖服务的商品目录记录，不是某个学生查询返回的课程记录。下单契约另有“课程ID”字段 `kcid` 与“课程名字” `kcname`。因此不能把 `24857` 或 `109` 直接塞入 `kcid`。查课/下单代码将 `$noun` 填入 `platform`，提示接入系统可能通过商品对接 ID 选择平台/服务，但附件没有 `$noun` 的来源或完整映射，故“商品对接ID → platform”仍须确认。[商品表头及首条记录](source.sanitized.md?plain=1#L377-L419)、[课程字段](source.sanitized.md?plain=1#L150-L160)、[请求构造](source.sanitized.md?plain=1#L101)、[下单请求构造](source.sanitized.md?plain=1#L175)

建议暂时区分四个概念：商品对接 ID、分类 ID、学生课程 ID `kcid`、上游订单 ID。订单 ID 又存在 `id/yid/oid` 三种字段写法，需逐接口确认；它们不能因为都是数字就共用一个字段。[补刷](source.sanitized.md?plain=1#L211-L230)、[同步](source.sanitized.md?plain=1#L261-L298)、[改密](source.sanitized.md?plain=1#L338-L362)

### 静态快照统计

统计方法：对实际文本行号 401–158959 的商品区，提取非空行；每 10 项构成一条商品（对接 ID、分类 ID、名称、7 个价格）。所有条目均满足两个整数标识和 7 个 Decimal 数值；分类区从 158975 行开始按 ID/名称成对解析。以下仅为附件快照，不能证明商品现在仍可购买、价格有效或单位为人民币。[商品区](source.sanitized.md?plain=1#L401-L158959)、[分类区](source.sanitized.md?plain=1#L158975-L159093)

| 统计项 | 结果 |
| --- | --- |
| 实际文本行数 | 159,094；末尾换行按 `split("\n")` 计算会多出一个空片段，不能当作额外文本行 |
| 商品记录 | 7,928 |
| 唯一商品对接 ID | 7,928；最小 24857，最大 69460，编号不连续 |
| 不同商品名称 | 7,916；名称不能作为唯一键 |
| 定义的分类 | 30 |
| 商品实际使用的分类 ID | 28，其中 26 有分类定义，2 无定义 |
| 缺失分类映射 | 164 有 6 条，169 有 4 条，共 10 条商品 |
| 有定义但没有商品的分类 | 94、101、105、107 |
| “你的价格” | 两条为 0；最高值 271039.72896，币种/计价单位未说明 |
| 价格列规律 | 7,928 条“你的价格”均等于 0.2 档；其余档相对 0.2 档分别是 1.5/2/2.5/3/4 倍 |

商品价格表标有“你的价格（0.200）”以及 `0.2/0.3/0.4/0.5/0.6/0.8` 六档。这些更像渠道倍率/等级的报价列，但表头没有定义费率、基础价格或结算单位，不能解释成每分钟、每课或固定人民币价格。零价格记录是“yl-新国开收录”“yl-新版职教云收录”；最高值对应“红杉-河北北方学院（课时）”，这个异常需要供应商复核，不能作为已确认报价。[价格表头](source.sanitized.md?plain=1#L387-L399)、[零价记录](source.sanitized.md?plain=1#L101941-L101979)、[最高值记录](source.sanitized.md?plain=1#L119761-L119779)

| 分类 ID | 附件分类名称 | 商品数 | 分类来源 |
| --- | --- | ---: | --- |
| 61 | 奶昔项目 | 28 | [L158975-L158977](source.sanitized.md?plain=1#L158975-L158977) |
| 62 | 龙龙项目 | 187 | [L158979-L158981](source.sanitized.md?plain=1#L158979-L158981) |
| 64 | 一流项目 | 183 | [L158983-L158985](source.sanitized.md?plain=1#L158983-L158985) |
| 66 | 小青项目 | 120 | [L158987-L158989](source.sanitized.md?plain=1#L158987-L158989) |
| 74 | 特斯拉项目 | 25 | [L158991-L158993](source.sanitized.md?plain=1#L158991-L158993) |
| 79 | 红衫项目 | 68 | [L158995-L158997](source.sanitized.md?plain=1#L158995-L158997) |
| 82 | 优优项目 | 31 | [L158999-L159001](source.sanitized.md?plain=1#L158999-L159001) |
| 83 | 霍希项目 | 22 | [L159003-L159005](source.sanitized.md?plain=1#L159003-L159005) |
| 86 | 五福项目 | 243 | [L159007-L159009](source.sanitized.md?plain=1#L159007-L159009) |
| 87 | 毛豆项目 | 18 | [L159011-L159013](source.sanitized.md?plain=1#L159011-L159013) |
| 94 | 叶族项目 | 0 | [L159015-L159017](source.sanitized.md?plain=1#L159015-L159017) |
| 95 | YYY项目 | 2,817 | [L159019-L159021](source.sanitized.md?plain=1#L159019-L159021) |
| 97 | 继续教育1号渠道 | 1,624 | [L159023-L159025](source.sanitized.md?plain=1#L159023-L159025) |
| 98 | 黑某项目 | 35 | [L159027-L159029](source.sanitized.md?plain=1#L159027-L159029) |
| 101 | 坤坤项目 | 0 | [L159031-L159033](source.sanitized.md?plain=1#L159031-L159033) |
| 102 | 青木项目 | 14 | [L159035-L159037](source.sanitized.md?plain=1#L159035-L159037) |
| 103 | 图图强国 | 3 | [L159039-L159041](source.sanitized.md?plain=1#L159039-L159041) |
| 105 | 狗尚项目 | 0 | [L159043-L159045](source.sanitized.md?plain=1#L159043-L159045) |
| 107 | 独家渠道 | 0 | [L159047-L159049](source.sanitized.md?plain=1#L159047-L159049) |
| 109 | PUP项目 | 54 | [L159051-L159053](source.sanitized.md?plain=1#L159051-L159053) |
| 161 | Tenu项目 | 340 | [L159055-L159057](source.sanitized.md?plain=1#L159055-L159057) |
| 162 | 8090继续教育 | 795 | [L159059-L159061](source.sanitized.md?plain=1#L159059-L159061) |
| 163 | 自营项目 | 48 | [L159063-L159065](source.sanitized.md?plain=1#L159063-L159065) |
| 164 | 未提供分类名称 | 6 | [商品L1401-L1519](source.sanitized.md?plain=1#L1401-L1519) |
| 168 | 至强项目 | 65 | [L159067-L159069](source.sanitized.md?plain=1#L159067-L159069) |
| 169 | 未提供分类名称 | 4 | [商品L5121-L5199](source.sanitized.md?plain=1#L5121-L5199) |
| 171 | 青木继教 | 714 | [L159071-L159073](source.sanitized.md?plain=1#L159071-L159073) |
| 175 | 欲梦项目 | 118 | [L159075-L159077](source.sanitized.md?plain=1#L159075-L159077) |
| 176 | CO项目 | 39 | [L159079-L159081](source.sanitized.md?plain=1#L159079-L159081) |
| 179 | 橘子项目 | 26 | [L159083-L159085](source.sanitized.md?plain=1#L159083-L159085) |
| 180 | 猫猫继续教育 | 164 | [L159087-L159089](source.sanitized.md?plain=1#L159087-L159089) |
| 181 | 茉莉项目 | 137 | [L159091-L159093](source.sanitized.md?plain=1#L159091-L159093) |

全目录不能统称“青木商品”：分类 102“青木项目”只有 14 条，分类 171“青木继教”714 条，分类 109“PUP项目”54 条，其余分布在其他渠道。青木项目包含学习通积分课/普通课/考试/人脸考试/继续教育/直播/阅读/单作业及多类智慧树服务；这些名称只表达卖方商品宣称。[青木首批条目](source.sanitized.md?plain=1#L1521-L1779)、[青木网页版条目](source.sanitized.md?plain=1#L47221-L47239)、[分类定义](source.sanitized.md?plain=1#L159035-L159073)

## 归档的脱敏范围

供公开或长期 GitHub 归档的来源文件应保留结构和行号，命名为 `source.sanitized.md`。已识别的秘密包括开头第 2 行对接 ID、第 3 行对接 KEY，以及第 80025 行商品名称中 URL 的 `user` 和 `token` 查询值；归档已替换其实际值，保留参数名和周边业务结构，不上传未经脱敏的原始附件。PHP 示例中的 `uid/key/user/pass` 是参数或变量名，保留这些名称才能分析接口，不应整行删除。[脱敏后的凭据位置](source.sanitized.md?plain=1#L2-L3)、[脱敏后的商品URL](source.sanitized.md?plain=1#L80021-L80039)、[变量与字段示例](source.sanitized.md?plain=1#L101-L106)

上述范围是此次识别到的显式凭据位置，不构成供应商公开授权证明。统计和报告未使用真实凭据值；本报告也没有包含这些值。

## 可核实与不可核实的边界

**此次已确认：** 附件的文字、字段表、PHP 请求构造和返回码判断；目录记录数、标识唯一性、分类缺失和价格算术关系；改密 `yid/oid`、下单/同步成功码不同等文档冲突。这些是文档层面的证据，不是服务端执行证据。[接口原文](source.sanitized.md?plain=1#L6-L368)、[目录原文](source.sanitized.md?plain=1#L371-L159093)

**此次尚未确认：** IP 当前归属和服务器身份、可信 TLS 通道、接口业务可达性、供应商企业主体、各平台授权、真实响应、学生课程 ID 来源、订单回执、实际扣费、履约/完成状态、退款规则、保存密码方式、数据保留周期，以及 `xgmm` 的真正效果。附件没有提供这些证明；本次也没有使用学生账号或对接凭据做线上测试。

**下一步建议：** 先向提供方索取无敏感信息的请求/响应样例和测试环境说明，逐项确认 `platform` 映射、`get` 课程结构、`add` 订单 ID、三类订单参数写法和各 action 成功码，再决定是否适合接入现有系统。只查文档、保存资料并不代表已经集成；未澄清契约前不宜把这份 PHP 片段直接作为生产实现。

## 无凭据网络核查补充

静态审读之外，主调查进行了默认信任链的 TLS 握手核查，结果为自签名证书验证失败；没有进行 HTTP 业务调用。验证方式与限制见 [归档说明](README.md#脱敏与验证边界)。这不能代替真实响应、扣费或履约验收。
