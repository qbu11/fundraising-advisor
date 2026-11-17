# 服务器部署与 cron 定时任务

先说结论：**本仓库有两种运行模式，requirements 完全不同，不要让工程师自己猜。**

| 模式 | 需要安装 Claude Code | 需要 LLM API Key | 产出 | 适用场景 |
|---|---|---|---|---|
| A. CLI 定时筛选 | **不需要** | 不需要 | 推荐 JSON（候选人、分数、证据） | 每天/每周定时拉候选人，接入自己的系统 |
| B. Agent 完整顾问报告 | **需要** | 需要 | SKILL.md 定义的 7 段融资顾问报告（Markdown） | 定时生成可直接阅读的融资建议和周报 |

- 模式 A 就是一个 Python 脚本，服务器上只要有 Python 3.10+ 和投资人 API 凭证。
- 模式 B 的"顾问部分"由 AI agent 完成，**必须在服务器上安装 Claude Code CLI**，并配置 LLM API key。只用模式 A 的团队不需要装任何 agent 运行时。

---

## 0. 两种模式共用的前置条件

1. **操作系统**：Linux（Ubuntu 22.04+ / Debian 12+ 已验证等效环境）或 Windows Server。本文以 Linux 为例，Windows 差异见第 4 节。
2. **Python 3.10+**，只用标准库，无需 `pip install`。
3. **投资人 API 凭证**：`INVESTOR_API_CLIENT_ID`、`INVESTOR_API_CLIENT_SECRET`，向 API 提供方申请。
4. **网络出口**：允许 HTTPS（443）访问 `api.miracleplus.com`。模式 B 还需要访问 Anthropic API。
5. **凭证存放**：服务端专用文件 `/etc/fundraising-advisor/env`，权限 `600`，属主为运行用户。**不要**把 `.env` 放进仓库目录，也不要写进 crontab。

```bash
sudo install -d -m 700 /etc/fundraising-advisor
sudo install -m 600 /dev/null /etc/fundraising-advisor/env
sudo tee /etc/fundraising-advisor/env >/dev/null <<'EOF'
INVESTOR_API_CLIENT_ID=your_client_id
INVESTOR_API_CLIENT_SECRET=your_client_secret
EOF
sudo chown <运行用户> /etc/fundraising-advisor/env
```

6. **代码部署**：

```bash
sudo install -d -m 755 /opt/fundraising-advisor
sudo git clone https://github.com/qbu11/fundraising-advisor.git /opt/fundraising-advisor
```

7. **项目输入文件**（每个被监控的项目一份 JSON，格式见 `references/input.schema.json`）：

```bash
sudo install -m 600 references/example-project.json /etc/fundraising-advisor/project.json
# 按实际项目编辑 /etc/fundraising-advisor/project.json
```

---

## 1. 模式 A：纯 CLI 定时筛选（不需要 Claude Code）

### 1.1 手动验证

```bash
cd /opt/fundraising-advisor
set -a; . /etc/fundraising-advisor/env; set +a
python3 scripts/recommend_investors.py \
  --project /etc/fundraising-advisor/project.json \
  --top-k 20
```

首次部署先用合成数据验证命令结构（不消耗 API、不需要凭证）：

```bash
python3 scripts/recommend_investors.py \
  --project references/example-project.json \
  --fixture fixtures/synthetic-replay.json \
  --top-k 10
```

### 1.2 cron 示例

```cron
# /etc/cron.d/fundraising-advisor
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin
# 每天 09:17 生成前一日候选名单（错开整点，避免与其它任务挤在一起）
17 9 * * * appuser cd /opt/fundraising-advisor && set -a && . /etc/fundraising-advisor/env && set +a && /usr/bin/python3 scripts/recommend_investors.py --project /etc/fundraising-advisor/project.json --top-k 20 > /var/lib/fundraising-advisor/results/$(date +\%F).json 2>> /var/log/fundraising-advisor/search.log
```

准备目录：

```bash
sudo install -d -o appuser -m 750 /var/lib/fundraising-advisor/results
sudo install -d -o appuser -m 750 /var/log/fundraising-advisor
```

退出码约定（可据此告警）：

| 退出码 | 含义 | 处理 |
|---:|---|---|
| 0 | 成功，stdout 是完整 JSON | — |
| 2 | 输入不合法、凭证缺失、API 不可用 | 检查 `error.code`；缺失凭证时 JSON `status` 为 `needs_input`，网络/权限问题为 `data_source_unavailable` |

失败时脚本仍然输出结构化 JSON，且 `recommendations` 为空；不会退回快照或编造候选人。告警逻辑建议检查 `status` 字段，而不是只看退出码。

### 1.3 systemd timer（推荐，替代 cron）

`/etc/systemd/system/fundraising-advisor.service`：

```ini
[Unit]
Description=Investor screening run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=appuser
WorkingDirectory=/opt/fundraising-advisor
EnvironmentFile=/etc/fundraising-advisor/env
ExecStart=/usr/bin/python3 scripts/recommend_investors.py --project /etc/fundraising-advisor/project.json --top-k 20
StandardOutput=append:/var/lib/fundraising-advisor/results/latest.json
StandardError=append:/var/log/fundraising-advisor/search.log
```

`/etc/systemd/system/fundraising-advisor.timer`：

```ini
[Unit]
Description=Daily investor screening

[Timer]
OnCalendar=*-*-* 09:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fundraising-advisor.timer
sudo systemctl list-timers fundraising-advisor.timer
```

---

## 2. 模式 B：Agent 完整顾问报告（需要 Claude Code）

只有需要 SKILL.md 里的 7 段报告（融资诊断、方案、投资人推荐、接触波次、聊天策略）时才用这个模式。定时、并发、成本都由 Claude Code 会话承担。

### 2.1 安装 Claude Code（服务器）

1. **Node.js 18+**（或使用官方安装脚本，二选一）：

```bash
# 方式一：npm
sudo apt-get install -y nodejs npm
sudo npm install -g @anthropic-ai/claude-code

# 方式二：官方安装脚本（以官方文档为准）
curl -fsSL https://claude.ai/install.sh | bash
```

2. **验证**：

```bash
claude --version
```

3. **配置 LLM 认证**（服务器无浏览器，使用 API key）：

```bash
sudo install -m 600 /dev/null /etc/fundraising-advisor/agent-env
sudo tee /etc/fundraising-advisor/agent-env >/dev/null <<'EOF'
ANTHROPIC_API_KEY=your_anthropic_api_key
EOF
sudo chown <运行用户> /etc/fundraising-advisor/agent-env
```

> Claude Code 也支持交互式登录，但 cron 场景必须使用 API key（或官方支持的长期令牌），否则会话会在无人值守时失效。

### 2.2 安装 Skill

Claude Code 只从约定的 skills 目录发现 skill，**仓库根目录的 `SKILL.md` 不会自动被发现**。二选一：

```bash
# 项目级（推荐）：在被调用的工作目录下
mkdir -p /opt/fundraising-advisor-work/.claude/skills
ln -s /opt/fundraising-advisor /opt/fundraising-advisor-work/.claude/skills/fundraising-advisor

# 或用户级：对运行用户全局生效
mkdir -p ~/.claude/skills
ln -s /opt/fundraising-advisor ~/.claude/skills/fundraising-advisor
```

验证 skill 被发现（headless，单次调用）：

```bash
cd /opt/fundraising-advisor-work
set -a; . /etc/fundraising-advisor/agent-env; set +a
claude -p "使用 fundraising-advisor skill 告诉我：项目输入 JSON 的最小必填字段是什么？" \
  --allowedTools "Read" \
  --permission-mode acceptEdits
```

### 2.3 手动跑一次完整报告

```bash
cd /opt/fundraising-advisor-work
set -a; . /etc/fundraising-advisor/env; . /etc/fundraising-advisor/agent-env; set +a

timeout 900 claude -p "读取 /etc/fundraising-advisor/project.json，使用 fundraising-advisor skill 生成完整融资顾问报告，写入 /var/lib/fundraising-advisor/reports/demo.md" \
  --allowedTools "Read" "Write" "Bash(python3:*)" \
  --permission-mode acceptEdits \
  --output-format json
```

说明：

- `-p/--print`：headless 单次执行，适合脚本和定时任务。
- `--allowedTools`：只放行 skill 需要的工具；`Bash(python3:*)` 限制为运行 Python 脚本，避免 agent 执行任意 shell。
- `--permission-mode acceptEdits`：无人值守时允许写文件。也可以按官方文档改用更细的权限配置。
- `--output-format json`：输出结构化结果，便于日志与告警。
- `timeout 900`：外层超时兜底，防止会话挂起。systemd 场景改用 `TimeoutStartSec=900`。

### 2.4 cron 示例（Agent 模式）

```cron
# /etc/cron.d/fundraising-advisor-agent
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin
# 每周一 09:37 生成完整顾问报告
37 9 * * 1 appuser cd /opt/fundraising-advisor-work && set -a && . /etc/fundraising-advisor/env && . /etc/fundraising-advisor/agent-env && set +a && timeout 900 claude -p "读取 /etc/fundraising-advisor/project.json，使用 fundraising-advisor skill 生成完整融资顾问报告，写入 /var/lib/fundraising-advisor/reports/$(date +\%F).md" --allowedTools "Read" "Write" "Bash(python3:*)" --permission-mode acceptEdits --output-format json >> /var/log/fundraising-advisor/agent.log 2>&1
```

> cron 不会加载用户 shell profile。必须在命令里显式 source 两个 env 文件，并使用绝对路径。`$(date +\%F)` 中的 `%` 在 crontab 里要写成 `\%`。

### 2.5 成本与并发

- Agent 模式每次报告会消耗 LLM tokens（取决于项目描述长度和报告长度）。建议从每周一次开始，不要并发跑多个实例。
- 投资人 API 调用本身由 skill 里的脚本完成，agent 只负责组织和撰写；API 的 429/5xx 已由脚本处理。
- 用外层 `timeout`（systemd 的 `TimeoutStartSec` 或 cron 包装脚本）兜底，避免会话挂起。Claude Code 当前版本没有 `--max-turns` 参数，不要照搬过时写法。

---

## 3. Windows Server

- 模式 A：安装 Python 3.10+，用任务计划程序（Task Scheduler）调用：

```powershell
$env:INVESTOR_API_CLIENT_ID = "..."   # 或读入受保护的环境配置文件
$env:INVESTOR_API_CLIENT_SECRET = "..."
$env:PYTHONUTF8 = "1"
C:\Python312\python.exe C:\opt\fundraising-advisor\scripts\recommend_investors.py `
  --project C:\etc\fundraising-advisor\project.json --top-k 20 |
  Out-File -Encoding utf8 C:\var\fundraising-advisor\results\$(Get-Date -Format yyyy-MM-dd).json
```

- 模式 B：同样需要安装 Claude Code（Windows 安装包或 npm），认证与 skill 目录规则与 Linux 一致（`%USERPROFILE%\.claude\skills\fundraising-advisor`）。
- 必须设置 `PYTHONUTF8=1`，否则中文 JSON 在部分终端/管道下会乱码。

---

## 4. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| JSON `status=needs_input`，错误含 `INVESTOR_API_CLIENT` | 环境变量没传入 cron | source `/etc/fundraising-advisor/env`；cron 中用绝对路径 |
| JSON `status=data_source_unavailable`，`code=network_error` | 服务器出网被拦 | 放行 443 到 `api.miracleplus.com` |
| 403 / `insufficient scope` | 凭证 scope 不对 | 向 API 提供方确认 `public funds` scope |
| 429 | 触发限流 | 脚本会尊重 `Retry-After`；降低运行频率 |
| Agent 说找不到 skill | skill 目录不对或未建软链 | 确认 `.claude/skills/fundraising-advisor/SKILL.md` 存在，重新跑 2.2 的验证命令 |
| Agent 能调用脚本但报告为空/失败 | 没传投资人 API 凭证给子进程 | agent 会话环境里同时 source `/etc/fundraising-advisor/env` |
| cron 里中文乱码 | 未设置 UTF-8 | cron 环境加 `LANG=C.UTF-8`，Python 加 `PYTHONUTF8=1` |
| 输出文件为空但退出码 0 | 重定向路径不可写 | 检查目录属主与权限 |

## 5. 安全清单

- 凭证文件 `600`，只给运行用户；不要提交进 git，不要出现在 crontab 命令行里（crontab 对其他用户可读）。
- Agent 模式限制 `--allowedTools`，不要使用 `--dangerously-skip-permissions`。
- 项目 JSON 可能含融资计划等敏感信息，同样按 `600` 管理。
- 输出目录保留策略按贵司合规要求设置（例如保留 90 天）。
- 投资人数据仅用于候选发现；联系人、任职、额度在对外使用前必须人工核验。
