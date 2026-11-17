# 投资机构与投资人推荐 API

面向外部服务端的 API，提供投资机构（Fund）和投资人（Fund Investor）的查询能力。适用于"给定创业者的赛道 / 阶段 / 金额 / 币种，推荐匹配的投资人"等场景。

> **命名约定**：**Fund** 指投资机构（如「红杉资本」）；**Fund Investor** 指投资人个人（如「张三」）。

---

## 一、快速开始

### 1. 获取访问令牌

采用 OAuth 2.0 客户端凭证模式（`client_credentials`）。调用方需向 API 提供方申请 `client_id` / `client_secret`，然后用它们换取 `access_token`。

```bash
curl -X POST "https://api.miracleplus.com/build/oauth/token" \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "client_credentials",
    "client_id": "<你的 client_id>",
    "client_secret": "<你的 client_secret>",
    "scope": "public funds"
  }'
```

响应：

```json
{
  "access_token": "<access_token>",
  "token_type": "Bearer",
  "expires_in": 7200,
  "scope": "public funds"
}
```

> **重要**：请求 token 时必须带上 `"scope": "public funds"`，否则拿到的令牌无法访问本组接口。
> 令牌有效期 7200 秒（2 小时），过期后重新获取即可。

### 2. 调用接口

所有接口都通过 `Authorization: Bearer <access_token>` 携带令牌：

```bash
curl -X GET "https://api.miracleplus.com/build/open_api/v2/server/fund_investors?sector_focus=AI&include=research" \
  -H "Authorization: Bearer <access_token>"
```

---

## 二、通用约定

### Base URL

```
https://api.miracleplus.com/build/open_api/v2/server
```

### 请求头

| Header | 必填 | 值 |
|---|---|---|
| `Authorization` | 是 | `Bearer <access_token>` |

### 响应格式

- 所有响应均为 JSON
- 时间字段统一 ISO 8601（UTC）：`YYYY-MM-DDTHH:mm:ssZ`
- ID 字段为字符串类型
- 空值字段返回 `null`，不省略
- **多值字段返回数组**：如 `org_sector_focus` 返回 `["AI", "消费"]` 而非 `"AI;消费"`

### 分页

所有列表接口支持分页：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | 页码，从 1 开始 |
| `page_size` | int | 20 | 每页条数，最大 100 |

响应结构：

```json
{
  "total": 18184,
  "page": 1,
  "page_size": 20,
  "items": [ ... ]
}
```

- `total` 是符合筛选条件的总记录数（不是全库总数）
- 翻页：`page=2`、`page=3`…

### 多值查询参数

需要传多个值的筛选条件（如多个赛道），支持两种形式：

```
# 逗号分隔
sector_focus=AI,SaaS

# 数组形式
sector_focus[]=AI&sector_focus[]=SaaS
```

两者等价，命中任一即返回（OR 语义）。

### 错误处理

| HTTP 状态码 | 含义 | 触发场景 |
|---|---|---|
| 400 | 参数错误 | 枚举/金额格式校验失败（`validation_error`），见下方「筛选宽容度」 |
| 401 | 未授权 | 令牌无效/过期/缺失，或缺少 `funds` scope |
| 404 | 不存在 | 指定 ID 的资源不存在 |

错误响应：

```json
{
  "error": "not_found",
  "message": "Fund not found"
}
```

> **筛选宽容度**：查询参数以宽松为主——筛选类参数（如 `sector_focus`、`currency`）传错不报错，查不到就返回空列表；未知参数被忽略；分页越界和金额语义（单传、负数、上下限写反）会被宽容处理。
>
> **严格校验（仅防"静默错误"）**：以下情况返回 HTTP 400 `validation_error`，message 会说明具体参数——非法枚举值（两个接口的 `include`/`order_by`，投资人接口的 `compliance_overall`，基金接口的 `data_confidence`）和非数字金额（`min_amount`/`max_amount` 必须是有限数字）。传错这些参数会让接口"看似正常实则没生效"，因此直接报错。

---

## 三、投资机构接口（Fund）

### 1. 获取投资机构列表

```
GET /funds
```

#### 请求参数

| 参数 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `q` | string | 机构名称模糊搜索（含别名） | `q=红杉` |
| `sector_focus` | string[] | 投资领域，命中任一即返回 | `sector_focus=AI,SaaS` |
| `investment_stage` | string[] | 投资阶段，命中任一即返回 | `investment_stage=早期,成长期` |
| `headquarters` | string | 总部城市，模糊匹配 | `headquarters=北京` |
| `data_confidence` | string | 数据置信度，取值 `高`/`中`/`低`，非法值返回 400 | `data_confidence=高` |
| `include` | string | 附加返回的字段组，逗号分隔，见下方 | `include=research,investors_count` |
| `order_by` | string | 排序方式，见下方 | `order_by=updated_at_desc` |
| `page` / `page_size` | int | 分页 | `page=1&page_size=20` |

#### `include` 字段组

不传 `include` 时仅返回基础字段。传以下值可追加对应字段：

| 值 | 追加内容 |
|---|---|
| `research` | 机构调研详情：简介、成立年份、管理规模、投资阶段、投资领域、资金来源、总部、代表案例、官网、团队规模、置信度、验证状态、调研来源、调研时间 |
| `investors_count` | 该机构下的投资人数 |

> `include` 只接受上表值，逗号组合，任一非法值返回 HTTP 400 `validation_error`。

#### `order_by` 排序

| 值 | 排序规则 |
|---|---|
| `updated_at_desc` | 更新时间倒序 |
| 不传 | 默认按创建时间倒序 |
| 传未知值 | HTTP 400 `validation_error` |

#### 响应示例（基础字段）

```json
{
  "total": 10879,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "10837",
      "chinese_name": "Cook'd AI",
      "english_name": null,
      "full_name": "Cook'd AI",
      "other_name": null,
      "created_at": "2026-07-29T13:07:18Z",
      "updated_at": "2026-07-29T13:07:18Z"
    }
  ]
}
```

#### 响应示例（`include=research` 追加字段）

```json
{
  "org_description": "AI投行面试准备平台...",
  "org_founded_year": "2023",
  "org_aum": null,
  "org_investment_stage": null,
  "org_sector_focus": ["AI", "职业教育", "投行面试准备"],
  "org_funding_source": null,
  "org_headquarters": "北京市朝阳区",
  "org_notable_portfolio": null,
  "org_website": null,
  "org_people_count": null,
  "org_data_confidence": "中",
  "org_verification_status": "verified",
  "org_sources": ["https://www.qcc.com/...", "https://aiqicha.baidu.com/..."],
  "research_timestamp": "2026-07-27T07:21:21Z",
  "last_updated": "2026-07-28T06:21:22Z"
}
```

---

### 2. 获取单个投资机构

```
GET /funds/:id
```

返回该机构的完整信息：基础字段 + 全部调研详情 + 投资人数。无需传 `include`。

```bash
curl -X GET "https://api.miracleplus.com/build/open_api/v2/server/funds/10837" \
  -H "Authorization: Bearer <access_token>"
```

不存在返回 404。

---

## 四、投资人接口（Fund Investor）

### 3. 获取投资人列表

```
GET /fund_investors
```

支持按「所属机构维度」与「投资人个人维度」组合筛选，常用于按创业者画像推荐匹配的投资人。

#### 请求参数

**通用**：

| 参数 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `q` | string | 按投资人姓名或所属机构名模糊搜索 | `q=张三` |
| `fund_id` | string | 按所属机构 ID 精确筛选 | `fund_id=26` |
| `include` | string | 附加返回的字段组，取值 `research`/`compliance`，逗号分隔 | `include=research,compliance` |
| `order_by` | string | 排序方式，见下方 | `order_by=score_desc` |
| `page` / `page_size` | int | 分页 | `page=1&page_size=20` |

**按所属机构筛选**（筛选的是机构属性，返回符合机构条件的投资人）：

| 参数 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `sector_focus` | string[] | 机构投资领域，命中任一 | `sector_focus=AI,SaaS` |
| `investment_stage` | string[] | 机构投资阶段，命中任一 | `investment_stage=早期` |

**按投资人个人筛选**：

| 参数 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `currency` | string[] | 币种，命中任一即返回；按**能力包含**匹配——传「人民币」会同时命中币种为「人民币」和「双币种（美元&人民币）」的投资人 | `currency=人民币,美元` |
| `person_expertise` | string[] | 个人专长领域，命中任一 | `person_expertise=AI` |
| `min_amount` + `max_amount` | number | 创业者融资区间（单位：万元人民币），返回投资范围与该区间**有重叠**的投资人。可单传或双传，详见下方匹配规则 | `min_amount=1000&max_amount=2000` |
| `compliance_overall` | string | 合规整体结论，取值 `trusted`/`suspect`/`risk` | `compliance_overall=trusted` |

#### 金额匹配规则

支持**双传**（区间重叠）或**单传**（半开区间）：

- **双传 `min_amount` + `max_amount`**：返回投资范围与创业者融资区间**有重叠**的投资人。

  ```
  投资人投资范围 [inv_min, inv_max]
  创业者融资需求 [min_amount, max_amount]
  匹配条件：inv_min ≤ max_amount 且 inv_max ≥ min_amount
  ```

  示例（投资人投资范围 1000 万 ~ 3000 万）：
  - 创业者融资 500 万 ~ 2000 万 → **匹配**（区间重叠）
  - 创业者融资 5000 万 ~ 6000 万 → **不匹配**（不重叠）

- **只传 `min_amount`**：返回**投资上限 ≥ min_amount**的投资人（即能投到这个金额及以上的）。
  匹配条件：`inv_max ≥ min_amount`

- **只传 `max_amount`**：返回**投资下限 ≤ max_amount**的投资人（即单笔下限不高于这个金额的）。
  匹配条件：`inv_min ≤ max_amount`

> `inv_min` / `inv_max` 为空的投资人无法判定金额范围，单传或双传都不会返回。

#### `include` 字段组

| 值 | 追加内容 |
|---|---|
| `research` | 个人调研详情：币种、投资方向、出手数、投资金额上下限、职业背景、代表案例、投资偏好、LinkedIn、教育背景、工作经历、专业领域、置信度、验证状态、调研来源、调研时间 |
| `compliance` | 合规核查：姓名核查、机构核查、职位核查、角色匹配、出手核查、整体结论、核查明细、搜索摘要、给创业者的建议 |

> **本接口不返回联系方式**（电话、邮箱、工作地），出于隐私保护。

#### `order_by` 排序

| 值 | 排序规则 |
|---|---|
| `score_desc` | 会面评分降序 |
| `updated_at_desc` | 更新时间倒序 |
| `created_at_desc` | 创建时间倒序 |
| `inv_min_desc` | 投资金额下限降序 |
| `inv_max_desc` | 投资金额上限降序 |
| 不传 | 默认按评价数倒序，再按创建时间倒序 |
| 传未知值 | HTTP 400 `validation_error` |

#### 响应示例（基础字段）

```json
{
  "total": 18184,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "1393",
      "name": "陈于思",
      "organization": "纪源资本",
      "job_title": "投资人",
      "field": "",
      "reviews_count": 17,
      "score": "B",
      "fund": {
        "id": "2524",
        "chinese_name": "纪源资本",
        "english_name": null
      },
      "created_at": "2020-11-30T16:45:32Z",
      "updated_at": "2026-01-28T12:42:23Z"
    }
  ]
}
```

#### 响应示例（`include=research` 追加字段）

```json
{
  "currency": "双币种（美元&人民币）",
  "direction": "AI应用，大模型",
  "deal_count_verified": null,
  "inv_range": "3000万元",
  "inv_min": 3000,
  "inv_max": 3000,
  "person_background": "百度投资并购部投资总监...",
  "person_representative_deals": null,
  "person_investment_preferences": null,
  "person_linkedin": "https://cn.linkedin.com/in/...",
  "person_education": "中国人民大学 统计分析专业 硕士",
  "person_education_detail": null,
  "person_work_experience": "百度 投资并购部 投资总监...",
  "person_expertise": "一级市场股权投资;战略投资;投融资",
  "person_data_confidence": "中",
  "person_verification_status": "verified",
  "person_sources": ["https://cn.linkedin.com/in/...", "https://www.zaih.com/..."],
  "research_timestamp": "2026-01-01T00:00:00Z",
  "last_updated": "2026-07-29T13:15:25Z"
}
```

#### 响应示例（`include=compliance` 追加字段）

```json
{
  "compliance_name_match": "verified",
  "compliance_org_match": "verified",
  "compliance_position_match": "verified",
  "compliance_role_match": "match",
  "compliance_deal_match": "unverifiable",
  "compliance_overall": "trusted",
  "compliance_detail": "姓名、机构和职位由多个公开页面共同锚定...",
  "search_summary": "执行3次GPT原生web_search...",
  "research_notes": "建议founder面谈时重点确认..."
}
```

---

### 4. 获取单个投资人

```
GET /fund_investors/:id
```

返回该投资人的完整信息：基础字段 + 全部调研详情 + 全部合规核查。无需传 `include`。联系方式不返回。

```bash
curl -X GET "https://api.miracleplus.com/build/open_api/v2/server/fund_investors/374" \
  -H "Authorization: Bearer <access_token>"
```

不存在返回 404。

---

## 五、典型场景：给创业者推荐匹配的投资人

某创业者做 AI 赛道、计划融资 1000-2000 万人民币、希望用人民币融资、优先推荐可信投资人：

```bash
curl -X GET "https://api.miracleplus.com/build/open_api/v2/server/fund_investors?sector_focus=AI&min_amount=1000&max_amount=2000&currency=人民币&compliance_overall=trusted&include=research,compliance&order_by=score_desc" \
  -H "Authorization: Bearer <access_token>"
```

返回符合全部条件的投资人列表，含投资金额范围、合规结论与调研详情，调用方可自行排序展示。

---

## 六、字段说明速查

### 投资机构字段

| 字段 | 类型 | 含义 | 何时返回 |
|---|---|---|---|
| `id` | string | 机构 ID | 始终 |
| `chinese_name` | string | 常用名 | 始终 |
| `english_name` | string \| null | 英文名 | 始终 |
| `full_name` | string \| null | 全称 | 始终 |
| `other_name` | string \| null | 别名 | 始终 |
| `created_at` / `updated_at` | string | 创建/更新时间 | 始终 |
| `org_description` | string | 机构简介 | `include=research` 或详情 |
| `org_founded_year` | string | 成立年份 | 同上 |
| `org_aum` | string | 管理规模 | 同上 |
| `org_investment_stage` | string | 投资阶段 | 同上 |
| `org_sector_focus` | string[] | 投资领域（数组） | 同上 |
| `org_funding_source` | string | 资金来源 | 同上 |
| `org_headquarters` | string | 总部城市 | 同上 |
| `org_notable_portfolio` | string[] | 代表案例（数组） | 同上 |
| `org_website` | string | 官网 | 同上 |
| `org_people_count` | string | 团队规模 | 同上 |
| `org_data_confidence` | string | 数据置信度（高/中/低） | 同上 |
| `org_verification_status` | string | 验证状态（verified/plausible/unverified） | 同上 |
| `org_sources` | string[] | 调研来源 URL（数组） | 同上 |
| `research_timestamp` / `last_updated` | string | 调研/更新时间 | 同上 |
| `fund_investors_count` | int | 投资人数 | `include=investors_count` 或详情 |

### 投资人字段

| 字段 | 类型 | 含义 | 何时返回 |
|---|---|---|---|
| `id` | string | 投资人 ID | 始终 |
| `name` | string | 姓名 | 始终 |
| `organization` | string \| null | 所属机构名 | 始终 |
| `job_title` | string \| null | 职位 | 始终 |
| `field` | string \| null | 领域 | 始终 |
| `reviews_count` | int | 会面评价数 | 始终 |
| `score` | string \| null | 评分（S/A/B/C） | 始终 |
| `fund` | object | 所属机构 `{id, chinese_name, english_name}` | 始终 |
| `created_at` / `updated_at` | string | 创建/更新时间 | 始终 |
| `currency` | string | 币种 | `include=research` 或详情 |
| `direction` | string | 投资方向 | 同上 |
| `deal_count_verified` | string | 出手数 | 同上 |
| `inv_range` | string | 投资范围（原文） | 同上 |
| `inv_min` / `inv_max` | int | 投资金额上下限（万元人民币） | 同上 |
| `person_background` | string | 职业背景 | 同上 |
| `person_representative_deals` | string | 代表案例 | 同上 |
| `person_investment_preferences` | string | 投资偏好 | 同上 |
| `person_linkedin` | string | LinkedIn | 同上 |
| `person_education` | string | 教育背景 | 同上 |
| `person_education_detail` | string | 教育时间线 | 同上 |
| `person_work_experience` | string | 工作经历 | 同上 |
| `person_expertise` | string | 专长领域 | 同上 |
| `person_data_confidence` | string | 数据置信度 | 同上 |
| `person_verification_status` | string | 验证状态 | 同上 |
| `person_sources` | string[] | 调研来源 URL（数组） | 同上 |
| `research_timestamp` / `last_updated` | string | 调研/更新时间 | 同上 |
| `compliance_name_match` | string | 姓名核查 | `include=compliance` 或详情 |
| `compliance_org_match` | string | 机构核查 | 同上 |
| `compliance_position_match` | string | 职位核查 | 同上 |
| `compliance_role_match` | string | 角色匹配 | 同上 |
| `compliance_deal_match` | string | 出手核查 | 同上 |
| `compliance_overall` | string | 整体结论（trusted/suspect/risk） | 同上 |
| `compliance_detail` | string | 核查明细 | 同上 |
| `search_summary` | string | 搜索摘要 | 同上 |
| `research_notes` | string | 给创业者的建议 | 同上 |

---

## 七、常见问题

**Q：为什么列表接口需要传 `include`？**
A：调研详情和合规核查字段较多，默认不返回以保持响应轻量。按需用 `include` 请求所需字段组即可。

**Q：详情接口需要传 `include` 吗？**
A：不需要。详情接口固定返回完整信息（机构返回基础+调研+投资人数；投资人返回基础+调研+合规）。

**Q：金额单位是什么？**
A：所有金额字段和参数单位都是「万元人民币」。

**Q：为什么投资人接口不返回联系方式？**
A：本接口面向服务端调用，出于隐私保护不返回电话、邮箱、工作地等联系方式。

**Q：传了未识别的参数值会怎样？**
A：分两类。筛选类参数（如 `sector_focus`、`currency`）传错不报错，查不到就返回空列表；未知参数被忽略。但两个接口的 `include`/`order_by`、投资人接口的 `compliance_overall`、基金接口的 `data_confidence` 必须是文档列出的枚举值，`min_amount`/`max_amount` 必须是数字，否则返回 HTTP 400 `validation_error`。

**Q：`org_sector_focus` 是数组还是字符串？**
A：数组。多值字段（`org_sector_focus`、`org_notable_portfolio`、`org_sources`、`person_sources`）都返回数组，无需调用方自行拆分。
