# 运行脚本

在项目根目录执行，先激活安装了本扩展的 Python 环境：

```bash
source .venv/bin/activate
```

拉取、性能实验、模型推理及 plain 入口自动加载项目根目录 `.env`，也支持 `--env-file` 指定其他文件；已导出的同名环境变量优先。
`MODEL_NAME`、`MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_STREAMING` 可直接配置模型推理，详见 [.env 配置](../docs/environment.md)。

| 脚本 | 用途 |
| --- | --- |
| [run_scb_compare.py](run_scb_compare.py) | 从 SCBench 固定抽取25题，运行 plain/once-full 完整链，调用上游官方评测并保存状态 |
| [scb_infer.py](scb_infer.py) | 使用本项目 Agent v2 执行一题的一组 checkpoint 链，保存轨迹、源码快照和完整地图 |
| [analyze_scb_compare.py](analyze_scb_compare.py) | 离线汇总 SCBench 成功率、搜索、费用与后续 checkpoint 配对，按题目聚类计算区间 |
| [evaluate_scb_snapshots.py](evaluate_scb_snapshots.py) | 在临时副本中准备官方 uv 项目，统一判定已生成快照或运行参考实现预检，保留旧报告 |
| [guard_scb_checkpoints.py](guard_scb_checkpoints.py) | 为已运行的 SCBench checkpoint 补充硬截止，保留中断轨迹、源码和费用未知标记，不重跑模型 |
| [pull_docker.py](pull_docker.py) | 拉取 20 个源码镜像，核对源码树并记录规模；支持只检查本地镜像 |
| [run_experiments.py](run_experiments.py) | 不调用模型，测量地图 token、生成耗时、解析规模和缓存复用 |
| [run_swebench.py](run_swebench.py) | 从 `.env` 或 `--model` 读取模型，运行关闭地图与启用地图的 SWE-bench 推理对照 |
| [run_plain.py](run_plain.py) | 单独运行 plain 组，关闭 RepoMap，默认使用修复镜像和断网容器 |
| [run_static.py](run_static.py) | plain 与固定 1024 tokens 的初始地图对照；每个任务只生成和注入一次，不随近期消息或源码修改刷新 |
| [run_full.py](run_full.py) | plain 与完整初始地图对照；不做排名/token 选择，关闭三项扫描上限，每任务只生成和注入一次，默认断网 |
| [prepare_offline_images.py](prepare_offline_images.py) | 预热 Gson、Tokio 依赖，构建并断网验证两个派生镜像，记录固定镜像 ID |
| [evaluate_swebench_offline.py](evaluate_swebench_offline.py) | 使用官方 SWE-bench 判题器测试已生成的补丁，强制评测容器断网并记录网络配置 |
| [prepare_budget_sweep.py](prepare_budget_sweep.py) | 固定本地镜像 ID，放宽并断网验证统一扫描范围，为预算实验准备快照 |
| [run_budget_sweep.py](run_budget_sweep.py) | 交错运行 0/1024/2048/4096 预算，默认两路推理并自动串行官方断网判题 |
| [analyze_budget_sweep.py](analyze_budget_sweep.py) | 汇总各预算的成功率、搜索与实际费用，按源码规模分组；支持持续更新报告 |
| [guard_budget_sweep.py](guard_budget_sweep.py) | 为已运行的预算实验附加进程级时限，保留超时轨迹并清理对应容器 |
| [recover_budget_timeout.py](recover_budget_timeout.py) | 对明确记录的基础设施阻塞补跑一次，保留原尝试及已返回费用，使用独立官方报告 |
| [collect_results.py](collect_results.py) | 汇总已完成实验，复核搜索与服务端费用，打包轨迹、补丁、地图及官方判题报告为本地 ZIP；运行中结果单列快照 |
| [evaluate_map_quality.py](evaluate_map_quality.py) | 实验 A：按官方补丁评估地图，显式区分once初始、interval最终与初始重建，并接受复核标注；全程离线 |
| [evaluate_historical_maps.py](evaluate_historical_maps.py) | 统一评估旧版、三档预算与once，自动选择旧版有效重试，分别导出初始/最终对照、逐条CSV与标注模板 |
| [reconstruct_initial_maps.py](reconstruct_initial_maps.py) | 校验历史代码哈希，从本地固定镜像重建interval首轮地图，容器断网只读；不调用模型或重新判题 |
| [run_repeated_budget.py](run_repeated_budget.py) | 顺序执行once与interval的预算重复实验，校验并冻结配置，自动判题及生成最终报告 |
| [analyze_repeated_budget.py](analyze_repeated_budget.py) | 汇总搜索次数/任务、定位成本、成功率约束、重复波动及两种策略的配对差值 |
| [analyze_patch_quality.py](analyze_patch_quality.py) | 离线比较489份已提交补丁的增删范围、文件路径与官方参考重叠，并按任务簇生成配对区间 |
| [judge_patch_maintainability.py](judge_patch_maintainability.py) | 使用.env-judge，对full-once与历史plain的18对补丁隐藏组别、交换顺序评审，保存全部输入与响应 |
| [analyze_maintainability_judge.py](analyze_maintainability_judge.py) | 离线汇总judge分数、展示顺序分歧、代码引用与格式恢复，不修改原始评分 |
| [export_search_cause_review.py](export_search_cause_review.py) | 从既有case-analysis导出每个搜索子命令的证据和语义标注模板，不调用模型 |

```bash
# 拉取并核对镜像，复用已有的本地层
python scripts/pull_docker.py --workers 3

# 只核对本地镜像
python scripts/pull_docker.py --inspect-only

# 先在两个仓库上验证性能实验
python scripts/run_experiments.py --limit 2 --budgets 1024 --repeats 1

# 全部仓库、三种预算，每种预算执行三次热缓存生成
python scripts/run_experiments.py --budgets 1024 2048 4096 --repeats 3

# 查看模型对照实验命令，不调用模型
python scripts/run_swebench.py --dry-run --repos pallets/flask

# 准备修复镜像（准备阶段联网；验证阶段断网）
python scripts/prepare_offline_images.py

# 预览全部 20 条 plain 命令，不调用模型
python scripts/run_plain.py --dry-run

# 预览单条任务的 plain/静态 1024 地图两组命令
python scripts/run_static.py --dry-run --repos pallets/flask

# 预览单条任务的 plain/完整单次地图两组命令
python scripts/run_full.py --dry-run --repos pallets/flask

# 使用 .env 运行全部 20 条 plain 推理
python scripts/run_plain.py --output benchmarks/results/plain-offline20
```

性能实验默认写入 `benchmarks/results/<时间>-<运行ID>/`，包含 `report.json`、`results.csv`、`summary.md`、地图文本与日志。
`--output` 可以指定一个空目录，`--help` 显示所有参数。

详细方法、指标含义和模型对照用法见 [实验运行指南](../docs/experiments.md)。
断网推理与补丁判题见 [容器断网与官方测试](../docs/experiments.md#4-容器断网与官方测试)。
plain 与 RepoMap 对照命令见 [plain 模式与对比](../docs/experiments.md#5-plain-模式与-repomap-对比)。
预算与仓库规模实验见 [预算对照](../docs/experiments.md#6-仓库规模与地图预算对照)。
固定 1024 tokens 且只注入一次的实验见 [静态地图对照](../docs/experiments.md#7-固定-1024-tokens只注入一次地图)。
完整地图只注入一次的实现、预览和实际运行命令见 [full-once](../docs/full-once.md)。

收集已有结果，不调用模型或重新判题：

```bash
python scripts/collect_results.py
```

输出到新的 `benchmarks/results/collected-<UTC时间>/` 和同名 ZIP，包含 SHA-256 来源清单。
已收集结果入口见 [实验结果汇总](../docs/results.md)。

地图质量应先于搜索收益评估：

```bash
python scripts/evaluate_map_quality.py \
  benchmarks/results/static1024-once-20261105T145121Z \
  --output /tmp/once-map-quality-A
```

五项指标的参考集、标注方法和证据要求见 [实验 A](../docs/map-quality-evaluation.md)。
缺少人工复核标注时相应指标为 `N/A`；该入口不调用模型、不生成新地图、不向推理输入官方补丁。

回溯此前实验，并加入已有的首轮重建地图：

```bash
python scripts/evaluate_historical_maps.py \
  --reconstruction-dir benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction \
  --output /tmp/historical-map-quality
```

不提供 `--reconstruction-dir` 时只评分实际保存的地图。结果与适用范围见 [历史地图质量回溯](../docs/historical-map-quality-results.md)。
