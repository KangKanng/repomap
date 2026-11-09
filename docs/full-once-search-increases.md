# Once-full 两条搜索增加案例

这里只分析同一组20任务中，18条正常提交配对里搜索增加的 Redis 与 SymPy。统计沿用冻结的显式 Bash 搜索子命令口径，未新增推理。

| 任务 | 总搜索 plain→full | 定位前及定位动作内 | 定位后 | 官方结果 |
| --- | ---: | ---: | ---: | --- |
| Redis | 7→12 | 1→1 | 6→11 | 两组通过 |
| SymPy | 6→10 | 1→0 | 5→10 | plain通过，full未通过 |

两条的搜索净增都出现在已定位目标之后。地图确实包含 Redis `src/t_stream.c::streamTrim`（行719）和 SymPy `sympy/geometry/point.py::distance`（行237）的声明；不能将增加简单归因为目标函数未进入地图。

## Redis：复现路径与底层编码排查扩大

任务要求修复 XTRIM MINID 在特定ID组合下误删较大ID的消息。

两组均在累计1次搜索后首次读到目标区域。plain在确认delta整数类型问题后修改两行类型，再用明确ID和自动生成ID场景验证。

full 首先误用 `/workspace`，导致该动作失败；随后在 `/testbed` 读取正确函数。后续使用当前时间生成ID，却仍传入问题中较旧的阈值，未立即复现相同现象，继而检查版本、历史、节点主ID、listpack存储和64位整数编码。使用报告中的明确ID后复现成功，再插入临时 `TRIMDBG` 日志，继续搜索 `listpack.c/.h` 的编码、`debug.c` 及 `lpGetInteger`。

具体证据（索引为轨迹messages数组下标）：

- full m3/a1：错误目录下的grep请求；m6/a0：纠正目录后首次读取目标。
- full m22/a1、m27/a0-a1：版本、现有测试和边界ID相关查找。
- full m36/a0-a1：两次listpack整数编码查找。
- full m39/a0-a1：debug设施及整数读取查找，共三条grep。
- full m44/a0：过滤临时诊断日志。

两组最终提交补丁逐字相同：仅把 `ms_delta` 与 `seq_delta` 从 `int` 改为 `int64_t`，官方均通过。额外排查没有改变最终提交或通过结果，不能仅凭这些现象声称地图提供了负面信息。

## SymPy：定位改善，但需求语义选择错误

任务已明确给出 `Point(2,0).distance(Point(1,0,2))` 应计算为 `sqrt(5)`。这需要把缺失维度按零参与距离计算，而不是拒绝不同维度的输入。

full 首个工具动作直接读取 `point.py`，因此定位成本从1降为0。随后它查询维度不匹配的API惯例和相关测试，倾向将加减、交点等操作的维度限制推广到distance，转而实现维度不同时抛出ValueError。

后续搜索范围扩大到内部调用点、其他distance定义、全系统同名point.py和问题标题文本；另有访问上游、下载新版本的尝试。这些网络命令不是本搜索计数器统计的grep/find子命令，不能把全部耗时都解释成新增4次搜索。

具体证据：

- full m3/a0：第一次模型动作直接读取目标文件，定位累计搜索为0。
- full m5/a0-a1、m13/a1：搜索distance测试与维度限制惯例。
- full m19/a0：在全系统查找其他point.py。
- full m25/a0-a2：查询内部调用、Point.distance引用及其他distance定义，共四条grep（包括结果过滤）。
- full m29/a0：在全系统搜索问题标题，动作返回码为-1。
- full m33/a0：修改distance，并扩展到taxicab_distance、midpoint、dot的维度检查。
- full m35/a0：自写验证以“跨维度应抛ValueError”为预期，未验证任务要求的sqrt(5)。
- full m41/a0：grep过滤几何测试日志，计入1次搜索。

plain则采用补零方案，官方通过。full提交后官方 `test_issue_11617` 失败，P2P四项通过。这里的主要问题是修复行为与明确任务要求不一致，而非未找到文件或函数。

## 解释范围

搜索计数包含显式搜索请求、输出过滤和测试日志grep，也可能包含因前置cd失败而未实际执行的搜索子命令。因此它衡量的是提出的搜索工作量，不完全等于已执行的源码定位程序数量；两组沿用相同口径。

地图提供路径和声明，不直接提供正确的运行时诊断或任务预期。Redis体现了定位后排查路径变长；SymPy体现了定位更快但需求判断走偏。一次新轨迹与一次历史轨迹不足以确定大地图是否因噪声导致分叉，不能据此直接认定排名策略或适配实现有缺陷。

完整证据：[搜索事件JSON](../benchmarks/results/full-once-20-20261107T093757Z/complete-metrics/search-increases.json)。下列原始轨迹可按messages索引复核：

- [redis/redis plain](../benchmarks/results/once-interval-repeats-20261105T180350Z/once/repeat-01/conditions/repeat-01-b0/plain/SWE-bench_Multilingual/redis__redis-10068/redis__redis-10068.traj.json)
- [redis/redis full](../benchmarks/results/full-once-20-20261107T093757Z/repomap/SWE-bench_Multilingual/redis__redis-10068/redis__redis-10068.traj.json)
- [sympy/sympy plain](../benchmarks/results/once-interval-repeats-20261105T180350Z/once/repeat-01/conditions/repeat-01-b0/plain/SWE-bench_Verified/sympy__sympy-11618/sympy__sympy-11618.traj.json)
- [sympy/sympy full](../benchmarks/results/full-once-20-20261107T093757Z/repomap/SWE-bench_Verified/sympy__sympy-11618/sympy__sympy-11618.traj.json)

整体指标见 [Once-full完整指标](full-once-complete-metrics.md)。
