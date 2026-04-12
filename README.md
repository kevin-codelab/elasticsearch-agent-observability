# elasticsearch-agent-observability

> 给一个 agent 项目快速搭起可观测底座：先看懂结构，再生成配置、应用 ES 资产、初始化写索引，并直接产出第一份报表。

## 这个项目是干什么的

很多 agent 项目不是不需要 observability，而是第一步太容易卡住。

你明明知道迟早要看这些东西：

- 调用次数
- 耗时分布
- 错误类型
- token 和成本
- 哪些 tool 最容易出问题

但真要开始做时，问题马上变成一串碎活：Collector 怎么配、索引模板怎么建、生命周期怎么设、首个写索引怎么起、报表要查什么。

`elasticsearch-agent-observability` 做的不是完整平台，而是把最难开始的那几步一次性搭起来：
**先看你的项目结构，再替你生成配置、应用 ES 资产、初始化写索引，并直接产出第一份报表。**

## 你会在什么场景用它

- 你已经有一个 agent 或 skill 项目，但出了问题时很难知道卡在哪
- 你准备把运行数据接到 Elasticsearch 9.x
- 你不想从零手写一堆 Collector YAML、模板、生命周期和查询配置
- 你希望第一次就把“生成 -> apply -> 查报表”这一条链路跑通

## 你真正会得到什么

跑完之后，你会拿到一套可继续修改、也可以立即落地的产物：

- 一份项目结构识别结果，知道它看懂了什么
- 一份 Collector 配置草稿，知道数据准备怎么采
- 一套 Elasticsearch 资产，知道数据准备怎么落
- 一份 apply 结果，知道模板、pipeline、ILM 和写索引是否已经进集群
- 一份运行脚本和 agent 环境变量模板，知道 Collector 和 agent 该怎么接起来
- 一份报表配置和第一份报表，知道查询链路已经通没通
- 一份摘要说明，知道哪里靠谱、哪里要小心

这就是它的价值：
**不是只替你生成文件，而是替你把可观测闭环真正推到“能验证”的状态。**

## 3 分钟跑通

第一次上手，先别研究术语，先直接跑完整 happy path。

```bash
python scripts/bootstrap_observability.py \
  --workspace /path/to/your-agent \
  --output-dir generated/bootstrap \
  --es-url http://localhost:9200 \
  --apply-es-assets
```

你只需要替换两处：

- `/path/to/your-agent`：你的 agent 项目目录
- `http://localhost:9200`：你的 Elasticsearch 地址

这个命令**不会修改你的 agent 源码**，但会在你显式加 `--apply-es-assets` 时把生成出来的 Elasticsearch 资产真正推到目标集群。

## 第一次跑完，先看什么

优先看这三个文件：

- `generated/bootstrap/bootstrap-summary.md`
- `generated/bootstrap/discovery.json`
- `generated/bootstrap/report.md`

第一次使用时，不要先追求“监控体系是不是完整”，先看两件事：

- **它有没有基本看懂你的项目**
- **报表链路有没有真的跑通**

如果它识别出了主要模块，并且已经能产出第一份报表，这一步就已经从“做一半”跨到了“可以验证”。

## 你会看到哪些产物

```text
generated/bootstrap/
├── discovery.json
├── otel-collector.generated.yaml
├── run-collector.sh
├── agent-otel.env
├── report.md
├── elasticsearch/
│   ├── index-template.json
│   ├── ingest-pipeline.json
│   ├── ilm-policy.json
│   ├── report-config.json
│   └── apply-summary.json
└── bootstrap-summary.md
```

可以把它们理解成：

- `discovery.json`：它觉得你的项目里有哪些关键模块
- `otel-collector.generated.yaml`：数据准备怎么采
- `run-collector.sh`：Collector 怎么直接启动
- `agent-otel.env`：agent 进程该带哪些 OTLP 环境变量
- `index-template.json`：数据准备怎么存
- `ingest-pipeline.json`：入库前做哪些清洗
- `ilm-policy.json`：数据保留多久、什么时候滚动
- `report-config.json`：报表准备怎么查
- `apply-summary.json`：ES 资产是否已经真正推到集群
- `report.md`：当前查询链路是否能跑出第一份结果
- `bootstrap-summary.md`：给人看的结果摘要和告警

## 如果第一次结果不对，通常先查什么

优先看 `bootstrap-summary.md` 的提示，常见情况有这些：

- `Discovery reached the --max-files limit`：项目太大，可能没扫全
- `No monitorable modules were detected`：路径不对，或者当前启发式没有识别出来
- `credentials were not written to disk`：不是报错，是在提醒你当前采用了更安全的默认模式
- 如果你显式使用了 `--embed-es-credentials`：把生成的 YAML 当成敏感文件处理
- 如果 `report.md` 还是空的：先看 Collector 是否启动，以及 agent 是否真的把 OTLP 数据发出来了

## 一个重要约定

当前默认契约下，logs 和 traces 都会写入同一个 alias：`<index-prefix>-events`。

简单理解就是：
**生成出来的配置默认都走同一个统一入口。**

这样做的好处是 Collector、模板、生命周期和报表查询更容易保持一致，不容易出现“写得进去、但报表查不到”的错位。

## 它适合你，如果

- 你有一个 agent 或 skill 项目
- 你已经有 Elasticsearch 9.x，或者准备接 Elasticsearch 9.x
- 你想先拿到一套能继续改、也能立刻验证的初稿，而不是从零开荒

## 它不适合你，如果

- 你想要一个现成的在线观测平台 UI
- 你希望它自动安装 Collector 二进制、自动改你的 agent 代码、再把整套平台全包掉
- 你还没有 Elasticsearch，也不打算接 Elasticsearch

## 当前版本的边界

当前版本解决的是 **bootstrap + apply + first report**，不是完整 observability 平台。

它会做这些事：

- 扫描项目结构
- 生成 Collector 配置
- 生成 Elasticsearch 资产
- 把资产真正 apply 到 Elasticsearch
- 初始化首个写索引
- 生成第一份报表
- 生成 Collector 启动脚本和 agent 环境变量模板

它现在不会替你做这些事：

- 自动安装 Collector 二进制本体
- 自动改写你的 agent 代码并替你植入 SDK
- 自动接管历史数据
- 提供完整在线 trace UI
- 替代 Langfuse / Phoenix 这类平台的工作台

## 默认安全策略

这部分很重要，因为默认值就是产品态度：

- 如果你传了 `--es-user` / `--es-password`，默认也**不会**把凭据直接写进 YAML
- 默认会写成环境变量占位：`${env:ELASTICSEARCH_USERNAME}` / `${env:ELASTICSEARCH_PASSWORD}`
- 只有显式加 `--embed-es-credentials`，才会把凭据内嵌进生成文件
- 默认会删除 `gen_ai.prompt`、tool 参数、tool 结果，尽量避免把敏感内容原样落盘

## 目标环境

| 环境 | 状态 |
|------|------|
| 自建 Elasticsearch 9.x | ✅ 支持 |
| 腾讯云 Elasticsearch Service 9.x | ✅ 支持 |
| 其他托管环境 | 🔜 后续扩展 |

## 仓库结构

```text
SKILL.md              agent 行为协议
scripts/              发现、生成、apply、报表脚本
references/           配置说明、字段说明、报表说明
generated/            产出目录（默认不提交）
```

## 一个实用建议

第一次不要试图“把整套监控一次性上完”。

先做这三件事：

1. 跑 `bootstrap_observability.py --apply-es-assets`
2. 启动 `run-collector.sh`
3. 看 `report.md` 里是不是已经有第一份结果

先确认“它有没有看懂你的项目，而且链路真的通了”，再继续做深度定制，会轻松很多。
