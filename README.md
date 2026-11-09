# mini-swe-repomap

把 [Aider Repository Map](https://aider.chat/docs/repomap.html) 的思路适配到
[mini-swe-agent v2](https://github.com/SWE-agent/mini-swe-agent)：提取定义与签名，
建立文件间的符号引用图，用当前任务与最近对话加权 PageRank，再选取 token 预算内的定义作为上下文。
也支持[完整地图单次注入](docs/full-once.md)：列出全部收集到的路径和符号，跳过任务排名与 token 选择，首次生成后保留在历史中。

这是独立扩展，不需要修改 mini-swe-agent。当前针对 **mini-swe-agent 2.4.6** 验证；不支持 v1。
它独立实现 Repo Map，不依赖或复制 Aider 的代码，也不保证与 Aider 输出一致。

完整文档见 [docs/README.md](docs/README.md)：
[适配方法与处理流程](docs/adaptation.md)、[使用指南](docs/usage.md)、
[Docker 仓库样本](benchmarks/docker_repositories/README.md)。

Docker 拉取和实验入口统一位于 [scripts/](scripts/README.md)，详细流程见 [实验运行指南](docs/experiments.md)。
脚本自动加载项目 `.env`；模型名、服务地址、密钥和流式设置见 [.env 配置](docs/environment.md) 与 [.env.example](.env.example)。

## 安装和使用

```bash
python -m pip install -e .

# 先预览，不调用模型
mini-repomap show /path/to/repo --task '修复登录校验' --tokens 1024

# 使用 mini 原有的模型配置、命令确认和轨迹保存
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c environment.cwd=/path/to/repo \
  -t '修复登录校验'
```

`mini-repomap config` 只输出已安装 YAML 文件的绝对路径，方便从任意工作目录使用。
mini 的 `-c` 会替换默认配置列表，因此上面显式保留了 `-c mini.yaml`。
API key、模型等配置沿用 mini 本身；可以加 `-m provider/model`。

SWE-bench 使用非交互式 Agent overlay，叠加到原有 benchmark 配置：

```bash
python -m minisweagent.run.benchmarks.swebench_single --help
python -m minisweagent.run.benchmarks.swebench_single \
  -c swebench.yaml -c "$(mini-repomap config --batch)" \
  --subset lite --split test \
  --instance django__django-11099
```

批量实验使用 `scripts/run_swebench.py`。mini 2.4.6 的内置批量 runner 固定使用自己的 Agent，
脚本通过并发调度支持 `agent_class` 的单实例 runner 接入扩展，详见 [实验运行指南](docs/experiments.md)。
仓库路径默认是 **Agent 执行环境的工作目录**：例如 SWE-bench 的 `/testbed`，不是宿主机目录。
读取通过 `Environment.execute()` 完成，远端仅需 Python 3.10+ 和 shell；无需安装本扩展或 Tree-sitter。
有 Git 时按 Git 列举文件；没有 Git 或不是 Git 仓库时遍历目录并排除常见缓存、依赖和构建目录。

## 配置

也可以把下面内容直接合并到已有 YAML：

```yaml
agent:
  agent_class: mini_swe_repomap.agent.InteractiveRepoMapAgent
  # 自动运行和 benchmark 改为 mini_swe_repomap.agent.RepoMapAgent
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

| 参数 | 行为 |
| --- | --- |
| `mode` | 默认 `budget` 按预算选择；`full` 输出全部收集到的路径和提取的定义 |
| `map_tokens` | `budget` 的地图 token 上限；`full` 忽略正数大小；`0` 在两种模式下均完全关闭收集与注入 |
| `root` | 在执行环境中解析；相对路径基于其工作目录 |
| `refresh` | `interval` 按间隔刷新；`once` 每个任务仅在首次查询前生成并注入一次 |
| `refresh_interval` | `interval` 模式下每多少次模型调用重新扫描并按对话重排；默认每次 |
| `python_command` | 执行环境内的 Python 可执行文件名或完整路径，不是 shell 命令 |
| `tokenizer` | Tiktoken 编码名；默认按 `cl100k_base` 计数，不等于所有模型的实际计费 token |
| `context_messages` | 用于重排的最近消息条数，另加原始任务；`0` 仅使用任务 |
| `max_files` / `max_file_bytes` / `max_total_bytes` | 限制文件数、单文件大小和源码总字节数；`null` 不限；触及限制会记录警告 |
| `strict` | 默认收集失败时记录警告、移除旧地图并继续；`true` 时抛出错误 |

地图以额外的 user 消息置于初始任务之后，保留原始 system/task 模板和工具调用顺序。
每次刷新替换这一个消息，避免重复累积。地图会随正常轨迹保存；刷新历史消息会影响 prompt cache 命中率。

固定 1024 tokens、每个任务只注入一次地图的对照实验使用 `scripts/run_static.py`，
详见 [静态地图对照](docs/experiments.md#7-固定-1024-tokens只注入一次地图)。
该模式只用原始任务排序，后续地图作为累计历史的一部分保留，不再扫描、重排或替换。

完整地图单次注入使用 `-c "$(mini-repomap config --full-once)"` 叠加在 Agent overlay 后，
Docker 对照入口为 `python scripts/run_full.py --dry-run --repos pallets/flask`。详见 [完整地图实现与用法](docs/full-once.md)。

## 支持和边界

- Tree-sitter 提取 Python、JavaScript、TypeScript/TSX、Go、Rust、Java、C/C++、C#、Ruby、PHP 的主要定义与签名。
  其他文件只列路径。签名保留所在类型的上下文与行号；过长签名最多显示 12 行、每行 240 字符。
- 图边根据同名符号引用构建，属于启发式关联，不是编译器级调用图；常见同名符号会降权或跳过。
- 预算模式中，任务中提到的路径、文件名和符号，以及最近的命令/观察结果会影响排序；完整模式不排序或按 token 选择。没有按 Aider 的“打开文件”状态自动扩容。
- 有 Git 时包括 tracked 与未被忽略的 untracked 文件，遵守 `.gitignore`；非 Git 回退不解析 ignore 规则。
  跳过符号链接、已删除文件、常见二进制文件、非 UTF-8 源码及过大的文件。
- 文件元数据指纹未变化时不传回源码；仓库变化时重新读取受限快照，宿主端只重新解析内容变化的文件，删除文件同步移除。
  缓存仅在当前 Agent 实例内保存，不写入目标仓库。Tree-sitter 在宿主端运行，使用自带语法的 language-pack 0.x。
- Tree-sitter 约束为 `>=0.25.2,<0.26`：完整仓库验证中复现了 0.26.0 连续解析时的原生崩溃，0.25.2 可正常运行。
- 地图帮助定位代码，不能代替读取文件与测试。未声称获得 SWE-bench 分数提升。

## 开发验证

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
python -m build
# 如果系统 Python 缺少 ensurepip，可使用：python -m build --installer uv
```

测试使用真实 Git 仓库、LocalEnvironment 和 mini 的确定性模型，无需 API key。
Docker 测试在守护进程可用且本地已有 `python:3.12-slim` 镜像时运行，否则跳过；可先 `docker pull python:3.12-slim`。
