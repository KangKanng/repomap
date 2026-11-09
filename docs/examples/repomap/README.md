# 实际 RepoMap 导出示例

这两份文本来自已完成的 RepoMap 实验，是最终轨迹保留的最后一版地图消息。逐字导出实际 `<repository_map>` 内容，没有重新构建地图或调用模型。

| 仓库 | 实测源码行数 | 地图 tokens | 地图行数 | 列出文件数 | 导出文件 |
| --- | ---: | ---: | ---: | ---: | --- |
| pallets/flask | 17,478 | 1014 | 160 | 17 | [flask-5014.txt](flask-5014.txt) |
| django/django | 352,429 | 1022 | 175 | 20 | [django-10097.txt](django-10097.txt) |

地图由文件路径、带原文件行号的类/函数定义和签名组成；`⋮` 表示省略的代码区间。地图会随任务和最近对话重新排序；此处只展示最终保留的地图，不代表每轮均相同。

实验配置为 `map_tokens=1024`、`refresh_interval=1`，token 数按 `cl100k_base` 计数。源码行数沿用实验 inventory 的受支持源码统计。

[metadata.json](metadata.json) 记录原轨迹路径、消息索引、精确 token 数及导出内容 SHA-256，便于核对。

上级文档：[Repo Map 文档](../../README.md)。
