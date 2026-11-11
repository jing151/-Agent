# 02 调用流程、动态决策与取舍

CLI → QuestionInput → KnowledgeAgent → 配置模型。模型逐轮选择SEARCH、READ、ASK_CLARIFICATION或ANSWER；工具结果与校验错误写入Observation，连同问题、真实历史和剩余预算进入下一请求。程序分发动作、限制预算和校验证据，不安排真实模型固定业务步骤或自动重试。

采用原生Python循环，便于解释控制边界，不使用Agent框架。httpx接入兼容Chat Completions；Pydantic验证边界；jieba/BM25、字符特征和业务概念路由用于本地检索；pytest负责提交测试。依赖版本见[锁文件](../requirements-lock.txt)。没有依赖框架名称代替决策证据。

最多8轮决策、6次工具尝试，失败也计数。无效动作和校验拒绝回流下一轮；HTTP没有自动重试或Mock降级。`completed`必须有成功READ授权的引用；章节READ只授权实际返回片段。声明覆盖、证据支持和适用性分层检查，仍不是通用语义蕴含证明。

## 至少两条真实决策路径

| 真实记录 | 观察后的实际变化 |
|---|---|
| [正常报修Trace](../examples/real/normal/trace.json) | SEARCH得到设备文档→READ→提出回答；校验反馈后模型自行修改声明映射并结束 |
| [资料外Trace](../examples/real/out_of_scope/trace.json) | SEARCH为空→模型换query继续SEARCH→仍无资料→结束为insufficient_evidence |
| [冲突Trace](../examples/real/conflict/trace.json) | 检索并读取双方→模型处理30/15天差异→列缺口并实际询问适用制度 |

这些是已保存真实请求，具有response_id、原始合法动作、输入上下文SHA和usage；不含完整内部思维链。`tests/`中的脚本化动作测试仅验证执行骨架，不能替代上述Real证据。

冲突依据适用范围、优先级与替代关系核对，日期更新不决定优先级。知识库每次运行重新加载、运行内保持快照；工具片段不授予引用权限，READ后再校验版本与逐字匹配。文档替换的动作序列、动作参数与工具返回分别对比，详见[替换记录](../examples/acceptance/document-replacement-comparison.json)。
