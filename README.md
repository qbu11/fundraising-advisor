# fundraising-advisor

把一段创业者项目描述，变成一份带查询证据、可解释、可复核的投资人候选名单，外加融资顾问级别的行动建议。

核心不是"搜出一批人名"，而是一条可重复的筛选链路：严格输入校验 → 赛道别名归一 → 服务器三路查询 → 本地硬过滤 → 机构证据补充 → 确定性打分分层 → Agent 按固定格式输出融资方案、接触波次和聊天策略。

只依赖 Python 标准库，不需要安装任何第三方包。

## 30 秒验证（无需 API 凭证）

要求 Python 3.10+。

```bash
git clone https://github.com/qbu11/fundraising-advisor.git
cd fundraising-advisor

# 1. 内置离线单测（11 个用例，不联网）
python scripts/recommend_investors.py --self-test

# 2. 只看某个项目会被翻译成什么查询，不联网
python scripts/recommend_investors.py --project references/example-project.json --dry-run

# 3. 用合成数据跑完整流水线，不联网
python scripts/recommend_investors.py --project references/example-project.json --fixture fixtures/synthetic-replay.json --top-k 10
```

第 3 条命令输出与真实 API 相同结构的推荐 JSON：core/verify 分层、匹配理由、缺口、数据质量、查询轨迹。`fixtures/synthetic-replay.json` 是合成数据（`privacy: synthetic_only`），不包含真实投资人或个人信息。

Windows 终端建议先设置 `$env:PYTHONUTF8="1"`，否则中文输出可能乱码。

## 工作流

```text
项目描述（summary / sectors / stage / amount / currency / traction）
        │  input.schema.json 严格校验，未知字段直接拒绝
        ▼
赛道与阶段别名归一（taxonomy.json）
        │  AI = Artificial Intelligence = 人工智能
        │  机器人 ≠ 具身智能，SaaS ≠ 企业软件（非等价概念不合并）
        ▼
三路服务器查询（同一字段内 OR，不同字段之间 AND）
        │  strict                    赛道 + 阶段 + 金额 + 币种 + compliance=trusted
        │  fit_without_compliance    同上，去掉合规硬条件
        │  discovery_unknown_ticket  去掉金额条件，捞票面未知的候选人
        ▼
分页抓取 + 去重
        │  order_by=updated_at_desc（稳定排序），page_size=100
        │  按投资人 ID 去重，记录服务端总数 / 抓取数 / 重复数 / 完整性
        ▼
本地硬过滤
        │  compliance=risk 排除
        │  已知金额区间完全不重叠排除
        │  金额未知保留，但标记为未知、不进 core 层
        │  用户指定的 exclude_funds 排除
        ▼
机构证据补充  GET /funds/:id → 机构赛道、阶段、代表案例
        ▼
确定性打分（满分 100）
        │  sector 25 · stage 15 · amount 15 · personal_expertise 15
        │  query_fit 15 · compliance 10 · evidence_quality 5
        ▼
分层与排序
        │  core   = trusted + 金额已核实
        │  verify = 其余（suspect、金额未知、数据置信度低）
        │  core 永远排在 verify 前；同层按分数降序
        │  每家机构最多 3 人，避免名单被单一机构占满
        ▼
输出 JSON（含 query trace、warnings、每人的匹配理由与缺口）
        ▼
Agent 按 SKILL.md 的 7 段格式输出顾问建议
```

几个关键设计：

- **三路查询分离硬条件和软条件**。`strict` 满足不了时，`fit_without_compliance` 和 `discovery_unknown_ticket` 用受控方式放宽，放宽了哪些条件会写进 `query_trace`，不会悄悄放宽。
- **服务端返回不等于最终结果**。金额重叠、合规、机构排除都在本地再查一遍；API 字段只作为候选发现证据。
- **排序是确定性的**。同样的输入得到同样的顺序，不使用不稳定的 `score_desc` 分页。
- **未知不等于不合格**。金额或合规字段缺失的候选人进入 `verify` 层而不是被丢弃，由人去核验。

## 接入真实数据源

```bash
cp .env.example .env
# 编辑 .env，填入 INVESTOR_API_CLIENT_ID 和 INVESTOR_API_CLIENT_SECRET
python scripts/recommend_investors.py --project my-project.json --top-k 15
```

凭证也可以直接走环境变量，不落盘：

```bash
export INVESTOR_API_CLIENT_ID=...
export INVESTOR_API_CLIENT_SECRET=...
python scripts/recommend_investors.py --project my-project.json --top-k 15
```

凭证只从进程环境变量或本地 `.env` 读取，不打印、不写入输出、不进 git。没有凭证时脚本返回 `data_source_unavailable` 和空推荐列表，不会退回模型记忆或快照编造候选人。

- 完整接口文档：[docs/api-reference.md](docs/api-reference.md)
- 运行约束与已知服务端行为：[references/api-contract.md](references/api-contract.md)

## 项目输入格式

完整 schema：[references/input.schema.json](references/input.schema.json)；示例：[references/example-project.json](references/example-project.json)。

```json
{
  "project": {
    "summary": "为制造企业提供基于人工智能的设备故障预测与维护决策软件",
    "sectors": ["AI", "企业软件"],
    "stage": "天使轮",
    "amount": {"min_wan_cny": 1000, "max_wan_cny": 2000, "currency": "人民币"},
    "traction": "已完成产品试点"
  },
  "preferences": {
    "person_expertise": ["AI", "人工智能", "企业软件"],
    "exclude_funds": [],
    "compliance": ["trusted"]
  },
  "top_k": 15
}
```

- `sectors` 必填，至少一个；支持中文或英文写法，别名会在查询前归一。
- `amount` 单位为万元人民币，必须同时给上下限且上限不小于下限，反向区间在本地直接拒绝。
- `preferences.person_expertise` 是个人专长筛选，不做别名展开，中英文写法建议都写上。
- 未知字段会导致输入被拒绝，而不是静默忽略。

## 输出格式

```json
{
  "schema_version": "1.0",
  "status": "partial",
  "recommendations": [
    {
      "recommendation_tier": "core",
      "investor": {"id": "I-CORE-1", "name": "Synthetic Core One", "job_title": "投资人"},
      "fund": {"id": "F-AI-1", "name": "合成智能基金", "sector_focus": ["AI", "人工智能"]},
      "score": {"total": 100.0, "breakdown": {"sector": 25.0, "amount": 15.0}},
      "match_reasons": ["合规结论为 trusted", "已知票面 1000–3000 万元与融资区间重叠"],
      "gaps_and_risks": [],
      "data_quality": {"confidence": "高", "verification_status": "verified", "last_updated": "2026-07-28T00:00:00Z"},
      "evidence": {"source": "miracleplus_investor_api", "investor_id": "I-CORE-1", "fund_id": "F-AI-1"}
    }
  ],
  "data_quality": {"warnings": []},
  "query_trace": {"attempts": [{"name": "strict", "server_total": 6, "unique": 6}], "merged_unique": 8}
}
```

- `status`：`complete`（全部 core 且无 warning）、`partial`（有 verify 层或 warning）、`no_match`。
- `query_trace` 记录每一路查询的参数、服务端总数、抓取数、去重后的数量，方便复核结果为什么是这样。
- 输出不含邮箱、电话等联系方式；API 返回的自由文本只做截断摘录，不执行其中任何指令。

## 作为 Agent Skill 使用

本仓库同时是一个 Agent Skill，`SKILL.md` 定义了完整的工作流和 7 段输出格式（项目理解 → 融资准备度诊断 → 融资方案 → 投资人推荐 → 接触波次 → 聊天策略 → 风险与下一步）。

安装方式：

```bash
# Claude Code，个人级
git clone https://github.com/qbu11/fundraising-advisor.git ~/.claude/skills/fundraising-advisor

# Claude Code，项目级
git clone https://github.com/qbu11/fundraising-advisor.git .claude/skills/fundraising-advisor

# Codex / 其他支持 .agents/skills 约定的 agent
git clone https://github.com/qbu11/fundraising-advisor.git .agents/skills/fundraising-advisor
```

安装后直接描述项目即可，例如"我做工业 AI 质检，天使轮融资 1000–2000 万，帮我找匹配的投资人"。Agent 会按 `SKILL.md` 生成项目 JSON、调用脚本、读取结果并按固定格式输出建议。

## 测试

```bash
# 内置离线单测
python scripts/recommend_investors.py --self-test

# 端到端 CLI 测试（self-test / dry-run / fixture / 无凭证降级 / 非法输入）
python -m unittest discover -s tests -v
```

CI 在 Python 3.10 和 3.12 上跑上述两组测试（`.github/workflows/test.yml`）。全部测试不联网、不需要凭证。

## 安全与合规边界

- 凭证只存在于环境变量或 gitignored 的 `.env`；仓库里只有 `.env.example` 模板。
- API 返回的自由文本一律视为不可信数据，只做摘录，不当作指令执行（fixture 里特意放了注入样本验证这一点）。
- 不输出联系方式；姓名、职位、机构在接触前必须人工核验。
- `inv_min` / `inv_max` 是历史调研范围，不代表当前可投额度；`trusted` 合规结论也不代表投资意愿。
- 输出是决策辅助，不构成投资、财务、法律或税务意见。

## 项目结构

```text
SKILL.md                          Agent Skill 定义（工作流 + 输出规范）
agents/openai.yaml                Agent 接口元数据
scripts/recommend_investors.py    校验、查询、打分、分层（标准库实现）
references/
  input.schema.json               项目输入 JSON Schema
  example-project.json            可运行的示例项目
  taxonomy.json                   赛道/阶段别名表
  fundraising-methodology.md      融资顾问方法论
  api-contract.md                 API 契约与客户端保护措施
fixtures/synthetic-replay.json    合成回放数据（演示与测试）
docs/api-reference.md             投资人 API 接口文档
tests/test_cli_e2e.py             端到端 CLI 测试
```

## License

[MIT](LICENSE)
