"""관리 프로세스: DENT의 프로세스들을 띄우고 지켜보고 종료한다. python -m dent.supervisor (런처가 뒤에 띄운다)

런처(uv run dent)가 묻기 · 패키지 설치를 마친 뒤 이 프로세스를 터미널과 떨어뜨려 띄운다(창을 닫아도 계속 돈다).
uv run dent --foreground면 런처가 같은 일을 자기 안에서 한다(run 함수, 터미널에 바로 보인다).
- 순서: 내장 DB 실행 · 테이블 올리기 → 모델 서버 → API 서버 · 작업 실행기 → 준비됨 → 지켜보기 → 종료.
  내장 DB를 여기서 실행하는 까닭: 런처의 터미널 창을 닫아도(Windows는 창에 붙은 프로세스가 같이 종료된다) DB가 남게.
- 기록(storage/run/dent.json): 관리 프로세스 번호 · 만든 시각 · 포트 · 단계(starting · ready · failed) · 단계 줄 · 실패 까닭.
  런처의 실행 화면 · uv run dent status · stop이 읽는다. 깔끔히 종료하면 지우고, 실패하면 까닭과 함께 남긴다.
- 종료: storage/run/stop 파일이 생기면(uv run dent stop) 차례로 종료한다. 신호(SIGINT · SIGTERM · SIGHUP, Windows는 Ctrl+Break)도 같다.
- 출력: 뒤에서 돌 때는 storage/logs/dent.log에 실행 화면과 같은 줄을 적는다(uv run dent logs가 이어 보인다).
  자식 프로세스의 출력은 '시각  이름  내용'으로 바꾸고(실행하는 동안은 경고 · 오류만), 모델 서버의 줄은 그 로그 파일에만 둔다.
  모델의 진행(받기 · 확인 · 불러오기 · 준비됨 · 실패)은 모델 서버에 물어 한 줄씩 적는다.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

import httpx
import psutil
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from prompt_toolkit.utils import get_cwidth
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.text import Text

from dent.system import devices, embedded_db, embedded_models
from dent.system.checks import ALEMBIC_INI
from dent.system.config import DEFAULT_PORT, LOOPBACK_HOST, PROJECT_ROOT, Settings, get_settings
from dent.system.devices import Device
from dent.system.embedded_models import (
    CATALOG,
    MODEL_OF_ROLE,
    ROLE_LABELS,
    EmbeddedModel,
    ModelChoice,
    ServerPhase,
    ServerState,
)
from dent.system.exceptions import StartupError
from dent.system.models import ConnectionRole
from dent.system.terminal import (
    ACCENT,
    CHILD_LINE,
    Terminal,
    bytes_text,
    elapsed_text,
    minutes_text,
)

# 띄울 프로세스 이름과 모듈
API = "API"
WORKER = "작업 실행기"
PROCESSES = {API: "dent.server", WORKER: "dent.worker"}

# 이 파일을 모듈로 띄우는 이름 (런처가 뒤에 띄운다)
SUPERVISOR_MODULE = "dent.supervisor"

# 관리 프로세스가 찾아 종료하는 DENT 프로세스의 모듈 (창을 닫아 주인 없이 남은 것을 정리할 때)
DENT_MODULES = ("dent.supervisor", "dent.server", "dent.worker", "dent.model_server")

# 기록 · 종료 요청 · 로그 파일 (storage 안)
RUN_DIR_NAME = "run"
RECORD_NAME = "dent.json"
STOP_NAME = "stop"
LOG_NAME = "dent.log"

# 기록의 단계
STARTING = "starting"
READY = "ready"
FAILED = "failed"

# 준비될 때까지 기다리는 최대 시간(초)과 확인 간격
READY_TIMEOUT_S = 60.0
READY_POLL_INTERVAL_S = 0.5

# 포트 주인을 물어보거나 상태를 물을 때 기다리는 시간(초)
PROBE_TIMEOUT_S = 2.0

# 실행된 뒤 프로세스가 살아 있는지 · 종료 요청이 있는지 보는 간격(초)과 모델 진행을 묻는 간격(초)
WATCH_INTERVAL_S = 0.5
MODEL_POLL_INTERVAL_S = 2.0

# 종료하라고 한 뒤 기다리는 최대 시간(초). 각 프로세스의 정리 시간(API 10초, 작업 실행기 30초)보다 길게.
STOP_TIMEOUT_S = {API: 15.0, WORKER: 40.0}

# 내장 모델 서버 프로세스의 모듈, 로그 이름(자식의 줄 앞 이름), 로그 파일 이름
MODEL_SERVER_MODULE = "dent.model_server"
MODEL_LOG_LABELS = {ConnectionRole.EMBEDDING: "임베딩 모델", ConnectionRole.JEV: "Jev 모델"}
MODEL_LOG_FILES = {
    ConnectionRole.EMBEDDING: "model-embedding.log",
    ConnectionRole.JEV: "model-jev.log",
}

# 모델 서버를 종료하라고 한 뒤 기다리는 최대 시간(초). 서버의 정리 시간(5초)보다 길게.
MODEL_STOP_TIMEOUT_S = 10.0

# 남은 프로세스를 종료하라고 한 뒤 기다리는 최대 시간(초). 넘으면 강제로 종료한다.
LEFTOVER_STOP_TIMEOUT_S = 15.0

# 멈춘 모델 서버의 마지막 출력을 몇 줄 보일지 (까닭을 찾게)
LAST_LINES = 12

# 내려받기 · 확인 진행을 로그로 알리는 간격(%). 받기는 10%마다, 확인은 빨라서 25%마다.
DOWNLOAD_REPORT_STEP = 10
VERIFY_REPORT_STEP = 25

# 100% (진행)
PERCENT = 100

# 실행 상황판의 칸 폭 (한글은 두 칸으로 센다)
BOARD_NAME_WIDTH = 8
BOARD_DEVICE_WIDTH = 22
BOARD_SERVER_WIDTH = 30

# 로그 파일에 적을 때의 줄 폭 (터미널이 아니라 줄을 자르지 않게 넉넉히)
LOG_WIDTH = 160

# 같은 프로세스인지 가를 때 만든 시각이 달라도 되는 차이(초). 번호는 다시 쓰일 수 있어 만든 시각도 본다.
CREATE_TIME_TOLERANCE_S = 1.0


@dataclass
class RunRecord:
    """실행 중인 DENT의 기록 (storage/run/dent.json)."""

    # 관리 프로세스 번호
    pid: int

    # 관리 프로세스를 만든 시각 (psutil). 번호가 다른 프로세스에 다시 쓰였는지 가른다.
    created: float

    # 화면 포트
    port: int

    # 실행한 시각 (time.time)
    started_at: float

    # 단계: starting · ready · failed
    state: str = STARTING

    # 단계 줄 (실행 화면이 그대로 보인다). 예: {'내장 DB': 'PostgreSQL 18.6 · 테이블 최신 0016'}
    steps: dict[str, str] = field(default_factory=dict)

    # 실패 까닭. 없으면 ''
    message: str = ""


@dataclass
class ModelServers:
    """띄운 모델 서버: 역할 → 프로세스 · 고른 것 · 보이는 장치 글."""

    # 역할 → 프로세스 (멈추면 빠진다)
    spawned: dict[ConnectionRole, subprocess.Popen[str]] = field(default_factory=dict)

    # 역할 → 고른 것
    choices: dict[ConnectionRole, ModelChoice] = field(default_factory=dict)

    # 역할 → 보이는 장치 글. 예: 'GPU 1 · RTX 4090'
    device_texts: dict[ConnectionRole, str] = field(default_factory=dict)

    # 역할 → 마지막으로 알린 진행 (단계, 진행 칸). 같은 것을 되풀이해 알리지 않게.
    reported: dict[ConnectionRole, tuple[ServerPhase | None, int]] = field(default_factory=dict)

    # 실행한 시각 (준비까지 걸린 시간을 알리려고)
    started_at: float = field(default_factory=time.monotonic)


class ChildOutput:
    """자식 프로세스의 출력을 받아 보인다(터미널 또는 로그 파일). 프로세스마다 읽는 스레드가 하나다.

    - API 서버 · 작업 실행기: 경고 · 오류는 늘, 정보 줄은 실행된 뒤(show_info)에만 보인다.
    - 모델 서버: 보이지 않는다(모델 서버의 로그 파일에 있다). 멈추면 마지막 줄들을 보인다(recent).
    """

    def __init__(self, term: Terminal) -> None:
        self.term = term

        # 실행된 뒤에는 정보 줄도 보인다
        self.show_info = False

        # 이름 → 마지막 출력 몇 줄
        self.recent: dict[str, deque[str]] = {}

    def follow(self, process: subprocess.Popen[str], label: str) -> None:
        """프로세스의 출력을 읽기 시작한다."""
        self.recent[label] = deque(maxlen=LAST_LINES)
        thread = threading.Thread(target=self._read, args=(process, label), daemon=True)
        thread.start()

    def _read(self, process: subprocess.Popen[str], label: str) -> None:
        """출력을 줄마다 읽어 보인다(프로세스가 끝나 출력이 닫힐 때까지)."""
        if process.stdout is None:
            return
        for raw in process.stdout:
            line = raw.rstrip("\r\n")
            if line.strip():
                self.recent[label].append(line)
                self._show(label, line)

    def _show(self, label: str, line: str) -> None:
        """보일 줄이면 '시각  이름  내용'으로 보인다."""
        is_model_server = label in MODEL_LOG_LABELS.values()
        if is_model_server:
            return
        is_info = CHILD_LINE.match(line) is not None and self.term.child_level(line) is None
        if is_info and not self.show_info:
            return
        self.term.console.print(self.term.child_line(line))


# ---------- 기록 · 종료 요청 ----------


def run_folder(settings: Settings) -> Path:
    """기록 · 종료 요청을 두는 폴더 (storage/run)."""
    return settings.storage_path / RUN_DIR_NAME


def log_path(settings: Settings) -> Path:
    """뒤에서 돌 때의 로그 (storage/logs/dent.log)."""
    return settings.storage_path / "logs" / LOG_NAME


def read_record(settings: Settings) -> RunRecord | None:
    """실행 중인 DENT의 기록. 없거나 모양이 틀리면 None."""
    try:
        data = json.loads((run_folder(settings) / RECORD_NAME).read_text(encoding="utf-8"))
        return RunRecord(**data)
    except (OSError, ValueError, TypeError):
        return None


def write_record(settings: Settings, record: RunRecord) -> None:
    """기록을 적는다(다른 프로세스가 반쯤 쓴 파일을 읽지 않게 옮겨 넣는다)."""
    folder = run_folder(settings)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f"{RECORD_NAME}.tmp"
    temporary.write_text(json.dumps(asdict(record), ensure_ascii=False), encoding="utf-8")
    temporary.replace(folder / RECORD_NAME)


def remove_record(settings: Settings) -> None:
    """기록과 종료 요청을 지운다(깔끔히 종료했을 때)."""
    (run_folder(settings) / RECORD_NAME).unlink(missing_ok=True)
    (run_folder(settings) / STOP_NAME).unlink(missing_ok=True)


def is_alive(record: RunRecord) -> bool:
    """기록의 관리 프로세스가 아직 살아 있는지 (번호와 만든 시각이 같아야 같은 프로세스다)."""
    try:
        process = psutil.Process(record.pid)
        return abs(process.create_time() - record.created) < CREATE_TIME_TOLERANCE_S
    except (psutil.Error, OSError):
        return False


def request_stop(settings: Settings) -> None:
    """관리 프로세스에 종료하라고 알린다(종료 요청 파일). 관리 프로세스가 곧 알아채고 차례로 종료한다."""
    folder = run_folder(settings)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STOP_NAME).write_text("stop", encoding="utf-8")


def stop_requested(settings: Settings) -> bool:
    """종료 요청이 있는지."""
    return (run_folder(settings) / STOP_NAME).exists()


def dent_processes(*, port: int) -> list[psutil.Process]:
    """이 프로젝트 폴더에서 이 포트로 도는 DENT 프로세스(관리 · API 서버 · 작업 실행기 · 모델 서버).

    창을 닫아 주인 없이 남은 것을 찾는다. 같은 폴더에서 다른 포트로 실행한 DENT(시험용)는 건드리지 않는다
    (환경 변수 PORT를 읽을 수 있으면 견준다, PORT가 없으면 기본 포트로 본다).
    """
    found: list[psutil.Process] = []
    me = os.getpid()
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            if process.pid == me:
                continue
            cmdline = process.info.get("cmdline") or []
            is_dent = any(
                part == "-m" and index + 1 < len(cmdline) and cmdline[index + 1] in DENT_MODULES
                for index, part in enumerate(cmdline)
            )
            if not is_dent or Path(process.cwd()).resolve() != PROJECT_ROOT:
                continue
            if _process_port(process) not in (None, port):
                continue
            found.append(process)
        except (psutil.Error, OSError):
            continue
    return found


def _process_port(process: psutil.Process) -> int | None:
    """프로세스의 화면 포트(환경 변수 PORT, 없으면 설정의 기본값). 읽을 수 없으면 None."""
    try:
        value = process.environ().get("PORT")
    except (psutil.Error, OSError):
        return None
    if value is None:
        # 환경 변수가 없으면 그 프로세스는 .env의 PORT(없으면 기본값)로 실행됐다.
        value = dotenv_values(PROJECT_ROOT / ".env").get("PORT") or str(DEFAULT_PORT)
    try:
        return int(value)
    except ValueError:
        return None


def stop_leftovers(processes: list[psutil.Process]) -> int:
    """주인 없이 남은 DENT 프로세스를 종료한다(제때 끝나지 않으면 강제로). 종료한 수."""
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            continue
    _gone, alive = psutil.wait_procs(processes, timeout=LEFTOVER_STOP_TIMEOUT_S)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            continue
    return len(processes)


# ---------- 띄우기 ----------


def spawn(settings: Settings, choices: list[ModelChoice]) -> subprocess.Popen[bytes]:
    """관리 프로세스를 터미널과 떨어뜨려 띄운다. 로그는 storage/logs/dent.log(지난 것은 dent.log.1)."""
    path = log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.replace(path.with_name(f"{LOG_NAME}.1"))
    remove_record(settings)
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        # 창 없는 자기 콘솔을 가진 새 프로세스 묶음: 런처의 창을 닫아도 살고, 자식에게 Ctrl+Break를 보낼 수 있다.
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        options["start_new_session"] = True
    with path.open("a", encoding="utf-8") as log:
        return subprocess.Popen(
            [sys.executable, "-m", SUPERVISOR_MODULE],
            cwd=PROJECT_ROOT,
            env=child_env(choices),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **options,
        )


def child_env(choices: list[ModelChoice]) -> dict[str, str]:
    """자식 프로세스의 환경 변수: 고른 내장 모델과, 출력을 바로바로 UTF-8로 내보내라는 값."""
    env = dict(os.environ)
    env[embedded_models.RUN_ENV] = embedded_models.encode_choices(choices)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def device_texts(choices: list[ModelChoice], found: list[Device]) -> dict[ConnectionRole, str]:
    """역할마다 보이는 장치 글: 'GPU 1 · RTX 4090', 'Apple GPU · Apple M4'."""
    texts: dict[ConnectionRole, str] = {}
    for choice in choices:
        device = next((item for item in found if item.key == choice.device), None)
        texts[choice.role] = (
            " · ".join(part for part in (device.label, device.name) if part)
            if device
            else choice.device
        )
    return texts


# ---------- 돌리기 ----------


def run(
    settings: Settings,
    choices: list[ModelChoice],
    term: Terminal,
    *,
    found: list[Device],
    finish_detail: str,
    on_ready: Callable[[], None] | None = None,
) -> int:
    """DB → 모델 서버 → API 서버 · 작업 실행기를 띄우고, 종료하라고 할 때까지 지켜본 뒤 차례로 종료한다. 끝난 코드.

    종료: 종료 요청 파일 · 신호(Ctrl+C · 창 닫기 · SIGTERM). 프로세스 하나가 멈추면 나머지도 종료하고 1.
    """
    record = RunRecord(
        pid=os.getpid(),
        created=psutil.Process().create_time(),
        port=settings.port,
        started_at=time.time(),
    )
    (run_folder(settings) / STOP_NAME).unlink(missing_ok=True)
    write_record(settings, record)
    _listen_for_stop()
    base_url = f"http://{LOOPBACK_HOST}:{settings.port}"
    output = ChildOutput(term)
    processes: dict[str, subprocess.Popen[str]] = {}
    models = ModelServers(device_texts=device_texts(choices, found))
    code = 0
    try:
        _start_database(settings, term, record)
        env = child_env(choices)
        _start_models(models, choices, env=env, output=output)
        for name, module in PROCESSES.items():
            processes[name] = _spawn(module, env=env, output=output, label=name)
        body = _wait_until_ready(base_url, processes, models, settings=settings, term=term)
        if body is None:
            _fail(settings, record, "실행되지 않았습니다 · storage/logs를 확인하세요")
            return 1
        record.state = READY
        write_record(settings, record)
        print_ready(term, base_url, body, models, settings=settings, finish_detail=finish_detail)
        output.show_info = True
        if on_ready is not None:
            on_ready()
        stopped = _watch(processes, models, settings=settings, term=term, output=output)
        if stopped is not None:
            term.fail(f"{stopped}가 멈췄습니다. 위의 안내를 확인하세요. 나머지도 종료합니다.")
            code = 1
    except KeyboardInterrupt:
        term.console.print()
    except StartupError as error:
        term.fail(error.message)
        _fail(settings, record, error.message)
        return 1
    finally:
        term.done("종료하는 중", "API 서버 · 작업 실행기 · 모델 서버 · DB", rail=False)
        _stop(processes, term)
        _stop_models(models, term)
        _stop_embedded_database(settings)
        term.end("DENT를 종료했습니다")
        if record.state != FAILED:
            remove_record(settings)
    return code


def _fail(settings: Settings, record: RunRecord, message: str) -> None:
    """실행하지 못했다고 기록에 남긴다(런처 · status가 까닭을 보인다)."""
    record.state = FAILED
    record.message = message
    write_record(settings, record)


def main() -> int:
    """뒤에서 도는 관리 프로세스: 런처가 넘긴 선택으로 실행하고, 출력은 storage/logs/dent.log에 적는다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    settings = get_settings()
    path = log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", buffering=1) as log:
        console = Console(
            file=log, color_system=None, width=LOG_WIDTH, soft_wrap=True, highlight=False
        )
        term = Terminal(console)
        choices = list(embedded_models.run_choices().values())
        found = devices.list_devices()
        return run(settings, choices, term, found=found, finish_detail="종료: uv run dent stop")


# ---------- DB ----------


def _start_database(settings: Settings, term: Terminal, record: RunRecord) -> None:
    """내장 DB를 실행하고(떠 있으면 붙고) 테이블을 최신으로 올린다. 외부 DB면 주소만 보인다. 안 되면 StartupError."""
    if not settings.uses_embedded_database:
        _step(settings, term, record, "외부 DB", settings.database_target())
        return
    embedded_db.ensure_running(settings)
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    head = ScriptDirectory.from_config(config).get_current_head()
    version = embedded_db.database_version(settings)
    parts = [f"PostgreSQL {version}" if version else "", f"테이블 최신 {head}"]
    _step(settings, term, record, "내장 DB", " · ".join(part for part in parts if part))


def _step(settings: Settings, term: Terminal, record: RunRecord, title: str, detail: str) -> None:
    """단계 줄을 보이고 기록에도 남긴다(뒤에서 돌 때 런처가 기록을 읽어 같은 줄을 보인다)."""
    term.done(title, detail)
    record.steps[title] = detail
    write_record(settings, record)


def _stop_embedded_database(settings: Settings) -> None:
    """내장 DB를 쓰면 종료한다. API 서버 · 작업 실행기 · 모델 서버를 종료한 뒤에 부른다."""
    if settings.uses_embedded_database:
        embedded_db.stop(settings)


# ---------- 프로세스 ----------


def _start_models(
    models: ModelServers,
    choices: list[ModelChoice],
    *,
    env: dict[str, str],
    output: ChildOutput,
) -> None:
    """고른 모델마다 모델 서버를 띄운다."""
    for choice in choices:
        role = choice.role
        models.choices[role] = choice
        models.spawned[role] = _spawn(
            MODEL_SERVER_MODULE, role.value, env=env, output=output, label=MODEL_LOG_LABELS[role]
        )


def _stop_models(models: ModelServers, term: Terminal) -> None:
    """모델 서버를 종료한다. 제때 끝나지 않으면 강제로 종료한다. 두 번 불러도 한 번만 종료한다."""
    for process in models.spawned.values():
        _ask_to_stop(process)
    for role, process in models.spawned.items():
        try:
            process.wait(timeout=MODEL_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            term.warn(f"{ROLE_LABELS[role]} 모델 서버가 제때 끝나지 않아 강제로 종료했습니다")
    models.spawned.clear()


def _spawn(
    module: str, *args: str, env: dict[str, str], output: ChildOutput, label: str
) -> subprocess.Popen[str]:
    """프로세스 하나를 띄우고 출력을 따라간다.

    종료는 이 프로세스가 한 번씩 알린다: Mac · Linux는 새 세션, Windows는 새 프로세스 묶음으로 띄운다
    (터미널의 Ctrl+C가 자식에게 바로 가지 않고, 이 프로세스가 차례로 종료하게).
    """
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(
        [sys.executable, "-m", module, *args],
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **options,
    )
    output.follow(process, label)
    return process


def _ask_to_stop(process: subprocess.Popen[str]) -> None:
    """종료하라고 알린다: Mac · Linux는 SIGTERM, Windows는 그 프로세스 묶음에 Ctrl+Break."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
    else:
        process.terminate()


def _listen_for_stop() -> None:
    """종료하라는 신호를 Ctrl+C와 똑같이 다룬다: SIGTERM, 창 닫기(Mac · Linux SIGHUP, Windows Ctrl+Break · 창 닫기)."""
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    for name in ("SIGHUP", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, _on_window_closed)


def _raise_keyboard_interrupt(_signum: int, _frame: FrameType | None) -> None:
    """종료하라는 신호를 Ctrl+C와 똑같이 다룬다."""
    raise KeyboardInterrupt


def _on_window_closed(_signum: int, _frame: FrameType | None) -> None:
    """창이 닫혔다: 터미널에 더 쓸 수 없으니 출력을 버리고(쓰다 멈추지 않게) Ctrl+C처럼 차례로 종료한다."""
    discard = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - 끝날 때까지 쓰는 출력
    sys.stdout = discard
    sys.stderr = discard
    raise KeyboardInterrupt


def is_port_in_use(port: int) -> bool:
    """이 PC의 포트를 누가 쓰고 있는지."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(PROBE_TIMEOUT_S)
        return sock.connect_ex((LOOPBACK_HOST, port)) == 0


def open_browser(base_url: str) -> None:
    """브라우저로 연다."""
    webbrowser.open(base_url)


# ---------- 실행 · 지켜보기 ----------


def _wait_until_ready(
    base_url: str,
    processes: dict[str, subprocess.Popen[str]],
    models: ModelServers,
    *,
    settings: Settings,
    term: Terminal,
) -> dict[str, Any] | None:
    """API가 준비되고 작업 실행기가 자리를 잡을 때까지 기다린다. 터미널이면 기다리는 동안 상황판을 보인다.

    실행되지 못하면 None. 종료 요청이 오면 KeyboardInterrupt. 모델 준비는 기다리지 않는다.
    """
    deadline = time.monotonic() + READY_TIMEOUT_S
    last_body: dict[str, Any] | None = None
    states: dict[ConnectionRole, ServerState | None] = {}
    flags = {"api": False, "worker": False}

    def render() -> RenderableType:
        return startup_board(term, models, states, flags)

    live: AbstractContextManager[Any] = (
        Live(get_renderable=render, console=term.console, transient=True, refresh_per_second=8)
        if term.console.is_terminal
        else nullcontext()
    )
    with httpx.Client(timeout=PROBE_TIMEOUT_S) as client, live:
        while time.monotonic() < deadline:
            if stop_requested(settings):
                raise KeyboardInterrupt
            for name, process in list(processes.items()):
                code = process.poll()
                if code is None:
                    continue
                # 작업 실행기가 0으로 끝났으면 다른 작업 실행기가 이미 떠 있는 것이다. 그것을 쓴다.
                is_other_worker_running = name == WORKER and code == 0
                if is_other_worker_running:
                    del processes[name]
                    continue
                term.fail(f"실행되지 않았습니다. {name}가 멈췄습니다. 위의 안내를 확인하세요.")
                return None
            for role in models.spawned:
                port = embedded_models.model_port(settings, role)
                states[role] = embedded_models.probe_server(port)
            try:
                response = client.get(f"{base_url}/readyz")
                last_body = response.json()
                flags["api"] = response.status_code == 200
                flags["worker"] = last_body["info"]["worker"]["ok"] or WORKER not in processes
                if flags["api"] and flags["worker"]:
                    for role, state in states.items():
                        models.reported[role] = report_key(state)
                    return last_body
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
            time.sleep(READY_POLL_INTERVAL_S)

    # 작업 실행기만 늦으면 그대로 실행한다. 화면에 '종료됨'으로 보인다.
    if last_body is not None and last_body.get("ready"):
        return last_body
    term.fail(f"{READY_TIMEOUT_S:.0f}초 안에 준비되지 않았습니다.")
    for check in (last_body or {}).get("checks", {}).values():
        if not check.get("ok"):
            term.note(str(check.get("detail")))
    return None


def startup_board(
    term: Terminal,
    models: ModelServers,
    states: dict[ConnectionRole, ServerState | None],
    flags: dict[str, bool],
) -> RenderableType:
    """실행하는 동안의 상황판: 모델마다 진행, API 서버 · 작업 실행기는 도는 기호 → 준비됨."""
    rail = term.symbols["rail"]
    lines = [Text.assemble((term.spinner(), ACCENT), "  실행하는 중")]
    for role in models.choices:
        line = Text.assemble((rail, "dim"), "    ")
        line.append_text(_pad(ROLE_LABELS[role], BOARD_NAME_WIDTH))
        line.append_text(_pad(models.device_texts.get(role, ""), BOARD_DEVICE_WIDTH))
        line.append_text(state_text(term, states.get(role)))
        lines.append(line)
    for key, name in (("api", "API 서버"), ("worker", "작업 실행기")):
        mark = Text("준비됨", style="green") if flags[key] else Text(term.spinner(), style=ACCENT)
        line = Text.assemble((rail, "dim"), "    ")
        line.append_text(_pad(name, BOARD_SERVER_WIDTH))
        line.append_text(mark)
        lines.append(line)
    return Group(*lines)


def state_text(term: Terminal, state: ServerState | None) -> Text:
    """모델 서버 상태 한 조각: 진행 막대 · 불러오는 중 · 준비됨 · 실패."""
    if state is None:
        return Text("실행하는 중", style="dim")
    if state.phase in (ServerPhase.DOWNLOADING, ServerPhase.VERIFYING):
        fraction = state.done_bytes / state.total_bytes if state.total_bytes else 0
        word = "받는 중" if state.phase is ServerPhase.DOWNLOADING else "확인 중"
        text = Text(f"{word} ", style=ACCENT)
        text.append_text(term.bar(fraction))
        sizes = f"{bytes_text(state.done_bytes)} / {bytes_text(state.total_bytes)}"
        detail = f" {round(fraction * PERCENT)}% · {sizes}"
        if state.eta_s is not None:
            detail += f" · {minutes_text(state.eta_s)}"
        text.append(detail, style="dim")
        return text
    if state.phase is ServerPhase.LOADING:
        return Text("불러오는 중", style=ACCENT)
    if state.phase is ServerPhase.READY:
        return Text("준비됨", style="green")
    return Text(f"실패 · {state.detail}", style="red")


def print_ready(
    term: Terminal,
    base_url: str,
    body: dict[str, Any],
    models: ModelServers,
    *,
    settings: Settings,
    finish_detail: str,
    after: str = "",
) -> None:
    """실행된 뒤의 줄: API 서버 · 작업 실행기 · 모델 셋의 상태 → 끝 줄(주소 · 종료하는 법) → 덧붙이는 흐린 줄(after)."""
    info = body.get("info", {})
    term.done("API 서버", f"{LOOPBACK_HOST}:{settings.port}", rail=False)
    worker_detail = str(info.get("worker", {}).get("detail", ""))
    worker_value = worker_detail.removeprefix("실행 중 · ").replace("대기 작업", "대기")
    term.done("작업 실행기", worker_value, rail=False)
    waiting = False
    for role in (ConnectionRole.EMBEDDING, ConnectionRole.JEV, ConnectionRole.LLM):
        name = ROLE_LABELS.get(role, "LLM")
        if role in models.choices:
            phase = models.reported.get(role, (None, 0))[0]
            is_ready = phase is ServerPhase.READY
            waiting = waiting or not is_ready
            word = "준비됨" if is_ready else "준비 중"
            term.done(f"{name} · 내장", f"{models.device_texts.get(role, '')} · {word}", rail=False)
            continue
        detail = str(info.get(role.value, {}).get("detail", "미연결"))
        term.done(name, detail, rail=False)
    term.rail()
    term.finish("실행 중", base_url, finish_detail)
    if waiting:
        term.after("모델은 뒤에서 준비합니다 · 진행은 홈의 모델 칸과 uv run dent status")
    if after:
        term.after(after)
    term.console.print()


def _watch(
    processes: dict[str, subprocess.Popen[str]],
    models: ModelServers,
    *,
    settings: Settings,
    term: Terminal,
    output: ChildOutput,
) -> str | None:
    """종료 요청이 오거나 프로세스 하나가 멈출 때까지 지켜본다. 멈춘 것의 이름, 종료 요청이면 None.

    모델 서버의 진행이 바뀌면 한 줄씩 알린다. 모델 서버가 멈추면 알리기만 하고 계속 지켜본다
    (모델 없이도 데이터를 보고 고칠 수 있다).
    """
    next_poll = 0.0
    while True:
        if stop_requested(settings):
            return None
        for name, process in processes.items():
            if process.poll() is not None:
                return name
        for role, process in list(models.spawned.items()):
            if process.poll() is not None:
                log = f"storage/logs/{MODEL_LOG_FILES[role]}"
                term.event(ROLE_LABELS[role], f"모델 서버가 멈췄습니다 · {log}", level="오류")
                for line in output.recent.get(MODEL_LOG_LABELS[role], []):
                    term.console.print(Text(f"          {line}", style="dim"))
                del models.spawned[role]
        if time.monotonic() >= next_poll:
            report_models(models, settings=settings, term=term)
            next_poll = time.monotonic() + MODEL_POLL_INTERVAL_S
        time.sleep(WATCH_INTERVAL_S)


def report_models(models: ModelServers, *, settings: Settings, term: Terminal) -> None:
    """준비가 끝나지 않은 모델 서버에 물어, 진행이 바뀌었으면 한 줄씩 알린다."""
    for role in list(models.spawned):
        previous = models.reported.get(role, (None, 0))
        if previous[0] in (ServerPhase.READY, ServerPhase.FAILED):
            continue
        state = embedded_models.probe_server(embedded_models.model_port(settings, role))
        key = report_key(state)
        if state is None or key == previous:
            continue
        models.reported[role] = key
        message, level = _report_line(state, models, role)
        term.event(ROLE_LABELS[role], message, level=level)


def _report_line(
    state: ServerState, models: ModelServers, role: ConnectionRole
) -> tuple[str, str | None]:
    """알릴 한 줄의 (내용, 수준)."""
    device = state.device or models.device_texts.get(role, "")
    if state.phase is ServerPhase.FAILED:
        return state.detail or "실패", "오류"
    if state.phase is ServerPhase.READY:
        return f"준비됨 · {device} · {elapsed_text(time.monotonic() - models.started_at)}", None
    if state.phase is ServerPhase.LOADING:
        return f"불러오는 중 · {device}", None
    word = "받는 중" if state.phase is ServerPhase.DOWNLOADING else "파일 확인 중"
    percent = state.done_bytes * PERCENT // state.total_bytes if state.total_bytes else 0
    detail = f"{word} {percent}% · {bytes_text(state.done_bytes)} / {bytes_text(state.total_bytes)}"
    if state.eta_s is not None:
        detail += f" · {minutes_text(state.eta_s)}"
    return detail, None


def report_key(state: ServerState | None) -> tuple[ServerPhase | None, int]:
    """알릴지 가르는 열쇠: (단계, 진행 칸). 받기는 10%, 확인은 25%마다 칸이 바뀐다."""
    if state is None:
        return (None, 0)
    is_files = state.phase in (ServerPhase.DOWNLOADING, ServerPhase.VERIFYING)
    if not is_files or not state.total_bytes:
        return (state.phase, 0)
    step = DOWNLOAD_REPORT_STEP if state.phase is ServerPhase.DOWNLOADING else VERIFY_REPORT_STEP
    return (state.phase, state.done_bytes * PERCENT // state.total_bytes // step)


def _stop(processes: dict[str, subprocess.Popen[str]], term: Terminal) -> None:
    """API 서버 · 작업 실행기에 종료하라고 한 번씩 알리고 기다린다. 제때 끝나지 않으면 강제로 종료한다."""
    for process in processes.values():
        _ask_to_stop(process)
    for name, process in processes.items():
        try:
            process.wait(timeout=STOP_TIMEOUT_S[name])
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            term.warn(
                f"{name}가 제때 끝나지 않아 강제로 종료했습니다. 하던 작업은 다음에 실행할 때 이어서 합니다"
            )


def model_of(role: ConnectionRole) -> EmbeddedModel:
    """역할의 내장 모델."""
    return CATALOG[MODEL_OF_ROLE[role]]


def short_path(path: Path) -> str:
    """경로를 짧게: 프로젝트 안이면 './…', 집 폴더 안이면 '~/…'."""
    for base, prefix in ((PROJECT_ROOT, "."), (Path.home(), "~")):
        try:
            return f"{prefix}/{path.resolve().relative_to(base)}"
        except ValueError:
            continue
    return str(path)


def _pad(text: str, width: int) -> Text:
    """한글 폭(두 칸)을 세어 width 칸까지 공백을 채운 글."""
    return Text(text + " " * max(1, width - get_cwidth(text)))


if __name__ == "__main__":
    sys.exit(main())
