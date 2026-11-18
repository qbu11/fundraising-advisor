# fundraising-advisor

把一段创业者项目描述变成一份带证据的投资人候选名单。只依赖 Python 标准库。

## 快速开始

```bash
git clone https://github.com/qbu11/fundraising-advisor.git
cd fundraising-advisor

# 离线单测（不需要凭证）
python scripts/recommend_investors.py --self-test

# 用合成数据跑完整流程（不需要凭证）
python scripts/recommend_investors.py --project references/example-project.json --fixture fixtures/synthetic-replay.json --top-k 10
```

## 接入真实 API

```bash
cp .env.example .env
# 填入 INVESTOR_API_CLIENT_ID 和 INVESTOR_API_CLIENT_SECRET
python scripts/recommend_investors.py --project my-project.json --top-k 15
```

没有凭证时返回空推荐和错误说明，不会编造候选人。

- 接口文档：[docs/api-reference.md](docs/api-reference.md)
- 服务器部署与 cron 定时任务：[docs/deployment.md](docs/deployment.md)
- 输入格式：[references/input.schema.json](references/input.schema.json)

## 作为 Agent Skill

```bash
git clone https://github.com/qbu11/fundraising-advisor.git ~/.claude/skills/fundraising-advisor
```

工作流与输出规范见 [SKILL.md](SKILL.md)。

## 测试

```bash
python scripts/recommend_investors.py --self-test
python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
