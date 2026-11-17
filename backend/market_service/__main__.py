"""``python -m market_service [serve|seed|sync]``."""
from __future__ import annotations

import argparse
import json
import sys

from .errors import MarketError
from .settings import get_settings


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m market_service", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="启动 HTTP 服务（默认）")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)
    commands.add_parser("seed", help="把仓库已有的行情与旧新闻导入空的数据仓库")
    sync = commands.add_parser("sync", help="在当前进程同步收盘后行情")
    sync.add_argument("--start-date", help="指定回补起始日期；省略时增量更新并重拉最近 3 个交易日")
    sync.add_argument("--end-date")
    sync.add_argument("--without-valuations", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command in (None, "serve"):
        import uvicorn

        uvicorn.run("market_service.app:create_app", factory=True, host=getattr(args, "host", "127.0.0.1"),
                    port=getattr(args, "port", 8100))
        return 0
    try:
        if args.command == "seed":
            from .seed import seed

            version = seed(settings)
            print(json.dumps({"data_version": version, "seeded": version is not None}))
            return 0
        from .sync import MarketSyncService

        service = MarketSyncService(settings)
        job = service.create({"mode": "backfill" if args.start_date else "incremental", "start_date": args.start_date,
                              "end_date": args.end_date,
                              "datasets": ["trade_cal", "daily"] + ([] if args.without_valuations else ["daily_basic"])},
                             launch=False)
        result = service.execute(job["job_id"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1
    except MarketError as error:
        print(json.dumps({"code": error.code, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
