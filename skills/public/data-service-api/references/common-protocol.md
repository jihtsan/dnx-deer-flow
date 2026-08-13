# 数据服务通用协议

## 环境与 URL

- 测试环境 host：`dmp-gateway.data-platform-dev.svc.swdnk8s.local`
- 生产环境 host：原文未提供。
- 已确认网关前缀：`/api/api_platform/openapi`
- 完整模板：`https://{host}/api/api_platform/openapi/{path}`
- 请求方式：`POST`
- 请求载荷：JSON body，`Content-Type: application/json`

> 来源说明：curl 示例使用 `/api_platform/openapi/`，与正文模板不同；现已按确认结果统一采用 `/api/api_platform/openapi`。Python 客户端通过 `DATA_SERVICE_BASE_URL` 注入包含该前缀的完整基地址，不自动修正路径。

## 请求头

| Header | 来源状态 | 说明 |
| --- | --- | --- |
| X-App-Key | 正式定义 | 调用方应用 AccessKey |
| X-App-Secret | 正式定义 | 调用方应用 AccessSecret |
| X-App-Nonce | 已确认必传 | 每次请求生成 12 位小写十六进制随机串，且单次唯一 |
| X-App-Timestamp | 已确认必传 | 13 位 Unix 毫秒时间戳 |

`X-App-Timestamp` 和 `X-App-Nonce` 每次请求均需生成。时间戳使用 Unix 毫秒；Nonce 使用密码学安全随机源生成 12 位小写十六进制串，并保证单次请求唯一。当前协议不额外推导未定义的签名算法。

## Group By 规则

- `group` 只允许一个字符串值，不允许数组或多个分组字段。
- 使用 strftime 风格格式：`%Y-%m`、`%Y-%m-%d`、`%Y-%m-%d %H`、`%Y-%m-%d %H:%M`、`%Y-%m-%d %H:%M:%S`。
- 是否必传仍以各接口原始“必传”列为准。
- 默认分组、排序和空值处理暂未定义，客户端不自行补默认值。

## 分页参数

| 参数 | 说明 |
| --- | --- |
| _pageNum | 页码，不传默认不分页 |
| _pageSize | 分页大小，如不传默认每页20条 |

`_pageNum` 不传时默认不分页；`_pageSize` 不传时原文给出的默认值为 20。

## 统一响应

| 字段 | 说明 |
| --- | --- |
| code | 返回code |
| msg | 返回信息 |
| data | 返回数据 |

- 成功示例：`code = 0`。
- 失败示例：`code = 42202`，`data = null`。
- 响应头 `X-Request-Id` 用于链路诊断，客户端应在异常中保留但避免打印密钥。
- 分页 `data` 字段包含 `total`、`current`、`pages`、`size`、`records`。

## 实现约束

1. 不在日志、异常或生成文件中输出 `X-App-Secret`。
2. 固定使用 `POST` 和 JSON body；URL 前缀固定为 `/api/api_platform/openapi`。
3. 将 `code != 0` 作为业务失败处理，同时保留 HTTP 状态与 `X-Request-Id`。
4. 不把分页字段强制转换为数字；原示例以字符串返回。
5. 校验 `group` 为单个受支持的格式字符串，不接受列表或多个值。
