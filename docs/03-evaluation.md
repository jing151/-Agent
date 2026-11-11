# 03 测试预期、实际结果、失败与限制

本包自包含测试结果：[汇总](../examples/tests/summary.json)、[pytest日志](../examples/tests/pytest.txt)、[JUnit](../examples/tests/pytest.xml)、[网络审计](../examples/tests/network.json)。输入、实际输出、Trace和故障配置均保存在下表所链接的目录；没有预填通过结果。

本轮实际29/29通过，网络尝试0、真实API新增0。测试目录完整随包提供，可在解压副本直接复算；历史503项结果单独存档，不与本包29项混算。

## 必须验收的六类场景

| 验收点 | 预期 | 离线实际结果及材料 | 同版Real证据 |
|---|---|---|---|
| 明确答案 | 读取D03，给出报修入口与真实引用 | completed：[输入](../examples/acceptance/normal/input.json) / [输出](../examples/acceptance/normal/output.json) / [Trace](../examples/acceptance/normal/trace.json) | [输出](../examples/real/normal/output.json) / [Trace](../examples/real/normal/trace.json)，通过 |
| 文档冲突 | 读取引用D01/D02，保留30/15天，指出缺口并请求确认，不选15天 | needs_clarification：[输入](../examples/acceptance/conflict/input.json) / [输出](../examples/acceptance/conflict/output.json) / [Trace](../examples/acceptance/conflict/trace.json) | [输出](../examples/real/conflict/output.json) / [Trace](../examples/real/conflict/trace.json)，通过 |
| 资料外问题 | 不编造年终奖金额或公司规则，明确缺口 | insufficient_evidence：[输入](../examples/acceptance/out_of_scope/input.json) / [输出](../examples/acceptance/out_of_scope/output.json) / [Trace](../examples/acceptance/out_of_scope/trace.json) | [输出](../examples/real/out_of_scope/output.json) / [Trace](../examples/real/out_of_scope/trace.json)，通过；空检索后自主改query |
| 改写后的相似问题 | 垫付/交票表达仍检索并处理双方制度 | needs_clarification：[输入](../examples/acceptance/paraphrase/input.json) / [输出](../examples/acceptance/paraphrase/output.json) / [Trace](../examples/acceptance/paraphrase/trace.json) | [实际失败](../examples/real/paraphrase_failed/output.json) / [Trace](../examples/real/paraphrase_failed/trace.json)，8轮未收敛，不能算通过 |
| 上下文追问 | 手机追问结合前面的邮箱历史，独立READ D05 | completed：[输入](../examples/acceptance/follow_up/input.json) / [输出](../examples/acceptance/follow_up/output.json) / [Trace](../examples/acceptance/follow_up/trace.json) | 本次冻结版未新增Real历史追问，当前为Mock验证 |
| 工具失败 | 首次SEARCH故障进入Observation，后续恢复；持续失败能安全停止 | completed：[输入](../examples/acceptance/tool_failure/input.json) / [故障配置](../examples/acceptance/tool_failure/run-config.json) / [输出](../examples/acceptance/tool_failure/output.json) / [Trace](../examples/acceptance/tool_failure/trace.json) | 当前为Mock故障验证，未冒充Real |

Mock与脚本化模型是流程测试替身。六类离线通过不代表真实模型六类全通过；真实口语改写的失败仍然存在。

## 参考实例与审核要点

| 检查 | 实际材料 | 测试 |
|---|---|---|
| 只有D01、无冲突时回答30天 | [输入](../examples/acceptance/single_d01/input.json) / [单文档KB](../data/variants/documents_d01_only.json) / [输出](../examples/acceptance/single_d01/output.json) / [Trace](../examples/acceptance/single_d01/trace.json) | `test_single_D01_has_no_invented_conflict` |
| 替换原文，核对答案/引用/动作 | [相同输入](../examples/acceptance/replacement_before/input.json) / [前输出](../examples/acceptance/replacement_before/output.json) / [前Trace](../examples/acceptance/replacement_before/trace.json) / [后输出](../examples/acceptance/replacement_after/output.json) / [后Trace](../examples/acceptance/replacement_after/trace.json) / [独立对照](../examples/acceptance/document-replacement-comparison.json) | `test_document_replacement_changes_actual_answer_quote_and_action_arguments` |
| 片段读取及授权范围 | [有效片段输出](../examples/acceptance/section_read/output.json) / [越过未读章节的拒绝记录](../examples/acceptance/section_unread/output.json) | `test_section_read_grants_only_the_returned_fragment` |
| 不存在的引文、错误版本、未READ引用、无依据事实被拒 | [虚假引文](../examples/acceptance/invalid_invented_quote/output.json) / [错误版本](../examples/acceptance/invalid_wrong_version/output.json) / [未READ](../examples/acceptance/invalid_unread/output.json) / [无依据声明](../examples/acceptance/invalid_unsupported_claim/output.json) | `test_invalid_citations_and_unsupported_answer_never_reach_user` |
| 校验反馈后由模型修正下一动作 | [输入](../examples/acceptance/citation_repair/input.json) / [输出](../examples/acceptance/citation_repair/output.json) / [Trace](../examples/acceptance/citation_repair/trace.json) | `test_validator_feedback_allows_model_to_repair_its_next_action` |
| 执行上限与无法恢复 | [步骤上限](../examples/acceptance/step_limit/output.json) / [工具上限](../examples/acceptance/tool_limit/output.json) / [持续READ故障](../examples/acceptance/persistent_read_failure/output.json) | `test_limits_and_unrecoverable_failure_are_explicit` |
| 检索基线对比与逐题排名 | [实际指标](../examples/acceptance/retrieval/metrics.json) | `test_retrieval_baseline_report_is_recomputable_offline` |
| 默认检索器D与A/B/C四系统对照 | [24题逐题结果](../examples/acceptance/retrieval-default-d/metrics.json) / [固定数据](../examples/retrieval/diagnostic-24.json) | `test_default_D_retrieval_comparison_uses_frozen_public_diagnostic_labels` |

替换D05后，移动端可操作变为“移动端暂不支持”，答案、引文、READ返回及ANSWER参数必须变化。工具动作序列可能仍是SEARCH/READ/READ/ANSWER；对照文件分别记录序列是否改变，不把动作参数变化夸大为工具次数或真实策略变化。当前替换检查为Mock。

12题对照验证原CLI保留的BM25/C评测入口；问答默认D另提供A/B/C/D在24条已公开诊断题上的实际复算。该24题原为历史预冻结留出，现已公开，本次没有改标签或调参，不重新宣称新盲测。结果口径、低精确率、负例误召回及未完整召回的冲突均保留，不能当作生产泛化结论。

| 24题诊断指标 | A原BM25 | B原混合 | C前期优化 | D当前默认 |
|---|---:|---:|---:|---:|
| Recall@3（17条正例宏平均） | 0.5882 | 0.7647 | 0.7059 | 0.7647 |
| Precision@3（分母固定3） | 0.2549 | 0.3529 | 0.3333 | 0.3529 |
| 冲突双方完整召回 | 2/6 | 4/6 | 4/6 | 4/6 |
| 负例误召回（越低越好） | 5/7 | 6/7 | 6/7 | 5/7 |

D相对原BM25提高了这批诊断题的召回和冲突完整召回，但仍漏掉2组冲突且5条负例误召回。结果没有因高分期待而改写。

## 真实失败与统计

两版合计50次HTTP、210200 total tokens，均在授权内；原版10次，修复版40次。修复版启动7/24个增强场景，6通过、R04口语改写未通过，17未执行；所有账本关闭，未再开启第二轮修复。完整档案见[运行索引](../examples/review-index.json)，原研究汇总见[快照](../examples/evidence/post-revalidation-summary.json)。快照内路径来自原工作区，本包实际位置以运行索引为准。

R04已经READ双方，但连续5个澄清候选被声明/分段/控制问句覆盖或有限推论规则拒绝，最终返回明确未完成；没有输出错误引用或按日期选边。R12也耗满8轮且有冗余UNKNOWN。只能说明正常、冲突、资料外各一次真实路径成立，不能证明完整稳定性、成功动态恢复或真实文档替换。

原工作区503项测试与本包自包含测试不是同一计数，历史材料保留于[研发基线](../examples/tests/research-baseline/workspace-tests.txt)。Python 3.11旧历史指标精确比较有末位浮点差异，见[原始日志](../examples/tests/research-baseline/python311-tests.txt)，没有改历史数值或断言制造成功。
