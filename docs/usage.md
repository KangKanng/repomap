# 使用指南

本文命令默认在项目根目录运行，并已激活安装本扩展的 Python 环境。
mini 的模型和 API key 沿用其原有配置，地图预览不调用模型。

项目 `scripts/` 入口自动读取根目录 `.env`，支持自定义模型地址、密钥和流式开关，详见 [.env 配置](environment.md)。

## 安装与环境

当前机器已经安装在 `/root/repomap/.venv`：

```bash
cd /root/repomap
source .venv/bin/activate
```

在新环境中，可以使用 uv 安装：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
source .venv/bin/activate
```

已有 Python 3.10+ 虚拟环境时，也可以运行 `python -m pip install -e .`。
宿主机需要安装扩展依赖；目标执行环境需要 shell 和 Python 3.10+，Git 用于准确列举被跟踪和忽略的文件。

如需首次设置 mini 的模型，运行：

```bash
mini-extra config setup
```

## 先预览地图

```bash
mini-repomap show /path/to/repo --task '修复登录校验' --tokens 1024

# 在本项目上查看与查询逻辑有关的定义
mini-repomap show . --task 'RepoMapAgent query' --tokens 1024

# 保存地图，便于比较不同任务和预算
mini-repomap show . --task 'RepoMapAgent query' --tokens 2048 > /tmp/repository-map.txt
```

输出包含文件路径、选中的定义、行号和省略标记。`--task` 影响选择，`--tokens` 限制整个地图文本的长度。
`--tokenizer` 可指定 Tiktoken 编码名；`--tokens 0` 直接跳过生成。
完整索引预览使用 `mini-repomap show /path/to/repo --full`，不按任务或 token 预算裁剪，并关闭三项扫描数量/字节上限。

## 本地使用

```bash
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c environment.cwd=/path/to/repo \
  -t '修复登录校验'
```

`mini-repomap config` 返回已安装的交互式 overlay 文件路径。它将 Agent 改为
`mini_swe_repomap.agent.InteractiveRepoMapAgent`，保留 mini 原有的命令确认和人工模式。

mini 一旦接收到 `-c`，会用指定列表替代默认配置文件，因此需要显式写上 `-c mini.yaml`。
多个配置按顺序合并，后面的值覆盖前面的值。

覆盖地图参数、保存运行轨迹：

```bash
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c environment.cwd=/path/to/repo \
  -c agent.repomap.map_tokens=2048 \
  -c agent.repomap.refresh_interval=3 \
  -o /tmp/repomap-run.traj.json \
  -t '定位并修复缓存失效问题'
```

`refresh_interval=3` 表示首次生成，之后每三次模型调用刷新一次。
需要指定模型时，用 mini 的 `-m` 参数传入你已配置并可用的模型名。

### 固定 1024 tokens，只注入一次

```bash
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c environment.cwd=/path/to/repo \
  -c agent.repomap.map_tokens=1024 \
  -c agent.repomap.refresh=once \
  -c agent.repomap.context_messages=0 \
  -c agent.repomap.strict=true \
  -o /tmp/repomap-once.traj.json \
  -t '定位并修复缓存失效问题'
```

`once` 在每个任务的首次模型请求前收集源码，只根据原始任务生成一张地图并插入一条消息。
后续不再收集、重排或替换，即使源码已修改也保留最初版本；读取源码时应以实际文件为准。
这条消息继续随 mini 的累计历史进入后续请求，并不表示仅首个 API 请求包含地图。
`refresh_interval` 与 `context_messages` 在 `once` 模式下不参与刷新或排序。
同一个 Agent 再次调用 `run()` 开始新任务时，会重新生成一次。
1024 是包含标签和说明的 token 上限，不保证恰好填满，按 `cl100k_base` 计数。

批量 plain/静态地图对照使用 [scripts/run_static.py](../scripts/run_static.py)，
命令见 [实验运行指南](experiments.md#7-固定-1024-tokens只注入一次地图)。

### 完整地图，只注入一次

```bash
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c "$(mini-repomap config --full-once)" \
  -c environment.cwd=/path/to/repo \
  -t '定位并修复问题'
```

`full-once` 使用 `mode=full`、`refresh=once`，列出全部收集到的路径与定义，绕过 PageRank 和 token 选择，并将三项扫描上限设为 `null`。签名显示与目录排除规则继续沿用现有实现。
地图只生成和插入一次，保留在后续累计历史中；`map_tokens=0` 仍可完全关闭。完整模式没有自动截断或窗口不足时回退预算模式。
Docker 对照脚本是 `python scripts/run_full.py --dry-run --repos pallets/flask`。
完整配置、单组运行命令和范围说明见 [完整 RepoMap 单次注入](full-once.md)。

## Docker 环境

下面用本地目录挂载到 Python 镜像演示完整 Agent 用法。把 `/absolute/path/to/repo` 替换为真实的绝对路径：

```bash
docker pull python:3.12-slim

mini -c mini.yaml -c "$(mini-repomap config)" \
  -c environment.environment_class=docker \
  -c environment.image=python:3.12-slim \
  -c environment.cwd=/workspace \
  -c 'environment.run_args=["--rm","--mount","type=bind,src=/absolute/path/to/repo,dst=/workspace"]' \
  -c 'environment.interpreter=["bash","-c"]' \
  -t '分析这个仓库并完成任务'
```

宿主机安装 mini 和本扩展即可，镜像内无需安装 Tree-sitter 或本扩展。
上例中 `root="."` 指容器的 `/workspace`，Agent 的修改会作用到挂载目录。
`python:3.12-slim` 默认未安装 Git，收集器会使用目录遍历回退；如需 `.gitignore` 语义，使用带 Git 的开发镜像。

对于已经包含源码的 SWE-bench 镜像，把 `environment.image` 设为清单里的完整名称，
将 `environment.cwd` 设为 `/testbed`，省略宿主机挂载配置即可。

部分非 Python 项目的镜像没有内置 Python。当前 Repo Map 收集器需要目标环境中的 Python 3.10+；
若镜像内已存在合适的解释器，可通过 `agent.repomap.python_command` 指定其绝对路径。
镜像拉取和源码行数统计并不代表其中已经具备这个运行条件。

## SWE-bench 单个任务

使用非交互式 `RepoMapAgent` overlay，叠加原有 benchmark 配置：

```bash
python -m minisweagent.run.benchmarks.swebench_single \
  --subset verified --split test \
  --instance pallets__flask-5014 \
  -c swebench.yaml -c "$(mini-repomap config --batch)" \
  -c agent.repomap.map_tokens=1024 \
  -o /tmp/flask-5014.traj.json
```

这条命令会调用你配置的模型。仓库和环境按该 benchmark 实例配置；地图读取容器 `/testbed` 中的代码。
`mini-repomap config --batch` 对应 `mini_swe_repomap.agent.RepoMapAgent`，执行过程不使用交互式命令确认。

## SWE-bench 批量运行

```bash
python scripts/run_swebench.py \
  --repos psf/requests pallets/flask \
  --model provider/model --variants repomap \
  --map-tokens 1024 --workers 1 \
  --output /tmp/repomap-swebench
```

将 `provider/model` 替换成 mini 可用的模型名。`--repos` 选择清单中的项目，`--workers` 控制并发实例数。
每个 Agent 独立维护源码和符号缓存；地图 token 会增加模型输入量。
mini 2.4.6 内置批量 runner 固定使用 `ProgressTrackingAgent`，不会加载配置中的自定义 Agent。
本脚本调度支持扩展的单实例 runner，并核对实际 Agent 类型与地图消息。

## 已选的 20 个 Docker 仓库镜像

镜像来自 SWE-bench Verified 和 Multilingual，每个镜像对应一个不同的项目。
源码保留在镜像内，无需克隆到宿主机。

- [按源码行数排序的项目表](../benchmarks/docker_repositories/README.md)
- [完整 inventory](../benchmarks/docker_repositories/inventory.json)：镜像 tag、digest、仓库 commit、源码文件数、行数、字节数与检查状态。
- [选取记录](../benchmarks/docker_repositories/selection.json)：数据集、实例、预期 commit 和镜像信息。

再次拉取并统计，或只核对本地镜像：

```bash
python scripts/pull_docker.py --workers 3
python scripts/pull_docker.py --inspect-only --workers 3
```

统计脚本在临时只读、断网容器中读取 Git tracked 工作区的文件流，在宿主机计算源码规模，
无需容器内有 Python，也不在宿主机解包仓库。统计包括测试、注释、空行和被跟踪的第三方源码，
与 Repo Map 的文件数、单文件大小、源码总字节数限制是不同的指标。

版本核对同时记录镜像实际 HEAD 和数据集 base commit，并比较它们的 Git 源码树 SHA。
部分官方 Python 镜像在 base commit 之后有一个 `SWE-bench` 构建提交，因此 HEAD 哈希不同，
源码树可以保持一致；完整清单分别记录提交与源码树的匹配结果。

## 运行实验脚本

```bash
# 无模型调用的性能实验
python scripts/run_experiments.py --budgets 1024 2048 4096 --repeats 3

# 查看模型基线/Repo Map 对照命令，不实际调用模型
python scripts/run_swebench.py --dry-run --repos pallets/flask
```

脚本统一位于 [scripts/](../scripts/README.md)，完整参数、指标、输出结构和实际模型运行方法见
[实验运行指南](experiments.md)。

## YAML 配置与参数

可直接将下列 Agent 配置合并到现有配置中；非交互式场景修改 `agent_class`：

```yaml
agent:
  agent_class: mini_swe_repomap.agent.InteractiveRepoMapAgent
  repomap:
    mode: budget
    map_tokens: 1024
    root: "."
    refresh: interval
    refresh_interval: 1
    python_command: python3
    tokenizer: cl100k_base
    context_messages: 6
    max_files: 5000
    max_file_bytes: 256000
    max_total_bytes: 16000000
    strict: false
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `mode` | `budget` | `budget` 按任务排名并遵守预算；`full` 输出所有收集到的路径和提取的定义 |
| `map_tokens` | `1024` | `budget` 的地图 token 上限；`full` 忽略正数大小；`0` 在两种模式下均关闭收集与注入 |
| `root` | `"."` | 执行环境中的仓库根路径，相对其工作目录解析 |
| `refresh` | `interval` | `interval` 按间隔刷新；`once` 每个任务只生成并注入一次 |
| `refresh_interval` | `1` | `interval` 模式下每多少次模型调用刷新一次，至少为 1 |
| `python_command` | `python3` | 目标环境内的解释器可执行文件名或完整路径；不接受附加 shell 参数 |
| `tokenizer` | `cl100k_base` | 用于预算计数的 Tiktoken 编码名 |
| `context_messages` | `6` | `interval` 排名参考的最近非 system、非地图消息数；`0` 仅使用任务；`once` 始终仅使用任务 |
| `max_files` | `5000` | 收集文件数上限，包含只列路径的文件；`null` 不限 |
| `max_file_bytes` | `256000` | 单文件大小上限，单位是字节；`null` 不限 |
| `max_total_bytes` | `16000000` | 支持语言的源码总字节数上限；`null` 不限 |
| `strict` | `false` | 收集失败时默认记录警告并继续；为 `true` 则抛出异常 |

对大仓库调高扫描上限与调高地图 token 预算分别控制候选覆盖量和模型输入量。
触及扫描限制时日志会指出地图不完整；`budget` 输出选出的索引，`full` 输出扫描范围内所有已提取的定义。

完全关闭地图：

```bash
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c agent.repomap.map_tokens=0 -t '你的任务'
```

## 在已有 Python 脚本中接入

沿用 mini 的配置与工厂，替换 Agent 类即可。把模型名和路径替换成你的配置：

```python
from minisweagent.agents import get_agent
from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.utils.serialize import recursive_merge

config = recursive_merge(
    get_config_from_spec("mini.yaml"),
    {
        "agent": {
            "agent_class": "mini_swe_repomap.agent.RepoMapAgent",
            "repomap": {"map_tokens": 1024, "strict": True},
        },
        "environment": {"cwd": "/path/to/repo"},
        "model": {"model_name": "provider/model"},
    },
)
model = get_model(config=config["model"])
environment = get_environment(config["environment"], default_type="local")
agent = get_agent(model, environment, config["agent"])
result = agent.run("定位并修复问题")
```

只生成地图、没有 Agent 循环的用法：

```python
from minisweagent.environments.local import LocalEnvironment
from mini_swe_repomap.repomap import RepoMap
from mini_swe_repomap.settings import RepoMapConfig
from mini_swe_repomap.source import EnvironmentSource

source = EnvironmentSource(
    LocalEnvironment(cwd="/path/to/repo"),
    RepoMapConfig(root="."),
)
mapper = RepoMap()
text = mapper.build(source.read(), context="修复缓存失效", map_tokens=1024)
print(text)
print("tokens:", mapper.token_count(text))
```

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| `Unknown agent type` 或导入失败 | 确认运行 mini 的 Python 环境中已安装本扩展，可先运行 `mini-repomap config` |
| 原有模板或配置缺失 | 显式保留 `-c mini.yaml` 或 `-c swebench.yaml`，再追加 overlay |
| `Repo map unavailable` | 检查执行环境中的根路径、Python 命令、权限与超时；可用 `strict=true` 显示错误 |
| 地图为空 | 检查预算是否为 0 或太小、根目录是否为空、文件是否被过滤 |
| 大仓库只覆盖一部分 | 检查 `max_files`、`max_file_bytes`、`max_total_bytes` 警告，并按资源情况调整 |
| 修改后仍看到旧定义 | 检查刷新周期；默认每次模型调用刷新，调大间隔会延后更新 |
| 非 Git 目录包含本应忽略的文件 | 回退遍历只排除固定目录，不读取 `.gitignore`；使用 Git 环境可恢复 Git 文件筛选 |
| token 与供应商账单不同 | 地图使用配置的 Tiktoken 编码计数，不包含消息封装和其他对话内容 |

## 开发验证

```bash
pytest
ruff check .
ruff format --check .
python -m build
```

本机系统 Python 缺少 `ensurepip` 时，可以使用 `python -m build --installer uv`。
测试采用 mini 的确定性模型，不需要 API key。Docker 测试要求本地已有 `python:3.12-slim` 且守护进程可用。
流程细节见 [适配方法与处理流程](adaptation.md)。
