# .env 配置

`scripts/pull_docker.py`、`scripts/run_experiments.py` 和 `scripts/run_swebench.py`
默认加载项目根目录的 `.env`，从其他工作目录调用脚本也使用同一个默认文件。
现有 `.env` 可以直接使用；首次配置可参考 [.env.example](../.env.example)。

## 模型字段

```dotenv
MODEL_NAME=openai/your-model
MODEL_BASE_URL=https://your-model-endpoint.example/v1
MODEL_API_KEY=replace-with-your-api-key
MODEL_STREAMING=false
```

| 字段 | 用途 |
| --- | --- |
| `MODEL_NAME` | SWE-bench 推理的默认模型名，省略 `--model` 时使用 |
| `MODEL_BASE_URL` | 模型服务的 API base URL，由模型后端在发起请求时读取 |
| `MODEL_API_KEY` | 模型请求使用的凭据，通过进程环境传递 |
| `MODEL_STREAMING` | 是否使用流式传输，支持 `true/false`、`1/0`、`yes/no`、`on/off`，不区分大小写 |
| `MSWEA_MODEL_NAME` | mini 原生默认模型变量；同时配置时优先于 `MODEL_NAME` |

对于兼容 Chat Completions 的自定义服务，推荐给模型名添加 `openai/` 前缀。
设置了 `MODEL_BASE_URL` 且模型名没有 `/` 时，加载器会自动补上这个前缀；已有前缀的名称保持原样。

`run_swebench.py` 默认使用 `EnvLitellmModel`：请求时读取 URL、密钥及流式开关，
将流式片段合并成完整响应后，再交给 mini 解析工具调用、计算费用并推进 Agent。
通过 `--config model.model_class=...` 显式选择其他后端时，其环境变量支持由该后端决定。

## 服务端费用

`EnvLitellmModel` 默认优先使用服务端返回的实际费用，不要求模型已经收录在 LiteLLM 价格表中：

1. 读取响应的 `usage.cost`；流式请求从最后的 usage 片段合并得到该字段。
2. 若没有该字段，读取 LiteLLM OpenRouter 适配器保存的
   `llm_provider-x-litellm-response-cost` 服务端费用字段。
3. 若服务端未提供费用，沿用 mini 的 LiteLLM 计价流程；模型价格未注册时仍报错。

OpenRouter 自动返回 `usage.cost`，表示账户实际扣费；
`usage.cost_details.upstream_inference_cost` 是上游推理费用，不能代替账户扣费。
流式响应同样包含费用，无需增加请求或查询账户余额。
参见 [OpenRouter Usage Accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)。

费用直接进入 mini 的逐次调用记录、累计费用和 `--cost-limit` 检查；
因此预算耗尽后会在下一次请求前停止，不会预先预测本次请求的费用。
服务端明确返回的 `0` 是有效费用；负数、非数字和非有限值会报错。
不把 LiteLLM 的 `_hidden_params.response_cost` 当作服务端实际费用，因为它也可能是本地估算值。

继续使用现有 `.env`，无需添加配置：

```bash
python scripts/run_swebench.py --repos pallets/flask --variants repomap --cost-limit 1
```

该命令会调用模型。轨迹中的 assistant 消息包含：

```json
{
  "extra": {
    "cost": 0.012345,
    "cost_source": "server:usage.cost"
  }
}
```

以上数字仅为格式示例。每次推理的累计费用位于轨迹和 `summary.json` 的
`info.model_stats.instance_cost`、`runs[].model_stats.instance_cost`。
只有来自服务端的费用会标记 `cost_source: "server:..."`；回退到原生计价时不带该标记。
请勿通过 `MSWEA_COST_TRACKING=ignore_errors` 获取实际费用：它只会在缺失价格时忽略异常并记为零。

## 使用方法

```bash
# 读取项目 .env，查看将使用的模型和实验命令
python scripts/run_swebench.py --repos pallets/flask --dry-run

# 使用 .env 中的模型配置运行对照，会调用模型
python scripts/run_swebench.py --repos pallets/flask

# 指定另一个环境文件
python scripts/run_swebench.py --env-file /path/to/experiment.env \
  --repos pallets/flask --dry-run

# 命令行覆盖默认模型
python scripts/run_swebench.py --model provider/other-model \
  --repos pallets/flask --dry-run
```

另外两个脚本同样支持 `--env-file`，用于向 Docker、Hugging Face 等依赖传递环境设置；
它们仍只做镜像准备或地图性能测量，不调用模型。

```bash
python scripts/pull_docker.py --env-file /path/to/experiment.env --dry-run
python scripts/run_experiments.py --env-file /path/to/experiment.env --dry-run
```

默认 `.env` 不存在时继续使用进程环境；显式指定的 `--env-file` 不存在时直接报错。
性能实验的子进程继承父进程环境，不重新加载其他环境文件。

## 优先级与配置保存

- 同名变量使用已导出的进程环境值，`.env` 只补充尚未设置的变量。
- 模型选择优先级为 `--model` → `MSWEA_MODEL_NAME` → `MODEL_NAME`。
- 显式 `model.model_kwargs` 配置优先于 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_STREAMING` 提供的请求默认值。
- `.env` 提供的密钥不写入生成的命令、`plan.json` 或模型轨迹配置，API 请求时通过环境读取。
- `.env`、`.env.*` 已加入 Git 忽略规则，源码包只包含 `.env.example`。

这里的自动加载作用于本项目的 `scripts/` 入口。直接执行上游 `mini` 时，继续使用 mini 自身的环境和全局配置机制。

## 依赖兼容性

当前使用 LiteLLM `>=1.105.1,<1.106`。本地验证复现了 1.106.x 在工具调用前加载可选 MCP 依赖失败的问题，
上游记录见 [LiteLLM issue #46045](https://github.com/BerriAI/litellm/issues/46045)。
项目已对普通响应、流式工具调用、环境配置优先级和序列化内容进行本地接口验证。

已验证普通及流式响应中的服务端费用、真实零费用、预算停止和轨迹累计；
服务端未提供费用时，保留 mini 原生的计价与错误处理行为。
