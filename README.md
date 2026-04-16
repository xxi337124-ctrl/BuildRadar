# BuildRadar

> 每天 5 分钟，知道该构建什么。

BuildRadar 是一个自动化的每日开发者趋势信号雷达。每天自动采集 6 个平台的数据，用 LLM 交叉分析生成结构化报告，自动发布到 GitHub Pages 静态网站。

## 数据源（6 个平台）

| 平台 | 方式 | 是否需要 key |
|------|------|------|
| Hacker News | Algolia HN Search API | 否 |
| GitHub Trending | HTML 爬取 | 否 |
| Product Hunt | GraphQL API v2 | **需要** `PH_TOKEN` |
| HuggingFace | 公开 JSON API (`/api/models?sort=trending`) | 否 |
| Google Trends | SerpApi | **需要** `SERPAPI_KEY` |
| Reddit | 公开 JSON 端点（`.json` 后缀） | 否 |

> PH_TOKEN / SERPAPI_KEY 缺失时对应采集器会自动跳过，不会影响其他数据源或报告生成。

## 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | 是 | Claude API key（缺失则使用本地兜底模板） |
| `PH_TOKEN` | 否 | Product Hunt Developer Token |
| `SERPAPI_KEY` | 否 | SerpApi key（用于 Google Trends） |

## 本地运行

```bash
pip install -r requirements.txt

# 完整流程（采集 → LLM 分析 → 生成站点）
export ANTHROPIC_API_KEY=sk-ant-...
python main.py

# 仅采集 + 本地兜底模板（不调用 Claude，快速测试）
python main.py --skip-llm

# 仅重建站点（基于 data/reports/ 下已有的报告）
python main.py --site-only

# 强制重新生成今日报告
python main.py --force
```

生成后可以用任何静态服务器预览：

```bash
cd site && python -m http.server 8000
# 浏览器打开 http://localhost:8000
```

## 目录结构

```
buildradar/
├── config.yaml             # 非敏感配置（采集数量、语言、subreddit 列表等）
├── main.py                 # 入口：采集 → 分析 → 生成 → 构建
├── config_loader.py        # 配置/环境变量加载工具
├── collectors/             # 6 个采集器
├── analyzer/               # Claude 报告生成器 + 离线兜底
├── publisher/              # Markdown 保存 + Jinja2 静态站点构建
├── templates/              # index / report / subscribe 模板（深色极简风）
├── data/
│   ├── raw/                # 每天原始采集数据 JSON
│   └── reports/            # 每日报告 Markdown（带 YAML frontmatter）
├── site/                   # 构建输出（部署到 GitHub Pages）
└── .github/workflows/daily.yml   # 每日 03:00 UTC 定时任务
```

## 报告结构（固定 5 大板块）

1. **🔥 今日 3 大信号**（强信号 = 跨 2+ 平台验证）
2. **🛠 本周该构建什么**（1 个可执行构建建议，含 MVP、技术栈、验证方法）
3. **📊 信号雷达**（Top 15 数据点表格，趋势标注 🔺🔻🔸）
4. **🔗 值得关注的链接**（Top 5 值得点击的链接 + 点评）

## 容错策略

- 每个采集器独立 `try/except`，单个失败只在 `raw_data` 中留下 `{"error": "..."}`，不影响其他数据源与最终报告生成
- Claude 的输入会通过 `analyzer.report_generator.truncate_data` 自动裁剪至 30K 字符以内，优先保留高分/高星条目
- LLM 调用失败时自动降级到离线兜底模板（`build_mock_report`），保证流水线始终产出文件
- 同一天重复运行默认跳过生成、只重建站点；加 `--force` 强制覆盖

## GitHub Actions 一键启用

仓库里的 `deploy/github-actions-daily.yml` 就是完整的每日 workflow。**启用方式**（只需 2 步）：

1. **移动到 GitHub 要求的目录**：
   ```bash
   mkdir -p .github/workflows
   git mv deploy/github-actions-daily.yml .github/workflows/daily.yml
   git commit -m "chore: enable daily workflow"
   git push
   ```
   > 之所以先放在 `deploy/`，是因为通过 GitHub App 推送的自动化提交默认不允许写入 `.github/workflows/` 目录（缺少 `workflows` 权限）。你本人 push 不受此限制。

2. **在仓库 Settings → Secrets and variables → Actions 中配置**：
   - `ANTHROPIC_API_KEY`（必填）
   - `PH_TOKEN`（选填，不填则跳过 Product Hunt）
   - `SERPAPI_KEY`（选填，不填则跳过 Google Trends）

启用后流程：

1. 每天 UTC 03:00（北京时间 11:00）自动触发，也可在 Actions 页面手动 `workflow_dispatch`
2. 采集 6 个平台 → LLM 分析 → 保存 Markdown → 构建 `site/`
3. 提交 `data/` 与 `site/` 回主分支
4. 通过 `peaceiris/actions-gh-pages@v4` 部署 `site/` 到 GitHub Pages

## License

MIT
