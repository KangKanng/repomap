# SCBench：plain 与 once-full

从 [SlopCodeBench](https://github.com/SprocketLab/slop-code-bench) 对应的独立题库中固定选择 **25 个题目**，覆盖全部 11 个类别，难度为 Easy 9、Medium 8、Hard 8。每题按顺序运行完整 checkpoint 链，每组预定 139 个 checkpoint，两组共 278 次。

结果入口：

- [运行状态](../benchmarks/results/scb-plain-once-full-20261109/status.json)
- [运行汇总](../benchmarks/results/scb-plain-once-full-20261109/report.md)
- [配对分析](../benchmarks/results/scb-plain-once-full-20261109/analysis.md)
- [固定协议与题目清单](../benchmarks/results/scb-plain-once-full-20261109/protocol.json)
- [逐 checkpoint CSV](../benchmarks/results/scb-plain-once-full-20261109/trials.csv)
- [参考实现环境预检](../benchmarks/results/scb-plain-once-full-20261109/preflight-v2.json)

状态显示 `running` 时仍是阶段性结果；两组完成数量可能不同，不应直接比较累计搜索总数。

## 题目与抽样

抽样种子为 `20261109`。先覆盖类别，再补足难度配额，不根据推理或测试结果换题。事先排除需要额外消息代理或多语言工具链的 `mocked_http`、`test_translator`。

所选题目为：`eve_route_planner`、`dynamic_config_service_api`、`eve_market_tools`、`file_backup`、`file_merger`、`layered_config_synthesizer`、`code_search`、`database_migration`、`env_manager`、`file_query_tool`、`metric_transform_lang`、`migrate_configs`、`rejector`、`xjq`、`cfgpipe`、`eve_industry`、`execution_server`、`mvvault`、`sith`、`datagate`、`sheeteval`、`circuit_eval`、`forge`、`meshctl`、`recli`。

框架和题库均在协议中记录 Git commit。`selection.json` 记录每题 checkpoint 的固定顺序。

## 两组设置

模型沿用项目 `.env`，本轮为 `openrouter/deepseek/deepseek-v4.1-flash-1017`。使用 mini-swe-agent 2.4.6、相同的固定 Python Docker 镜像、相同提示词、相同限制；正式调度同时运行 4 对任务。每个 checkpoint 限 250 次模型调用、已返回费用 3 美元、1200 秒软时限。

运行中发现流式请求能超过软时限后，补充了 1380 秒硬截止与外部守护。启用时间及此前已发生的超时保留在协议和 `guard.json` 中；不重新推理。硬截止取消请求后仍可能有未知费用，报告中的已记录调用、token 和费用不是完整账单。

- **plain**：`map_tokens=0`，关闭地图收集和注入。
- **once-full**：使用本项目 `full-once.yaml`，完整索引工作区路径和符号，关闭三项扫描上限，每个 checkpoint 只生成一次并保留在累计历史中。

每个 checkpoint 重置对话，使用上游 `just-solve.jinja` 渲染当前规格；各组保留自己上一轮产生的代码和虚拟环境。模型看不到未来规格、官方测试、参考实现和判题反馈。首次通常还没有既有实现，部分题目提供的静态素材也可能产生地图，所以首轮单列，主搜索比较使用后续 checkpoint。

每条运行只尝试一次。模型或服务异常保留为失败；中断后的 checkpoint 缺失单列。服务端未提供费用时，已记录费用只是已知部分，不视为完整账单。

## 官方评测适配

上游框架锁定 mini-swe-agent v1，本项目依赖 v2，因此使用两个独立 Python 环境：本项目负责推理，上游环境负责 `slop-code eval-snapshot`。不修改题目、测试或官方评测代码。

评测使用上游 local runtime。预检发现其 `LocalExecRuntime` 没有执行环境中的 `setup.eval_commands`，导致参考实现声明了依赖仍报 `ModuleNotFoundError`。进一步预检还发现，直接在启动命令中加入相对的 `--with-requirements requirements.txt` 会在测试切换工作目录时失效。

统一流程先把提交复制到临时目录，再准备 uv 项目和依赖锁：

```text
uv init --bare --no-workspace --python /usr/bin/python3  # 已有项目时跳过
uv add --no-sync -r requirements.txt
slop-code eval-snapshot <prepared-copy> ...             # 官方 uv run 入口
```

实现源码不作修改，uv 在实际测试执行时安装项目声明的依赖。参考实现没有依赖清单时创建空清单。修正前报告保存在 `preflight-initial-local-runtime/` 和 `preflight/`；统一重判保存在 `preflight-v2/`、各 checkpoint 的 `evaluation-v2/`。配对分析只使用统一后的报告，不增加模型推理次数。此实验是使用官方测试的适配对照，不等同于发布榜单配置。

每次官方评测使用独立 Linux 网络命名空间并启用 loopback，避免 API 题目固定端口相互干扰。依赖先在外部预热，测试阶段设 `UV_OFFLINE=1`。准备后的 `pyproject.toml`、`uv.lock` 与依赖清单保存在判题目录 `bootstrap/`。端口隔离前的报告另存在 `*-before-network-isolation/`，不用于最终统计。

`uv run --quiet` 抑制依赖初始化日志，保留程序本身的标准错误；否则首个检查空 stderr 的用例会被 uv 的启动信息干扰。`execution_server` 的官方就绪时限只有 5 秒，额外使用已同步的 `uv run --active --quiet` 环境，避免把首次安装依赖算作服务启动耗时。两组配置一致。25题首轮参考实现的核心、功能和回归测试均已通过预检；`env_manager` 参考实现另有一个错误处理用例未通过，原始记录保留。

## 比较口径

主要成功指标是核心、功能和回归测试全部通过；另报核心测试全部通过、全部测试通过（包含错误处理）、用例通过数和回归通过数。官方分组与 prior-tests 配置保持原样。

搜索复用本项目的 Bash 语法解析器，统计显式文本搜索和文件定位子命令；不把 `ls`、读取文件或嵌入脚本中的自定义查找计作搜索。后续 checkpoint 配对只纳入双方正常提交且官方评测有效的运行，另报双方严格通过的子集。失败的零搜索不能成为收益。

两组代码会独立演化，观察到的差异包括实现路径变化。区间按题目整体重采样，保留题内 checkpoint 的关联；一次运行不能证明稳定因果收益。

轨迹中的实际响应模型、token、费用和地图均可复核。原始运行目录还保存了源码快照、官方判题结果和 `quality_analysis`。

## 入口

```bash
# 固定抽样和协议，不调用模型；output 必须不存在
.venv/bin/python scripts/run_scb_compare.py \
  --count 25 --workers 8 --prepare-only --output <new-output>

# 执行已固定的两组完整链并自动判题
.venv/bin/python scripts/run_scb_compare.py --prepared --output <new-output>

# 离线刷新配对分析，不新增推理或判题
.venv/bin/python scripts/analyze_scb_compare.py <output>
```

`evaluate_scb_snapshots.py <output>` 可持续发现已生成快照并用统一环境重判；加 `--preflight` 对固定题目的首轮参考实现验证环境。重判结果与旧记录分别保存。

上游框架需要独立安装：在 `benchmarks/slop-code-bench/` 中执行 `uv sync --frozen --no-dev`。推理依赖继续使用项目根目录 `.venv`，不混装两套 mini-swe-agent。
