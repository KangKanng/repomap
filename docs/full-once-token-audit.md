# Full-once RepoMap token 占用审计

已逐一重新编码 20 张实际注入的地图，复核轨迹及地图 SHA-256，并检查已返回请求中的地图内容；140 项一致性检查全部通过。整个审计只读取既有结果，模型调用为 0。

**Lucene 在首次模型请求时就超过窗口。** 不是后续历史积累，也不是多张地图重复叠加。完整模式没有 token 选择或自动回退，配置中的正数 `map_tokens: 1024` 不构成上限。

## 计数口径

- 地图 token 数：实际 `<repository_map>` 消息完整文本，使用运行配置的 `cl100k_base` 重新编码，包括说明、标签、路径、签名片段、行号和省略标记。
- 服务端输入：从已返回响应的 `usage.prompt_tokens` 读取；表中最大值是已返回请求的最大值。jq 超时后的未返回请求不在该最大值内。
- Lucene 没有返回正常模型响应，输入数直接来自服务端拒绝请求时的报错：文本输入与工具定义分别给出。
- 本地分词编码与服务端计数口径不同，不能将两者相减来精确估算其他消息或认定分词异常。窗口超限的结论以服务端报错为依据。

## 20 条地图及单次输入

| 仓库 | 地图 tokens（本地） | 文件路径数 | 首次输入 tokens（服务端） | 已返回请求最大输入（服务端） |
| --- | ---: | ---: | ---: | ---: |
| apache/lucene | 1,537,102 | 6,285 | 1,469,348（拒绝请求） | — |
| django/django | 676,573 | 5,809 | 707,995 | 728,087 |
| laravel/framework | 454,903 | 2,247 | 446,017 | 464,500 |
| sympy/sympy | 440,019 | 1,410 | 434,981 | 466,715 |
| redis/redis | 260,822 | 1,354 | 268,994 | 303,447 |
| rubocop/rubocop | 247,176 | 1,884 | 253,446 | 270,358 |
| tokio-rs/tokio | 149,118 | 606 | 154,521 | 200,916 |
| briannesbitt/carbon | 116,198 | 1,978 | 115,930 | 154,041 |
| fmtlib/fmt | 79,878 | 192 | 82,296 | 101,016 |
| caddyserver/caddy | 64,206 | 345 | 67,413 | 86,014 |
| google/gson | 64,187 | 321 | 66,123 | 85,260 |
| burntsushi/ripgrep | 59,472 | 194 | 62,977 | 105,641 |
| pallets/flask | 38,836 | 242 | 41,278 | 45,088 |
| preactjs/preact | 36,592 | 264 | 37,539 | 60,100 |
| jqlang/jq | 31,745 | 213 | 33,841 | 51,067 |
| sharkdp/bat | 28,942 | 578 | 31,887 | 82,363 |
| jekyll/jekyll | 27,239 | 679 | 29,687 | 43,047 |
| gin-gonic/gin | 22,799 | 98 | 24,121 | 43,040 |
| psf/requests | 11,122 | 112 | 12,531 | 38,638 |
| axios/axios | 4,476 | 157 | 6,451 | 31,855 |

20 张地图分别属于 20 个任务，不在同一上下文中。将不同任务或不同请求的输入 token 相加，得到的是累计输入量，不是单次上下文长度。

## Lucene 首次请求拆分

首次发给模型的历史只有 `system → task → repository_map`，尚未发生 Agent 工具调用。退出错误记录在失败之后追加，没有进入首次请求。

| 消息正文 | 本地 cl100k_base tokens |
| --- | ---: |
| System | 17 |
| 原始任务与任务模板 | 1,063 |
| RepoMap | 1,537,102 |
| 正文合计（不含消息格式及工具 schema） | 1,538,182 |

地图占本地三条消息正文的 **99.93%**。地图含 **6,285** 个路径、**94,251** 行渲染的签名/声明片段，UTF-8 文本 **5,540,347** 字节；不是将源码正文全文直接发送。

服务端报错中的计数：

| 项目 | 服务端 tokens |
| --- | ---: |
| 文本输入 | 1,469,281 |
| 工具定义 | 67 |
| 请求输入合计 | 1,469,348 |
| 此端点上下文上限 | 1,048,576 |
| 输入已超出 | 420,772 |

服务端输入已经超限，尚未涉及后续对话增长。两套分词计数虽然不同，但都表明该地图不能装入此次端点窗口。

## 哪些内容占用较大

按路径组件 `test/tests/testing/spec/specs/test-framework/test_framework/__tests__` 与常见测试文件命名作启发式分组。这是体积统计，不是“与任务无关”的语义标签；部分测试或测试框架仍可能与任务直接相关。

| 仓库 | 测试路径文件块 tokens | 占文件块 token 和 | 仅保留其余路径的地图估算 tokens |
| --- | ---: | ---: | ---: |
| apache/lucene | 568,208 | 36.97% | 968,894 |
| django/django | 455,463 | 67.32% | 221,110 |
| laravel/framework | 214,891 | 47.24% | 240,012 |
| sympy/sympy | 145,451 | 33.06% | 294,568 |
| redis/redis | 30,152 | 11.56% | 230,670 |
| rubocop/rubocop | 12,405 | 5.02% | 234,771 |
| tokio-rs/tokio | 27,897 | 18.71% | 121,221 |
| briannesbitt/carbon | 81,430 | 70.11% | 34,768 |
| fmtlib/fmt | 62,133 | 77.83% | 17,745 |
| caddyserver/caddy | 4,427 | 6.90% | 59,779 |

逐文件块独立编码后的 token 和与完整地图的计数可能有少量 BPE 边界差异；占比使用文件块的计数和作分母。最后一列对实际保留下来的地图文本重新整体编码，仍是本地估算，未调用模型，也没有改变本次输入。

Lucene 测试相关路径约占 **36.97%**；按上述规则去除后仍有 **968,894** 本地 tokens，接近此次服务端上限，还需考虑分词差异、其他输入、后续历史与生成空间。不能仅凭此估算保证之后不超限。

Django 的地图为 **676,573** 本地 tokens；首请求服务端计数 **707,995**，本次已返回请求的最大输入 **728,087**，未达到此次窗口上限。

Lucene 占用最高的文件块：

| 路径 | 本地 tokens |
| --- | ---: |
| `lucene/core/src/java/org/apache/lucene/index/IndexWriter.java` | 5,950 |
| `lucene/test-framework/src/java/org/apache/lucene/tests/util/LuceneTestCase.java` | 4,516 |
| `lucene/memory/src/java/org/apache/lucene/index/memory/MemoryIndex.java` | 4,243 |
| `lucene/core/src/test/org/apache/lucene/index/TestIndexWriter.java` | 4,081 |
| `lucene/core/src/java/org/apache/lucene/codecs/lucene90/Lucene90DocValuesProducer.java` | 3,956 |

其最大单个文件块只有 5,950 tokens，说明此次 Lucene 的超限来自大量文件与声明的总规模，未发现由一个异常巨大的文件块主导的情况。

## 另一个渲染观察

Bat 的地图为 901,934 字节，但只有 28,942 个本地 tokens。其测试夹具 `tests/benchmarks/test-src/jquery-3.3.1.min.js` 产生了大量空格；最长渲染行有 71,294 个字符，其中代码部分开头为 71,286 个空格。现有 JavaScript 箭头函数分支按原列位置补缩进，会在压缩为单行的代码上产生这类膨胀。这解释了该例字节数与 token 数差距很大，属于值得单独修正的渲染问题，不能据此将 Lucene 超限归因于同样原因。本次审计没有修改提取器或历史地图。

## 结论

完整地图并不保证小于模型窗口；单次生成和注入也不减少它在每个请求中占据的上下文。此次超限已由服务端计数确认，主要来自 Lucene 全量索引的体积。若改变地图范围、预算或窗口适配，应登记为新的实验条件，保留本次失败记录，不能将裁剪后的结果归入本次完整地图组。

完整数据：[审计 JSON](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/audit.json)、[20条 CSV](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/map-tokens.csv)、[逐文件体积 CSV](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/file-block-tokens.csv)、[复算程序](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/implementation/audit_tokens.py)。

## Lucene 仓库全文 token 数

从本组实际使用的固定镜像，在只读、断网容器中流式读取 Git tracked 工作区文本，在宿主机用 `cl100k_base` 逐文件编码并求和。未添加路径或消息包装，没有调用模型。

| 范围 | 文件数 | tokens |
| --- | ---: | ---: |
| 与 RepoMap 扫描范围一致的支持语言源码，包含测试源码 | 5,288 | **12,712,769** |
| 仓库全部可读 UTF-8 tracked 文本，包含源码、测试、文档及配置 | 6,241 | **17,630,615** |
| 实际完整 RepoMap，包含路径、标签、说明与签名片段 | 6,285 条索引路径 | **1,537,102** |

因此完整地图 token 数相当于源码全文的 **12.09%**，相当于全部可读文本的 **8.72%**。

排除符号链接、含空字节或无法按 UTF-8 解码的内容；源码一栏另遵守地图扫描范围及支持语言后缀。所有 5,288 个源码文件均与既有源码快照逐一核对内容一致，固定镜像 HEAD 和数据集基线的 Git tree 相同。计数是本地编码结果，不等于任意模型的实际服务端计数。

[计数与来源 JSON](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/lucene-source/counts.json) · [逐文件 token CSV](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/lucene-source/file-tokens.csv) · [复算程序](../benchmarks/results/full-once-20-20261107T093757Z/token-audit/implementation/count_lucene_tokens.py)。
