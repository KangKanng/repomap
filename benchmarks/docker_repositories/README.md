# Docker 仓库样本

通过 `docker pull` 拉取的 SWE-bench 官方源码镜像，每个镜像对应一个不同的项目。
源码保留在镜像内的 `/testbed`，无需 clone 到宿主机。

按源码物理行数从小到大排序。统计 Git tracked 源码，包含测试、注释、空行及被跟踪的第三方代码；不等于去注释的 SLOC。
`image_bytes` 是 Docker 报告的镜像逻辑大小，共享层不能直接累加为实际磁盘占用。

| # | 仓库 | 行数最多的语言 | 源码文件 | 源码行数 | 镜像大小 GiB | 实例 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 1 | [axios/axios](https://github.com/axios/axios) | JavaScript | 114 | 12,686 | 2.51 | `axios__axios-4731` |
| 2 | [psf/requests](https://github.com/psf/requests) | Python | 70 | 13,679 | 3.55 | `psf__requests-1142` |
| 3 | [gin-gonic/gin](https://github.com/gin-gonic/gin) | Go | 75 | 13,935 | 1.06 | `gin-gonic__gin-1805` |
| 4 | [pallets/flask](https://github.com/pallets/flask) | Python | 80 | 17,478 | 3.94 | `pallets__flask-5014` |
| 5 | [sharkdp/bat](https://github.com/sharkdp/bat) | JavaScript | 87 | 21,496 | 2.88 | `sharkdp__bat-1892` |
| 6 | [jekyll/jekyll](https://github.com/jekyll/jekyll) | Ruby | 148 | 22,127 | 1.65 | `jekyll__jekyll-8047` |
| 7 | [google/gson](https://github.com/google/gson) | Java | 194 | 36,759 | 1.54 | `google__gson-1014` |
| 8 | [preactjs/preact](https://github.com/preactjs/preact) | JavaScript | 210 | 37,908 | 2.69 | `preactjs__preact-2757` |
| 9 | [burntsushi/ripgrep](https://github.com/burntsushi/ripgrep) | Rust | 86 | 41,249 | 2.52 | `burntsushi__ripgrep-2209` |
| 10 | [jqlang/jq](https://github.com/jqlang/jq) | C/C++ | 77 | 45,247 | 0.81 | `jqlang__jq-2235` |
| 11 | [caddyserver/caddy](https://github.com/caddyserver/caddy) | Go | 213 | 54,265 | 2.01 | `caddyserver__caddy-4774` |
| 12 | [fmtlib/fmt](https://github.com/fmtlib/fmt) | C/C++ | 69 | 67,663 | 0.76 | `fmtlib__fmt-1683` |
| 13 | [tokio-rs/tokio](https://github.com/tokio-rs/tokio) | Rust | 550 | 108,282 | 2.02 | `tokio-rs__tokio-4384` |
| 14 | [redis/redis](https://github.com/redis/redis) | C/C++ | 572 | 245,197 | 0.96 | `redis__redis-10068` |
| 15 | [rubocop/rubocop](https://github.com/rubocop/rubocop) | Ruby | 1,551 | 283,279 | 1.58 | `rubocop__rubocop-13362` |
| 16 | [briannesbitt/carbon](https://github.com/briannesbitt/carbon) | PHP | 1,953 | 313,470 | 1.05 | `briannesbitt__carbon-2665` |
| 17 | [django/django](https://github.com/django/django) | Python | 2,559 | 352,429 | 4.11 | `django__django-10097` |
| 18 | [laravel/framework](https://github.com/laravel/framework) | PHP | 2,015 | 354,539 | 1.20 | `laravel__framework-46234` |
| 19 | [sympy/sympy](https://github.com/sympy/sympy) | Python | 1,106 | 446,166 | 3.65 | `sympy__sympy-11618` |
| 20 | [apache/lucene](https://github.com/apache/lucene) | Java | 5,289 | 1,188,071 | 2.25 | `apache__lucene-11760` |

版本核对：16 个镜像的 HEAD 与数据集 base commit 相同；另外 4 个镜像包含额外构建提交，HEAD 哈希不同，但源码树 SHA 与 base commit 一致。实际 HEAD、提交说明、源码树 SHA 和数据集 base commit 已分别记录。

完整镜像名、固定 digest、仓库 commit、分语言行数和拉取日志见 [inventory.json](inventory.json)。

再次拉取并核对：

```bash
python scripts/pull_docker.py
```

只核对本地镜像：

```bash
python scripts/pull_docker.py --inspect-only
```

来源：[SWE-bench Multilingual](https://huggingface.co/datasets/SWE-bench/SWE-bench_Multilingual)、[SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)。
