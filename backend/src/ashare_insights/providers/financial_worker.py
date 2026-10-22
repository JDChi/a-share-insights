"""Private subprocess entrypoint: stdout contains only the result envelope."""

import contextlib
import json
import sys

from ashare_insights.providers.financial import EM_FUNCTIONS, SINA_TABLES, market_symbol


def main():
    request = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import akshare as ak
            import pandas as pd

            source, code, table = (
                request["source"],
                request["code"],
                request["statement"],
            )
            symbol = market_symbol(code)
            if source == "eastmoney":
                frame = getattr(ak, EM_FUNCTIONS[table])(symbol=symbol)
            elif source == "sina":
                frame = ak.stock_financial_report_sina(
                    stock=symbol.lower(), symbol=SINA_TABLES[table]
                )
            else:
                raise ValueError("unknown source")
            rows = [
                {str(k): None if pd.isna(v) else str(v) for k, v in row.items()}
                for row in frame.to_dict(orient="records")
            ]
        result = dict(rows=rows)
    except Exception as exc:
        result = dict(error=type(exc).__name__)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
