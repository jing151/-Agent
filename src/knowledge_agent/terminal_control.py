"""Progress observations and duplicate-draft limits, not an action policy."""
from __future__ import annotations
import hashlib
import json
import re

from .claim_mapping import compact


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()


class FailedAnswerGuard:
    def __init__(self):
        self.failures: dict[str,int]={}

    def failed(self,args: dict,stage: str,errors: list[str],reads: list[dict]) -> bool:
        refs=[(item["doc_id"],item["version_date"],compact(item["quote"])) for item in args.get("citations",[])]
        def claim_value(item):
            return (item["kind"],compact(item["text"]),tuple(sorted(refs[index] for index in item.get("citation_indices",[]) if index<len(refs))))
        claim_ids={item.get("claim_id",f"legacy_{index}"):claim_value(item) for index,item in enumerate(args.get("claims",[]))}
        segments=sorted((item.get("part","answer"),compact(item["text"]),
            tuple(sorted(claim_ids.get(ref,("unknown_claim",ref)) for ref in item["claim_ids"]))) for item in args.get("answer_segments",[]))
        value={"answer":compact(args["answer"]),"status":args["status"],"references":sorted(refs),
            "claims":sorted(claim_value(item) for item in args.get("claims",[])),"segments":segments,
            "gaps":sorted(compact(item) for item in args.get("gaps",[])),"stage":stage,
            "errors":sorted(re.sub(r"claim_\d+:|segment_[^:]+:","item:",str(item)) for item in errors),
            "read_evidence":sorted({fingerprint({key:doc.get(key) for key in ["doc_id","version_date","section_id","content","scope","priority","supersedes"]}) for doc in reads})}
        signature=fingerprint(value)
        self.failures[signature]=self.failures.get(signature,0)+1
        return self.failures[signature]>=2


class ProgressTracker:
    """Summarize novelty without claiming an attribute has supporting evidence."""
    def __init__(self):
        self.candidates: set[str]=set()
        self.reads: set[str]=set()
        self.no_new_search_count=0

    def observe(self,action: str,observation: dict,*,steps_left: int,tools_left: int) -> dict:
        summary={"new_candidate_fragments":0,"new_read_fragments":0,
            "decisions_remaining":steps_left,"tool_calls_remaining":tools_left}
        if observation["ok"] and action=="SEARCH":
            current={fingerprint({key:item.get(key) for key in ["doc_id","version_date","section_id","snippet","scope","priority","supersedes"]})
                for item in observation["result"]["results"]}
            count=len(current-self.candidates)
            self.candidates.update(current)
            self.no_new_search_count=0 if count else self.no_new_search_count+1
            summary["new_candidate_fragments"]=count
        elif observation["ok"] and action=="READ":
            doc=observation["result"]["document"]
            current=fingerprint({key:doc.get(key) for key in ["doc_id","version_date","section_id","content","scope","priority","supersedes"]})
            summary["new_read_fragments"]=int(current not in self.reads)
            if current not in self.reads:
                self.no_new_search_count=0
            self.reads.add(current)
        summary["no_new_search_count"]=self.no_new_search_count
        if self.no_new_search_count>=2 or steps_left<=2 or tools_left==0:
            summary["notice"]="候选主题相关不等于所问属性有证据。请评估现有证据和剩余校验轮次，避免重复无新增信息的尝试；下一动作由模型自主决定，未知项保持具体缺口。"
        return summary
