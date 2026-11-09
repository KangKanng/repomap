# 实验结果汇总

最新完成的480次重复实验见 [最终分析](final-experiment-analysis.md)：471次正常提交、9次推理超时；包含同任务配对、成功率敏感性、费用及20条逐项结果。原评分中Gson有1条目标测试未执行，另行单列。

完整 Markdown 报告见 [RepoMap 实验结果整理](experiment-results.md)，包含实验配置、性能、两轮完整对照、20 个仓库逐任务表、规模分组和本轮 once 阶段性结果。

本轮 once 的最终实现与20条原因分析见 [Once 实现与逐任务分析](once-implementation-and-case-analysis.md)。
此前80条RepoMap运行的官方文件召回及首轮地图重建对照见 [历史地图质量回溯](historical-map-quality-results.md)，含20条逐任务明细和标注状态。

2026-11-05 收集了两轮完整模型对照：20 个仓库、6 个条件，共 120 条最终有效结果、122 次运行尝试。
另附 20 个仓库的无模型地图性能实验。本轮单次 1024 地图实验的进度单独保存为快照。

- [完整汇总与口径](../benchmarks/results/collected-20261105T150926Z/README.md)
- [结果压缩包 ZIP，约 6 MB](../benchmarks/results/collected-20261105T150926Z.zip)
- [分组汇总 CSV](../benchmarks/results/collected-20261105T150926Z/groups.csv)
- [120 条最终任务明细 CSV](../benchmarks/results/collected-20261105T150926Z/trials.csv)
- [122 次运行尝试 CSV](../benchmarks/results/collected-20261105T150926Z/attempts.csv)
- [来源与 SHA-256 清单](../benchmarks/results/collected-20261105T150926Z/manifest.json)

## 已完成模型结果

模型均为 `deepseek/deepseek-v4.1-flash-1017`。这些已完成的 RepoMap 条件使用每轮刷新和固定预算。

| 轮次 | 设置 | 官方通过 | 搜索子命令 | 已返回费用 USD，含失败尝试 |
| --- | --- | ---: | ---: | ---: |
| 早期历史对照 | plain | 16/20 | 228 | 0.285331 |
| 早期历史对照 | RepoMap 1024 | 17/20 | 260 | 1.806458 |
| 统一预算对照 | plain | 16/20 | 234 | 0.303534 |
| 统一预算对照 | RepoMap 1024 | 16/20 | 197 | 0.951946 |
| 统一预算对照 | RepoMap 2048 | 16/20 | 242 | 1.652802 |
| 统一预算对照 | RepoMap 4096 | 17/20 | 222 | 1.523449 |

同一轮内对比：早期 1024 搜索增加 14.0%；统一预算对照的 1024 搜索减少 15.8%、2048 增加 3.4%、4096 减少 5.1%。
两轮的镜像准备、扫描范围和运行次数存在差别，详见完整汇总，不能把跨轮差异归因于预算。

“通过”指满足该任务官方规定的修复与回归判分项，不表示仓库全量测试无失败。
搜索统计包含显式文件定位、文本搜索及日志过滤请求，不包含地图内部扫描；已从全部轨迹重算并核对原统计。

## 压缩包内容

- 122 份模型轨迹，敏感字段和已知凭据经过检查与脱敏处理。
- 120 份最终提交补丁、120 份官方逐任务报告、80 份实际保留地图。
- 原报告、逐任务搜索命令审计、按仓库规模统计和失败原因分析。
- 无模型性能实验报告及 20 份地图；生成成功 20/20，与修复成功率分别统计。
- 各文件来源路径、原文件与副本的 SHA-256；未复制 `.env`、runner 日志或镜像。

2,632 条收费响应均已核对 `extra.cost == usage.cost`，上述实验已返回费用总计 **$6.52352059976**。
其中 Redis 一条已中止、未返回请求的费用未知，该合计不是最终账单。
任务结果和搜索使用最终有效轨迹；所有失败尝试及其已返回费用单列保留。

## 本轮静态地图实验

本轮已完成40次推理和官方评测流程：plain 15/20、once 17/20；搜索259→285（增加10.0%）。
Lucene两组均因缺少Gradle wrapper而未执行目标测试，原始resolved=false保留，环境阻塞在新分析中单列。

[单次1024地图完整报告](../benchmarks/results/static1024-once-20261105T145121Z/budget-report.md) ·
[实现及20条逐任务分析](once-implementation-and-case-analysis.md)。
此前压缩包内的once进度仍为生成时快照，不参与上表历史统计。

重新收集已有结果，无新增模型调用或判题：

```bash
python scripts/collect_results.py
```

每次创建新的 `benchmarks/results/collected-<UTC时间>/` 及同名 ZIP，保留原实验文件。
