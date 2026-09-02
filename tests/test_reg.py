import json

from quant_onboarding.reg import ConfirmationLedger, ResearchPassport, Status, evaluate_reg


def valid_evidence(**overrides):
    evidence = {
        "data_coverage": 0.99,
        "time_order_valid": True,
        "future_leak": False,
        "reproducible": True,
        "ic_observations": 60,
        "net_effect": 0.02,
        "cost_model_complete": True,
        "robust_across_subperiods": True,
        "lag_sensitivity_ok": True,
    }
    evidence.update(overrides)
    return evidence


def test_reg_green_case_and_two_layer_judgment():
    report = evaluate_reg(valid_evidence())
    assert all(gate.status is Status.GREEN for gate in report.gates)
    assert report.research_validity.startswith("有效")
    assert report.strategy_action.startswith("继续")


def test_d_t_or_p_red_invalidates_research():
    for override in (
        {"data_coverage": 0.5},
        {"future_leak": True},
        {"reproducible": False},
    ):
        report = evaluate_reg(valid_evidence(**override))
        assert report.research_validity.startswith("无效")
        assert report.strategy_action.startswith("停止")


def test_s_c_or_r_red_can_be_credible_stop_not_invalid():
    report = evaluate_reg(
        valid_evidence(
            ic_observations=5,
            net_effect=-0.03,
            robust_across_subperiods=False,
            lag_sensitivity_ok=False,
        )
    )
    assert report.research_validity.startswith("有效")
    assert report.strategy_action.startswith("停止")


def test_missing_execution_evidence_blocks_strategy_not_research_validity():
    report = evaluate_reg(valid_evidence(cost_model_complete=False))
    assert report.research_validity.startswith("有效")
    assert next(gate for gate in report.gates if gate.code == "C").status is Status.RED
    assert report.strategy_action.startswith("停止")


def test_passport_distinguishes_reveal_reproduction_and_contamination():
    passport = ResearchPassport()
    passport.record_reproduction()
    assert passport.research_decision_count == 0
    passport.reveal_confirmation()
    passport.record_engineering_rerun()
    passport.change_design_after_reveal("changed factor weight after viewing confirmation")
    assert passport.confirmation_reveal_count == 1
    assert passport.reproduction_run_count == 2
    assert passport.research_decision_count == 2
    assert passport.contaminated
    assert passport.contamination_reason


def test_confirmation_ledger_is_hash_chained_and_tamper_evident(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = ConfirmationLedger(path)
    ledger.append("freeze", {"config_hash": "abc"})
    ledger.append("reproduction", {"same_inputs": True})
    assert ledger.verify()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["payload"]["config_hash"] = "tampered"
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    assert not ledger.verify()
