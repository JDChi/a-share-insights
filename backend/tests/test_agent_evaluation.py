import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent_evaluation", Path(__file__).parents[1] / "scripts/evaluate_agent.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_amount_units_rounding_and_negative_values():
    assert evaluation.supported_money_mentions("约736.39亿元", ["73638840000"])
    assert evaluation.supported_money_mentions("净额-12.34万元", ["-123400"])
    assert not evaluation.supported_money_mentions("736.39万元", ["73638840000"])
    assert not evaluation.supported_money_mentions("123400元", ["-123400"])


def test_frozen_agent_dataset_categories_and_old_rag_unchanged():
    fixture = json.loads(
        (Path(__file__).parents[1] / "evaluations/agent-maotai-2024.json").read_text()
    )
    assert len(fixture["questions"]) == 20
    assert {
        c: sum(q["category"] == c for q in fixture["questions"])
        for c in ("rag", "api", "unavailable")
    } == dict(rag=8, api=8, unavailable=4)
    assert all(
        "expected_value" in q for q in fixture["questions"] if q["category"] == "api"
    )
