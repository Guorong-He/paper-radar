# Paper Radar

每周六凌晨 3 点准备、早上 8 点只读预检、周日凌晨 3 点续跑并发布（Asia/Shanghai）的具身感知论文雷达。

## 当前已具备

- 研究画像配置
- SQLite 数据库 schema
- OpenAlex / arXiv 候选论文采集
- 规则驱动的初始标签与评分
- 每期 Top 10 选择
- Codex-native 工作流：本地脚本准备研究包，Codex 负责真正的全文理解与总结
- 本地导出 `digest.json` 与 `digest.md`
- 生成 `email.html` 与 `digest.html`
- 每期强制 10/10 论文都有关键图；无图论文自动让位给下一篇有图候选
- Tier 1/2 正式论文在常规全文获取失败后，会自动调用合规收紧的 ScanSci PDF 回退层：仅使用开放来源、出版社官方 API 或用户已授权的机构会话，并对 PDF 身份与质量做二次验证
- 生成邮件附件包 `output/paper-radar-digest.zip`（默认只打包本期引用图，避免把历史缓存图全部带进邮件）
- 发布后公网校验：可检查 `latest/`、永久 issue、历史归档和公开 `research_packet.json` 是否已经同步
- 低上下文去重：`python3 scripts/paper_radar_weekly.py history-check` 只输出重复计数与命中论文，不把历史全文或分析载入模型，并与周任务使用同一期日期
- 按期持久化断点：`data/issues/YYYY-MM-DD/` 保存候选缓存、冻结席位、MyLOFT 队列与运行事件，失败后只续跑未完成阶段
- 隔离测试：`scripts/run_tests_isolated.py` 将所有可变状态重定向临时目录，防止测试污染真实周刊
- 仓库级 skill：显式调用 `$paper-radar` 可按统一运行时、断点和发布门禁恢复流程

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 -m paper_radar.cli init-db
python3 -m paper_radar.cli run
```

如果暂时不想请求外部 API，可以用 fixture 模式验证：

```bash
python3 -m paper_radar.cli run --fixture
```

## 推荐工作流

```bash
PYTHON=/Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
export PYTHONPATH=src
$PYTHON scripts/run_tests_isolated.py
$PYTHON -m paper_radar.cli --issue-date previous-sunday warm-candidate-cache
$PYTHON scripts/paper_radar_weekly.py run-report
$PYTHON scripts/paper_radar_weekly.py prepare-weekly
$PYTHON scripts/paper_radar_weekly.py run-report
```

同一期重试默认命中已保存的候选缓存；只有候选缓存损坏、发现配置实质变更或明确需要全量抓取时，才使用 `prepare-weekly --refresh-sources`。
`paper_radar_weekly.py` 会让周六和周日自动映射到同一个周日 issue date；候选发布日期超出回溯窗口或晚于 issue date 7 天的记录会被拒绝。

这会生成：
- `output/research_packet.json`
- `output/prepare_status.json`
- `output/source_status.json`

随后由 Codex 读取该文件中的全文与元数据，完成高质量分析并写回：
- `output/analyses.json`

最后运行：

```bash
python3 scripts/paper_radar_weekly.py render-from-analyses
```

生成：
- `output/digest.html`
- `output/email.html`

如需邮件附件包：

```bash
python3 scripts/paper_radar_weekly.py bundle-email
```

生成：
- `output/paper-radar-digest.zip`

发布到 GitHub Pages 后，如需确认公网已经真正刷新：

```bash
python3 scripts/paper_radar_weekly.py verify-publication --public-url https://guorong-he.github.io/paper-radar/
```

## 目录

```text
config/        研究画像与评分配置
data/          SQLite 数据库
data/issues/   按期隔离的可恢复运行状态
output/        每次运行导出的结果
src/           应用代码
tests/         基础测试
.agents/skills/paper-radar/  仓库级运行手册
```
