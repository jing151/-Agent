# Enterprise Knowledge Q&A Agent 提交说明

- 项目名称：Enterprise Knowledge Q&A Agent（企业知识问答 Agent）。
- 选题：题号3，企业知识问答 Agent。
- GitHub：https://github.com/jing151/-Agent
- 最终commit SHA：运行 `git rev-parse HEAD` 获取，实际值在本次交付回复中提供。
- 测试：29项独立测试，见 `examples/tests/summary.json`；运行方式见README。
- 真实结果及已知限制：见 `docs/03-evaluation.md`，不宣称真实改写场景全部通过。
- 本人实际投入：约4小时。本人负责需求确认、交付范围与验证要求，AI辅助框架搭建、实现和实验执行；详见[本人贡献与AI使用](docs/04-ai-usage.md)。

## 简短说明与思路

项目围绕“答案必须有原文依据”实现企业制度问答。采用原生Python ReAct循环，将检索与原文读取分开，让模型根据工具结果决定补检、读取、追问或结束。面对30天与15天等制度冲突，保留双方引用并确认适用范围；证据不足时说明资料缺口，达到执行上限时返回未完成状态。

交付重点是可复制的CLI命令、六类场景的输入/输出/Trace及可执行测试。29项离线测试通过，已有真实模型路径和失败记录可查；真实改写泛化与完整增强验收仍有不足，后续优先改善语义校验与收敛能力。

SHA不能作为其自身提交内容固定填写；评审可用仓库当前commit与本次交付回复交叉核对。
