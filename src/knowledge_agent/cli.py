from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from .agent import KnowledgeAgent
from .config import AgentConfig, ModelConfig
from .evaluation import evaluate_retrieval
from .llm.mock_llm import MockLLM
from .llm.real_llm import RealLLM
from .schemas import QuestionInput
from .tools.document_reader import DocumentStore
from .tools.document_search import DocumentTools

SCENARIOS: dict[str, dict[str, Any]] = {
    "normal": {"question": "公司设备出现故障，应该在哪里提交报修？"},
    "conflict": {"question": "员工费用报销期限是多少？"},
    "out_of_scope": {"question": "公司今年年终奖具体发放多少钱？"},
    "paraphrase": {"question": "员工垫付费用后最迟多少天需要交票？"},
    "follow_up": {"question": "那手机上也可以操作吗？",
                  "history": [{"role": "user", "content": "公司邮箱忘记密码了怎么办？"}]},
    "tool_failure": {"question": "公司设备出现故障，应该在哪里提交报修？"},
}


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_failures(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        try:
            name, count = value.split(":", 1)
            tool = {"search": "search_documents", "read": "read_document"}[name]
            number = {"once": 1, "always": -1}.get(count)
            if number is None:
                number = int(count)
                if number < 1:
                    raise ValueError
            result[tool] = number
        except (ValueError, KeyError):
            raise ValueError("--fail-tool must be search:once, read:always, or search:<positive integer>") from None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="企业知识问答 Agent；Mock 仅验证离线流程。")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--scenario", choices=list(SCENARIOS))
    source.add_argument("--input", type=Path)
    source.add_argument("--question")
    parser.add_argument("--kb", type=Path, default=Path("data/documents.json"))
    parser.add_argument("--trace", type=Path, help="JSON trace path")
    parser.add_argument("--output", type=Path, help="Full result JSON path")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=6)
    parser.add_argument("--answer-quality", choices=["off", "shadow", "enforce"], default="enforce",
                        help="回答适用性：off关闭附加检查，shadow只记录，enforce反馈错误并消除完整重复的澄清原因（默认）")
    parser.add_argument("--fail-tool", action="append", default=[])
    parser.add_argument("--evaluate-retrieval", action="store_true")
    parser.add_argument("--eval-set", type=Path, default=Path("examples/retrieval/eval_set.json"))
    args = parser.parse_args(argv)
    load_dotenv(override=False)
    model = None
    try:
        if args.evaluate_retrieval:
            data = evaluate_retrieval(args.kb, args.eval_set)
        else:
            config = AgentConfig(args.max_steps, args.max_tool_calls, require_claims=args.mode == "real",
                                 answer_quality_mode=args.answer_quality)
            payload = (json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else
                       {"question": args.question} if args.question else SCENARIOS[args.scenario or "normal"])
            request = QuestionInput.model_validate(payload)
            failures = parse_failures(args.fail_tool)
            if args.scenario == "tool_failure" and not failures:
                failures = {"search_documents": 1}
            store = DocumentStore(args.kb)
            model = RealLLM(ModelConfig.from_env()) if args.mode == "real" else MockLLM()
            result = KnowledgeAgent(model, DocumentTools(store, failures), config).run(request)
            data = result.model_dump(mode="json")
            if args.trace:
                write_json(args.trace, data["trace"])
        if args.output:
            write_json(args.output, data)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        # Business statuses are returned in JSON. Nonzero exit codes are configuration/runtime failures.
        return 0
    except (OSError, ValueError, ValidationError) as exc:
        message = "Input/schema validation failed; inspect input or knowledge-base structure." if isinstance(exc, ValidationError) else str(exc)
        print(json.dumps({"error": type(exc).__name__, "message": message}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if isinstance(model, RealLLM):
            model.close()
