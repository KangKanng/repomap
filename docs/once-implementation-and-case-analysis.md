# Once RepoMap：实现方案与20条任务的搜索差异分析

整理时间：`2026-11-05T16:23:21.363460+00:00`（UTC）。实验目录：`static1024-once-20261105T145121Z`。40次推理与官方评测流程均已结束，评测驱动完成时间为 `2026-11-05T16:04:18.608733+00:00`。Lucene的两次评测被缺失构建依赖阻塞，目标测试未执行，见第7节。

**本轮结果：plain 15/20，once 17/20；搜索 259→285（+10.0%），已返回费用 +18.1%。**搜索减少9条、增加9条、相同2条。一次地图没有在本轮整体减少搜索，但多解决了2条任务；更少搜索与更高修复质量并不总是同时发生。

## 1. 本次实现方案

本项目在 mini-swe-agent 的查询入口增加 RepoMapMixin，保留 mini 的模型调用、动作执行、累计历史和限制机制。本轮启用 `refresh=once`，不是 Aider 原版生成器，也未启用 auto 或动态预算。

```yaml
agent:
  repomap:
    map_tokens: 1024
    refresh: once
    context_messages: 0
    strict: true
    max_files: 20000
    max_file_bytes: 1073741824
    max_total_bytes: 134217728
```

一次任务的处理流程：

1. `run(task)` 重置本任务的地图注入状态，mini 初始化系统消息和任务消息。
2. 第一次 `query()` 在实际容器工作区收集源码；优先按 Git 列举文件，排除依赖/缓存目录、符号链接和二进制文件。
3. 宿主机用 Tree-sitter 提取定义、引用和签名，按同名符号构建文件图；采用任务相关性加权的 PageRank 排序。
4. 本轮只使用原始任务文字，不使用最近6条消息；将候选定义裁剪到最多1024个 `cl100k_base` tokens，包含说明和标签。
5. 地图作为一条额外 user 消息插在原始任务之后，随后进入 mini 原有的模型/工具循环。
6. 后续请求不再收集、重排、替换或新增地图；同一个 Agent 开始新任务时再生成一次。plain 仅将 map_tokens 设为0，完全跳过地图。

“只注入一次”指消息只插入一次。mini 的累计历史会在后续 API 请求中继续包含这条地图，因此不是只在第一个请求中发送、也不是后续地图 token 永远免费。固定消息避免持续改写该位置，但服务端缓存和最终费用仍需实际测量。

排序仍是启发式：完整路径命中任务加20、路径组件命中加5、文件内符号名命中加10；引用频次取平方根，任务提及符号加强、下划线名称降权。签名最多12行、每行240字符。没有编译器级作用域解析，也没有专门排除压缩第三方样本。

源码：[查询与状态重置](../src/mini_swe_repomap/agent.py)、[符号排序与预算裁剪](../src/mini_swe_repomap/repomap.py)、[静态配置](../src/mini_swe_repomap/config/once.yaml)、[实验入口](../scripts/run_static.py)。全局默认仍可按 interval 刷新，本轮通过 once overlay 显式启用单次策略。

## 2. 实验控制与指标边界

20个仓库各一个任务，两组使用同一固定镜像、模型、任务提示词、工具、累计历史、扫描上限和运行限制；每个任务只进行一次有效运行。两组唯一有意改变的是地图 token 预算：0/1024。实例间轮换组别位置，两路推理、单路官方判题；容器均为 `network=none`、`pull=never`。

推理模型配置为 `openrouter/deepseek/deepseek-v4.1-flash-1017`；运行上限为250步、1200秒、费用停止阈值$3，进程级时限1380秒。模型 API 请求由宿主机发出；容器断网不等于模型服务断网。

主指标为模型显式请求的搜索子命令，复用原 Bash AST 统计器，保持与此前实验一致：

- 计入 grep/rg/git grep 等文本搜索与过滤，以及 find/git ls-files 等文件定位；同一动作中的多个子命令分别计数。
- 不计普通 cat/sed 读取、ls、脚本内部搜索、RepoMap内部扫描；一条请求不保证所有条件分支都执行成功。
- 既含源码定位，也含测试/日志/环境过滤，故不能把总数直接叫“代码定位次数”。
- 发现一条维护命令 Requests `O022A0` 的 `find ... -delete` 也被原口径计入。若仅剔除此条，once为284，仍高于plain的259；本报告不静默修改原统计。
- 官方通过表示规定的修复/回归判分项满足，不代表仓库全量测试均成功。

## 3. 完整结果与搜索变化分布

| 指标 | plain | once 1024 |
| --- | --- | --- |
| 推理/评测记录 | 20/20 | 20/20 |
| 官方 resolved | 15/20 | 17/20 |
| 搜索子命令 | 259 | 285 |
| 包含搜索的动作 | 205 | 224 |
| 模型调用 | 456 | 432 |
| 前5次调用中的搜索 | 94 | 112 |
| 第6次及之后的搜索 | 165 | 173 |
| 输入 tokens | 9922285 | 9940567 |
| 输出 tokens | 189717 | 220314 |
| 缓存输入 token 占比 | 87.05% | 86.55% |
| 已返回服务端费用 USD | 0.3438668601425 | 0.4059658072025 |

配对结局：两组都通过14条；once独有通过为 fmt、Tokio、Laravel；plain独有通过为 Caddy；两组都未通过为 SymPy、Lucene。增加的搜索合计81次、减少合计55次，净增加26次。Django +24、Requests +17、Tokio +12 是主要增加项；jq −19、RuboCop −10、fmt −7 是主要减少项。

| 描述性子集 | 任务数 | plain搜索 | once搜索 | 搜索变化 |
| --- | --- | --- | --- | --- |
| 全部任务 | 20 | 259 | 285 | +10.0% |
| 两组都通过 | 14 | 161 | 170 | +5.6% |
| 排除同一依赖阻塞的Lucene | 19 | 247 | 268 | +8.5% |

“两组都通过”是按结果筛选的描述性子集，存在选择偏差，不是无偏的因果估计。官方主分数仍按20条保留，未因Lucene环境阻塞改写原判题。

### 20条任务总览

| 仓库 | plain搜索 | once搜索 | 差值 | plain判题 | once判题 | 可见过程差异 |
| --- | --- | --- | --- | --- | --- | --- |
| axios/axios | 5 | 8 | +3 | 通过 | 通过 | 前期依赖与测试核对增多 |
| psf/requests | 12 | 29 | +17 | 通过 | 通过 | 历史版本、环境探索与方案反复 |
| gin-gonic/gin | 2 | 6 | +4 | 通过 | 通过 | 中间件调用链理解与复核增多 |
| pallets/flask | 5 | 3 | -2 | 通过 | 通过 | 初始定位与约定查询减少 |
| sharkdp/bat | 23 | 27 | +4 | 通过 | 通过 | 多文件调用点前置扫描增多 |
| jekyll/jekyll | 7 | 7 | +0 | 通过 | 通过 | 搜索前移与后移抵消 |
| google/gson | 5 | 3 | -2 | 通过 | 通过 | 文件定位与依赖查询减少 |
| preactjs/preact | 13 | 8 | -5 | 通过 | 通过 | 浏览器测试环境探索减少 |
| burntsushi/ripgrep | 14 | 14 | +0 | 通过 | 通过 | 替换/多行范围追踪相抵 |
| jqlang/jq | 33 | 14 | -19 | 通过 | 通过 | 更窄修复范围、重复验证过滤减少 |
| caddyserver/caddy | 30 | 25 | -5 | 通过 | 未通过 | 地址归并验证较少、修复不完整 |
| fmtlib/fmt | 22 | 15 | -7 | 未通过 | 通过 | 最小修复避免回归及后段探索 |
| tokio-rs/tokio | 14 | 26 | +12 | 未通过 | 通过 | 更完整的类型覆盖与编译验证 |
| redis/redis | 11 | 7 | -4 | 通过 | 通过 | 类型分析集中、后段查询减少 |
| rubocop/rubocop | 17 | 7 | -10 | 通过 | 通过 | 查询被源码阅读与脚本检查替代 |
| briannesbitt/carbon | 11 | 10 | -1 | 通过 | 通过 | 补丁范围与验证路线略有收敛 |
| django/django | 3 | 27 | +24 | 通过 | 通过 | 上游/缓存探索及正则边界反复验证 |
| laravel/framework | 9 | 13 | +4 | 未通过 | 通过 | URL 归一化边界与验证更完整 |
| sympy/sympy | 11 | 19 | +8 | 未通过 | 未通过 | 语义误读未纠正，缓存/参考探索增多 |
| apache/lucene | 12 | 17 | +5 | 未通过 | 未通过 | 调用链检查增多，双方测试依赖阻塞 |

## 4. 逐条分析：任务、过程、补丁与搜索增减

证据编号中 P 表示 plain、O 表示 once，后三位为模型调用序号，A 后为该次调用中的动作序号。每条末尾链接包含原始任务、实际地图、完整补丁、逐步命令、输出摘录及其原轨迹消息/动作索引。

这里的“原因”首先解释可见命令数量的差异；将这些差异进一步归因于地图，需要重复实验。地图是否包含最终修改文件是事后重叠指标，不能当成模型使用地图的证明，也不是预先知道的正确目标标签。

### 01. axios/axios（12,686行）

**任务要求：**让 maxBodyLength 的默认无限制语义在是否经过 follow-redirects 时保持一致，并补充文档。

**结果：**搜索 5→8（+3），调用 16→15；官方 通过→通过。前5次调用搜索 5→8。

**地图事实：**实际 1016 tokens，列出 13 个文件；与 once 最终提交文件的重叠：`lib/adapters/http.js`。

**Plain过程：**`P002A0` 直接围绕 maxBodyLength/maxContentLength/follow-redirects 查询，`P004A0` 对照依赖，`P005A0`/`P005A1` 查测试和默认值；5 次搜索全部发生在前 5 次调用。最终在未设置有限上限时传 Infinity，并更新 README。

**Once过程：**`O001A1` 至 `O004A1` 同样完成配置、依赖、测试及文档检查；额外查询了 maxRedirects 测试、follow-redirects README，并做了一次全仓库 maxBodyLength 复核。8 次搜索也全部发生在前 5 次调用。核心补丁与 plain 一致，均通过。

**增减解释：**增加主要来自早期核对范围扩大，而不是后期修补反复。地图包含 lib/adapters/http.js，但仍不能替代依赖默认值和测试行为的确认；搜索增加时调用数反而从 16 降至 15。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/axios__axios-4731.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/axios__axios-4731.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/axios__axios-4731/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/axios__axios-4731/report.json)。

### 02. psf/requests（13,679行）

**任务要求：**无请求体的 GET 请求不应被自动添加 Content-Length: 0；有请求体时仍应正确计算长度。

**结果：**搜索 12→29（+17），调用 20→35；官方 通过→通过。前5次调用搜索 4→4。

**地图事实：**实际 1023 tokens，列出 14 个文件；与 once 最终提交文件的重叠：`requests/models.py`。

**Plain过程：**很早定位 requests/models.py，`P012A0` 实施修改；`P014A0`/`P015A0` 对照已安装 requests 的实现。最终无体 GET/HEAD 不设头，其他无体方法仍设 0。

**Once过程：**两组前 5 次调用均搜索 4 次。`O010A0`/`O011A0`/`O015A0`/`O016A0`/`O018A0` 搜索全局安装包、历史版本、缓存及任务文件；`O021A0`—`O024A0` 处理导入/PYTHONPATH 与旧字节码问题。最终补丁仅在 body 非 None 时设置长度，取消所有无体请求的默认 0。

**增减解释：**额外 17 次全部在第 5 次调用之后：后段从 8 次增至 25 次，主要是历史/安装环境探索及实现迭代。`O022A0` 的 find requests -name *.pyc -delete 是清理操作，也被原统计器归入文件定位，不能全部叫源码搜索。两种不同补丁都通过本任务判分项。

**Judge复核：**采纳其环境探索与两种补丁语义的对照；不采纳“plain未查已安装版本”，`P014A0`/`P015A0` 已证明查过；也不采纳“收到地图之前定位”的时间描述，once 地图在首次请求前已注入。复现先后不直接贡献搜索计数。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/psf__requests-1142.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/psf__requests-1142.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/psf__requests-1142/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/psf__requests-1142/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/psf__requests-1142.json)。

### 03. gin-gonic/gin（13,935行）

**任务要求：**StaticFS 遇到不存在文件产生 404 时，避免默认中间件链重复执行而记录两次日志。

**结果：**搜索 2→6（+4），调用 11→20；官方 通过→通过。前5次调用搜索 2→3。

**地图事实：**实际 1000 tokens，列出 12 个文件；与 once 最终提交文件的重叠：`routergroup.go`。

**Plain过程：**`P001A1` 查 StaticFS/文件服务，`P003A0` 查 allNoRoute/noRoute；最终只将 routergroup.go 中 allNoRoute 改为 noRoute。

**Once过程：**地图包含 routergroup.go。`O003A0`、`O004A0`、`O006A0` 继续追 Logger、Context.Next/Abort/reset 和请求处理链；`O009A0`/`O013A0` 再检查 handler 选择。最终提交与 plain 完全相同的一行修复。

**增减解释：**增加的 4 次源于对日志和中间件执行链的额外理解与复核。目标文件已被地图覆盖、补丁又相同，现有证据不支持“因为找不到文件而多搜”的解释。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/gin-gonic__gin-1805.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/gin-gonic__gin-1805.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/gin-gonic__gin-1805/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/gin-gonic__gin-1805/report.json)。

### 04. pallets/flask（17,478行）

**任务要求：**Blueprint 名称为空时抛出 ValueError，保持现有点号限制。

**结果：**搜索 5→3（-2），调用 9→9；官方 通过→通过。前5次调用搜索 5→2。

**地图事实：**实际 997 tokens，列出 16 个文件；与 once 最终提交文件的重叠：`src/flask/blueprints.py`。

**Plain过程：**`P001A0` 搜索 class Blueprint，`P002A1` 对照 ValueError 和空名约定，`P003A0` 查相关测试；5 次搜索都在前 5 次调用。

**Once过程：**地图包含 src/flask/blueprints.py；`O001A1` 检查空名测试，`O002A1` 查版本，`O008A0` 只是用 grep 核对 patch.txt 中 diff 文件列表。代码补丁与 plain 完全相同。

**增减解释：**少了初始类定位和异常约定查找，符合较直接读取目标文件的路径；但 once 的 3 次中还有补丁清单过滤，两组调用数同为 9。任务本身也直接给出 Blueprint 类名，不能将全部减少确定归因于地图。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/pallets__flask-5014.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/pallets__flask-5014.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/pallets__flask-5014/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/pallets__flask-5014/report.json)。

### 05. sharkdp/bat（21,496行）

**任务要求：**新增可重复配置的 --ignored-suffix，让配置文件中的自定义后缀进入语法识别路径。

**结果：**搜索 23→27（+4），调用 36→29；官方 通过→通过。前5次调用搜索 7→16。

**地图事实：**实际 812 tokens，列出 6 个文件；与 once 最终提交文件的重叠：`src/bin/bat/app.rs`。

**Plain过程：**逐步追 CLI、Config、assets 以及 PrettyPrinter 的参数传递；最后修改 8 个文件，含 src/pretty_printer.rs，使用 &[&str] 传递后缀。

**Once过程：**地图仅覆盖最终 7 个修改文件中的 src/bin/bat/app.rs，没有覆盖 src/assets.rs。`O002A0`—`O005A2` 密集扫描 CLI、Config、调用点和 cargo 缓存；最终使用 &[String] 传递后缀，修改 7 个文件。

**增减解释：**前 5 次调用搜索从 7 增至 16，增加 9；后段从 16 降至 11，减少 5，合计增加 4。多文件特性的参数传播仍需扫描调用点，1K 地图未提供完整修改面。调用数 36→29，但一次调用可包含多个 grep，因此搜索数与调用数方向不同。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/sharkdp__bat-1892.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/sharkdp__bat-1892.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/sharkdp__bat-1892/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/sharkdp__bat-1892/report.json)。

### 06. jekyll/jekyll（22,127行）

**任务要求：**where_exp 支持连续多个 and/or 比较，而不是只解析一次二元组合。

**结果：**搜索 7→7（+0），调用 17→18；官方 通过→通过。前5次调用搜索 6→3。

**地图事实：**实际 996 tokens，列出 16 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**`P001A0` 枚举文件，`P002A0`/`P004A0` 找 where_exp/parse_condition，`P005A2` 查 Liquid::Condition 的实现。

**Once过程：**地图未覆盖最终修改的 lib/jekyll/filters.rb。前期搜索 6→3，但 `O008A0` 查 Liquid 二元解析，`O009A0`/`O010A0` 对照 group_by_exp 和同类入口，补回后段查询。

**增减解释：**两组均将一次 and/or 组合改为循环串联并返回首 condition；总量相同，但阶段分布不同（前段 6/3，后段 1/4）。地图没有消除依赖库语义核对，不能从相同总数推断过程相同。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/jekyll__jekyll-8047.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/jekyll__jekyll-8047.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/jekyll__jekyll-8047/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/jekyll__jekyll-8047/report.json)。

### 07. google/gson（36,759行）

**任务要求：**JsonTreeReader.skipValue() 跳过根对象后 stackSize 为 0 时不再访问下标 -1，同时保持路径计数行为。

**结果：**搜索 5→3（-2），调用 14→10；官方 通过→通过。前5次调用搜索 3→3。

**地图事实：**实际 978 tokens，列出 12 个文件；与 once 最终提交文件的重叠：`gson/src/main/java/com/google/gson/internal/bind/JsonTreeReader.java`。

**Plain过程：**`P001A0` 用 find 定位 JsonTreeReader；`P002A1` 同时搜索 JsonReader 的错误目录与测试，再在 `P004A0` 追到 stream/JsonReader；`P007A0` 查 Maven 版本。

**Once过程：**地图覆盖 JsonTreeReader.java；`O001A2` 查测试，`O002A2` 再确认类位置，`O005A1` 查构建依赖。最终与 plain 相同，给 pathNames/pathIndices 写入增加 stackSize>0 守卫。

**增减解释：**初始文件定位和部分依赖源码查询减少，调用从 14 降至 10；两组补丁相同，属于较清晰的路径缩短案例。但任务已给出准确类名和异常行号，不能证明这些信息必须由地图提供。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/google__gson-1014.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/google__gson-1014.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/google__gson-1014/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/google__gson-1014/report.json)。

### 08. preactjs/preact（37,908行）

**任务要求：**progress 的 value=0 仍应写入 DOM value 属性，避免被视为不确定进度。

**结果：**搜索 13→8（-5），调用 26→15；官方 通过→通过。前5次调用搜索 6→4。

**地图事实：**实际 1018 tokens，列出 18 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**定位 diff/value 后，`P005A0`/`P006A0` 查询浏览器依赖及 Chrome 路径，`P007A0`/`P008A0`/`P010A0` 继续找测试、属性断言和 Karma 配置；最终用 PROGRESS 节点与属性是否缺失做特殊处理。

**Once过程：**实际初始地图未覆盖最终修改文件 src/diff/index.js。`O001A0`—`O003A0` 查 value/progress，`O007A0` 仍检查浏览器环境，但后续更快用复现和最小修复收敛；条件改为 progress 且 value 为 0 时强制更新。

**增减解释：**减少主要来自测试/浏览器环境和属性断言的额外探索较少，调用 26→15。地图不含最终目标文件，是“有减少，但不能直接当成地图定位命中”的案例；两种条件都通过官方判分。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/preactjs__preact-2757.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/preactjs__preact-2757.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/preactjs__preact-2757/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/preactjs__preact-2757/report.json)。

### 09. burntsushi/ripgrep（41,249行）

**任务要求：**多行捕获替换时，不把为匹配/前后查看而扩展的 subject 尾部误输出到替换结果。

**结果：**搜索 14→14（+0），调用 32→34；官方 通过→通过。前5次调用搜索 1→3。

**地图事实：**实际 968 tokens，列出 13 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**`P010A0`/`P011A0`/`P016A0` 追 replace_with_captures_at 和 captures_iter_at，`P018A0` 查 SinkMatch 的范围，最后用手动 capture 迭代限制复制区间。

**Once过程：**地图未覆盖最终修改的 crates/printer/src/util.rs。`O005A0`/`O006A0`/`O012A0`/`O014A0` 额外追 Sunk 和多行搜索器，再回到 captures_iter_at。最终修复路线与 plain 类似。

**增减解释：**搜索子命令同为 14，但搜索请求 8→11、调用 32→34。两组都需要理解“完整subject”和“实际匹配range”的区别，只是查询被拆成了不同动作；不能把子命令相同解释为节省成本。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/burntsushi__ripgrep-2209.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/burntsushi__ripgrep-2209.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/burntsushi__ripgrep-2209/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/burntsushi__ripgrep-2209/report.json)。

### 10. jqlang/jq（45,247行）

**任务要求：**支持 -0 选项以用 NUL 分隔输出；当前基线已经有 RAW_NUL/nul-output 分支，需要识别数字短选项并保持参数解析正确。

**结果：**搜索 33→14（-19），调用 48→19；官方 通过→通过。前5次调用搜索 6→4。

**地图事实：**实际 1013 tokens，列出 8 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**`P011A0`—`P026A0` 反复查询选项、文档、NUL 字符串和上游版本；在 src/main.c 中除识别 -0 外，还增加 raw-output0 别名及嵌入 NUL 字符串检查。`P036A0`/`P039A0`/`P043A0`/`P046A0` 反复 grep 错误文本验证。

**Once过程：**`O001A1`—`O003A0` 确认已有 RAW_NUL 代码，主要修改 isoptish 对 -0/堆叠选项的识别并补 help；`O008A2` 查解析入口，`O011A0` 对照数字选项测试。没有追加 plain 中的别名和嵌入 NUL 检查。

**增减解释：**减少 19 次，伴随修改范围收敛和多轮错误文本过滤减少；调用 48→19。地图没有覆盖 src/main.c，因此不能把这 19 次直接解释为地图命中目标。两种修改都满足本轮官方判分项，额外功能查询和日志过滤构成相当一部分差异。

**Judge复核：**采纳修复范围及重复验证差异；judge 把 api_calls/search_actions 写成 P_OFFICIAL 的子字段，实际来源是轨迹 metrics，本报告已按原统计字段引用。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/jqlang__jq-2235.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/jqlang__jq-2235.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/jqlang__jq-2235/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/jqlang__jq-2235/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/jqlang__jq-2235.json)。

### 11. caddyserver/caddy（54,265行）

**任务要求：**default_bind 单个选项接受多个地址，使未单独 bind 的站点绑定全部默认地址；同时应保持适配输出可稳定比较。

**结果：**搜索 30→25（-5），调用 34→22；官方 通过→未通过。前5次调用搜索 6→12。

**地图事实：**实际 968 tokens，列出 15 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**实现多地址解析及 []string 传递后，`P017A0`—`P020A0`/`P025A1` 继续追地址归并、pairing 和排序；最终在 consolidateAddrMappings 的每个 association 上 sort.Strings(a.addresses)。

**Once过程：**同样完成多地址解析，`O006A2`/`O007A2` 查 listener 与地址逻辑，但最终只在 listenerAddrsForServerBlockKey 返回前排序 listenersList。地图不含 options.go/addresses.go。

**增减解释：**少 5 次搜索，却遗漏归并后顺序的稳定化。官方 TestCaddyfileAdaptToJSON 展示 srv1 的 IPv4/IPv6 listen 顺序与期望相反；plain 的归并层排序通过，once 失败。可见少搜索与验证覆盖不足可以同时出现，不能将其记为无条件的效率收益。

**Judge复核：**采纳排序位置及官方顺序 diff 的解释；不采纳“字典序排序直接让 tcp6 排到 tcp4 前面”的表述，证据支持排序发生在不同阶段、后续归并仍改变顺序。不能把 parser 中 d.Next 的差异当成此次失败原因。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/caddyserver__caddy-4774.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/caddyserver__caddy-4774.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/caddyserver__caddy-4774/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/caddyserver__caddy-4774/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/caddyserver__caddy-4774.json)。

### 12. fmtlib/fmt（67,663行）

**任务要求：**字符格式化时 %-5c 应保留左对齐，同时保留现有零填充等回归行为。

**结果：**搜索 22→15（-7），调用 34→19；官方 未通过→通过。前5次调用搜索 5→5。

**地图事实：**实际 1023 tokens，列出 13 个文件；与 once 最终提交文件的重叠：`include/fmt/printf.h`。

**Plain过程：**删除字符分支的强制右对齐，并额外修改 zero-fill 的 arithmetic 条件，把 char 排除。`P017A0` 之后继续追 ZeroFlag、字符类型谓词和上游版本。

**Once过程：**地图包含 include/fmt/printf.h；保留最小变更，仅在 align!=left 时强制 right。`O006A1`/`O007A1` 核对基类和字符测试，未改变零填充条件。

**增减解释：**少 7 次主要来自补丁保持最小范围，后段搜索 17→10，而前 5 次均为 5。plain 破坏了 PrintfTest.ZeroFlag，官方 %05c 的期望为 0000x、实际为四个空格加 x；once 保留行为并通过。不能把主要差异归为首次定位，前段计数并没有下降。

**Judge复核：**采纳 ZeroFlag 回归及最小补丁解释；“地图减少初始搜索”仅是假设，实际两组前 5 次搜索相同。测试按任务要求不可修改，不能把 plain 没修改断言说成遗漏。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/fmtlib__fmt-1683.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/fmtlib__fmt-1683.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/fmtlib__fmt-1683/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/fmtlib__fmt-1683/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/fmtlib__fmt-1683.json)。

### 13. tokio-rs/tokio（108,282行）

**任务要求：**网络类型可用于 catch_unwind，满足 UnwindSafe/RefUnwindSafe；实现需覆盖实际公共网络类型所依赖的完整内部类型链。

**结果：**搜索 14→26（+12），调用 15→24；官方 未通过→通过。前5次调用搜索 8→10。

**地图事实：**实际 1024 tokens，列出 31 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**围绕 UnsafeCell<Atomic*> 修复 4 个 loom 原子包装器。`P003A3`/`P004A2`/`P008A0` 查询原子使用点；该修复仍未覆盖 parking_lot::Mutex 中 Waiters、Slab 等阻碍。

**Once过程：**`O002A1` 扩大到公开网络类型，`O012A0`/`O013A0` 用编译期断言暴露更多 UnwindSafe 错误；`O017A1`/`O020A0` 检查 Windows named pipe 和其余 net 类型。最终在 7 个源码文件的公开类型上实现 marker traits。地图未覆盖这些最终修改文件。

**增减解释：**增加 12 次，伴随覆盖范围扩大和编译诊断过滤，而不是单纯定位效率下降。官方 plain 在 net_types_unwind.rs 编译阶段报 UnsafeCell<Waiters/Slab/Slots> 相关 E0277；once 通过 net_types_are_unwind_safe 和 unix_net_types_are_unwind_safe。

**Judge复核：**纠正 judge 的“plain补丁未能应用”：run_instance.log 明确记录 4 个原子文件 cleanly applied。harness 在没有可解析测试结果时会保留 patch_successfully_applied=false；此处直接原因是补丁尚未消除所有 trait 编译错误。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/tokio-rs__tokio-4384.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/tokio-rs__tokio-4384.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/tokio-rs__tokio-4384/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/tokio-rs__tokio-4384/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/tokio-rs__tokio-4384.json)。

### 14. redis/redis（245,197行）

**任务要求：**XTRIM MINID 比较完整的 64 位消息 ID，不应因毫秒或序列号差值溢出而删除阈值以上消息。

**结果：**搜索 11→7（-4），调用 22→12；官方 通过→通过。前5次调用搜索 1→6。

**地图事实：**实际 1016 tokens，列出 12 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**定位 streamTrim 后，`P006A0`—`P009A0` 多次核对 lpGetInteger、master_id 和 delta 类型；`P011A0`/`P014A0` 再查询 MINID 测试。

**Once过程：**地图未覆盖 src/t_stream.c，但首步即直接查询该文件。`O002A1`—`O004A1` 集中追 ID 解码和节点配置，`O006A0` 核对 delta；最终与 plain 完全相同，将 ms_delta、seq_delta 改为 int64_t。

**增减解释：**前段搜索 1→6，后段 10→1，合计减少 4。主要是把类型与 ID 逻辑分析集中完成，减少后续重复核对和测试查找；不属于“地图直接给出目标文件”的证据。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/redis__redis-10068.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/redis__redis-10068.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/redis__redis-10068/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/redis__redis-10068/report.json)。

### 15. rubocop/rubocop（283,279行）

**任务要求：**把 #@ivar/#$gvar/#@@cvar 等变量插值当作动态字符串，避免把必要的 freeze 误判为冗余。

**结果：**搜索 17→7（-10），调用 20→19；官方 通过→通过。前5次调用搜索 7→5。

**地图事实：**实际 974 tokens，列出 10 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**`P003A0` 找 FrozenStringLiteral mixin，`P005A0`—`P013A1` 多次搜索 dstr、immutable_literal? 和边界测试；后续又确认默认 Ruby 分析版本及配置，复现脚本还遇到对 String 调 type 的错误。

**Once过程：**`O001A0`—`O003A0` 集中查 interpolation/dstr 和 mixin，`O007A0` 复核所有相关判断调用；虽然 `O010A0`/`O011A0` 的 AST 脚本也修正过 parser 引擎参数，`O015A0`/`O016A0` 还运行 prism 验证，但这些不都产生搜索命令。

**增减解释：**搜索 17→7，但调用仅 20→19。plain 对谓词、用例及 AST API 的多次检索被 once 的直接源码阅读/脚本检查替代，两组最终同为 children.all?(&:str_type?) 的单行补丁。地图没有覆盖该 mixin；这是搜索与其他操作重新分配的案例。

**Judge复核：**judge 将“总搜索量”方向写成 increase，与原始 17→7 相反，已纠正；其插值节点语义与相同补丁的判断可由 diff 和测试复核。不得将无搜索的 AST 脚本动作当成搜索增加。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/rubocop__rubocop-13362.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/rubocop__rubocop-13362.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/rubocop__rubocop-13362/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/rubocop__rubocop-13362/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/rubocop__rubocop-13362.json)。

### 16. briannesbitt/carbon（313,470行）

**任务要求：**年末微秒边界上的 floorYear() 不应因浮点累计误差向前跳到下一年。

**结果：**搜索 11→10（-1），调用 39→28；官方 通过→通过。前5次调用搜索 4→4。

**地图事实：**实际 1024 tokens，列出 36 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**提出更广的 floor/ceil 修正：保存原值，在最终结果越界时循环重建边界；`P018A0` 额外查各精度测试，`P032A0`/`P033A0` 再查 avoidMutation、trait 和执行配置。

**Once过程：**地图未覆盖 src/Carbon/Traits/Rounding.php。采用较窄的 floor 守卫，在归一化单位超过原单位时重新计算；`O014A0`/`O015A0` 查单位 setter，`O021A0` 查边界测试。

**增减解释：**两组前段均为 4 次，后段 7→6，仅少 1 次。更明显的是调用 39→28，伴随补丁范围更窄和验证路线不同；这个幅度不足以单独归因地图检索能力。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/briannesbitt__carbon-2665.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/briannesbitt__carbon-2665.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/briannesbitt__carbon-2665/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/briannesbitt__carbon-2665/report.json)。

### 17. django/django（352,429行）

**任务要求：**URLValidator 拒绝用户信息段中的未编码 :、@、/，并防止查询串中的 @ 让无效 URL 被错误接受。

**结果：**搜索 3→27（+24），调用 10→33；官方 通过→通过。前5次调用搜索 3→6。

**地图事实：**实际 985 tokens，列出 19 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**`P001A1` 定位 URLValidator，`P002A1` 查现有验证测试，`P004A1` 复核同类正则，最终只改一行字符类，排除空白、:、@、/。

**Once过程：**地图未覆盖 validators.py，但 `O001A0` 已直接读取目标类。`O006A1` 检查边界样例，`O007A0`—`O011A1` 以及 `O023A0`—`O030A0` 大量查已安装 Django、任务文字、缓存、补丁文件及上游正则；还比较候选正则对查询串绕过的表现。最终字符类额外排除 ? 和 #。

**增减解释：**多出的 24 次中，前段增加 3（3→6），后段增加 21（0→21）。主要是参考实现/环境检索和边界语义反复确认，而不是目标文件未找到。两组最终都通过官方判分；不能从这轮证明额外严格字符类带来额外官方解决收益。

**Judge复核：**judge 输入生成时 once 尚待判；本报告以最终官方两组均通过更新。地图不含目标文件只是事实，不能直接作为多搜索的因果解释；`O001A0` 已定位目标。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/django__django-10097.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/django__django-10097.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/django__django-10097/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/django__django-10097/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/django__django-10097.json)。

### 18. laravel/framework（354,539行）

**任务要求：**previous() 的 referrer 与当前 URL 相同时使用 fallback，并正确处理等价 URL 的表示差异。

**结果：**搜索 9→13（+4），调用 17→24；官方 未通过→通过。前5次调用搜索 5→5。

**地图事实：**实际 1022 tokens，列出 20 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**快速在 UrlGenerator.php 增加 $url !== $this->current() 严格字符串比较；`P006A1` 对照 current()/previousPath，后续边界探索较少。

**Once过程：**`O003A1`/`O005A1` 对照 current/full，`O014A0`/`O014A1` 查其他框架副本，`O015A0`/`O015A1` 追 session/referrer，`O018A0`/`O022A0` 查格式化和 mock。最终用 rtrim 去尾斜线并同时比较 current()/full()。

**增减解释：**增加 4 次集中在 URL 归一化、完整 URL 及验证方式。官方 plain 的失败明确是期望 http://www.foo.com、实际 http://www.foo.com/；once 处理尾斜线等价后通过。这是更完整语义带来额外查询、同时改善结果的案例，地图未覆盖最终修改文件。

**Judge复核：**judge 当时没有 plain 的最终判题；已补充最终尾斜线断言失败，不能继续写“plain是否通过未知”。指标伪引用 TASK:search_actions 已改为轨迹 metrics。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/laravel__framework-46234.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/laravel__framework-46234.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/laravel__framework-46234/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/laravel__framework-46234/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/laravel__framework-46234.json)。

### 19. sympy/sympy（446,166行）

**任务要求：**异维点计算欧氏距离时保留额外维度，题给示例应得到 sqrt(5)，而不是忽略第三维或抛错。

**结果：**搜索 11→19（+8），调用 17→29；官方 未通过→未通过。前5次调用搜索 5→3。

**地图事实：**实际 1004 tokens，列出 23 个文件；与 once 最终提交文件的重叠：`sympy/geometry/point.py`。

**Plain过程：**`P001A1`/`P001A2`/`P003A0`/`P004A2` 读取 Point 和维度约定，后续查安装版本并跑几何测试。最终错误地把不同维度改为 ValueError。

**Once过程：**地图包含 point.py，前 5 次搜索更少；但 `O008A0`、`O015A0`—`O019A0` 又查轮子包、pip 缓存和任务文件，`O009A0` 查 zip_longest 后仍选择异维抛错。最终与 plain 只在异常文字上有差别。

**增减解释：**前段少 2 次、后段多 10 次，合计增加 8；更多参考查找没有纠正任务语义误读。最终官方两组 test_issue_11617 均失败。找到正确文件、甚至查过 zip_longest，并不代表选择了任务要求的零补齐方案。

**Judge复核：**judge 输入时两组尚待判，最终已确认为都失败。任务明确写 sqrt(5)，应优先采用该要求，不把“上游可能选择抛错”作为同等合理的目标；无需查询上游才能判断此处与需求冲突。含解释文字的伪引用已人工对应实际动作。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/sympy__sympy-11618.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/sympy__sympy-11618.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/sympy__sympy-11618/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/sympy__sympy-11618/report.json) · [judge原始审阅](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/judge/sympy__sympy-11618.json)。

### 20. apache/lucene（1,188,071行）

**任务要求：**NO_INTERVALS 的迭代器在尚未定位时 docID() 为 -1，nextDoc()/advance() 后进入 NO_MORE_DOCS 状态，避免空查询与 conjunction 组合异常。

**结果：**搜索 12→17（+5），调用 19→18；官方 未通过→未通过。前5次调用搜索 5→6。

**地图事实：**实际 1018 tokens，列出 16 个文件；与 once 最终提交文件的重叠：无。

**Plain过程：**追 IntervalBuilder、IntervalIterator 和 ConjunctionUtils，最终增加 exhausted 布尔状态，未定位返回 -1，推进后返回 NO_MORE_DOCS；`P012A0`—`P014A0` 查 Gradle wrapper、构建产物及 jar。

**Once过程：**地图未覆盖 IntervalBuilder.java。`O003A0`—`O008A2` 更广地追迭代器/查询组合，`O011A0`/`O015A0` 同样查 Gradle 和 Lucene jar；最终使用 doc=-1 的整型状态并在推进时赋 NO_MORE_DOCS。

**增减解释：**多 5 次来自更广的调用/迭代器验证和构建依赖查找，调用却 19→18。两份补丁从代码上都表达所需状态转换，但官方目标测试均未执行：Gradle 尝试下载缺失的 gradle-wrapper.jar，断网下失败并退出 3。这里不能将两组 resolved=false 解释为补丁语义错误，也不能比较哪个补丁更正确。

**Judge复核：**本例直接依据官方 test_output 的缺失 wrapper 与退出码分析，未追加 judge。官方 infra_failure=false 未识别这一环境阻塞；原判分保留，同时单列“目标测试未执行”。

证据：[任务与逐步证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/evidence/apache__lucene-11760.md) · [结构化证据](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/cases/apache__lucene-11760.json) · [plain官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b0/evaluation/plain/logs/run_evaluation/static1024-once-20261105T145121Z-b0-plain/plain-offline/apache__lucene-11760/report.json) · [once官方报告](../benchmarks/results/static1024-once-20261105T145121Z/conditions/static1024-once-20261105T145121Z-b1024/evaluation/repomap/logs/run_evaluation/static1024-once-20261105T145121Z-b1024-repomap/repomap-offline/apache__lucene-11760/report.json)。

## 5. 从逐条证据可以归纳的机制

### 5.1 增加并不都来自定位失败

Requests 的前5次搜索相同，增加17次全部在后段；Django 首步已经直接读取目标文件，后段仍额外增加21次。这两例主要增加在历史版本、安装副本、缓存与任务文字查找，以及方案反复验证。不能直接把这些命令归为地图未找到目标文件。

Tokio +12、Laravel +4 同时带来更完整的类型/URL边界处理，并改变官方结果。这部分搜索与修复覆盖面扩大相伴，和纯粹无效探索应分开。SymPy +8 则没有纠正核心需求误读，说明更多探索本身也不保证正确。

### 5.2 减少也不都代表收益

Flask、Gson 的相同补丁和较短定位路径，与减少查询相符；jq、RuboCop 则主要体现修改范围、脚本检查和重复验证方式不同。Caddy少搜5次但失败，明确说明少搜索可以伴随遗漏验证。Carbon仅少1次，远小于其调用数量变化，不能夸大为检索能力提升。

### 5.3 地图覆盖与因果归因

本节的重叠以 Agent 自己的提交为参照，属于描述性指标；新增的 [实验 A](map-quality-evaluation.md) 改用官方补丁作为文件召回参考，并另评符号、依赖、精度与实际利用证据，两者不能混用。

20份once地图中，8份与本次最终修改文件至少有一处重叠；其余12份没有重叠。SymPy覆盖目标却修错，jq/RuboCop不覆盖目标仍少搜并通过。Bat地图仍列出压缩jQuery benchmark样本，未包含核心assets.rs。文件重叠不足以解释结果，需要结合任务语义、实际阅读和验证路径。

### 5.4 搜索、调用和费用是不同维度

本轮搜索增加10.0%，模型调用却减少5.3%，因为一次调用可以包含多个搜索。两组缓存输入占比均约87%，但once总费用仍高18.1%；固定前缀没有保证更少的总输入、输出或更短修复路径。这里报告服务端计量，不用本地单价推算。

## 6. .env-judge 的使用与人工复核

使用 `.env-judge` 配置的 `xopglm52` 完成9次事后审阅：Requests、jq、Caddy、fmt、Tokio、RuboCop、Django、Laravel、SymPy。输入包含公开任务、两组实际地图/补丁、逐步动作与输出摘录、当时已生成的官方报告；judge没有执行工具，也没有修改推理或官方判分。未展示密钥或服务地址。

服务地址配置为完整聊天请求地址，最初因SDK重复拼接路径返回404；仅在调用侧去掉重复路径，未改 `.env-judge`，也未更换模型。9次成功审阅均未返回 `usage.cost`，因此judge费用单列为未知，不混入DeepSeek实验费用，也不按0估算。

对judge输出进行了以下复核：

- 动作ID存在性与原始搜索数自动核对；指标伪引用被重新对应到原始 metrics。
- 纠正RuboCop把17→7写成“增加”的方向错误；无搜索的AST脚本不算搜索。
- 纠正Requests“plain未查安装版本”和“once在收到地图前定位”的说法，原轨迹及注入时序与之矛盾。
- 纠正Tokio把报告的 patch_successfully_applied=false 直接解释为补丁无法应用：运行日志记录候选4文件均cleanly applied，实际阻碍是剩余trait编译错误。
- 对Django、Laravel、SymPy，将judge输入时尚待判的状态更新为最终官方状态；不额外付费重判来改写已知事实。
- 排序阶段、因果效果、需求之外的兼容性等推断均保留边界；逐条审阅修正记录保存在manual-analysis.json。

## 7. 判题和环境例外

**Lucene：目标测试未执行。** 两组test_output均显示Gradle尝试下载缺失的gradle-wrapper.jar，因容器断网失败并退出3；没有产生TestIntervalBuilder执行结果。原官方resolved=false、infra_failure=false均保留，但人工复核明确将这两次单列为相同依赖阻塞，不能据此断言补丁语义错误。

**Tokio：源码补丁应用成功，但测试编译失败。** plain运行日志记录4个原子文件应用成功；官方新增测试在Waiters/Slab/Slots的内部可变性上报E0277。该harness的grading.py会在找不到可解析测试结果时提前返回默认字段，所以patch_successfully_applied=false不单独证明git apply失败。

这两种情况都可能没有tests_status，但原因不同：Lucene是构建依赖缺失，Tokio是提交未覆盖完整编译约束。SymPy则有明确任务测试失败；不能把三者统一称为断网问题。

## 8. 实验结论与下一步验证

本轮支持的结论是：单次1024地图相较plain净多解决2条，同时搜索和费用总量更高。各任务增减由定位、代码理解、修改范围、历史参考、测试环境以及修复语义共同决定。一次运行不足以证明地图造成某条路径，或证明once普遍优于每轮刷新。

如继续验证，应先补齐Lucene离线wrapper等测试依赖，并保持两组镜像一致；将源码定位、环境探索和日志过滤分别标注；在相同任务上重复对照。进一步比较地图生成算法时，保持mini框架一致，单独替换生成器，避免把Agent历史管理与地图效果混在一起。这里没有新开修复推理、重新判题或更改现有结果。

## 9. 可复核材料

- [本轮完整汇总](../benchmarks/results/static1024-once-20261105T145121Z/budget-report.md)
- [本轮40条CSV](../benchmarks/results/static1024-once-20261105T145121Z/budget-trials.csv)
- [最终审计](../benchmarks/results/static1024-once-20261105T145121Z/final-audit.json)
- [20条人工分析记录](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/manual-analysis.json)
- [分析快照与汇总](../benchmarks/results/static1024-once-20261105T145121Z/case-analysis/analysis-summary.json)
- [此前各轮实验整理](experiment-results.md)

本轮888条收费响应均核对extra.cost与usage.cost一致，40次评测容器创建均核对为network=none。DeepSeek本轮已返回费用合计 **$0.7498326673450**；judge费用另列未知。
