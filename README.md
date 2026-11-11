# Enterprise Knowledge Q&A Agent

面向员工制度与FAQ的原生Python ReAct CLI。模型根据SEARCH/READ的Observation重新选择补检、读取、追问或结束；最多8轮决策、6次工具尝试。数据为7份带ID、版本日期的合成资料。

- [逐项题面核对](docs/06-assignment-checklist.md)
- [六类验收：预期、实际结果与输入/输出/Trace](docs/03-evaluation.md)
- [本包测试汇总](examples/tests/summary.json) / [pytest原始日志](examples/tests/pytest.txt) / [JUnit](examples/tests/pytest.xml)
- [已保存真实运行索引](examples/review-index.json) / [已知真实失败](examples/real/paraphrase_failed/output.json)

## 环境、安装与测试

Python 3.11+，实际独立安装验证环境为Python 3.13.13。无需数据库、GPU、前端或Agent框架。PowerShell在本目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install --no-deps -e .
.venv\Scripts\python.exe scripts/verify_submission.py --output-dir examples/local-review-1
```

最后一条命令运行`tests/`，阻断网络并保存实际输入、输出、Trace、pytest日志、JUnit与网络审计。每次使用新目录，避免覆盖记录。也可直接运行：

包内已保存本轮29/29通过的结果；历史503项测试另列，不混用计数。

```powershell
.venv\Scripts\python.exe -m pytest -q tests --artifacts-dir examples/local-pytest-1 --junitxml=examples/local-pytest-1/pytest.xml
```

Linux/macOS将解释器路径换为`.venv/bin/python`。首次安装依赖需要联网；所有提交测试无须密钥、不发送真实API请求。包内测试自包含，不依赖原工作区数千份历史实验文件。

## CLI与输入输出

```powershell
.venv\Scripts\python.exe -m knowledge_agent --mode mock --input examples/inputs/normal.json --output examples/local-normal.json --trace examples/local-normal-trace.json
.venv\Scripts\python.exe -m knowledge_agent --mode mock --input examples/inputs/conflict.json --answer-quality enforce
.venv\Scripts\python.exe -m knowledge_agent --mode mock --input examples/inputs/tool_failure.json --fail-tool search:once
.venv\Scripts\python.exe -m knowledge_agent --mode mock --input examples/inputs/follow_up.json --kb examples/variants/documents_v2.json
```

输入包含`question`与可选`history`（user/assistant）。输出包含`answer`、`citations`（文档ID、版本、原文片段）、`gaps`、`status`、`claims`、`evidence_review`和逐轮`trace`。业务状态的CLI退出码为0，应检查JSON中的status；预算耗尽明确返回`max_steps_reached`，失败草稿不作为最终答案。

CLI默认`enforce`。库的兼容默认是shadow，正式严格调用应显式使用`AgentConfig(8,6,True,"enforce")`。工具失败示例的输入是问题与历史，故障参数另存于对应`run-config.json`；用输入文件复算时须显式传`--fail-tool search:once`。

## 效果与边界

六类题目验收及单D01、片段读取权限、引用负例、文档替换等均有自包含离线测试。正常报修、双制度冲突、资料外金额还各有同一冻结版的真实输入/输出/Trace。真实资料外路径在连续空检索后自主改变query，证明实际循环中存在观察后的动作调整。

Mock和脚本化模型只验证流程，不能证明真实模型泛化。真实口语改写仍有一次8轮未收敛，已完整保留。完整24场景增强矩阵未完成，动态恢复、三次稳定性和真实文档替换没有取得完整证据。本次整理没有修改运行时、关闭严格检查、重写失败或新增API调用。

累计真实50次请求后按授权止损，所有账本关闭。工作区历史503项结果另存于[研发基线](examples/tests/research-baseline/workspace-tests.txt)，与本包自包含测试计数分开。

## 结构与提交信息

`src/`代码；`tests/`自包含测试；`examples/acceptance/`实际验收输入输出/Trace；`examples/tests/`测试结果；`examples/real/`代表性真实路径；`examples/real-archive/`原始实测档案；`data/`合成资料；`docs/01`至`06`为需求、设计、评估、AI披露、复盘与题面核对。

题号3：企业知识问答 Agent。本人投入约4小时，主要负责需求确认、交付范围与验证要求；框架搭建、实现和实验执行由AI辅助完成，具体分工见[AI使用与本人贡献](docs/04-ai-usage.md)。仓库：[jing151/-Agent](https://github.com/jing151/-Agent)；公开可读。简短说明见[提交说明](SUBMISSION.md)，最终commit SHA使用`git rev-parse HEAD`获取。

`.env.example`给出真实模式所需变量，本包没有实际密钥。现成真实材料已随包保存，默认运行与测试均离线。
