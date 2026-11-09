# 完整 RepoMap 单次注入

`full-once` 在每个任务首次模型请求前，收集仓库快照，列出全部收集到的文件路径与可提取的符号，再插入一条上下文消息。后续不扫描、不重排、不替换地图。新任务再次调用 `run()` 时重新生成。

这里的完整指**索引全部收集到的路径与符号，不按 token 预算选择子集**。不是把所有源码正文放入上下文。

## 实现与配置

直接叠加已安装的 [full-once.yaml](../src/mini_swe_repomap/config/full-once.yaml)：

```yaml
agent:
  repomap:
    mode: full
    map_tokens: 1024
    refresh: once
    context_messages: 0
    strict: true
    max_files: null
    max_file_bytes: null
    max_total_bytes: null
```

- `mode=full` 绕过个性化 PageRank、任务相关性排序和 token 预算选择，输出所有提取的定义；没有定义或不支持解析的文件仅列路径。文件和行号排序保持确定性。
- `map_tokens` 为正数时开启；在 `full` 模式下正数的大小不限制地图，`0` 仍关闭收集和注入，保持 plain 的行为。
- 三个扫描上限的 `null` 表示不限。普通预算配置的默认上限保持原值；只设置 `mode=full` 不会自动移除扫描上限，需要使用上述 overlay。
- `refresh=once` 复用现有单次注入逻辑，`context_messages` 不参与生成。生成和提取缓存仍按源码内容更新，每个新任务重新收集一次。
- `strict=true` 时收集失败直接报错；无法读取或解码等跳过行为仍会记录警告，应检查日志。

代码分别在 [repomap.py](../src/mini_swe_repomap/repomap.py)、[agent.py](../src/mini_swe_repomap/agent.py)、[settings.py](../src/mini_swe_repomap/settings.py) 和 [_collect.py](../src/mini_swe_repomap/_collect.py)。

## 预览和本地运行

激活项目环境后执行。预览不调用模型：

```bash
source .venv/bin/activate
mini-repomap show /path/to/repo --full > /tmp/full-repomap.txt
```

`--full` 应用相同的无限扫描设置；`--task` 不改变完整地图，`--tokens` 的正数不产生裁剪，`--tokens 0` 关闭。

本地任务使用两个 overlay：第一个选择 Agent，第二个设置完整单次地图。

```bash
mini -c mini.yaml -c "$(mini-repomap config)" \
  -c "$(mini-repomap config --full-once)" \
  -c environment.cwd=/path/to/repo \
  -o /tmp/full-once.traj.json \
  -t '定位并修复问题'
```

非交互 SWE-bench 入口使用 `-c swebench.yaml -c "$(mini-repomap config --batch)" -c "$(mini-repomap config --full-once)"`。
`config --full-once` 只输出地图行为 overlay 的路径，因此需与 Agent overlay 分开叠加。

## Docker 实验入口

[scripts/run_full.py](../scripts/run_full.py) 自动读取项目 `.env`，对同一任务比较 plain 与 full-once，沿用修复镜像和 Docker `--network=none`。只预览命令：

```bash
python scripts/run_full.py --dry-run --repos pallets/flask
```

实际运行单个仓库的两组推理：

```bash
python scripts/run_full.py --repos pallets/flask --workers 1 \
  --output benchmarks/results/full-once-flask
```

仅运行 full-once 组可以直接使用通用入口：

```bash
python scripts/run_swebench.py --repos pallets/flask --variants repomap --offline \
  --image-overrides benchmarks/docker_repositories/offline_images.json \
  --config src/mini_swe_repomap/config/offline.yaml \
  --config src/mini_swe_repomap/config/full-once.yaml \
  --output benchmarks/results/full-once-flask-only
```

这些推理入口不自动执行官方补丁判题；判题方式见 [实验运行指南](experiments.md#4-容器断网与官方测试)。
应继续使用搜索次数为主指标、首次目标定位成本为辅助指标，并通过官方 Resolved Rate 检查成功率约束。
实现验证使用确定性模型；随后启动的正式 20 条单组实验见 [Full-once 实验记录](full-once-experiment.md)，该实验复用历史 plain，不新增 plain 推理。

## 上下文与范围

```text
system → 原始任务 → 完整 repository_map → assistant → 工具观察 → …
```

“单次”指只生成和插入一次。地图留在累计历史中，后续 API 请求仍携带同一内容；不存在跨请求只传一次的特殊存储机制。代码修改后地图不会同步更新，行号可能过期。

完整模式沿用原有索引规则：支持的语言提取类、函数、方法、接口、类型等主要定义，不保证识别每个变量。签名最多显示 12 行、每行 240 字符，方法可带所属类型及装饰器上下文；函数正文不展开。

文件选择也沿用原规则：Git 跟踪文件和未忽略的 untracked 文件；非 Git 环境回退为目录遍历。缓存、依赖和构建目录、符号链接、常见二进制后缀、含空字节或非 UTF-8 的支持语言源码等仍被排除。`null` 只关闭三项数量/字节上限，不取消上述规则。不能把它理解为所有磁盘文件都进入模型。

完整地图没有 token 截断或自动退回预算地图。日志记录路径数、按配置编码计算的 token 数；若地图加累计历史超过模型上下文窗口，由模型服务或上游 runner 返回错误。实际计费仍以服务端返回为准。

## 验证

使用确定性模型与临时仓库验证：超过 1024 tokens 的全部定义保留、任务改变不影响地图、扫描上限可单独关闭、三种模型消息格式中每任务只收集一次、编辑后地图保持不变、新任务重建，以及 plain 完全关闭地图。脚本 dry-run 另外验证 `.env` 公共入口、两组相同镜像及容器断网配置。
