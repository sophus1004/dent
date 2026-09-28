"""실행 명령: uv run dent [stop | status | logs] [--no-browser] [-y] [--foreground]

- uv run dent: 설정 → 내장 모델 고르기 → 모델 패키지 → 관리 프로세스(dent.supervisor)를 뒤에 띄우고, 실행될 때까지 보인 뒤 끝난다.
  DENT는 뒤에서 계속 돈다(터미널 창을 닫아도). 이미 실행 중이면 [브라우저 열기 · 종료 · 다시 실행]를 고른다.
- uv run dent stop: 어디서든 종료한다(번호를 몰라도 된다). 창을 닫아 주인 없이 남은 프로세스 · 내장 DB도 정리한다.
- uv run dent status: 실행 중인지 · 주소 · 실행한 지 얼마 · 서버와 모델 상태(받는 중이면 진행).
- uv run dent logs: 관리 프로세스의 로그(storage/logs/dent.log)를 이어 본다. Ctrl+C는 보기만 멈추고 DENT는 계속 돈다.
- uv run dent --foreground: 뒤로 보내지 않고 이 터미널에서 돈다(개발용, Ctrl+C로 종료한다).

실행 화면은 왼쪽 세로 줄에 단계가 하나씩 쌓이는 모양이다(system/terminal.py).
- 내장 모델: 역할(임베딩 · Jev)마다 올릴 곳(이 PC의 GPU 번호 · Apple GPU · CPU · 올리지 않음)과 파일(받기 · 받아 둠 ·
  이 PC의 폴더)을 묻는다. 처음에는 '올리지 않음'에 둔다. Enter는 지난번 그대로, Esc는 남은 것 모두 지난번대로.
  -y이거나 터미널이 아니면 묻지 않고 지난번대로 실행한다(고른 적이 없으면 올리지 않음). 고른 것은 storage/models/choices.json에
  적고(다음 기본값), 환경 변수(embedded_models.RUN_ENV)로 관리 프로세스에 넘긴다. .env는 쓰지 않는다.
- 모델 패키지가 없으면 설치를 묻고, uv가 이 PC에 맞는 PyTorch 판(CUDA · ROCm · Apple · CPU)을 고정한 버전으로 받는다.
- 앞서 강제로 끝나 모델 서버가 남아 있으면 종료하고 새로 띄운다(이번에 고른 장치 · 파일을 쓰게).
"""

import argparse
import importlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

import httpx
from rich.live import Live
from rich.text import Text

from dent import supervisor
from dent.system import devices, embedded_db, embedded_models
from dent.system.config import APP_VERSION, LOOPBACK_HOST, PROJECT_ROOT, Settings, get_settings
from dent.system.devices import Device
from dent.system.embedded_models import MODEL_OF_ROLE, ROLE_LABELS, ModelChoice, ServerState
from dent.system.exceptions import ExternalServiceError, StartupError
from dent.system.models import ConnectionRole
from dent.system.terminal import Option, Terminal, bytes_text, elapsed_text

# 포트 주인을 물어보거나 상태를 물을 때 기다리는 시간(초)
PROBE_TIMEOUT_S = 2.0

# 뒤에 띄운 관리 프로세스가 실행될 때까지 따라가는 최대 시간(초). DB를 실행하고 테이블을 올리는 시간까지 넣는다.
FOLLOW_TIMEOUT_S = 180.0

# 실행되는지 · 종료되는지 보는 간격(초)
POLL_INTERVAL_S = 0.5

# 종료하라고 한 뒤 관리 프로세스가 끝나기를 기다리는 최대 시간(초). 작업 실행기의 정리 시간(30초)보다 넉넉히.
STOP_WAIT_S = 90.0

# 남아 있던 모델 서버를 종료한 뒤 포트가 빌 때까지 기다리는 최대 시간(초)
LEFTOVER_STOP_TIMEOUT_S = 15.0

# 잠금 파일을 제약 파일로 내보내기를 기다리는 최대 시간(초)
EXPORT_TIMEOUT_S = 120.0

# 설치에 실패했을 때 보일 uv 출력의 마지막 줄 수
INSTALL_TAIL_LINES = 8

# 실행하지 못했을 때 보일 로그의 마지막 줄 수, logs가 처음에 보일 줄 수
FAILURE_TAIL_LINES = 15
LOG_TAIL_LINES = 40

# 1시간 · 1분 (실행한 지 얼마를 보인다)
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60

# 100% (진행)
PERCENT = 100

# 로그 한 줄: '12:01:03  작업    경고  내용'
LOG_LINE = re.compile(
    r"^(?P<time>\d\d:\d\d:\d\d)  (?P<label>\S+)(?P<gap>\s+)(?P<level>(?:경고|오류)  )?(?P<message>.*)$"
)

# 고르기 줄 아래의 도움말
DEVICE_HINT = "↑↓ 고르기 · Enter 확인 · Esc 남은 것 모두 지난번대로 실행"
FILES_HINT = "↑↓ 고르기 · Enter 확인 · Esc 지난번대로"
PATH_HINT = "Tab 자동 완성 · Enter 확인 · Esc 뒤로"
INSTALL_HINT = "↑↓ 고르기 · Enter 확인"
RUNNING_HINT = "↑↓ 고르기 · Enter 확인 · Esc 그대로 두기"

# 끝 줄에 붙이는 안내
STOP_HINT = "종료: uv run dent stop"
BACKGROUND_HINT = "뒤에서 돌고 있습니다 · 상태 uv run dent status · 로그 uv run dent logs"

# 모델 줄의 역할 이름 (/readyz models의 role)
ROLE_NAMES = {"embedding": "임베딩", "jev": "Jev", "llm": "LLM"}


def main(argv: list[str] | None = None) -> None:
    """DENT를 실행하고 종료한다."""
    parser = argparse.ArgumentParser(
        prog="dent", description="DENT를 실행하고 종료합니다. 실행하면 뒤에서 돕니다."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("stop", "status", "logs"),
        help="stop 종료 · status 상태 보기 · logs 로그 이어 보기 (없으면 실행)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="실행된 뒤 브라우저를 열지 않는다"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="묻지 않고 지난번에 고른 내장 모델로 실행한다"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="뒤로 보내지 않고 이 터미널에서 돈다(개발용, Ctrl+C로 종료한다)",
    )
    args = parser.parse_args(argv)

    # UTF-8이 아닌 곳(옛 Windows 콘솔 · 파일로 보내기)에서 한글 · 기호를 못 쓰면 멈추지 않고 바꿔 쓴다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    term = Terminal()
    try:
        settings = get_settings()
    except StartupError as error:
        term.header(APP_VERSION)
        term.fail(error.message)
        sys.exit(1)

    if args.command == "logs":
        sys.exit(logs_command(settings, term))
    term.header(APP_VERSION)
    if args.command == "stop":
        sys.exit(stop_command(settings, term))
    if args.command == "status":
        sys.exit(status_command(settings, term))
    sys.exit(start_command(settings, term, args))


# ---------- 실행 ----------


def start_command(settings: Settings, term: Terminal, args: argparse.Namespace) -> int:
    """실행한다: 묻기 · 패키지 → 관리 프로세스(뒤, 또는 --foreground면 여기서) → 실행될 때까지 보이기. 끝난 코드."""
    base_url = f"http://{LOOPBACK_HOST}:{settings.port}"
    if _is_running(settings, base_url):
        start_again = _already_running(settings, term, base_url, args)
        if not start_again:
            return 0
    elif _port_owner(settings.port, base_url) == "other":
        term.fail(
            f"{settings.port} 포트를 다른 프로그램이 쓰고 있습니다. "
            "그 프로그램을 종료하거나 .env의 PORT를 바꾸세요."
        )
        return 1
    term.done(
        "설정", f"storage {supervisor.short_path(settings.storage_path)} · 포트 {settings.port}"
    )

    ask = term.interactive and not args.yes
    try:
        found = devices.list_devices()
        choices = _choose_models(settings, term, found=found, ask=ask)
        choices = _ensure_packages(choices, term, found=found, ask=ask)
        _clear_leftovers(settings, choices, term)
    except StartupError as error:
        term.fail(error.message)
        return 1
    except KeyboardInterrupt:
        term.fail("실행을 멈췄습니다")
        return 130

    if args.foreground:

        def open_page() -> None:
            if not args.no_browser:
                supervisor.open_browser(base_url)

        return supervisor.run(
            settings, choices, term, found=found, finish_detail="종료 Ctrl+C", on_ready=open_page
        )
    process = supervisor.spawn(settings, choices)
    return _follow_start(
        settings,
        term,
        process,
        choices,
        found=found,
        base_url=base_url,
        open_page=not args.no_browser,
    )


def _follow_start(
    settings: Settings,
    term: Terminal,
    process: subprocess.Popen[bytes],
    choices: list[ModelChoice],
    *,
    found: list[Device],
    base_url: str,
    open_page: bool,
) -> int:
    """뒤에 띄운 관리 프로세스가 실행될 때까지 따라가며 보인다(기록의 단계 줄 · 상황판). 실행되면 0, 아니면 1."""
    models = supervisor.ModelServers(
        choices={choice.role: choice for choice in choices},
        device_texts=supervisor.device_texts(choices, found),
    )
    states: dict[ConnectionRole, ServerState | None] = {}
    flags = {"api": False, "worker": False}
    shown: set[str] = set()
    body: dict[str, Any] | None = None
    failure = ""

    def render() -> Any:
        return supervisor.startup_board(term, models, states, flags)

    live: AbstractContextManager[Any] = (
        Live(get_renderable=render, console=term.console, transient=True, refresh_per_second=8)
        if term.console.is_terminal
        else nullcontext()
    )
    deadline = time.monotonic() + FOLLOW_TIMEOUT_S
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT_S) as client, live:
            while time.monotonic() < deadline:
                record = supervisor.read_record(settings)
                if record is not None:
                    for title, detail in record.steps.items():
                        if title not in shown:
                            shown.add(title)
                            term.done(title, detail)
                    if record.state == supervisor.FAILED:
                        failure = record.message
                        break
                if process.poll() is not None:
                    failure = failure or "관리 프로세스가 멈췄습니다"
                    break
                for role in models.choices:
                    port = embedded_models.model_port(settings, role)
                    states[role] = embedded_models.probe_server(port)
                body = _readyz(client, base_url, flags)
                if body is not None and record is not None and record.state == supervisor.READY:
                    break
                body = None
                time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        term.console.print()
        term.warn("실행을 멈춥니다 · 띄운 것을 종료합니다")
        _stop_running(settings, term)
        return 130
    if body is None:
        term.fail(failure or f"{FOLLOW_TIMEOUT_S:.0f}초 안에 준비되지 않았습니다")
        _show_log_tail(settings, term)
        if process.poll() is None:
            _stop_running(settings, term)
        return 1
    for role, state in states.items():
        models.reported[role] = supervisor.report_key(state)
    supervisor.print_ready(
        term,
        base_url,
        body,
        models,
        settings=settings,
        finish_detail=STOP_HINT,
        after=BACKGROUND_HINT,
    )
    if open_page:
        supervisor.open_browser(base_url)
    return 0


def _readyz(client: httpx.Client, base_url: str, flags: dict[str, bool]) -> dict[str, Any] | None:
    """/readyz를 물어 API 서버 · 작업 실행기 표시를 고친다. 둘 다 준비됐으면 답, 아니면 None."""
    try:
        response = client.get(f"{base_url}/readyz")
        body = response.json()
        flags["api"] = response.status_code == 200
        flags["worker"] = bool(body["info"]["worker"]["ok"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return body if flags["api"] and flags["worker"] else None


def _is_running(settings: Settings, base_url: str) -> bool:
    """DENT가 실행 중인지: 관리 프로세스가 살아 있거나 포트에서 DENT가 답한다."""
    record = supervisor.read_record(settings)
    if record is not None and supervisor.is_alive(record):
        return True
    return _port_owner(settings.port, base_url) == "dent"


def _already_running(
    settings: Settings, term: Terminal, base_url: str, args: argparse.Namespace
) -> bool:
    """이미 실행 중일 때: 브라우저 열기 · 종료 · 다시 실행을 고른다. 다시 실행이면 True(종료한 뒤 이어서 실행한다).

    터미널이 아니거나 -y면 묻지 않고 알린 뒤 브라우저만 연다.
    """
    if not term.interactive or args.yes:
        term.finish("DENT가 이미 실행 중입니다", base_url, STOP_HINT)
        if not args.no_browser:
            supervisor.open_browser(base_url)
        return False
    options = [
        Option("브라우저 열기"),
        Option("종료"),
        Option("다시 실행", "내장 모델을 다시 고름"),
    ]
    try:
        index = term.select(
            f"DENT가 이미 실행 중입니다 · {base_url}", options, default=0, hint=RUNNING_HINT
        )
    except KeyboardInterrupt:
        return False
    if index is None or index == 0:
        term.finish("열었습니다", base_url, STOP_HINT)
        supervisor.open_browser(base_url)
        return False
    _stop_running(settings, term)
    if index == 1:
        return False
    term.rail()
    return True


# ---------- 종료 · 상태 · 로그 ----------


def stop_command(settings: Settings, term: Terminal) -> int:
    """종료한다. 실행 중이 아니면 그렇다고 알린다."""
    if not _stop_running(settings, term):
        term.end("실행 중이 아닙니다")
    return 0


def _stop_running(settings: Settings, term: Terminal) -> bool:
    """실행 중인 DENT를 종료한다(관리 프로세스에 종료 요청 → 끝나기를 기다림). 무엇이든 종료했으면 True.

    관리 프로세스 없이 남은 프로세스 · 내장 DB(창을 닫았거나 강제로 끝난 뒤)도 정리한다.
    """
    record = supervisor.read_record(settings)
    if record is not None and supervisor.is_alive(record):
        supervisor.request_stop(settings)
        deadline = time.monotonic() + STOP_WAIT_S
        with term.status("종료하는 중 · API 서버 · 작업 실행기 · 모델 서버 · DB"):
            while supervisor.is_alive(record) and time.monotonic() < deadline:
                time.sleep(POLL_INTERVAL_S)
        if supervisor.is_alive(record):
            term.warn("제때 끝나지 않아 남은 것을 강제로 종료합니다")
            _stop_leftovers(settings)
        term.end("DENT를 종료했습니다")
        return True
    if _stop_leftovers(settings):
        term.end("남아 있던 DENT를 정리했습니다")
        return True
    supervisor.remove_record(settings)
    return False


def _stop_leftovers(settings: Settings) -> bool:
    """관리 프로세스 없이 남은 DENT 프로세스와 내장 DB를 종료한다. 무엇이든 종료했으면 True."""
    leftovers = supervisor.dent_processes(port=settings.port)
    has_database = (
        settings.uses_embedded_database
        and (settings.embedded_database_path / "postmaster.pid").exists()
    )
    if leftovers:
        supervisor.stop_leftovers(leftovers)
    if has_database:
        embedded_db.stop(settings)
    supervisor.remove_record(settings)
    return bool(leftovers) or has_database


def status_command(settings: Settings, term: Terminal) -> int:
    """실행 중인지 · 주소 · 실행한 지 얼마 · 서버와 모델 상태를 보인다."""
    base_url = f"http://{LOOPBACK_HOST}:{settings.port}"
    record = supervisor.read_record(settings)
    alive = record is not None and supervisor.is_alive(record)
    with httpx.Client(timeout=PROBE_TIMEOUT_S) as client:
        flags = {"api": False, "worker": False}
        body = _readyz(client, base_url, flags)
        if body is None and flags["api"]:
            body = _get_json(client, f"{base_url}/readyz")
    if not alive and body is None:
        if record is not None and record.state == supervisor.FAILED:
            term.warn(f"지난번에 실행하지 못했습니다 · {record.message}")
        leftovers = supervisor.dent_processes(port=settings.port)
        if leftovers:
            term.warn(
                f"주인 없이 남은 DENT 프로세스 {len(leftovers)}개 · uv run dent stop으로 정리하세요"
            )
        term.end("실행 중이 아닙니다 · 실행: uv run dent")
        return 0
    parts = [base_url]
    if record is not None:
        parts.append(f"{_uptime_text(time.time() - record.started_at)}째")
    if not alive:
        parts.append("관리 프로세스 없음 · 창에서 실행했거나 남은 것")
    term.done("실행 중", " · ".join(parts))
    if body is not None:
        worker = (
            str(body["info"]["worker"]["detail"])
            .removeprefix("실행 중 · ")
            .replace("대기 작업", "대기")
        )
        term.done("API 서버", "준비됨" if body.get("ready") else "준비 안 됨", rail=False)
        term.done("작업 실행기", worker, rail=False)
        for item in body.get("models", []):
            name, detail = _model_status(item)
            term.done(name, detail, rail=False)
        term.rail()
    term.end(f"{STOP_HINT} · 로그: uv run dent logs")
    return 0


def _model_status(item: dict[str, Any]) -> tuple[str, str]:
    """/readyz models 한 줄을 (이름, 값)으로: ('임베딩 · 내장', 'mps · 준비됨'), ('Jev', '연결됨 · …')."""
    name = ROLE_NAMES.get(str(item.get("role")), str(item.get("role")))
    embedded = item.get("embedded") is not None
    state = item.get("state")
    done, total = item.get("done_bytes") or 0, item.get("total_bytes") or 0
    percent = done * PERCENT // total if total else 0
    words = {
        "ok": "준비됨" if embedded else "연결됨",
        "downloading": f"받는 중 {percent}% · {bytes_text(done)} / {bytes_text(total)}",
        "verifying": f"파일 확인 중 {percent}%",
        "loading": "불러오는 중",
        "failed": f"실패 · {item.get('detail', '')}",
        "off": "미연결",
    }
    parts = [
        item.get("device") if embedded else item.get("model"),
        words.get(str(state), str(state)),
    ]
    if state == "ok" and not embedded and item.get("detail"):
        parts.append(str(item["detail"]))
    return (f"{name} · 내장" if embedded else name), " · ".join(str(part) for part in parts if part)


def _uptime_text(seconds: float) -> str:
    """실행한 지 얼마: '3시간 12분' · '12분' · '40초'."""
    hours, rest = divmod(int(seconds), SECONDS_PER_HOUR)
    minutes = rest // SECONDS_PER_MINUTE
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분"
    return f"{int(seconds)}초"


def logs_command(settings: Settings, term: Terminal) -> int:
    """관리 프로세스의 로그를 마지막 몇 줄부터 이어 본다. Ctrl+C는 보기만 멈춘다."""
    path = supervisor.log_path(settings)
    if not path.exists():
        term.header(APP_VERSION)
        term.end("로그가 없습니다 · 뒤에서 실행한 적이 없습니다")
        return 0
    try:
        with path.open(encoding="utf-8", errors="replace") as log:
            for line in log.read().splitlines()[-LOG_TAIL_LINES:]:
                term.console.print(_log_text(line))
            while True:
                line = log.readline()
                if line:
                    term.console.print(_log_text(line.rstrip("\r\n")))
                else:
                    time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        term.console.print()
        term.console.print(Text("로그 보기를 멈췄습니다 · DENT는 계속 돕니다", style="dim"))
    return 0


def _log_text(line: str) -> Text:
    """로그 한 줄에 색을 입힌다: 시각 흐림 · 경고 노랑 · 오류 빨강. 모양이 다르면 그대로."""
    matched = LOG_LINE.match(line)
    if matched is None:
        return Text(line)
    text = Text.assemble(
        (matched["time"] + "  ", "dim"), (matched["label"] + matched["gap"], "cyan")
    )
    level = matched["level"]
    if level:
        text.append(level, style="yellow bold" if level.startswith("경고") else "red bold")
    text.append(matched["message"])
    return text


def _show_log_tail(settings: Settings, term: Terminal) -> None:
    """실행하지 못했을 때 로그의 마지막 줄들을 흐리게 보인다."""
    try:
        lines = (
            supervisor.log_path(settings).read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return
    for line in lines[-FAILURE_TAIL_LINES:]:
        term.console.print(Text(f"   {line}", style="dim"))
    term.console.print(
        Text(f"   전체 로그: {supervisor.short_path(supervisor.log_path(settings))}", style="dim")
    )


def _get_json(client: httpx.Client, url: str) -> dict[str, Any] | None:
    """주소의 JSON 답. 안 되면 None."""
    try:
        return client.get(url).json()
    except (httpx.HTTPError, ValueError):
        return None


# ---------- 내장 모델 고르기 · 패키지 ----------


class UseLastChoices(Exception):
    """고르는 도중 Esc: 남은 역할은 모두 지난번대로 한다."""


def _choose_models(
    settings: Settings, term: Terminal, *, found: list[Device], ask: bool
) -> list[ModelChoice]:
    """역할마다 내장 모델을 올릴지 · 어디에 · 어떤 파일로 정한다. 물었으면 다음 기본값으로 적어 둔다."""
    last = embedded_models.load_last_choices(settings)
    decided: dict[ConnectionRole, ModelChoice | None] = {}
    use_last = not ask
    for role in MODEL_OF_ROLE:
        if not use_last:
            try:
                decided[role] = _ask_role(settings, term, role, last=last, found=found)
                continue
            except UseLastChoices:
                use_last = True
        decided[role] = _last_choice(settings, term, role, last=last, found=found)
    if ask:
        embedded_models.save_choices(settings, decided)
    return [choice for choice in decided.values() if choice is not None]


def _ask_role(
    settings: Settings,
    term: Terminal,
    role: ConnectionRole,
    *,
    last: dict[ConnectionRole, ModelChoice | None],
    found: list[Device],
) -> ModelChoice | None:
    """역할 하나의 올릴 곳과 파일을 묻는다. 올리지 않으면 None, Esc면 UseLastChoices."""
    model = supervisor.model_of(role)
    title = _role_title(role)
    off_index = len(found)
    default = _default_device_index(role, last=last, found=found)
    was_chosen = role in last
    options = [
        Option(device.label, _device_detail(device), _last_note(was_chosen, index == default))
        for index, device in enumerate(found)
    ]
    options.append(
        Option("올리지 않음", "연결 설정의 서버를 씀", _last_note(was_chosen, default == off_index))
    )
    index = term.select(
        f"{title} · {bytes_text(model.total_bytes)} · 올릴 곳",
        options,
        default=default,
        hint=DEVICE_HINT,
    )
    if index is None:
        raise UseLastChoices
    if index == off_index:
        term.chosen(title, "올리지 않음", "연결 설정의 서버를 씀")
        return None
    device = found[index]
    term.chosen(title, device.label, device.name)
    folder = _ask_files(settings, term, role, previous=last.get(role))
    return ModelChoice(role=role, device=device.key, folder=folder)


def _default_device_index(
    role: ConnectionRole, *, last: dict[ConnectionRole, ModelChoice | None], found: list[Device]
) -> int:
    """고르기의 처음 자리: 지난번 장치(없어졌으면 첫 장치). 고른 적이 없거나 지난번에 올리지 않았으면 '올리지 않음'.

    처음에는 '올리지 않음'에 둔다(Enter만 눌러 모르는 사이에 모델 파일 수 GB를 받지 않게).
    """
    if role not in last:
        return len(found)
    previous = last[role]
    if previous is None:
        return len(found)
    matched = [index for index, device in enumerate(found) if device.key == previous.device]
    return matched[0] if matched else 0


def _last_note(was_chosen: bool, is_default: bool) -> str:
    """지난번에 고른 자리 옆의 표."""
    return "지난번" if was_chosen and is_default else ""


def _ask_files(
    settings: Settings, term: Terminal, role: ConnectionRole, *, previous: ModelChoice | None
) -> str | None:
    """파일을 묻는다: storage/models에 받기(받아 둠) 또는 이 PC의 폴더. 이 PC의 폴더면 그 경로, 아니면 None."""
    model = supervisor.model_of(role)
    label = ROLE_LABELS[role]
    own = settings.embedded_models_path / model.key
    has_own = embedded_models.layout_of(own, model) is not None
    own_label = "받아 둠" if has_own else "받기"
    own_detail = (
        supervisor.short_path(own)
        if has_own
        else f"{supervisor.short_path(own)} · {bytes_text(model.total_bytes)}"
    )
    previous_folder = previous.folder if previous is not None else None
    default = 1 if previous_folder else 0
    options = [
        Option(own_label, own_detail, _last_note(previous is not None, default == 0)),
        Option("이 PC의 폴더", previous_folder or "경로 입력", _last_note(True, default == 1)),
    ]
    while True:
        index = term.select(f"{label} · 파일", options, default=default, hint=FILES_HINT)
        if index is None:
            index = default
        if index == 0:
            term.chosen(f"{label} · 파일", own_label, own_detail)
            return None
        answer = term.ask_path(f"{label} · 폴더", default=previous_folder or "", hint=PATH_HINT)
        if not answer:
            continue
        folder = Path(answer).expanduser().resolve()
        if _check_folder(settings, term, role, folder):
            return str(folder)


def _check_folder(settings: Settings, term: Terminal, role: ConnectionRole, folder: Path) -> bool:
    """이 PC의 폴더가 같은 모델인지 확인한다(처음이면 sha256, 확인한 적 있으면 크기 · 시각). 되면 True."""
    model = supervisor.model_of(role)
    label = ROLE_LABELS[role]
    title = f"{label} · 파일"
    if not folder.is_dir():
        term.warn(f"폴더가 없습니다 · {folder}")
        return False
    if embedded_models.files_ready(settings, folder, model) is not None:
        term.chosen(title, "이 PC의 폴더", f"{folder} · 확인됨")
        return True
    if embedded_models.layout_of(folder, model) is None:
        missing = embedded_models.first_missing(folder, model)
        term.warn(f"{model.key} 파일이 없거나 크기가 다릅니다 · {folder} · {missing}")
        return False
    count = len(model.files)
    try:
        with term.progress(f"{label} · sha256 확인", total=model.total_bytes) as advance:
            embedded_models.verify_files(settings, folder, model, on_progress=advance)
    except ExternalServiceError as error:
        term.warn(error.message)
        return False
    term.chosen(title, "이 PC의 폴더", f"{folder} · sha256 {count} / {count}")
    return True


def _last_choice(
    settings: Settings,
    term: Terminal,
    role: ConnectionRole,
    *,
    last: dict[ConnectionRole, ModelChoice | None],
    found: list[Device],
) -> ModelChoice | None:
    """지난번에 고른 것을 쓴다. 그 장치 · 폴더가 이제 없으면 말없이 바꾸지 않고 올리지 않는다."""
    title = _role_title(role)
    if role not in last:
        term.chosen(title, "올리지 않음", "고른 적 없음")
        return None
    choice = last[role]
    if choice is None:
        term.chosen(title, "올리지 않음", "지난번")
        return None
    device = next((item for item in found if item.key == choice.device), None)
    if device is None:
        term.warn(f"{title} · 지난번 장치({choice.device})가 이 PC에 없어 올리지 않습니다")
        return None
    folder = embedded_models.model_folder(settings, choice)
    has_files = embedded_models.layout_of(folder, choice.model) is not None
    if choice.folder is not None and not has_files:
        term.warn(f"{title} · 지난번 폴더에 모델 파일이 없어 올리지 않습니다 · {folder}")
        return None
    files = _files_text(choice, has_files=has_files)
    detail = " · ".join(part for part in (device.name, files, "지난번") if part)
    term.chosen(title, device.label, detail)
    return choice


def _ensure_packages(
    choices: list[ModelChoice], term: Terminal, *, found: list[Device], ask: bool
) -> list[ModelChoice]:
    """고른 모델에 필요한 패키지가 없으면 설치를 묻고 설치한다. 설치하지 않으면 모델을 올리지 않는다(빈 목록)."""
    if not choices:
        return choices
    models = [choice.model for choice in choices]
    missing = embedded_models.missing_packages(models)
    if not missing:
        return choices
    if not ask:
        term.warn(
            "모델 패키지(PyTorch 등)가 없어 이번에는 내장 모델을 올리지 않습니다 · "
            "uv run dent를 실행해서 설치를 고르세요"
        )
        return []
    options = [
        Option("설치하고 실행", f"{devices.machine_summary(found)} → 맞는 PyTorch 판"),
        Option("이번에는 올리지 않음", "연결 설정의 서버를 씀"),
    ]
    index = term.select(
        "모델 패키지가 없습니다 · PyTorch · transformers · laya",
        options,
        default=0,
        hint=INSTALL_HINT,
    )
    if index != 0:
        term.chosen("모델 패키지", "이번에는 올리지 않음")
        return []
    if not _install_packages(term):
        return []
    importlib.invalidate_caches()
    still = embedded_models.missing_packages(models)
    if still:
        term.warn(f"설치했지만 찾지 못했습니다 · {', '.join(still)}")
        return []
    return choices


def _install_packages(term: Terminal) -> bool:
    """uv로 모델 패키지를 이 PC에 맞는 판으로 설치한다. DENT의 패키지 버전은 잠금 파일대로 둔다. 되면 True."""
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        term.warn("uv를 찾지 못해 설치하지 못했습니다 · uv run dent로 실행하세요")
        return False
    started = time.monotonic()
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dent-") as folder:
        constraints = Path(folder) / "constraints.txt"
        try:
            subprocess.run(
                embedded_models.export_command(uv, constraints),
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=EXPORT_TIMEOUT_S,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            term.warn("설치를 준비하지 못했습니다 · uv export")
            return False
        code = _run_streaming(
            embedded_models.install_command(uv, sys.executable, constraints), term, lines
        )
    if code != 0:
        term.warn("모델 패키지를 설치하지 못했습니다")
        for line in lines[-INSTALL_TAIL_LINES:]:
            term.note(line)
        term.rail()
        return False
    torch_line = next((line for line in lines if line.startswith("+ torch==")), "")
    installed = torch_line.removeprefix("+ ").replace("==", " ")
    took = elapsed_text(time.monotonic() - started)
    term.done("모델 패키지 설치", " · ".join(part for part in (installed, took) if part))
    return True


def _run_streaming(command_line: list[str], term: Terminal, lines: list[str]) -> int:
    """명령을 돌리며 마지막 출력 한 줄을 도는 줄에 보인다. 출력은 lines에 쌓는다. 끝난 코드를 돌려준다."""
    process = subprocess.Popen(
        command_line,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    with term.status("모델 패키지 설치") as update:
        if process.stdout is not None:
            for raw in process.stdout:
                line = raw.strip()
                if line:
                    lines.append(line)
                    update(line)
        return process.wait()


def _clear_leftovers(settings: Settings, choices: list[ModelChoice], term: Terminal) -> None:
    """모델 서버 포트가 비었는지 본다. 앞서 남은 DENT 모델 서버면 종료하고 비운다(이번 선택으로 새로 띄우려고).

    다른 프로그램이 쓰면 StartupError.
    """
    for choice in choices:
        port = embedded_models.model_port(settings, choice.role)
        if not supervisor.is_port_in_use(port):
            continue
        server = embedded_models.probe_server(port)
        leftover_pid = (
            server.pid if server is not None and server.model == choice.model.key else None
        )
        if leftover_pid is None:
            raise StartupError(
                f"{port} 포트를 다른 프로그램이 쓰고 있습니다. 내장 모델은 PORT 다음 두 포트"
                "(PORT+1 · PORT+2)를 씁니다. 그 프로그램을 종료하거나 .env의 PORT를 바꾸세요."
            )
        _stop_pid(leftover_pid)
        deadline = time.monotonic() + LEFTOVER_STOP_TIMEOUT_S
        while supervisor.is_port_in_use(port) and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_S)
        label = ROLE_LABELS[choice.role]
        if supervisor.is_port_in_use(port):
            raise StartupError(f"앞서 남은 {label} 모델 서버를 종료하지 못했습니다 · 포트 {port}")
        term.done(f"{label} · 앞서 남은 모델 서버", "종료했습니다 · 새로 띄웁니다")


def _device_detail(device: Device) -> str:
    """장치 줄의 흐린 값: 'RTX 4090 · 여유 20.1 / 24.0 GB'."""
    parts = [device.name]
    if device.total_bytes is not None and device.free_bytes is not None:
        total = device.total_bytes / 1024**3
        free = device.free_bytes / 1024**3
        parts.append(f"여유 {free:.1f} / {total:.1f} GB")
    return " · ".join(part for part in parts if part)


def _files_text(choice: ModelChoice, *, has_files: bool) -> str:
    """고른 파일을 짧게: '받아 둠' · '받기 · 2.1 GB'(아직 없음) · '이 PC · /경로'."""
    if choice.folder is not None:
        return f"이 PC · {choice.folder}"
    return "받아 둠" if has_files else f"받기 · {bytes_text(choice.model.total_bytes)}"


def _port_owner(port: int, base_url: str) -> str:
    """포트를 누가 쓰는지 본다: 'free', 'dent', 'other'."""
    if not supervisor.is_port_in_use(port):
        return "free"
    try:
        response = httpx.get(f"{base_url}/healthz", timeout=PROBE_TIMEOUT_S)
        if response.json().get("app") == "dent":
            return "dent"
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return "other"


def _stop_pid(pid: int) -> None:
    """이 런처의 자식이 아닌 프로세스(앞서 남은 모델 서버)에 종료하라고 알린다. 이미 없으면 아무것도 하지 않는다."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _role_title(role: ConnectionRole) -> str:
    """'임베딩 · bge-m3'"""
    return f"{ROLE_LABELS[role]} · {supervisor.model_of(role).key}"
