# 历史实验 A：地图质量回溯结果

此前 **80 条 RepoMap 运行**已补做官方目标文件召回评测：旧版1024共20条，以及扩展扫描的1024、2048、4096三档共60条。
同时纳入20条once作为对照。共100条推理运行；预算实验另外重建60张首轮地图，合计160个地图视图，仍是同一批20条任务。
本次没有模型调用、重新推理或重新判题，原实验轨迹与官方结果保持不变。

目前完成的是五项中的 **Target File Recall**。目标符号、关键依赖、全图符号相关性和实际利用证据仍待标注，四项均为 `N/A`，不记为0，也不宣称五项全部完成。

## 初始地图对照

真值来自同一任务、同一 `base_commit` 的官方 `patch`；每组均有28个官方目标文件，其中26个存在于基线，2个为新增文件。
宏平均对20条任务等权，微平均对全部目标文件等权。文档、changelog等官方修改文件保留在分母中。

| 条件 | 地图来源 | 文件召回宏平均 | 微平均 | 既存目标微平均 | 至少命中一个 / 全命中 |
| --- | --- | ---: | ---: | ---: | ---: |
| Interval 1024 | 重建首轮 | 30.00% | 6/28，21.43% | 6/26，23.08% | 6/20 · 6/20 |
| Interval 2048 | 重建首轮 | 45.00% | 9/28，32.14% | 9/26，34.62% | 9/20 · 9/20 |
| Interval 4096 | 重建首轮 | 61.43% | 14/28，50.00% | 14/26，53.85% | 13/20 · 12/20 |
| Once 1024 | 轨迹实际保存的固定地图 | 40.71% | 9/28，32.14% | 9/26，34.62% | 9/20 · 8/20 |

预算实验首轮上下文为“原始任务＋首条用户消息的最后24000字符”，包含任务模板中的通用工作说明；once仅使用原始任务。
因此同为1024也会得到不同排序，不能把差异全部归因于刷新次数。

Once相较Interval首轮1024：Bat由0/7变为1/7，fmt、Tokio、Sympy各由0/1变为1/1；Preact由1/1变为0/1。
合计命中增加3个文件，宏平均增加10.71个百分点。这表明上下文选择值得单独控制测试；此处只说明覆盖差异，不证明搜索或修复收益由它引起。

扩大预算会覆盖更多目标，但4096首轮仍有7/20条完全未命中：Carbon、Caddy、Django、Laravel、Redis、Rubocop、Lucene。
覆盖不足并非只发生在超大仓库；例如Carbon也未命中。4096首轮的Bat覆盖2/7，其中包含`src/assets.rs`，仍遗漏5个官方目标文件。

## 最终保留地图对照

旧版与Interval不断替换历史中的地图消息，轨迹最终只保留最后一张；这些地图可能受后期对话与Agent修改影响。
它们只反映结束时的地图覆盖，不能代替首轮质量，也不能证明更早的定位利用了相关条目。

| 条件 | 扫描范围 | 文件召回宏平均 | 微平均 | 既存目标微平均 | 至少命中一个 / 全命中 |
| --- | --- | ---: | ---: | ---: | ---: |
| 旧版1024 | 原始较小上限 | 45.00% | 9/28，32.14% | 9/26，34.62% | 9/20 · 9/20 |
| Interval 1024 | 扩展上限 | 40.00% | 8/28，28.57% | 8/26，30.77% | 8/20 · 8/20 |
| Interval 2048 | 扩展上限 | 60.71% | 13/28，46.43% | 13/26，50.00% | 13/20 · 12/20 |
| Interval 4096 | 扩展上限 | 68.21% | 15/28，53.57% | 15/26，57.69% | 15/20 · 13/20 |

旧版没有完整代码哈希记录，本次只评估其实际保留地图，没有补造首轮地图。Ripgrep使用启动问题修复后选定的有效重试轨迹。
旧版45.00%高于扩展扫描1024的40.00%，不能推导出缩小扫描范围更优：两次运行轨迹、后期上下文及最终源码不同。

同一预算的初始和最终地图也会变化：

| 预算 | 目标文件命中数增加的任务 | 减少的任务 | 其余任务 |
| --- | --- | --- | ---: |
| 1024 | Ripgrep、fmt、Carbon，各增加1个 | Preact减少1个 | 16条不变 |
| 2048 | Bat、Ripgrep、Tokio、Carbon，各增加1个 | 无 | 16条不变 |
| 4096 | Caddy、Carbon，各增加1个 | Bat减少1个，`src/assets.rs`消失 | 17条不变 |

例如Interval 1024的Preact首轮包含`src/diff/index.js`，最终地图却不包含它；Carbon三档首轮均未命中，最终均包含`src/Carbon/Traits/Rounding.php`。
这直接说明，拿最终地图解释Agent最初的搜索会产生时间顺序错误。后期命中可能发生在Agent已经定位或修改目标之后，不能反过来算作首轮定位收益。

## 逐任务结果与原始证据

- [8组视图汇总和20条逐任务表](../benchmarks/results/map-quality-history-20261105/summary.md)
- [逐任务CSV：160行，区分快照与预算](../benchmarks/results/map-quality-history-20261105/by-instance.csv)
- [机器可读汇总与各报告哈希](../benchmarks/results/map-quality-history-20261105/comparison.json)
- [首轮1024完整结果](../benchmarks/results/map-quality-history-20261105/interval-initial-1024/report.json) · [标注模板](../benchmarks/results/map-quality-history-20261105/interval-initial-1024/annotation-template.json)
- [首轮2048完整结果及标注模板目录](../benchmarks/results/map-quality-history-20261105/interval-initial-2048/summary.md)
- [首轮4096完整结果及标注模板目录](../benchmarks/results/map-quality-history-20261105/interval-initial-4096/summary.md)
- [旧版最终地图报告](../benchmarks/results/map-quality-history-20261105/legacy-final-1024/summary.md)
- 首轮重建清单及镜像/源码/上下文哈希：[1024](../benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction/budget-1024.json) · [2048](../benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction/budget-2048.json) · [4096](../benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction/budget-4096.json)
- [原始历史上下文与地图生成逻辑](../benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction/code/mini_swe_repomap/agent.py)
- [复核记录](../benchmarks/results/map-quality-history-20261105/audit.json)

各组目录均保存`maps/`、官方补丁参考`references/`、`annotation-template.json`和逐项命中详情。
首轮重建目录额外保存每条任务的`initial-context.txt`及`sources.json.gz`，用于后续符号、依赖与相关性复核。

## 重建与评分的可信范围

1. 校验历史`agent.py`、`settings.py`、`source.py`、`repomap.py`、`symbols.py`、`_collect.py`和包初始化文件的哈希，与预算实验启动时记录一致；归档这7份代码。
2. 从每条原始轨迹读取固定`sha256`镜像、原始配置和首条用户消息，仅用公开任务构建上下文；官方补丁只在地图生成后用于评分。
3. 容器设置`--network=none --read-only --pull=never`。20个容器均只有`lo`网络接口，源码HEAD树与基线树匹配；收集指纹与原实验预检记录20/20一致。
4. 调用原版`RepoMapMixin.query`的首轮分支，用不会发起模型请求的本地消息捕获器接收地图，分别生成3档预算。
5. 保存60张地图、源码快照和上下文哈希；全部地图未超预算，也未触及扩展扫描上限。原先已重建的Bat、Preact共6张与本次逐字一致。

这是可复核的确定性重建，**不是当时实际请求的逐轮抓包**。已有匹配代码、镜像、上下文和指纹增强了可复现性，但不会补出历史上未保存的所有动态地图。
源码快照的压缩包用于分析读取，不会向原Agent回灌官方答案。

## 后续四项的评测边界

评分与标注流程见 [实验 A 方法](map-quality-evaluation.md)。本次已经为所有视图导出模板，但尚未完成以下审阅：

| 指标 | 当前状态 | 仍需依据 |
| --- | --- | --- |
| Target Symbol Recall | N/A，0/20已评分/组 | 官方补丁、任务与基线声明的完整目标符号集 |
| Dependency Coverage | N/A，0/20已评分/组 | 经复核的关键依赖端点及调用/类型关系证据 |
| Symbol Precision | N/A，0/20已评分/组 | 全图可见声明清单及所有条目的相关性标注 |
| Map Utilization | N/A，0/20已复核/组 | once的真实引用证据链；interval还需每次请求地图及可用时间 |

最终地图可能来自Agent修改后的源码，基线符号行号必须先做坐标对齐；不能拿同一份基线声明位置直接评估所有快照。
未观察到显式引用也不等于证明没有使用地图，文件命中和搜索量减少都不能替代利用证据。
Plain没有地图，这五项为不适用；其搜索量、解决率和费用仍使用原对照实验结果。

重新生成评分报告：

```bash
source .venv/bin/activate
python scripts/evaluate_historical_maps.py \
  --reconstruction-dir benchmarks/results/budget-sweep-20261102T035731Z/map-quality-initial-reconstruction \
  --output /tmp/historical-map-quality
```

该命令只读取既有证据，不启动容器、不联网、不调用模型。需要重新构建首轮地图时，另用 [重建入口](../scripts/reconstruct_initial_maps.py)，步骤见 [评测方法](map-quality-evaluation.md#8-离线评测入口与标注流程)。
