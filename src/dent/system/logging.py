"""로그 설정.

터미널에는 사람이 읽을 한 줄을, 파일에는 시각과 자세한 내용을 남긴다.
로그 파일은 storage/logs/ 아래에 자정마다 새로 만들고 14일치만 둔다.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from dent.system.config import Settings
from dent.system.exceptions import StartupError

# 로그 파일을 남겨 두는 날 수
LOG_RETENTION_DAYS = 14

FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# 이 라이브러리들은 경고부터만 남긴다. 정보 로그가 너무 많아 필요한 줄이 묻힌다.
QUIET_LOGGERS = (
    "sqlalchemy",
    "httpx",
    "httpcore",
    "alembic",
    "asyncio",
    "uvicorn.error",
    # 내장 DB 패키지: 붙을 때마다 psql 명령과 출력을 정보 로그로 남긴다
    "embedded_postgres",
)


class ConsoleFormatter(logging.Formatter):
    """터미널용 한 줄. 어느 프로세스의 말인지 앞에 붙이고, 경고·오류는 그렇다고 적는다."""

    def __init__(self, label: str) -> None:
        super().__init__()

        # 줄 앞에 붙일 프로세스 이름. 예: [API], [작업 실행기]
        self.label = label

    def format(self, record: logging.LogRecord) -> str:
        level = "오류: " if record.levelno >= logging.ERROR else ""
        level = "경고: " if record.levelno == logging.WARNING else level
        return f"[{self.label}] {level}{record.getMessage()}"


def setup_logging(settings: Settings, *, process: str, label: str) -> None:
    """이 프로세스의 로그를 준비한다. 로그 폴더에 쓸 수 없으면 StartupError."""
    logs_dir = settings.storage_path / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            logs_dir / f"{process}.log",
            when="midnight",
            backupCount=LOG_RETENTION_DAYS,
            encoding="utf-8",
        )
    except OSError:
        raise StartupError(f"로그 폴더에 쓸 수 없습니다: {logs_dir}. 권한을 확인하세요.") from None
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(ConsoleFormatter(label))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(settings.log_level.upper())
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def report_startup_error(error: StartupError, *, label: str) -> None:
    """실행할 때 멈춘 이유를 알린다. 로그가 아직 준비되지 않았으면 터미널에 바로 쓴다."""
    is_logging_ready = bool(logging.getLogger().handlers)
    if is_logging_ready:
        logging.getLogger("dent").error(error.message)
    else:
        print(f"[{label}] 오류: {error.message}", file=sys.stderr)
