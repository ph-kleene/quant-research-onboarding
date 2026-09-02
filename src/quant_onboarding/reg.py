"""Research Evaluation Gates (REG) and append-only confirmation governance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class Status(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class Gate:
    code: str
    name: str
    status: Status
    rule_type: str
    evidence: str
    explanation: str
    next_step: str


@dataclass(frozen=True)
class REGReport:
    gates: tuple[Gate, ...]
    research_validity: str
    strategy_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": [{**asdict(gate), "status": gate.status.value} for gate in self.gates],
            "research_validity": self.research_validity,
            "strategy_action": self.strategy_action,
        }


def _status(condition: bool, yellow: bool = False) -> Status:
    return Status.GREEN if condition else (Status.YELLOW if yellow else Status.RED)


def evaluate_reg(evidence: dict[str, Any]) -> REGReport:
    """Evaluate six explicit gates without imposing a profitability requirement."""

    coverage = float(evidence.get("data_coverage", 0.0))
    d = Gate(
        "D",
        "数据",
        _status(coverage >= 0.95, coverage >= 0.85),
        "项目教学决策",
        f"coverage={coverage:.3f}",
        "权限、覆盖、历史股票池和复权数据是否完整",
        "补齐数据或停止研究",
    )
    time_ok = bool(evidence.get("time_order_valid", False)) and not bool(
        evidence.get("future_leak", False)
    )
    t = Gate(
        "T",
        "时序",
        _status(time_ok),
        "方法必需条件",
        f"time_order_valid={time_ok}",
        "所有输入必须满足 usable_from <= signal_at < execution_at",
        "修正时钟和标签后重跑",
    )
    reproducible = bool(evidence.get("reproducible", False))
    p = Gate(
        "P",
        "复现",
        _status(reproducible),
        "方法必需条件",
        f"reproducible={reproducible}",
        "冻结键和确定性复现必须一致",
        "补齐环境/数据/配置证据",
    )
    observations = int(evidence.get("ic_observations", 0))
    s = Gate(
        "S",
        "统计",
        _status(observations >= 36, observations >= 24),
        "项目教学决策",
        f"ic_observations={observations}",
        "证据量与分期统计是否足够",
        "补充样本或停止推进",
    )
    net_effect = float(evidence.get("net_effect", 0.0))
    cost_model_complete = bool(evidence.get("cost_model_complete", True))
    cost_status = (
        Status.RED if not cost_model_complete else _status(net_effect > 0, abs(net_effect) <= 0.01)
    )
    c = Gate(
        "C",
        "成本",
        cost_status,
        "团队未来可配置",
        f"cost_model_complete={cost_model_complete}; net_effect={net_effect:.4f}",
        "成本和受限成交后的效应是否仍有推进价值",
        "补齐受限成交证据、复核成本假设或停止推进",
    )
    robust = bool(evidence.get("robust_across_subperiods", False)) and bool(
        evidence.get("lag_sensitivity_ok", False)
    )
    r = Gate(
        "R",
        "稳健性",
        _status(robust, bool(evidence.get("one_robustness_check_passed", False))),
        "项目教学决策",
        f"robust={robust}",
        "子期、参数和滞后敏感性是否一致",
        "补做稳健性或停止推进",
    )
    gates = (d, t, s, c, r, p)
    invalid = any(gate.status is Status.RED for gate in (d, t, p))
    if invalid:
        validity = "无效：D/T/P 至少一门红灯"
    elif any(gate.status is Status.YELLOW for gate in (d, t, p)):
        validity = "有条件有效：方法门存在黄灯，结论必须降级"
    else:
        validity = "有效：D/T/P 无红灯"
    if invalid:
        action = "停止：先修复研究有效性"
    elif any(gate.status is Status.RED for gate in (s, c, r)):
        action = "停止：当前证据不支持策略推进"
    elif any(gate.status is Status.YELLOW for gate in (s, c, r)):
        action = "补证：完成指定敏感性后再评审"
    else:
        action = "继续：进入下一阶段评审（不代表投资建议）"
    return REGReport(gates, validity, action)


@dataclass
class ResearchPassport:
    confirmation_reveal_count: int = 0
    research_decision_count: int = 0
    reproduction_run_count: int = 0
    contaminated: bool = False
    contamination_reason: str = ""

    def reveal_confirmation(self) -> None:
        self.confirmation_reveal_count += 1
        self.research_decision_count += 1

    def record_reproduction(self) -> None:
        self.reproduction_run_count += 1

    def record_engineering_rerun(self) -> None:
        self.reproduction_run_count += 1

    def change_design_after_reveal(self, reason: str) -> None:
        if self.confirmation_reveal_count:
            self.contaminated = True
            self.contamination_reason = (
                reason or "research design changed after confirmation reveal"
            )
            self.research_decision_count += 1


class ConfirmationLedger:
    """Append-only hash-chained JSONL evidence; records cannot restore an unseen state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line]
        if not lines:
            return "0" * 64
        return str(json.loads(lines[-1])["event_hash"])

    def append(self, event_type: str, payload: dict[str, Any]) -> str:
        previous = self._last_hash()
        event = {
            "event_type": event_type,
            "recorded_at": datetime.now(UTC).isoformat(),
            "previous_hash": previous,
            "payload": payload,
        }
        canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event_hash = hashlib.sha256(canonical.encode()).hexdigest()
        event["event_hash"] = event_hash
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event_hash

    def verify(self) -> bool:
        previous = "0" * 64
        if not self.path.exists():
            return True
        for line in self.path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            claimed = event.pop("event_hash")
            if event.get("previous_hash") != previous:
                return False
            canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            actual = hashlib.sha256(canonical.encode()).hexdigest()
            if actual != claimed:
                return False
            previous = claimed
        return True
