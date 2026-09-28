"""설정 읽기.

.env와 환경 변수에서 값을 읽는다. 값이 빠졌거나 모양이 틀리면 무엇이 틀렸는지
한국어 한 문장으로 알려 주는 StartupError를 낸다. 꼭 있어야 하는 값은 없다: DATABASE_URL을 비우면
내장 DB(storage/pgdata, system/embedded_db.py)를 쓰므로 .env 없이도 실행된다.
.env에는 실행하기 전에 있어야 하는 값(DB 주소, storage, 포트 …)만 둔다. 외부 모델 서버(임베딩 · Jev · LLM)
연결은 화면의 연결 설정에서 저장하고 system_connections에서 읽는다(system/connections.py).
내장 모델은 .env에 두지 않고 런처가 실행할 때 묻는다(system/embedded_models.py). 여기에는 포트 · 폴더만 있다.
"""

from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from dent.system.exceptions import StartupError

# 프로젝트 루트. 이 파일(src/dent/system/config.py)에서 세 단계 위다. 상대 경로는 이곳 기준으로 읽는다.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 앱 버전. pyproject.toml의 version을 설치된 패키지 정보에서 읽는다(한 곳에만 적으려고).
APP_VERSION = version("dent")

# 로그인이 없는 프로그램이라 이 PC 안에서만 연다. 설정으로 바꿀 수 없다.
LOOPBACK_HOST = "127.0.0.1"

DEFAULT_PORT = 8000

# 내장 DB의 데이터 폴더 이름 (storage 안)
EMBEDDED_DB_DIR_NAME = "pgdata"

# 외부 DB 주소에 포트가 없을 때 PostgreSQL 기본 포트
DEFAULT_DB_PORT = 5432

# 내장 모델 서버의 포트: 화면 포트(PORT) 바로 뒤 (임베딩 +1, Jev +2). .env 값을 늘리지 않으려고 PORT에서 셈한다.
EMBEDDED_EMBEDDING_PORT_OFFSET = 1
EMBEDDED_JEV_PORT_OFFSET = 2

# 내장 모델 파일을 두는 폴더 이름 (storage 안)
EMBEDDED_MODELS_DIR_NAME = "models"


class Settings(BaseSettings):
    """DENT 설정 값. 이름은 .env의 대문자 이름과 같다(DATABASE_URL → database_url)."""

    # .env는 프로젝트 루트에서 읽는다. 모르는 이름은 무시한다(다른 도구의 값이 섞일 수 있음).
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 앱이 쓰는 외부 DB 주소. 비밀번호가 들어 있어 로그에 찍히지 않게 SecretStr로 둔다.
    # 비우면 내장 DB(storage/pgdata)를 쓴다.
    database_url: SecretStr | None = None

    # 테스트 전용 DB 주소. 테스트만 쓴다.
    test_database_url: SecretStr | None = None

    # 파일 루트. 상대 경로면 프로젝트 루트 기준이다.
    storage_dir: Path = Path("storage")

    # 화면과 API를 여는 주소. 127.0.0.1만 허용한다.
    host: str = LOOPBACK_HOST

    # 화면과 API를 여는 포트.
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)

    # 허깅페이스 토큰. 비공개·동의가 필요한 데이터셋을 가져올 때만 쓴다. 없으면 공개 데이터셋만.
    hf_token: SecretStr | None = None

    # 로그 수준.
    log_level: str = "INFO"

    @field_validator("database_url", "test_database_url", mode="before")
    @classmethod
    def treat_blank_as_missing(cls, value: object) -> object:
        """빈 값(DATABASE_URL=)은 없는 것으로 본다. 비워 두면 내장 DB를 쓰라는 뜻이다."""
        is_blank = isinstance(value, str) and not value.strip()
        return None if is_blank else value

    @field_validator("host")
    @classmethod
    def require_loopback(cls, value: str) -> str:
        """주소는 127.0.0.1만 받는다. 다른 PC에서 들어오지 못하게 하려는 것이다."""
        is_loopback = value == LOOPBACK_HOST
        if not is_loopback:
            raise ValueError("127.0.0.1만 쓸 수 있습니다")
        return value

    @property
    def storage_path(self) -> Path:
        """storage 폴더의 절대 경로."""
        path = self.storage_dir.expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def uses_embedded_database(self) -> bool:
        """DATABASE_URL이 없어 내장 DB를 쓰는지."""
        return self.database_url is None

    @property
    def embedded_database_path(self) -> Path:
        """내장 DB의 데이터 폴더 (storage/pgdata)."""
        return self.storage_path / EMBEDDED_DB_DIR_NAME

    @property
    def embedded_models_path(self) -> Path:
        """내장 모델 파일 폴더 (storage/models). 모델마다 그 이름의 폴더를 둔다."""
        return self.storage_path / EMBEDDED_MODELS_DIR_NAME

    @property
    def embedded_embedding_port(self) -> int:
        """내장 임베딩 모델 서버의 포트 (PORT + 1)."""
        return self.port + EMBEDDED_EMBEDDING_PORT_OFFSET

    @property
    def embedded_jev_port(self) -> int:
        """내장 Jev 모델 서버의 포트 (PORT + 2)."""
        return self.port + EMBEDDED_JEV_PORT_OFFSET

    def database_target(self) -> str:
        """DB를 사람이 읽는 모양으로: 내장이면 '내장 · 데이터 폴더', 외부면 'host:port/데이터베이스'."""
        if self.database_url is None:
            return f"내장 · {self.embedded_database_path}"
        return describe_database(self.database_url.get_secret_value())

    def describe(self) -> str:
        """비밀번호와 키를 뺀 설정 요약 한 줄. 실행할 때 로그 첫 줄에 남긴다."""
        return f"DENT {APP_VERSION} · DB {self.database_target()} · storage {self.storage_path}"


def describe_database(url: str) -> str:
    """DB 주소에서 비밀번호를 빼고 'host:port/데이터베이스' 모양으로 돌려준다.

    소켓 파일로 붙는 주소(?host=/폴더)면 'socket 폴더/데이터베이스'.
    """
    parsed = make_url(url)
    socket_dir = parsed.query.get("host")
    if parsed.host is None and socket_dir:
        return f"socket {socket_dir}/{parsed.database}"
    return f"{parsed.host}:{parsed.port or DEFAULT_DB_PORT}/{parsed.database}"


@lru_cache
def get_settings() -> Settings:
    """설정을 한 번 읽어 돌려준다. 값이 빠졌거나 틀리면 StartupError."""
    try:
        return Settings()
    except ValidationError as error:
        raise StartupError(describe_settings_error(error)) from None


def describe_settings_error(error: ValidationError) -> str:
    """설정 검사 오류를 사람이 할 일까지 담은 한 문장으로 바꾼다."""
    missing: list[str] = []
    invalid: list[str] = []
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "?"
        if name == "HOST":
            return (
                "HOST는 127.0.0.1만 쓸 수 있습니다. 로그인이 없는 프로그램이라 "
                "다른 PC에서 들어오면 안 됩니다. .env에서 HOST를 지우세요."
            )
        if item["type"] == "missing":
            missing.append(name)
        else:
            invalid.append(name)
    if missing:
        return (
            f"설정에 빠진 값이 있습니다: {', '.join(missing)}. "
            ".env를 확인하세요 (없으면 cp .env.example .env)."
        )
    return f"설정 값의 모양이 틀렸습니다: {', '.join(invalid)}. .env를 확인하세요."
