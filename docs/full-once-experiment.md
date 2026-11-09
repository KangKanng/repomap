# Full-once 单组实验与历史 plain 对照

本轮按要求只新增 **20 次 full-once 推理**，每个任务一次；plain 复用既有结果，不新增 plain 推理。

- [持续更新的结果与逐任务对照](../benchmarks/results/full-once-20-20261107T093757Z/report.md)
- [运行状态](../benchmarks/results/full-once-20-20261107T093757Z/status.json)
- [固定实验协议](../benchmarks/results/full-once-20-20261107T093757Z/protocol.json)
- [20 条运行命令](../benchmarks/results/full-once-20-20261107T093757Z/plan.json)
- [复用的 plain 指标与原始轨迹来源](../benchmarks/results/full-once-20-20261107T093757Z/reused-baseline.json)
- [自动执行、判题与报告程序](../benchmarks/results/full-once-20-20261107T093757Z/implementation/run_experiment.py)
- [实际地图 token 审计与 Lucene 超限原因](full-once-token-audit.md)
- [Once-full 完整评测指标与20条明细](full-once-complete-metrics.md)
- [Steps与搜索定位行为统计表](full-once-steps-and-localization.md)

## 预先固定的设置

使用之前的 20 个 SWE-bench 任务与相同的固定镜像 ID，包含上一轮已使用的 Lucene 修复镜像。配置模型为 `openrouter/deepseek/deepseek-v4.1-flash-1017`，认证和服务地址继续读取项目 `.env`，不写入实验协议。实际返回的模型 ID、每次响应的费用来源会另行统计。

`mode=full`、`refresh=once`、三项扫描上限均为 `null`。首次请求前生成全部收集到的路径和符号索引，不按 token 数选择；地图随后随累计历史保留，不刷新。主要定义的提取与签名显示、目录排除规则保持原实现，详见 [full-once 实现](full-once.md)。

推理并发 2，官方判题并发 1。两阶段容器均断网；收集器 Python 以只读卷挂载。每条推理沿用 250 步、已返回费用上限 3 美元、1200 秒软时限与 1380 秒进程硬时限；官方单任务测试时限 900 秒。不因上下文超限而自动裁剪地图或切换为预算模式。

## 历史基线与统计口径

在新推理开始前，固定选择 [上一轮重复实验](repeated-budget-experiment.md) 的 **once / repeat-01 / plain（预算 0）** 作为主对照，包含同一批 20 条任务，全部正常提交，官方通过 **16/20**，搜索子命令合计 **249**，平均 **12.45/任务**。

主指标是显式搜索子命令数/任务。只在新组正常提交的任务中与对应历史任务配对计算均值与差值；失败或超时不因搜索为 0 而成为“减少搜索”的证据。另报全部 20 条的完成率与官方成功率，检查成功率约束。

辅助定位指标复用冻结的官方补丁基线附近代码参考，只用于事后分析。统计首次显式读取目标代码时累计的搜索子命令数，只有两组均可观测的任务进入定位配对；缺失记 `N/A`。官方补丁和定位参考不会进入推理上下文。

新组所有已返回响应的服务端费用单独累加；历史 plain 不产生新的推理费。未返回请求可能存在未知费用，不能把已返回费用视为完整账单。

这是一轮新结果对一轮预先选定历史结果的对照。运行时间和服务状态可能不同，且没有新增重复实验，因此只能报告观察差异，不能据此证明因果收益、统计稳定性或成功率非劣性。

## 自动流程

后台程序按仓库源码行数升序调度 20 条 full-once，逐条保存轨迹、补丁、服务端费用、地图快照与结果。官方判题在已有至少 3 条完成时开始，复用已判结果并在剩余任务完成后继续判题。

`report.md`、`report.json` 和 `trials.csv` 持续更新。全部推理和判题结束后，状态进入 `completed` 或 `completed_with_errors`；出现上下文超限、模型错误或测试环境问题会保留原始记录，不自动重新付费推理。原有实验目录保持不变。
