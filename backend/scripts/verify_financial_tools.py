"""Live 15-company probe. No PDF downloads or database writes."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from ashare_insights.config import get_settings
from ashare_insights.providers.financial import FinancialService, METRICS

COMPANIES = [
    ("SH", "600519", "贵州茅台"),
    ("SH", "600887", "伊利股份"),
    ("SH", "600900", "长江电力"),
    ("SH", "601012", "隆基绿能"),
    ("SH", "600309", "万华化学"),
    ("SZ", "000333", "美的集团"),
    ("SZ", "000651", "格力电器"),
    ("SZ", "000858", "五粮液"),
    ("SZ", "002594", "比亚迪"),
    ("SZ", "300750", "宁德时代"),
    ("BJ", "920000", "安徽凤凰"),
    ("BJ", "920001", "纬达光电"),
    ("BJ", "920002", "万达轴承"),
    ("BJ", "920003", "中诚咨询"),
    ("BJ", "920010", "凯添燃气"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output must not exist")
    args.output.mkdir(parents=True)
    service = FinancialService(get_settings())
    results = []
    for market, code, name in COMPANIES:
        result = service.query(code, args.year, list(METRICS))
        entry = dict(market=market, code=code, name=name, result=result)
        results.append(entry)
        (args.output / "results.json").write_text(
            json.dumps(
                dict(
                    run_at=datetime.now(UTC).isoformat(),
                    year=args.year,
                    companies=results,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            market,
            code,
            result["status"],
            len(result["items"]),
            result["missing"],
            flush=True,
        )
    lines = [
        "# 财务工具真实接口验证",
        "",
        "|市场|代码|公司|状态|指标数/9|缺失|",
        "|---|---|---|---|---|---|",
    ]
    for entry in results:
        r = entry["result"]
        lines.append(
            f"|{entry['market']}|{entry['code']}|{entry['name']}|{r['status']}|{len(r['items'])}/9|{','.join(r['missing'])}|"
        )
    lines += [
        "",
        "这是单次接口观测，不代表持续稳定性。失败、缺失及逐来源尝试见 results.json。",
    ]
    (args.output / "REPORT.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
