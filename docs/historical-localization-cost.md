# 历史Interval：定位搜索成本补评

已对此前每轮刷新RepoMap的实验补评：早期plain/1024共40条，以及统一镜像与扫描范围的四档预算共80条，合计120条选定最终轨迹。
本次仅分析既有轨迹，无模型调用、无重新判题，不修改正在运行的重复实验实现或旧官方评分。

## 1. 指标口径

沿用 [重复实验的定位成本规则](repeated-budget-experiment.md#2-定位成本的可核对实现)，复用完全相同的`localization_metrics.py`：

- 以官方patch确定基线目标文件和hunk区域，寻找相邻源码中足够长且在文件内唯一的代码行。
- 命令显式读取该目标文件，工具动作整体退出码为0，并且模型可见输出包含参考代码片段，才计为自动观察到定位。
- 主指标是到该动作结束的累计搜索子命令数，**包含定位动作**；另报模型轮数及严格位于该动作之前的搜索数。
- 只显示文件名、地图里包含目标、原始日志中有代码但未展示给模型，均不算成功观察。
- 未观察到为`N/A`，不是0次搜索、定位失败或“Agent没有读过”。配对比较只用双方均可观察的同一批任务。

这是“首次通过工具读到任一官方目标基线区域”的操作性指标，不代表已经覆盖全部修复文件、理解了任务，或证明使用了地图。
0次可以是真实结果：Agent直接cat/sed读取目标代码，此前没有显式搜索。

## 2. 四档预算实验

四档均有20/20条可观察定位记录，使用同一轮plain基线。

| 条件 | 定位搜索/任务 | 相对plain减少 | 定位模型轮数 | 全程搜索/任务 | 原官方通过 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Plain（0） | 2.00 | — | 2.70 | 11.70 | 16/20 |
| Interval 1024 | 1.50 | 25.0% | 2.40 | 9.85 | 16/20 |
| Interval 2048 | 1.45 | 27.5% | 2.05 | 12.10 | 16/20 |
| Interval 4096 | 1.70 | 15.0% | 2.20 | 11.10 | 17/20 |

按任务比较，1024为10条定位搜索更少、8条相同、2条更多；2048为10/7/3；4096为9/8/3。

定位成本与总搜索方向可以不同。2048首次定位搜索减少27.5%，但全程搜索从234增至242（+3.4%）：节省了初始定位搜索，后续理解、验证和其他探索仍可能增加。
该预算的定位均值最低，但这只是本次单次样本观察，不能据此认定2048是通用最优预算。

## 3. 早期1024历史对照

| 条件 | 定位可观察 | 定位搜索均值 | 定位模型轮数 | 全程搜索/任务 | 原官方通过 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 早期plain | 20/20 | 2.15 | 3.25 | 11.40 | 16/20 |
| 早期interval 1024 | 19/20 | 1.63 | 2.32 | 13.00 | 17/20 |

上表两组定位均值的分母不同，不能直接相减当作配对效果。
在共同可观察的19条任务中，plain为2.11次，RepoMap为1.63次，**配对定位搜索减少22.5%**；9条减少、7条相同、3条增加。
全程搜索仍为228→260（+14.0%），再次说明初始定位较省并不保证全程搜索更少。

早期与预算实验的镜像、扫描上限及时间不同，分别使用各自plain，不混用基线；每条任务每条件只有一次选定最终运行。
Ripgrep使用修复启动后的最终有效轨迹，Redis plain使用超时后的有效补跑；原始失败尝试没有混进定位主表。

## 4. Jekyll的N/A不等于没有读取

早期RepoMap的Jekyll轨迹在assistant消息15、动作0执行：

```bash
sed -n '380,450p' lib/jekyll/filters.rb; grep -R "group_by_exp" -n lib/jekyll/filters.rb
```

对应工具消息16整体退出码为1，但可见输出中包含目标区域代码。
当前规则以整个动作退出码为门槛，不能自动把其中的读取单独认定为成功，因而主表保留`not_observed`。
这是保守自动规则对复合命令的漏检边界，不能解释为Agent未定位，也没有静默放宽规则以提高覆盖。

该条另存 [人工复核说明](../benchmarks/results/historical-localization-20261106/manual-review-jekyll.json)，但没有将其补成0或混入自动配对均值。
若以后改为子命令级成功判定，应同时重算旧实验和新重复实验，并重新报告指标版本。

## 5. 逐任务证据与复现

- [全部条件汇总与两张20任务定位表](../benchmarks/results/historical-localization-20261106/summary.md)
- [120条轨迹逐项结果CSV](../benchmarks/results/historical-localization-20261106/results.csv)
- [完整JSON：匹配源码、命令、消息/动作索引与哈希](../benchmarks/results/historical-localization-20261106/report.json)
- [归档一致性复核](../benchmarks/results/historical-localization-20261106/audit.json)
- [离线评测实现](../benchmarks/results/historical-localization-20261106/implementation/evaluate_historical_localization.py)

已逐字核对全部120份原始轨迹与此前收集包，核对每条任务的`base_commit`与官方参考，重新计算的全程搜索数均与旧报告一致。
定位规则未改动，原始轨迹和成功率未改写。此前Lucene的测试依赖阻塞按旧结果保留，不将历史`resolved=false`全部解释为补丁错误。
初始定位可以从推理轨迹评估；旧动态地图没有逐请求快照，仍不能据此确认当时使用了哪张地图或建立因果归因。

```bash
source .venv/bin/activate
python benchmarks/results/historical-localization-20261106/implementation/evaluate_historical_localization.py \
  --references benchmarks/results/historical-localization-20261106/localization-reference.json \
  --output /tmp/historical-localization-recheck
```

只读取本机归档与轨迹，不启动容器、不联网、不调用模型。
