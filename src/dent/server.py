"""API 서버 프로세스를 실행한다: python -m dent.server [--reload]

실행하기 전에 설정 · 로그 · storage · DB · pgvector · 테이블 버전을 확인하고,
모두 되면 127.0.0.1에서 요청을 받기 시작한다. 평소에는 런처(uv run dent)가 이것을 띄운다.
"""

import argparse
import asyncio
import logging
import sys

import uvicorn

from dent.system.checks import check_database, check_storage
from dent.system.config import LOOPBACK_HOST, Settings, get_settings
from dent.system.db import dispose_engine, init_engine
from dent.system.exceptions import StartupError
from dent.system.logging import report_startup_error, setup_logging

logger = logging.getLogger("dent.server")

LABEL = "API"

# 종료할 때 받은 요청을 마치기까지 기다리는 최대 시간(초)
GRACEFUL_SHUTDOWN_S = 10


def main(argv: list[str] | None = None) -> int:
    """API 서버를 실행한다. 실행할 때 확인에서 멈추면 1을 돌려준다."""
    parser = argparse.ArgumentParser(prog="python -m dent.server", description="DENT API 서버")
    parser.add_argument("--reload", action="store_true", help="코드가 바뀌면 다시 띄운다 (개발용)")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
        setup_logging(settings, process="api", label=LABEL)
        logger.info(settings.describe())
        check_storage(settings)
        asyncio.run(_check_database(settings))
    except StartupError as error:
        report_startup_error(error, label=LABEL)
        return 1

    logger.info(
        "확인을 마쳤습니다. http://%s:%d 에서 요청을 받기 시작합니다.", LOOPBACK_HOST, settings.port
    )
    # log_config=None: uvicorn이 로그 설정을 덮어쓰지 않게 해서, 오류도 같은 모양으로 남긴다.
    uvicorn.run(
        "dent.main:app",
        host=LOOPBACK_HOST,
        port=settings.port,
        reload=args.reload,
        log_config=None,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )
    return 0


async def _check_database(settings: Settings) -> None:
    """DB 연결, pgvector, 테이블 버전을 본다. 서버는 다른 이벤트 루프에서 돌므로 엔진을 닫고 끝낸다."""
    try:
        await check_database(init_engine(), settings)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(main())
