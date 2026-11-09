# 实验运行指南

脚本统一位于 [scripts/](../scripts/README.md)，使用现有的 20 个 SWE-bench 源码镜像。
默认性能实验只生成地图；模型推理对照使用独立入口，从 `--model` 或项目 `.env` 读取模型设置。
拉取、性能与推理入口支持 `--env-file`；字段及优先级见 [.env 配置](environment.md)。

评测优先顺序为：先做 [实验 A：地图质量验证](map-quality-evaluation.md)，再解释搜索量、解决率、费用和性能。
实验 A 使用冻结地图、官方补丁和经复核的任务参考集事后评分，不向 Agent 提供官方答案；离线入口无需模型凭据。
已完成的旧版、预算与once地图对照见 [历史地图质量回溯](historical-map-quality-results.md)，区分实际初始、重建初始与最终保留地图。

## 1. 准备环境与拉取镜像

```bash
cd /root/repomap
source .venv/bin/activate

# 预览镜像列表
python scripts/pull_docker.py --dry-run

# 拉取并核对 20 个镜像
python scripts/pull_docker.py --workers 3

# 镜像已在本机时，只核对版本与源码规模
python scripts/pull_docker.py --inspect-only --workers 3
```

镜像选择保存在 [selection.json](../benchmarks/docker_repositories/selection.json)。
拉取脚本更新 [inventory.json](../benchmarks/docker_repositories/inventory.json) 和
[规模排序表](../benchmarks/docker_repositories/README.md)，并保存各镜像的日志。

核对镜像 ID、digest、源码文件数与行数，以及镜像 HEAD 和数据集 base commit 的源码树 SHA。
只有源码树匹配且存在可识别源码时，镜像才标为 `ready`。
目录定位基于脚本自身路径，可从其他工作目录通过脚本的绝对路径运行。
旧的 `benchmarks/docker_repositories/pull.py` 保留为兼容入口。

## 2. 性能实验

```bash
# 预览要运行的项目
python scripts/run_experiments.py --dry-run

# 小规模验证
python scripts/run_experiments.py --limit 2 --budgets 1024 --repeats 1

# 指定仓库、任务和预算，0 表示空地图对照
python scripts/run_experiments.py \
  --repos gin-gonic/gin pallets/flask fmtlib/fmt \
  --budgets 0 128 1024 --repeats 1 \
  --task 'Find public APIs and request handling entry points'

# 全部 20 个仓库
python scripts/run_experiments.py --budgets 1024 2048 4096 --repeats 3
```

不指定 `--repos` 或 `--limit` 时，运行 inventory 中的全部项目，顺序与规模排序清单一致。
优先使用 inventory 记录的 digest 读取本地镜像，不自动拉取缺失镜像。

### 流程与测量范围

1. 在只读、断网、用完即删除的容器中导出 Git tracked 工作区的文件流。
2. 宿主机按支持语言、目录过滤、文件大小、文件数和源码总字节数限制构建快照。
3. 每个预算创建新的 `RepoMap`，测量空符号缓存的一次 `build()`。
4. 在同一对象上，对相同源码与任务再次执行 `build()`，重复 `--repeats` 次。
5. 校验地图 tokens 未超过预算，且热缓存结果与首次结果一致。
6. 保存地图文本、逐预算指标和汇总报告。

容器内无需 Python。解析、图排序、token 计数在宿主机执行，源码流不解包到宿主机。
这里测量受限 tracked 快照上的 Repo Map 算法，不包含模型调用，
也不测量 `EnvironmentSource` 在目标环境中执行 Python 收集器的端到端耗时。
普通 Agent 收集器还支持未被忽略的 untracked 文件；本实验只使用 tracked 文件。

每个仓库在独立 Python 子进程中运行，默认串行。`--timeout` 限制每个仓库的总时间；
失败写入报告后继续其他仓库。临时容器使用运行时生成的唯一名称，并在结束时清理。
任何仓库失败时，整个脚本最终返回非零退出码。

### 参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--manifest` | 项目中的 `inventory.json` | 镜像与仓库信息 |
| `--repos` | 全部 | 一个或多个 `owner/repository` 名称 |
| `--limit` | 不限制 | 运行筛选后的前 N 个仓库 |
| `--budgets` | `1024 2048 4096` | 地图预算，允许 0 |
| `--repeats` | `3` | 每个预算的热缓存重复次数，另有一次空缓存生成 |
| `--task` | 通用 API 与入口分析任务 | 各仓库共用的排名上下文 |
| `--tokenizer` | `cl100k_base` | Tiktoken 编码名称 |
| `--max-files` | `5000` | 快照文件数上限，含只保留路径的文件 |
| `--max-file-bytes` | `256000` | 单文件大小上限 |
| `--max-total-bytes` | `16000000` | 支持语言的源码总字节数上限 |
| `--timeout` | `300` | 每个仓库最多运行的秒数 |
| `--output` | 自动生成目录 | 指定一个空的结果目录 |
| `--dry-run` | 关闭 | 只列出项目与镜像，不创建容器 |

扫描上限控制候选覆盖范围，token 预算控制地图长度。扫描被截断的原因记录在各仓库的 `warnings` 中。
预算为 0 时仍复用实验的源码读取步骤，但不解析或输出地图，因此不等于完整 Agent 的无地图耗时。

### 输出和指标

```text
benchmarks/results/<时间>-<运行ID>/
├── config.json                 # 参数、系统与依赖版本
├── report.json                 # 全部仓库及逐预算结果
├── results.csv                 # 每个仓库、每个预算一行
├── summary.md                  # 汇总表
├── cases/                      # 各仓库输入配置和结果 JSON
├── logs/                       # 子进程与容器日志
└── maps/<owner__repo>/<预算>.txt
```

| 指标 | 含义 |
| --- | --- |
| `collection_seconds` | 读取容器文件流并构建宿主快照的耗时 |
| `cold_build_seconds` | 空符号缓存的一次 `build()`，不含源码传输和 `RepoMap` 构造 |
| `warm_build_median_seconds` | 复用解析缓存后，重新排序与渲染的中位耗时 |
| `map_tokens` | 整个地图的 Tiktoken 数，含标签与说明 |
| `snapshot_files` / `snapshot_source_files` | 保留的路径总数 / 支持语言文件数 |
| `snapshot_source_bytes` | 成功解码后的源码 UTF-8 字节数 |
| `parsed_definitions` | 解析缓存中的定义总数，非地图选中的定义数 |
| `rendered_files` / `rendered_lines` | 地图包含的文件数 / 带行号的片段行数 |
| `peak_rss_mib` | 整个仓库子进程的最大 RSS，含读取、解析和排序 |

这些指标描述资源消耗和输出规模，不代表任务解决率或定位准确率。

## 3. SWE-bench 模型推理对照

两组使用相同实例、模型和运行限制：

- `baseline`：同一非交互式扩展 Agent，`map_tokens=0`。
- `repomap`：启用地图，并设置 `strict=true`，收集失败时直接报错。

先查看命令：

```bash
python scripts/run_swebench.py --dry-run --repos pallets/flask
```

实际运行时，将 `provider/model` 替换成 mini 中可用的模型名：

```bash
python scripts/run_swebench.py \
  --repos pallets/flask psf/requests \
  --model provider/model --map-tokens 1024 \
  --workers 1 --cost-limit 3 --step-limit 250
```

已在 `.env` 中设置 `MODEL_NAME`、`MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_STREAMING` 时，可以省略 `--model`。

这条命令会调用模型。每个仓库使用选取清单中固定的一个实例；脚本并发调度 mini 的单实例 runner，结果按数据集分组。
mini 2.4.6 内置批量 runner 固定使用 `ProgressTrackingAgent`，不会加载 `agent_class`，
因此这里使用支持扩展的单实例入口，并从轨迹核对实际 Agent 类型和地图消息。
`--variants baseline` 或 `--variants repomap` 可只运行一组；`--config` 可多次传入额外的 mini 配置；
`--output` 指定空结果目录。

启用 `repomap` 时，先检查镜像内配置的解释器是否为 Python 3.10+；不满足时在模型调用前停止。
`--python-command` 可指定镜像内已有的解释器。
若自定义配置通过 `run.env_startup_command` 准备 Python，可以配合 `--skip-preflight`；实际 Agent 仍使用严格收集模式。

结果写入 `benchmarks/results/swebench-<时间>-<运行ID>/`：

- `plan.json`：命令、实例和分组。
- `summary.json`：逐实例退出状态、模型调用数、费用、轨迹路径、是否含地图。
- `baseline/<数据集>/`、`repomap/<数据集>/`：mini 的 `preds.json`、轨迹及 runner 日志。

脚本检查预期轨迹和地图。异常、缺失轨迹或启用地图却没有地图时返回非零退出码。
达到 mini 的步数或费用限制会如实记录。
这个入口执行推理，不自动运行 SWE-bench 测试判题器；解决率需对两组 `preds.json` 另行执行标准评测。

## 4. 容器断网与官方测试

推理阶段可以通过 Docker 参数断网。以下命令使用全部 20 条实例，只运行启用 RepoMap 的一组：

```bash
python scripts/run_swebench.py --variants repomap --workers 2 \
  --config 'environment.run_args=["--rm","--pull=never","--network=none"]'
```

`--network=none` 作用于 Agent 执行命令的容器；模型 API 请求仍由宿主机发起。
镜像须提前拉取。断网容器中缺少的语言依赖无法在线安装，相应失败会保留在轨迹和测试日志中。

部分非 Python 镜像没有 RepoMap 收集器需要的 Python 3.10+。
本次 20 条批量运行使用宿主机下载的 Python 3.12.14 独立发行版，
通过只读 bind mount 挂载到 `/opt/repomap-python`，并将
`--python-command` 设置为 `/opt/repomap-python/bin/python3.12`。
这不会替换镜像中用于项目测试的 Python 或其他语言环境。
多语言镜像没有 Conda 时，应跳过 mini 默认 `/root/.bashrc` 中的 Conda 初始化；
否则启动报错会出现在提交标记之前，导致 Agent 无法正常识别提交。
本次使用带存在性检查的 `BASH_ENV` 初始化脚本，Python 镜像仍激活原有 `testbed` 环境。
运行目录中的 `offline-config.yaml` 保存完整挂载配置，`preflight.json` 记录全部 20 个镜像的解释器和网卡检查。
这种带挂载的配置需先独立验证解释器，再使用 `--skip-preflight`，因为默认预检查不加载自定义挂载参数。

生成补丁后，再执行官方判题器。判题工具作为独立依赖安装，当前适配并验证 SWE-bench 5.0.2：

```bash
uv venv /tmp/repomap-eval
uv pip install --python /tmp/repomap-eval/bin/python 'swebench==5.0.2'

/tmp/repomap-eval/bin/python scripts/evaluate_swebench_offline.py \
  benchmarks/results/<已完成的运行目录> --variant repomap --workers 1 --timeout 900
```

批量推理仍在运行时，可加 `--completed-only` 先测试已经结束的实例；
后续再次运行会复用已有官方报告，只评测新完成的补丁。全部推理结束后再次运行即可汇总完整 20 条结果。

该入口从 `plan.json` 和轨迹读取选取的实例与补丁，核对官方数据集的 base commit，
并交给原版判题器执行测试补丁、解析测试日志和判定 `resolved`。
只在 Docker 容器创建时注入并核验 `network_mode=none`，不修改官方测试或判分规则。
宿主机仍可下载官方数据集；评测容器全程断网。

结果保存在原运行目录的 `evaluation/<variant>/`：

- `config.json`：判题器版本、实例列表、超时和断网策略。
- `predictions.json`、`dataset.json`：本次实际使用的补丁与官方测试数据快照。
- `network-audit.jsonl`：实际创建的每个评测容器及其网络模式。
- `logs/run_evaluation/`：逐实例测试输出、补丁应用日志和 `report.json`。
- `*-offline.*.json`：官方汇总，包括解决、未解决、空补丁和环境错误。

测试数据快照仅保存在宿主机结果目录，不挂载进推理容器。
`Submitted` 表示推理已提交补丁；最终是否修复成功以官方测试的 `resolved` 字段为准。

## 5. plain 模式与 RepoMap 对比

`scripts/run_plain.py` 运行选取清单中的全部 20 条实例，默认并发为 2，单条费用上限 3、步数上限 250、时间上限 1200 秒。
模型及凭据继续从 `.env` 读取。plain 是 `baseline` 的同等模式：使用相同 Agent、提示词和模型后端，
将 `agent.repomap.map_tokens` 固定为 `0`，跳过源码收集、地图构建和地图注入。
轨迹、费用、`preds.json` 和汇总存放在输出目录的 `plain/` 分组。

### 修复两个镜像

Gson 原镜像缺少动态加载的 Surefire JUnit provider；Tokio 原镜像没有完整 Cargo 缓存，且其未跟踪的 Cargo.lock 需要更新。
准备脚本基于相同实例的原始镜像预热依赖，不更换实例或修改已跟踪的仓库源码：

```bash
python scripts/prepare_offline_images.py
```

准备阶段允许联网下载依赖；随后强制在 `--network=none` 容器中验证。
Gson 验证原有 `JsonReaderPathTest`，Tokio 验证启用 `full` feature 的库测试。
镜像中不包含官方测试补丁或模型补丁。
Dockerfile 位于 `benchmarks/docker_repositories/offline/`。

脚本生成：

- `benchmarks/docker_repositories/offline_images.json`：实例 ID 到固定本地镜像 ID 的覆盖表。
- `benchmarks/docker_repositories/offline_image_inventory.json`：原镜像、派生镜像、源码树和断网验证记录。
- `benchmarks/docker_repositories/logs/offline-*.log`：构建和断网验证日志。

默认使用本机固定镜像 ID；其他机器需要重新运行准备脚本生成对应映射。
另外 18 个镜像保持选取清单中的设置。

### 单独运行 plain

```bash
# 只预览
python scripts/run_plain.py --dry-run

# 先运行一个实例
python scripts/run_plain.py --repos pallets/flask \
  --output benchmarks/results/plain-flask

# 全部 20 条，会调用模型
python scripts/run_plain.py --workers 2 \
  --output benchmarks/results/plain-offline20

# 对 plain 补丁执行官方断网测试
/root/.cache/repomap-eval-venv/bin/python scripts/evaluate_swebench_offline.py \
  benchmarks/results/plain-offline20 --variant plain --workers 1
```

plain 默认加载 `src/mini_swe_repomap/config/offline.yaml`，按镜像是否存在 Conda 初始化测试环境，
并强制容器网络为 `none`。与 `--offline` 冲突的 `--network=host` 等配置会被拒绝。
`--repos`、`--limit`、`--model`、`--env-file`、`--config`、`--cost-limit`、`--step-limit` 和 `--output` 与主脚本相同。

### 同一环境运行两组

严格对比需要两组使用相同实例、模型、镜像 ID、预算和 shell 配置。
以下命令在这台机器上为两组挂载同一个已准备好的 Python 运行时，统一解决非 Python 镜像缺少收集器解释器的问题：

```bash
python scripts/run_swebench.py --variants repomap plain --offline \
  --image-overrides benchmarks/docker_repositories/offline_images.json \
  --config src/mini_swe_repomap/config/offline.yaml \
  --collector-runtime /root/.cache/repomap-python/cpython-3.12.14-linux-x86_64-gnu \
  --workers 2 --map-tokens 1024 --cost-limit 3 --step-limit 250 \
  --output benchmarks/results/repomap-vs-plain
```

该命令生成 40 次推理任务。`--collector-runtime` 会只读挂载到 `/opt/repomap-python`，
并在包含挂载的断网容器内预检查 Python；无需 `--skip-preflight`。
plain 单独运行时也可加同一个 `--collector-runtime` 参数。
两组唯一有意改变的 Agent 设置是地图预算：RepoMap 为 1024，plain 为 0。

对两个分组分别使用 `--variant repomap` 和 `--variant plain` 进行官方评测。
比较同一实例的 `resolved`、调用次数和服务端费用；`plan.json` 和轨迹记录实际镜像与配置。
此前 Repomap 的费用来自修复前的推理环境；修复镜像后的历史补丁复评只更新测试结果。
比较两种模式的成本和调用数时，应使用上面同一配置重新运行的两组数据。

### 复用已有补丁验证修复镜像

无需再次调用模型，可以选择两个实例并把评测写到新目录：

```bash
/root/.cache/repomap-eval-venv/bin/python scripts/evaluate_swebench_offline.py \
  benchmarks/results/swebench-offline20-20261029T095019Z \
  --instances google__gson-1014 tokio-rs__tokio-4384 \
  --image-overrides benchmarks/docker_repositories/offline_images.json \
  --output benchmarks/results/swebench-offline20-20261029T095019Z/evaluation/repaired-images-v2
```

更改评测镜像时必须指定新的 `--output`，以保留旧报告并避免复用旧的失败结果。

## 6. 仓库规模与地图预算对照

这个实验检验同一批20条任务在不同地图预算下的表现，不从历史两轮结果推断预算门槛。
四档为 `0 1024 2048 4096`：0使用plain，其余使用固定token预算和每轮刷新；保持累计历史，不启用摘要。
默认共80次模型推理，使用项目 `.env`。模型调用会产生费用。

先验证已有本地镜像和扩大的扫描范围，不克隆仓库、不下载新镜像、不调用模型：

```bash
sweep_dir=benchmarks/results/budget-sweep-example
runtime_dir=/root/.cache/repomap-python/cpython-3.12.14-linux-x86_64-gnu

python scripts/prepare_budget_sweep.py \
  --output "$sweep_dir" --collector-runtime "$runtime_dir"
```

准备脚本使用Gson、Tokio的依赖预热镜像，将所有镜像固定为本地不可变ID；
在只读、断网容器中核对base commit源码树、记录镜像预置的工作区修改，并实际执行收集器。
统一扫描限制为20000个文件条目、单文件1073741824字节、支持语言源码总量134217728字节。
准备阶段只要仍触及这些限额就报错；语言过滤、符号链接处理和不可解码文件的跳过仍保持原规则。
单文件限额是允许值，并不预先分配1 GiB内存；支持语言文件同时受128 MiB总量限制。

预览和执行：

```bash
# 不调用模型，检查80条命令与各预算的执行顺序
python scripts/run_budget_sweep.py --output "$sweep_dir" \
  --collector-runtime "$runtime_dir" --dry-run

# 真实推理：默认workers=2，单条250步、1200秒、费用停止阈值3
python scripts/run_budget_sweep.py --output "$sweep_dir" \
  --collector-runtime "$runtime_dir" --workers 2

# 运行中可在另一终端持续生成统计报告；驱动结束后自动停止
python scripts/analyze_budget_sweep.py "$sweep_dir" --watch
```

每个仓库的四个预算轮换执行位置，20个仓库中每档在每个位置恰好出现5次。
通过独立的新容器保持相同镜像起点，所有推理和判题容器均为network=none。
模型API请求由宿主机发起。官方判题默认单路，与两路推理重叠执行。
每条任务的计时从runner启动到退出，包含准备及首次地图构建；不包括官方判题或异步容器清理。

驱动使用原 `run_swebench.py` 生成命令并复用单条执行逻辑，不改变Agent、地图算法或官方测试。
`--resume` 只执行尚未记录完成的任务；已有失败尝试保留，不自动挑选成功重跑结果。
同一输出目录已存在计划时必须显式加 `--resume`，并且模型、预算等命令必须与原计划一致。

输出包含：

- `scan-coverage.json`、`selection-pinned.json`、`scan-config.yaml`：扫描覆盖与统一配置。
- `plan.json`、`summary.json`、`experiment.json`：80条计划、逐条结果和实验元数据。
- `conditions/<本次运行名>-b<预算>/`：每档原生轨迹、预测与官方评测，互不混用报告。
- `evaluation-progress.json`：官方判题进度及错误。
- `budget-report.md`、`budget-report.json`、`budget-trials.csv`：按预算和源码长度分组的结果。
- `search-audit.json`：按Bash语法识别的搜索片段与解析错误。

候选预算的规则预先固定为：一个规模组全部预算判题结束且无运行/基础设施错误后，
选择达到该组最高通过数的最小预算（包括plain=0）。费用、搜索次数和耗时另列。
每个仓库也列出本次测试中通过的最小预算，并标记较小预算成功、较大预算失败的非单调情况。
每组仅5个仓库、每仓库一个任务、每档一次最终运行，结果是下一轮实验的候选，不能代表普遍的仓库长度门槛。

### 进程级时限与基础设施补跑

Agent的1200秒时间限制只在模型调用之间检查，无法中止正在等待的单次请求。
预算驱动另设 `--process-timeout 1380`，在独立工作进程层强制终止超时任务，并清理其专属容器；
原轨迹和已返回费用保留。已经运行的旧驱动可通过 `guard_budget_sweep.py <运行目录>` 附加同样时限。

明确记录的服务请求阻塞可以通过 `recovery-plan.json` 安排一次基础设施补跑；
`recover_budget_timeout.py <运行目录>` 等原驱动结束后执行，使用相同模型、镜像与原生Agent参数和独立官方评测目录。
原始summary与失败尝试不覆盖；分析器通过 `trial-overrides.json` 选择补跑结果，并将原尝试已返回费用加回。
被终止的未返回请求可能有尚未确认的费用，必须标记未知；不得计为零，也不宣称已返回费用就是最终账单。
补跑仅用于基础设施阻塞，不自动重跑官方测试失败的补丁。

## 7. 固定 1024 tokens，只注入一次地图

`scripts/run_static.py` 对同一组选定任务运行 plain 与静态 RepoMap 两组。
固定模型、镜像、任务、提示词、累计历史、工具和运行限制；两组有效配置仅地图预算不同：plain 为 0，RepoMap 为 1024。
模型及凭据继续从 `.env` 读取，推理容器强制 `--network=none`、`--pull=never`，默认使用已有的 Gson/Tokio 修复镜像。

静态配置为 [once.yaml](../src/mini_swe_repomap/config/once.yaml)：

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

每个任务首次请求前收集源码，仅按原始任务生成地图，向初始任务后插入一条 user 消息。
后续不再收集、重排、刷新或重复注入；地图作为累计历史的一部分继续随请求发送。
1024 是包含标签和说明的 `cl100k_base` token 上限，不要求恰好填满。
地图不会跟随代码修改更新，模型仍须读取实际文件确认定义和行号。
扫描上限复用最近预算实验的扩大配置，与地图 token 预算独立。

先预览一条任务的两组命令，不调用模型或启动容器：

```bash
python scripts/run_static.py --dry-run --repos pallets/flask \
  --collector-runtime /root/.cache/repomap-python/cpython-3.12.14-linux-x86_64-gnu
```

执行一条任务的对照：

```bash
python scripts/run_static.py --repos pallets/flask --workers 1 \
  --collector-runtime /root/.cache/repomap-python/cpython-3.12.14-linux-x86_64-gnu \
  --output benchmarks/results/static1024-flask
```

不传 `--repos` 时选择全部 20 条，产生 40 次推理。
脚本固定两组、1024 预算和 once 配置；其余参数沿用 `run_swebench.py`。
结果保存在 `plain/`、`repomap/`，包含原生轨迹、预测、计划及汇总；唯一地图正文在 RepoMap 轨迹中保留。
使用 [官方断网判题脚本](../scripts/evaluate_swebench_offline.py) 分别验证两组：

```bash
python scripts/evaluate_swebench_offline.py benchmarks/results/static1024-flask --variant plain --workers 1
python scripts/evaluate_swebench_offline.py benchmarks/results/static1024-flask --variant repomap --workers 1
```

搜索计数继续复用 `scripts/search_metrics.py`；该统计器识别显式搜索请求，其中仍包含日志过滤。
分析时分别报告文件定位、源码搜索和日志过滤；人工复核分类时保留原命令证据。
地图内部文件收集另计开销，不计为模型主动搜索。
同时报告全部任务的解决率与搜索总数，以及两组都通过任务的搜索差异，避免提前失败造成的低计数被当成收益。

需要交错调度两组、进程级时限及增量官方判题时，可复用预算驱动：

```bash
static_dir=benchmarks/results/static1024-once-example
runtime_dir=/root/.cache/repomap-python/cpython-3.12.14-linux-x86_64-gnu
python scripts/prepare_budget_sweep.py --output "$static_dir" --collector-runtime "$runtime_dir"
python scripts/run_budget_sweep.py --output "$static_dir" --collector-runtime "$runtime_dir" \
  --budgets 0 1024 --map-refresh once --workers 2 --process-timeout 1380
```

此入口为同样的静态地图配置生成原生 runner 命令，固定全部镜像为本地不可变 ID，
每个任务交替安排 plain/RepoMap 的执行位置，并使用单路官方判题。
`experiment.json` 记录 `repomap_refresh=once`、`context_messages=0`，
`python scripts/analyze_budget_sweep.py "$static_dir" --watch` 持续汇总搜索、费用与判题结果。

## 8. 实验 A：地图质量验证（优先）

| 指标 | 评估内容 |
| --- | --- |
| Target File Recall | 地图对官方修改文件的召回；另报任务扩展文件与基线既存文件口径 |
| Target Symbol Recall | 对任务相关函数、类、变量等声明的召回，按基线位置和必要的签名消歧 |
| Dependency Coverage | 目标所需的关键仓库内依赖端点是否可见 |
| Symbol Precision | 地图全部可见符号中与任务相关的比例，完整标注后计算 |
| Map Utilization | 明确引用地图并指导后续定位/读取的证据，不等同于文件命中或搜索减少 |

公式、标注模板、利用证据等级、宏/微平均、空分母及未知项处理见 [地图质量评测方法](map-quality-evaluation.md)。
官方 `patch` 只用于事后参考，不用 Agent 自己提交的 patch 替代目标集；不得混入 `test_patch` 或返回推理上下文。

```bash
python scripts/evaluate_map_quality.py \
  benchmarks/results/static1024-once-20261105T145121Z \
  --output /tmp/once-map-quality-A
```

入口读取本地已保存的官方评测数据，输出文件召回、逐任务明细和其余指标的标注模板。
未经复核的符号、依赖、相关性和利用行为为 `N/A`，不自动从文件重叠推算。
当前只评估可确认的 once 初始地图；旧 interval 轨迹缺少逐轮地图，不能作为首轮质量对照。
