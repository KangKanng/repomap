# Once-full steps与搜索定位行为统计

所有逐任务单元格使用 **plain→once-full**。主汇总沿用18条正常提交配对；首次目标定位汇总沿用17条双方可观测配对。jq和Lucene单列展示，不参与主收益统计。

- **steps（已保存）**：`info.model_stats.api_calls`，对应mini-swe-agent的`n_calls`；在模型查询前递增，包含已保存的失败查询，不是底层HTTP重试次数。
- **已返回steps**：轨迹中的assistant轮次数。Lucene为1次已保存查询、0次返回；jq为超时前已落盘前缀，不能推断完整任务步数或未保存的等待请求数。
- **含搜索steps**：某assistant轮次的任一工具动作包含搜索子命令，该轮计1；一轮多个grep仍只计1个搜索step，但仍按原规则计多条搜索子命令。
- **首次目标定位step**：首次符合冻结规则的成功显式目标区域读取所处assistant轮次，1起始；未观测到记N/A。该规则不覆盖所有可能的等价实现路径。
- **目标区域有效读取动作**：对完整轨迹沿用上述成功读取判据，统计所有符合条件的工具动作，含带可见目标代码的grep；不是全仓库读文件总数，也不证明地图被利用。
- **定位后搜索**：定位工具动作之后的搜索，可能包含同一step内的后续动作；“定位后后续steps”只计算后面的模型轮次。这两个统计使用不同粒度，不应直接相减。
- 搜索计数包含显式请求、输出过滤和日志grep，部分请求可能因前置命令失败而未执行。相同命令文本重复不等于无效搜索，因为工作目录和源码版本可能不同。

## 汇总

| 指标 | plain | once-full | 口径/变化 |
| --- | --- | --- | --- |
| steps合计 | 429 | 303 | -29.37% |
| 平均steps/任务 | 23.833 | 16.833 | -29.37% |
| steps中位数 | 21.500 | 15.000 | 18条正常提交配对 |
| 含搜索的steps合计 | 161 | 87 | -45.96% |
| 平均搜索steps/任务 | 8.944 | 4.833 | 一个step中多次搜索只计一个搜索step |
| 搜索steps占已返回steps | 37.53% | 28.71% | -8.82个百分点 |
| 不含搜索的已返回steps | 268 | 216 | 包含读取、编辑、验证和提交等，不再细分语义 |
| 搜索子命令合计 | 236 | 123 | 沿用主指标口径 |
| 含搜索的工具动作 | 178 | 110 | 一个step可能有多个工具动作 |
| 全部工具动作 | 519 | 458 | 不是steps |
| 平均工具动作/已返回step | 1.210 | 1.512 | 按动作总数/返回steps总数计算 |
| 搜索动作返回码非零 | 6 | 4 | 包括无匹配和前置命令失败，不直接等于搜索本身失败 |
| 搜索动作缺少返回码 | 0 | 0 | 可观测性指标 |
| 相同搜索命令文本的重复次数 | 2 | 0 | 原文字串去重；不代表冗余次数 |
| 目标区域有效读取动作 | 50 | 43 | 按冻结目标读取规则核验，不是全部源码读取 |
| 平均首次目标定位step | 2.765 | 1.176 | 17条双方可观测配对 |
| 首step即定位 | 0/17 | 14/17 | full为82.35% |
| 零搜索即定位 | 0/17 | 10/17 | full为58.82% |
| 平均定位后后续steps | 21.647 | 15.118 | 仅后续模型查询轮次，不含定位所在step |
| 定位前及定位动作内搜索合计 | 39 | 9 | 17条配对，含整个定位工具动作 |
| 定位后搜索合计 | 174 | 101 | 含定位所在step内后续工具动作中的搜索 |

## 20条steps与搜索行为

| 仓库 | steps | 已返回steps | 工具动作 | 含搜索steps | 搜索子命令 | full状态 |
| --- | --- | --- | --- | --- | --- | --- |
| axios/axios | 22→16 | 22→16 | 31→30 | 10→3 | 13→5 | Submitted |
| psf/requests | 28→16 | 28→16 | 47→34 | 15→6 | 28→6 | Submitted |
| gin-gonic/gin | 19→14 | 19→14 | 22→18 | 5→3 | 6→4 | Submitted |
| pallets/flask | 11→6 | 11→6 | 11→6 | 4→1 | 5→1 | Submitted |
| sharkdp/bat | 48→26 | 48→26 | 63→41 | 16→12 | 26→22 | Submitted |
| jekyll/jekyll | 15→11 | 15→11 | 23→11 | 5→4 | 6→4 | Submitted |
| google/gson | 13→13 | 13→13 | 20→17 | 5→2 | 5→3 | Submitted |
| preactjs/preact | 16→11 | 16→11 | 16→18 | 7→4 | 9→6 | Submitted |
| burntsushi/ripgrep | 43→26 | 43→26 | 43→43 | 14→12 | 17→14 | Submitted |
| jqlang/jq | 19→12 | 19→12 | 24→12 | 5→4 | 8→4 | 超时；仅保存前缀 |
| caddyserver/caddy | 23→13 | 23→13 | 23→22 | 8→4 | 11→5 | Submitted |
| fmtlib/fmt | 34→12 | 34→12 | 34→21 | 16→3 | 23→6 | Submitted |
| tokio-rs/tokio | 14→26 | 14→26 | 30→26 | 8→10 | 23→13 | Submitted |
| redis/redis | 21→25 | 21→25 | 24→35 | 7→8 | 7→12 | Submitted |
| rubocop/rubocop | 31→13 | 31→13 | 34→21 | 8→1 | 9→2 | Submitted |
| briannesbitt/carbon | 27→25 | 27→25 | 31→34 | 11→2 | 18→2 | Submitted |
| django/django | 29→12 | 29→12 | 29→25 | 13→3 | 17→3 | Submitted |
| laravel/framework | 14→20 | 14→20 | 17→28 | 5→3 | 7→5 | Submitted |
| sympy/sympy | 21→18 | 21→18 | 21→28 | 4→6 | 6→10 | Submitted |
| apache/lucene | 14→1 | 14→0 | 15→0 | 5→0 | 5→0 | 上下文拒绝；0返回 |

## 20条首次定位与定位后行为

| 仓库 | 首次搜索step | 首次目标定位step | 定位累计搜索 | 定位后搜索 | 目标区域有效读取动作 | 定位后后续steps |
| --- | --- | --- | --- | --- | --- | --- |
| axios/axios | 1→1 | 2→2 | 1→1 | 12→4 | 3→1 | 20→14 |
| psf/requests | 1→2 | 3→1 | 2→0 | 26→6 | 2→3 | 25→15 |
| gin-gonic/gin | 1→1 | 2→1 | 1→0 | 5→4 | 2→1 | 17→13 |
| pallets/flask | 1→2 | 3→1 | 4→0 | 1→1 | 2→1 | 8→5 |
| sharkdp/bat | 1→1 | 3→1 | 4→2 | 22→20 | 8→6 | 45→25 |
| jekyll/jekyll | 1→1 | 4→1 | 4→1 | 2→3 | 2→1 | 11→10 |
| google/gson | 1→2 | 2→1 | 1→0 | 4→3 | 2→3 | 11→12 |
| preactjs/preact | 1→2 | 3→2 | 4→2 | 5→4 | 3→1 | 13→9 |
| burntsushi/ripgrep | 2→2 | 4→1 | 2→0 | 15→14 | 3→4 | 39→25 |
| jqlang/jq | 2→3 | 3→1 | 2→0 | 6→4 | 1→2 | 16→11 |
| caddyserver/caddy | 1→1 | 2→1 | 2→1 | 9→4 | 5→3 | 21→12 |
| fmtlib/fmt | 1→1 | 2→1 | 2→1 | 21→5 | 2→2 | 32→11 |
| tokio-rs/tokio | 1→1 | 3→N/A | 6→N/A | 17→N/A | 2→0 | 11→N/A |
| redis/redis | 1→1 | 2→2 | 1→1 | 6→11 | 3→5 | 19→23 |
| rubocop/rubocop | 2→3 | 4→1 | 4→0 | 5→2 | 1→1 | 27→12 |
| briannesbitt/carbon | 1→2 | 3→1 | 3→0 | 15→2 | 3→3 | 24→24 |
| django/django | 2→1 | 3→1 | 2→0 | 15→3 | 2→2 | 26→11 |
| laravel/framework | 1→2 | 2→1 | 1→0 | 6→5 | 3→5 | 12→19 |
| sympy/sympy | 2→2 | 3→1 | 1→0 | 5→10 | 2→1 | 18→17 |
| apache/lucene | 1→N/A | 2→N/A | 1→N/A | 4→N/A | 4→0 | 12→N/A |

18条正常提交任务的总steps从429降为303，含搜索steps从161降为87；每个已返回step承载的工具动作从1.210增至1.512。steps和搜索次数不必同向变化，例如Tokio为steps14→26、搜索23→13；SymPy为steps21→18、搜索6→10。

全部20条的已保存steps为462→316，已返回steps为462→315；这些包含异常，不能替代18条正常任务的主比较。Tokio的full轨迹未观测到符合目标读取规则的定位，因此定位相关字段为N/A；这不表示它没有读取其他代码或没有完成修复。

本次仅分析既有轨迹，未新增模型调用，未修改原始实验或既有主指标。40条轨迹的400项统计核对通过，并验证了4个边界场景：同一step跨定位边界的多动作、先直接读取再搜索、失败查询无assistant响应、缺少工具观察时保持N/A。

## 数据与复算

- [20条行为CSV](../benchmarks/results/full-once-20-20261107T093757Z/complete-metrics/behavior/trials.csv)
- [行为指标JSON](../benchmarks/results/full-once-20-20261107T093757Z/complete-metrics/behavior/behavior.json)
- [1,028条工具动作证据JSONL](../benchmarks/results/full-once-20-20261107T093757Z/complete-metrics/behavior/actions.jsonl)
- [复算程序](../benchmarks/results/full-once-20-20261107T093757Z/complete-metrics/behavior/implementation/summarize_behaviors.py)
- [验证结果](../benchmarks/results/full-once-20-20261107T093757Z/complete-metrics/behavior/validation.json)

整体结果见[Once-full完整评测指标](full-once-complete-metrics.md)。
