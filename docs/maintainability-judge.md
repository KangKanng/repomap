# Full-once 补丁可维护性：LLM Judge 配对评估

评估日期：2026-11-08。使用项目 `.env-judge` 中配置的 **`xopglm52`**；36 次响应返回的模型标识均为 `xopglm52`。

**本轮没有观察到 full-once 补丁的整体可维护性优势：full-once 与 plain 的平均评分均为 3.84/5。** 交换候选顺序后，4 对一致偏好 full-once、3 对一致偏好 plain、7 对均判平局，另 4 对发生偏好分歧。局部正负案例同时存在。

## 选了哪一组

选取最新的 [full-once 单组实验](full-once-experiment.md)，对照该实验原先预定的 `once / repeat-01 / plain`。保留全部 18 对双方正常提交的补丁，没有按判题结果或 judge 评分筛选。

另外 2 条 full-once 没有提交补丁，不能做维护性评分，单列为 jq 的 `ProcessTimeout` 和 Lucene 的 `BadRequestError`；没有把它们打成 0 分，也没有假装全部 20 条都获得评分。未新跑代码修复模型或改写原有官方判题。

这是一次新评审对历史实验补丁的比较，20 个仓库仍各只有 1 条任务。18 对补丁和 36 次评审不是 36 个独立任务。

## 如何评

每对补丁随机标为 A/B，首轮分配由任务 ID 和固定种子 20261108 的哈希确定，第二次独立请求交换 A/B。两个请求互相看不到评分。模型参数固定为 `temperature=0`、`max_tokens=4500`、超时 180 秒，并发 2，不自动重试。

judge 收到任务描述、两份完整 diff，以及两份补丁修改位置并集附近的共同基线源码片段。源码从固定镜像的停止容器中复制，未运行镜像内代码。片段包括文件前 30 行和 hunk 前后约 20 行；bat 的 `src/assets.rs` 片段触及长度上限，输入明确标注了截断。

judge 看不到组别名称、RepoMap、官方参考补丁、测试评分、搜索次数或之前的人工分析。没有因实验禁止提交测试而对“未新增测试”扣分。

四个维度各打 1–5 整数分，高分表示较好，等权计算均分；先对每份补丁的四项求均值，再合并交换顺序的两次评分，最后按任务等权汇总。

| 维度 | 判断内容 |
| --- | --- |
| 可读性 `clarity` | 命名、注释、控制流和实现意图 |
| 范围与内聚 `scope_cohesion` | 是否围绕需求集中修改、是否改变无关行为 |
| 复用与抽象 `reuse_abstraction` | 是否使用合适共享层、避免重复逻辑和额外状态 |
| 后续维护便利性 `evolution_cost` | 后续新增类型、调用点或配置时保持一致的难易；高分表示成本较低 |

3 分定义为合理但普通，5 分需要明确良好设计证据，1 分需要明确严重问题；短补丁不自动高分。功能正确性风险要求另列，不能用未知测试是否通过代替维护性评分。完整提示词已冻结在 [rubric.txt](../benchmarks/results/maintainability-judge-full-once-20261108/rubric.txt)。

## 总体评分

| 指标 | plain | full-once | full-once − plain |
| --- | ---: | ---: | ---: |
| 综合均分 | 3.8403 | 3.8403 | 0.0000 |
| 可读性 | 3.7778 | 3.8889 | +0.1111 |
| 范围与内聚 | 4.3889 | 4.4722 | +0.0833 |
| 复用与抽象 | 3.6944 | 3.6667 | −0.0278 |
| 后续维护便利性 | 3.5000 | 3.3333 | −0.1667 |

综合评分差按 18 条任务整对重采样 10,000 次，固定种子 20261108，探索性 95% 区间为 **[−0.4583, +0.4028]**。评分是单个模型给出的有序等级，均值和区间只作描述；均分相同不证明两种条件等效，也不能证明 RepoMap 在其他任务上没有收益。

按合并后的数值均分，full-once 为 **7 高 / 6 相同 / 5 低**。如果要求两次直接偏好一致，则是 **4 胜 / 7 平 / 3 负 / 4 分歧**。这两种口径不同：judge 可以在分数略有差异时仍判平局，不能把数值微差自动当成稳定胜负。

| 仓库 | plain 均分 | full-once 均分 | 差值 | 两次直接偏好 |
| --- | ---: | ---: | ---: | --- |
| axios | 4.125 | 4.375 | +0.250 | 分歧：平 / full |
| requests | 3.250 | 3.875 | +0.625 | full / full |
| gin | 3.750 | 3.750 | 0.000 | 平 / 平 |
| flask | 4.250 | 4.250 | 0.000 | 平 / 平 |
| bat | 2.875 | 4.000 | +1.125 | full / full |
| jekyll | 4.375 | 4.375 | 0.000 | 平 / 平 |
| gson | 4.125 | 4.125 | 0.000 | 平 / 平 |
| preact | 3.375 | 3.875 | +0.500 | full / full |
| ripgrep | 4.000 | 3.500 | −0.500 | plain / plain |
| caddy | 4.125 | 3.750 | −0.375 | 分歧：plain / full |
| fmt | 2.375 | 4.375 | +2.000 | full / full |
| tokio | 5.000 | 2.125 | −2.875 | plain / plain |
| redis | 4.375 | 4.375 | 0.000 | 平 / 平 |
| rubocop | 4.250 | 4.250 | 0.000 | 平 / 平 |
| carbon | 3.500 | 3.125 | −0.375 | 平 / 平 |
| django | 4.125 | 4.250 | +0.125 | 分歧：plain / full |
| laravel | 4.375 | 3.500 | −0.875 | plain / plain |
| sympy | 2.875 | 3.250 | +0.375 | 分歧：plain / full |

正负差异集中在少量任务。只删除一个任务，综合差的范围就会从 −0.1176 到 +0.1691；该敏感性不用于挑选有利子集。主结果保留全部 18 对。

## judge 对具体设计的判断

以下是模型意见及代码核对，不是已证实的因果效果。原评分保持不变。

- **fmt：偏好 full-once，+2.00。** full-once 只修复字符左对齐覆盖；plain 同时改动通用 zero-fill 处理并增加字符填充逻辑。judge 认为后者职责分散、影响范围更广。这是“集中修复”得到较高分的例子。[首轮原始评审](../benchmarks/results/maintainability-judge-full-once-20261108/responses/fmtlib__fmt-1683.order-1.json)
- **bat：偏好 full-once，+1.125。** plain 同时引入 `HighlightingAssets` 中的后缀状态、setter 和额外调用分支；full-once 通过参数显式传递配置。judge 认为后者少一套状态同步机制。两份方案也有公共接口演进的权衡，不能仅凭本次评分证明其 API 设计最优。[首轮原始评审](../benchmarks/results/maintainability-judge-full-once-20261108/responses/sharkdp__bat-1892.order-1.json)
- **Requests：偏好 full-once，+0.625。** full-once 保留用户显式 `Content-Length`，修改集中在长度准备函数；plain 还改了空 iterable 的 streaming 判断。judge 偏好较小的行为修改范围。[首轮原始评审](../benchmarks/results/maintainability-judge-full-once-20261108/responses/psf__requests-1142.order-1.json)
- **Tokio：偏好 plain，−2.875。** plain 在共享 `Registration` 层集中实现 trait；full-once 在 8 个文件、9 个叶子类型上复制 18 个实现。judge 两次都认为前者更集中，后者容易在新增类型时遗漏。这个评价支持对重复维护点的担忧，不能替代 panic 后状态不变量的证明。[首轮原始评审](../benchmarks/results/maintainability-judge-full-once-20261108/responses/tokio-rs__tokio-4384.order-1.json)
- **Laravel：偏好 plain，−0.875。** judge 更喜欢复用 `current()` 的局部条件，对 full-once 内联 `rtrim` 和使用 `full()` 的解释成本给出较低分。这里恰好展示维护性分与功能判题的区别：这条 full-once 原官方通过、plain 未通过，不能用“较易读”替代需求边界验证。

## 评分可靠性与复核

5 对原始 diff 完全相同的补丁是 gin、Flask、Gson、Redis、RuboCop。它们的 10 次评审均判平局，候选内各维度同分。不同请求间仍会出现绝对分值变化，因此没有把 `temperature=0` 当作完全确定性保证。

4 对交换顺序后偏好不一致：Axios、Caddy、Django、SymPy。这些分歧可能同时来自位置偏好与模型自身波动，不能单独归因于其中一种。

**SymPy 第二次评审有明确的需求理解问题。** 任务要求异维距离保留额外维度并得到 `sqrt(5)`，judge 却以“隐式补零语义不明”为由偏好抛异常的 full-once 方案；第一次评审则偏好按需求补零的 plain。原始分数和偏好都保留，没有按人工意见改写。它说明 judge 不能取代需求核对，尤其不能把这条的正均分差解释成更好的修复。[第二次原始输出](../benchmarks/results/maintainability-judge-full-once-20261108/responses/sympy__sympy-11618.order-2.json)

36 次响应中 32 次直接符合程序要求；4 次具有格式偏差，已在离线汇总中恢复：

- Preact 第二次、ripgrep 第一次：评分齐全，但 finding 使用了评分维度之外的标签。
- fmt 第二次、SymPy 第二次：`candidates` 对象在 `pairwise_preference` 前少一个闭合花括号，仅补该括号即可解析完整原始分数。

没有因此付费重评，也没有修改原始响应文件、数值分、A/B 对应关系或直接偏好。`summary.json` 是执行器的严格格式统计；本报告使用记录恢复过程的 **`audit-summary.json`**，两者口径差异已经明示。

220 条代码引用中，202 条在对应候选 diff 或共同基线片段中逐字匹配，15 条仅有空白差异，3 条需要人工定位：bat 的 Rust 字符串续行省略反斜杠、Caddy 省略中间注释、SymPy 用省略号拼接两个函数名。三者能定位到相应代码，但都不符合提示词的逐字引用要求，未伪装成自动校验通过。另行记录在 [manual-review.json](../benchmarks/results/maintainability-judge-full-once-20261108/manual-review.json)。

本次重点复核了主要正负案例和引用异常，没有把模型的全部 `correctness_risks` 都当成真实缺陷。judge 缺少完整依赖源码、测试执行与未来维护实践，较高分也不是安全或正确性认证。

## 调用与费用

36 次请求均已返回，包含 143,302 输入 tokens、28,727 输出 tokens，合计 172,029 tokens。全部服务端 usage 均未报告费用，因此 **费用未知**，没有按 0 计费，也没有混入原代码修复实验费用。没有为更换展示顺序之外的评分重复付费，没有更换 `.env-judge` 模型或修改该文件。

## 材料与复算

- [选择清单、A/B 揭盲映射与输入哈希](../benchmarks/results/maintainability-judge-full-once-20261108/manifest.json)
- [评分、任务簇区间和恢复审计汇总](../benchmarks/results/maintainability-judge-full-once-20261108/audit-summary.json)
- [逐任务配对评分 CSV](../benchmarks/results/maintainability-judge-full-once-20261108/paired-scores.csv)
- [逐条引用核对](../benchmarks/results/maintainability-judge-full-once-20261108/evidence-validation.json)
- [执行配置与校验记录](../benchmarks/results/maintainability-judge-full-once-20261108/run-metadata.json)
- [调用脚本](../scripts/judge_patch_maintainability.py) · [离线分析脚本](../scripts/analyze_maintainability_judge.py)
- [此前 489 份补丁的范围和安全审计](patch-quality-analysis.md)

```bash
# 仅离线复算，保留原始评分，不调用模型
.venv/bin/python scripts/analyze_maintainability_judge.py

# 请求 judge；已有响应均默认复用，包括已保存的格式失败记录
# 显式 --retry-errors 才会再次付费请求错误记录；本次评估无需重跑
.venv/bin/python scripts/judge_patch_maintainability.py
```
