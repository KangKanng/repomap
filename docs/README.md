# Repo Map 文档

本项目将 [Aider Repository Map](https://aider.chat/docs/repomap.html) 的思路接入 mini-swe-agent v2：
在模型查询前，按当前任务选取仓库中的重要定义与签名，生成受 token 预算约束的代码索引。

## 阅读顺序

| 文档 | 内容 |
| --- | --- |
| [SCBench plain 与 once-full](scb-plain-once-full.md) | 25题完整 checkpoint 链的固定抽样、官方评测适配、运行状态和配对结果 |
| [480次实验最终分析](final-experiment-analysis.md) | 全部once/interval重复结果、搜索与定位分解、成功率约束、费用、地图时间关系、20条明细及统计区间 |
| [补丁可维护性与安全性审计](patch-quality-analysis.md) | 489份实际补丁的范围统计、具体代码审阅与通过判题后仍可触发的bat空后缀栈溢出复现 |
| [Full-once补丁可维护性Judge](maintainability-judge.md) | 使用.env-judge的xopglm52，对18对补丁隐藏组别并交换顺序评审36次，保留评分、分歧与引用复核 |
| [适配方法与处理流程](adaptation.md) | 扩展接口、模块职责、源码收集、符号提取、图排序、预算裁剪、消息注入、缓存与失败处理 |
| [使用指南](usage.md) | 安装、地图预览、本地 Agent、Docker、SWE-bench、参数配置、Python 接入与排错 |
| [完整 RepoMap 单次注入](full-once.md) | 不按预算裁剪的全量符号索引、关闭扫描上限、单次上下文注入及 Docker 对照脚本 |
| [Full-once 单组实验](full-once-experiment.md) | 仅新跑20条完整单次地图，复用历史plain；自动判题与搜索、定位成本及成功率对照 |
| [Once-full 完整评测指标](full-once-complete-metrics.md) | 全20条结果、18条搜索配对、17条定位配对、官方测试明细、费用/tokens/缓存、五项地图质量状态及逐任务表 |
| [Once-full steps与搜索定位行为](full-once-steps-and-localization.md) | 每任务steps、工具动作、搜索steps、首次定位step、定位前后搜索和目标区域读取；20条明细及CSV |
| [Once-full 两条搜索增加案例](full-once-search-increases.md) | Redis的额外复现和编码排查、SymPy的需求语义偏离；定位前后拆分及逐搜索证据 |
| [Full-once token 占用审计](full-once-token-audit.md) | 20张实际地图复算、Lucene首请求超限拆分、本地与服务端计数、测试路径体积和逐文件CSV |
| [超大仓库的 RepoMap 方案](large-repository-repomap-options.md) | Lucene紧凑完整地图实测、预算选择、模块范围、分层按需加载及上下文预检设计 |
| [实验运行指南](experiments.md) | scripts 入口、Docker 拉取、20 个仓库性能实验、模型基线对照、指标与结果文件 |
| [实验 A：地图质量验证（优先）](map-quality-evaluation.md) | 官方补丁参考、文件/符号召回、关键依赖覆盖、符号精度、利用证据及离线标注评分入口 |
| [历史地图质量回溯](historical-map-quality-results.md) | 旧版、三档预算和once的官方文件召回；初始重建与最终地图分别统计及逐任务明细 |
| [历史Interval定位搜索成本](historical-localization-cost.md) | 旧版1024与四档预算的首次目标读取成本、同轮plain配对、20条逐任务明细及N/A复核 |
| [扩大预算与重复实验](repeated-budget-experiment.md) | 先once后interval，四档预算各重复3次；搜索主指标、定位成本、成功率约束及自动报告 |
| [实验结果汇总](results.md) | 已完成实验对照、逐任务明细、原始轨迹、补丁、官方报告和 ZIP 压缩包 |
| [完整实验结果（Markdown）](experiment-results.md) | 配置与统计口径、性能测试、历史对照、四档预算、20 仓库逐任务表及 once 阶段性结果 |
| [Once 实现与逐任务分析](once-implementation-and-case-analysis.md) | 单次地图实现、40次完整结果、20条任务需求和轨迹、搜索增减原因、judge复核及评测环境例外 |
| [Auto 与动态预算实施计划](auto-dynamic-budget-plan.md) | 上游源码依据、刷新策略、活动文件适配、预算计算、指标与验证计划（待实现） |
| [实际地图示例](examples/repomap/README.md) | 从已完成实验导出的 Flask、Django 地图原文与来源元数据 |
| [.env 配置](environment.md) | 模型名、API base URL、密钥、流式开关、环境文件路径及覆盖顺序 |
| [Docker 仓库样本](../benchmarks/docker_repositories/README.md) | 20 个不同项目的源码镜像，按实测源码行数排序 |
| [镜像完整清单](../benchmarks/docker_repositories/inventory.json) | 镜像名、digest、仓库 commit、源码规模、逐语言统计与检查状态 |

## 当前版本

- 已验证 mini-swe-agent **2.4.6**，包依赖范围为 `>=2.4.6,<3`；v1 不在支持范围内。
- 宿主机运行符号解析、图排序和 token 计数；源码从 Agent 的实际执行环境中读取。
- 宿主机依赖随扩展安装；Agent 执行环境需要 shell 和 Python 3.10+。
- 默认地图预算为 1024 tokens，按配置的 Tiktoken 编码计数。
- 已支持 `refresh=once`：每个任务只根据原始任务生成并注入一次地图，后续保持不变；实验入口为 `scripts/run_static.py`。
- 已支持完整单次地图：`mode=full` 配合 `refresh=once`，跳过排名与 token 选择；入口为 `scripts/run_full.py`，配置为 `full-once.yaml`。
- 本实现独立于 Aider，图关系属于符号引用启发式，地图用于定位代码。

快速开始见 [使用指南：本地使用](usage.md#本地使用)。项目入口见 [根目录 README](../README.md)。
