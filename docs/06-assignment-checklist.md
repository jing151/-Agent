# 06 企业知识问答 Agent题面逐项核对

本页根据用户提供的正式题面核对，题号3，题名为“企业知识问答 Agent”。基础功能、离线验收、真实决策证据与真实可靠性分别列出。

| 项目要求 | 实现/材料 | 当前结论 |
|---|---|---|
| 帮助员工找到制度或产品问题的可靠答案 | [需求](01-requirements.md)、[评估](03-evaluation.md) | 已实现证据约束；保留真实改写误拒与不收敛限制，不宣称生产级可靠 |
| 问题与可选对话历史 | [CLI输入示例](../examples/inputs/follow_up.json)、`test_history_enters_model_context_without_becoming_policy_evidence` | 已有输入与实际模型上下文检查 |
| 带文档ID、版本日期的合成FAQ/制度 | [原知识库](../data/documents.json) | 7份合成资料，未提交真实隐私资料 |
| 文档检索与片段读取工具 | [设计](02-solution.md)、`test_section_read_grants_only_the_returned_fragment` | SEARCH返回片段，READ支持章节；越过未读章节的引用被拒 |
| 回答、引用ID和原文片段、缺口、任务状态 | [正常输出](../examples/acceptance/normal/output.json)、[资料外输出](../examples/acceptance/out_of_scope/output.json) | 字段齐备，引用独立核对本轮成功READ与版本 |
| 模型观察后补检、读取、追问或结束 | [至少两条真实路径](02-solution.md)、[资料外Real Trace](../examples/real/out_of_scope/trace.json) | 有真实空检索后改query及正常/冲突不同动作路径；没有固定真实业务动作序列 |
| 冲突说明并查证，不凭空选答案 | [冲突Real输出](../examples/real/conflict/output.json)、[Trace](../examples/real/conflict/trace.json) | 读取引用双方，保留30/15天并请求确认，未按日期选15天 |
| 六类必须验收 | [六类对照表](03-evaluation.md) | 六类离线输入/实际输出/Trace齐备；真实改写失败、真实历史与故障未新增验证，模式逐项标明 |
| 仅D01且无冲突时可回答30天 | [输出](../examples/acceptance/single_d01/output.json)、[Trace](../examples/acceptance/single_d01/trace.json) | 独立单文档KB测试，不制造冲突 |
| 引用支持回答；错误引用属于关键缺陷 | [评估负例](03-evaluation.md)、`test_invalid_citations_and_unsupported_answer_never_reach_user` | 测试拦截虚假片段、错版本、未READ与无依据声明；不是一般语义蕴含保证 |
| 替换文档内容，核对答案与动作 | [对照文件](../examples/acceptance/document-replacement-comparison.json) | 分别记录答案、引文、READ返回、ANSWER参数及动作序列变化；未冒充Real替换实验 |
| 检索改进与冲突处理 | [默认D四系统逐题对照](../examples/acceptance/retrieval-default-d/metrics.json)、[设计](02-solution.md) | 原生BM25/字符/概念路由及冲突元信息处理；公开同源诊断题的指标不代表新盲测或生产泛化 |

## 通用提交规范

`src/`、`tests/`、`examples/`、`data/`、README、五份核心文档、依赖与`.env.example`齐备。测试结果、真实/Mock Trace都在`examples/`，评估文档链接对应文件。运行命令可复制，执行上限及未完成状态有测试；真实记录含步骤、参数、返回、后续动作与简要依据，不含内部思维链。

题号3与本人约4小时投入已确认，实际分工见[AI使用与本人贡献](04-ai-usage.md)。仓库为[jing151/-Agent](https://github.com/jing151/-Agent)，公开可读；实际commit SHA使用`git rev-parse HEAD`获取。完整24场景增强矩阵未通过，不能据本包宣称全部真实效果通过。
