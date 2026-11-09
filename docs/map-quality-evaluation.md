# 实验 A：地图质量验证

地图质量是优先评测项。先验证地图提供的信息是否覆盖任务，再解释搜索量、修复率与费用变化。
搜索减少不能替代地图质量评测，修复成功也不能证明地图被使用。

本评测使用**官方补丁、任务要求与基线源码**建立事后参考，使用 Agent 实际收到的地图和轨迹作为被评测对象。
Agent 自己提交的补丁只可作为另一种实现路径的辅助材料，不能代替官方补丁作为召回真值。

## 1. 五项指标

| 指标 | 评估问题 | 计算与证据 |
| --- | --- | --- |
| Target File Recall | 地图是否包含官方修改的目标文件？ | 命中的官方目标文件数 / 官方目标文件总数；另报任务扩展目标与基线既存目标口径 |
| Target Symbol Recall | 地图是否包含相关函数、类、变量等？ | 可见目标声明数 / 经复核的目标符号数；按路径、基线声明位置和必要的声明片段匹配 |
| Dependency Coverage | 地图是否包含目标所需的关键依赖？ | 命中的关键依赖端点数 / 经复核的仓库内关键依赖端点数；文件与符号单位分别标明 |
| Symbol Precision | 地图中的符号有多少与任务相关？ | 相关可见符号数 / 全部可见符号数；需要完整地图符号清单和相关性标注 |
| Map Utilization | Agent 是否利用地图指导后续定位？ | 基于明确地图引用、对应条目和随后定位/读取动作的证据链；报告已确认引用及未知状态，不用文件命中冒充实际使用 |

前四项均保存分子、分母、逐项命中/未命中与标注状态；同时报告逐任务宏平均和有效单位微平均。
缺少标注和空分母分别输出 `pending_annotation` / `not_applicable`，数值为 `null`，Markdown 为 `N/A`。
不能把未标注当作0分，也不能把空目标集当作100%。

## 2. 参考集与防止信息泄漏

1. 在正常推理中仅根据公开任务和仓库生成地图，保存原始地图、轨迹、预算、镜像/基线版本和 SHA-256。
2. 地图已冻结后，评测进程读取同一实例、同一 `base_commit` 的官方 `patch`。不要把 `test_patch` 合并进生产代码目标集。
3. 从官方补丁提取目标文件和 hunk 位置；结合任务与基线源码复核目标符号、关键依赖。标注目标集时尽量遮蔽地图命中情况和 Agent 成败，避免只挑已命中的目标。
4. 在地图符号清单上标注相关性；在完整轨迹上标注利用证据。
5. 单独运行事后评分，不将补丁、参考集、judge输出或隐藏测试送回 Agent，不修改原推理和官方判题。

入口默认只接受已保存的 `refresh=once` 初始地图。此前 interval 轨迹仅保留最终地图，需显式选择 `--snapshot retained-final`；不能把它当作首轮地图。
经原始代码哈希、固定镜像和原始首轮上下文验证的重建地图，可用 `--snapshot reconstructed-initial --reconstruction <清单>` 单列评测；重建不是历史请求抓包。
动态地图若要评估每轮与累计覆盖，仍需保存每次请求的地图文本、哈希、源码版本和出现时间，不能用最终地图解释此前定位行为。
当前评分器拒绝给单一 interval 快照套用利用行为标注；修改后的最终源码声明位置也不能未经对齐直接复用基线符号标注。

官方补丁是一种可复核的参考实现，不一定是唯一正确修改路径。任务确实涉及但官方未修改的文件、符号和依赖可以补充标注，但应保留依据并单独报告扩展口径。

## 3. Target File Recall

设 `G_files` 为官方 `patch` 中的目标文件集合，`M_files` 为地图实际列出的文件集合：

```text
Target File Recall = |G_files ∩ M_files| / |G_files|
```

路径按仓库根目录规范化，不能只匹配 basename。重命名以基线旧路径匹配初始地图；删除文件使用旧路径。
新增文件仍保留在“全部官方目标”分母中，列入 `new_files_not_present_at_base`；同时计算 `existing_target_file_recall`，避免隐藏“初始地图无法包含新文件”的影响。
官方补丁中的文档、changelog 等也保留在该口径中，不根据结果悄悄删除。

任务中要求理解、但官方未修改的文件放入 `additional_target_files`，必须附 `reason` 和任务/源码 `evidence`。
复核后另计算 `augmented_target_file_recall`，不改变纯官方补丁口径。

注意：此前报告中的“地图与 Agent 最终提交文件重叠”仍是描述性指标，**不是这里的 Target File Recall**。
例如本轮 Tokio 的 Agent 在公共网络类型层修改，而官方补丁采用另一层实现，两种文件集合不同。

## 4. Target Symbol Recall

设 `G_symbols` 为官方补丁与任务要求支持的目标符号集合，`M_symbols` 为地图中实际可见的声明：

```text
Target Symbol Recall = |G_symbols ∩ M_symbols| / |G_symbols|
```

标注单位包括函数、方法、类、接口、类型、字段、常量和任务相关变量。每个目标记录：

- 唯一 `id`，建议使用 `路径::限定作用域.名称`，重载时再带声明位置/签名。
- `path`、`scope`、`kind`、`name` 和基线声明 `line`。
- 可选 `declaration`，用于区分同一行/同名重载；需要与地图中的实际声明片段一致。
- 标注依据、复核者，以及官方 hunk 与任务要求的关系。

当前实现通过“路径＋基线声明行＋名称 token＋可选声明片段”检查可见性，不做仅凭名字的全仓匹配。
方法所在类被列出不等于方法被列出；在正文或调用参数中提及名字也不等于目标声明可见。
无法消歧时应保留未完成状态并补充源码依据，不能猜成命中。

官方 hunk 的上下文只是候选线索：修改可能在函数体中，相关目标也可能是未修改的调用者、类或字段。
新增符号需要明确记录不存在于基线的事实。当前地图生成器没有展示某类变量时，应反映为未覆盖，不能从已复核目标集中删掉它。

## 5. Dependency Coverage

对每个任务建立独立的关键依赖参考集 `G_deps`：

- 与修复行为直接相关的被调用函数、调用者、继承接口、数据结构或跨文件类型。
- 依据来自基线源码、官方修改和任务语义；不直接把当前 RepoMap 的引用图当真值。
- 去重的评测单位是依赖端点，例如 `src/helper.py::parse`；同一端点被多个目标调用不重复加分。
- 记录目标与依赖之间的关系、关键性理由和源码证据。不能把整个仓库的 import 列表都叫关键依赖。

```text
Dependency Coverage = |G_deps 中被地图覆盖的端点| / |G_deps|
```

`kind=file` 只评估文件可见；符号依赖需要相应声明可见，不能用同文件任意其他符号替代。
这测量依赖端点覆盖，不声称地图展示了具体依赖边。
仓库外依赖可标 `eligible=false`，但必须写 `exclusion_reason` 并单列；无关键依赖时为 `N/A`。

## 6. Symbol Precision

先建立地图中**全部实际可见声明**的清单，而不是全仓库解析出的所有符号，也不是地图的行数。
限定路径/作用域/声明位置后去重；渲染时出现的父类等上下文声明也保留在清单中，不能为提高精度任意删去。

每项相关性标为 `relevant`、`irrelevant` 或 `unknown`：

- `relevant`：理解、定位、修复或验证本任务所需，附具体任务/代码关系。
- `irrelevant`：与本任务无关，如无关联的压缩库、demo、benchmark样本。
- `unknown`：证据不足或标注尚有分歧。

```text
Symbol Precision = relevant / 全部可见符号
```

主指标只有在 `inventory_complete=true` 且相关性全部复核、无 `unknown` 时才计算。
部分标注另报 `labeled_subset_precision`、`label_coverage`；不能拿“已标的几项”代替全图精度。
清单完整但有未知项时，另报区间：

```text
下界 = relevant / N
上界 = (relevant + unknown) / N
```

建议两名标注者独立复核分歧；judge 可以给出候选相关性和证据，但不能只因同名、同目录或被修改就判相关。

## 7. Map Utilization

利用行为按以下状态标注：

| 状态 | 解释 |
| --- | --- |
| `confirmed_reference` | Agent明确引用地图及相应条目，随后有可核对的定位/读取动作 |
| `consistent` | 轨迹与利用地图相容，例如首步读到地图中的文件，但无法排除任务描述或模型已有知识 |
| `not_observed` | 完整轨迹中未见明确利用证据，不等于证明没有使用 |
| `unknown` / `pending_annotation` | 轨迹或标注不足，无法判断 |

确认事件需要记录原 assistant 消息索引、逐字引用、地图条目、随后动作的消息/动作索引、命令摘录和操作类型。
评分器检查引用确实存在且明确提及地图，条目确实可见，动作及命令存在，时间顺序正确；操作语义仍需人工复核。
同一文件后来被 grep/cat、补丁文件命中、搜索量减少，单独都不足以确认使用。

报告输出 `confirmed_references / 已复核完整轨迹数`，命名为**明确引用证据下界**，附标注覆盖率和各状态数量。
标注未覆盖全量时，该下界只描述标注子集。真实心智使用率和因果收益保持未知；即使明确引用，也不能证明没有地图就无法定位。

## 8. 离线评测入口与标注流程

入口：[evaluate_map_quality.py](../scripts/evaluate_map_quality.py)，指标实现：[map_quality_metrics.py](../scripts/map_quality_metrics.py)。
读取已完成 run 的 `plan.json`、轨迹和官方评测保存的 `evaluation/.../dataset.json`，核对实例和 `base_commit`。
评分入口不启动容器、不联网、不调用模型，不重新生成地图或修改推理数据。
历史地图重建使用单独脚本，在断网、只读、`--pull=never` 容器中读取现有镜像，不重新推理或判题。

```bash
source .venv/bin/activate

# 第一次：计算官方文件召回，导出其余指标的标注模板
python scripts/evaluate_map_quality.py \
  benchmarks/results/static1024-once-20261105T145121Z \
  --output /tmp/once-map-quality-A

# 复制 annotation-template.json，填写真实实例下的标注与复核信息
cp /tmp/once-map-quality-A/annotation-template.json /tmp/once-map-quality-labels.json

# 标注完成后写入新的报告目录；保留原map_sha256及reference_base_commit
python scripts/evaluate_map_quality.py \
  benchmarks/results/static1024-once-20261105T145121Z \
  --labels /tmp/once-map-quality-labels.json \
  --output /tmp/once-map-quality-A-labeled

# 历史interval实验：显式评估最终保留地图，按预算筛选
python scripts/evaluate_map_quality.py \
  benchmarks/results/budget-sweep-20261102T035731Z \
  --snapshot retained-final --budget 2048 \
  --output /tmp/interval-2048-final-quality

# 汇总旧版、三档interval与once；读取已验证的首轮重建清单
python scripts/evaluate_historical_maps.py \
  --reconstruction-dir benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction \
  --output /tmp/historical-map-quality
```

历史汇总入口会从旧版修复后的报告选取最终有效轨迹，避免误用 Ripgrep 启动失败的首次尝试。
各预算单独输出，保留实例、地图、官方补丁与原始轨迹哈希，不混合初始地图和最终地图。

需要重新构建首轮地图时，使用已归档并与实验 `source_hashes` 一致的旧版代码：

```bash
python scripts/reconstruct_initial_maps.py \
  benchmarks/results/budget-sweep-20261102T035731Z \
  --historical-package benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction/code/mini_swe_repomap \
  --output /tmp/reconstructed-initial-maps
```

脚本实际调用历史 `RepoMapMixin.query` 的首轮分支，以原始公开任务与首条用户消息构建上下文，保存源码快照、上下文、代码与地图哈希。
生成过程只取数据集的公开任务，不向生成器输入官方补丁；评分在地图固定后另行执行。

模板根字段为 `schema_version: 1` 和 `instances`。每个实例的主要字段：

| 字段 | 用途 |
| --- | --- |
| `reference_base_commit` / `map_sha256` | 固定参考版本与被评测地图，避免把标注套到另一张地图 |
| `reviewers` | 标注/复核者标识及分歧处理记录 |
| `additional_target_files.reviewed/items` | 经任务要求补充的目标文件及理由、证据 |
| `target_symbols.reviewed/items` | 目标符号集合；未复核时不计算召回 |
| `dependencies.reviewed/items` | 关键依赖端点集合 |
| `map_symbols.inventory_complete/relevance_reviewed/items` | 全图可见符号清单及相关性标签 |
| `utilization.reviewed/status/events` | 地图引用与后续定位的证据链 |

符号条目结构示例，不是实际任务标注：

```json
{
  "id": "src/api.py::API.run",
  "path": "src/api.py",
  "scope": "API",
  "kind": "method",
  "name": "run",
  "line": 12,
  "declaration": "def run(self, value):",
  "evidence": "官方hunk和基线方法声明",
  "relevance": "relevant"
}
```

`utilization.events` 中确认事件另需：`reference_message_index`、`quote`、`explicit_map_reference=true`、
`map_item`、`action_message_index`、`action_index`、`action_contains`、`operation=locate/read`。
引用与命令必须来自真实轨迹；不能由 judge 编写一段看似合理的引用。

产物：`summary.md`、`report.json`、`results.csv`、`annotation-template.json`，以及逐任务官方参考 `references/` 和实际地图 `maps/`。
JSON保存各指标状态、分子分母、命中/未命中、标注覆盖率、原文件路径与SHA-256。

## 9. 当前20条once任务的首批结果

已按官方补丁重新计算文件召回，未使用Agent最终补丁作为参考：

- **Target File Recall 宏平均：40.71%**。
- **微平均：9/28，32.14%**。
- **基线既存目标口径：宏平均40.83%，微平均9/26（34.62%）**。
- 9/20条任务至少命中一个官方目标文件，其中8/20条覆盖全部官方目标文件。
- 其余4项目前尚未完成所需标注，均为 `N/A`，不是0分或已完成的全指标实验。

官方补丁包含2个基线不存在的新文件，完整结果另报既存目标召回。
这一结果与此前基于Agent提交的“8/20有重叠”口径不同；Tokio等任务采用了与官方不同的修复路径。

[首批评测报告](../benchmarks/results/static1024-once-20261105T145121Z/map-quality-A/summary.md) ·
[完整JSON](../benchmarks/results/static1024-once-20261105T145121Z/map-quality-A/report.json) ·
[20条标注模板](../benchmarks/results/static1024-once-20261105T145121Z/map-quality-A/annotation-template.json)

此前旧版与三档预算的80条RepoMap运行现已补评文件召回，并加入60张预算首轮重建地图；详见 [历史地图质量回溯](historical-map-quality-results.md)。
初始与最终快照分别列出，其他四项仍待标注，不与文件召回混称为全五项完成。

后续比较预算、刷新策略或生成器时，应先比较这五项的可评分覆盖率及地图质量，再与搜索量、官方解决率和费用关联分析；不从一次相关性观察直接推导因果。
