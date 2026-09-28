"""런처 테스트: 지난번 선택으로 실행(-y · 터미널 아님), 없어진 장치는 올리지 않기, 모델 패키지가 없을 때,
앞서 남은 모델 서버 종료 · 다른 프로그램이면 멈춤, 종료(stop) · 상태(status) · 로그 줄 · 이미 실행 중일 때."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from dent import launcher, supervisor
from dent.system import embedded_models
from dent.system.config import Settings
from dent.system.devices import Device
from dent.system.embedded_models import ModelChoice, ServerPhase, ServerState
from dent.system.exceptions import StartupError
from dent.system.models import ConnectionRole
from dent.system.terminal import Terminal

CPU = Device("cpu", "CPU", "8코어")
GPU = Device("cuda:0", "GPU 0", "RTX 4090")


def _terminal() -> tuple[Terminal, io.StringIO]:
    buffer = io.StringIO()
    return Terminal(Console(file=buffer, width=120, color_system=None)), buffer


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_dir=tmp_path, port=8100)


def test_choose_models_without_asking_puts_up_nothing_before_first_choice(tmp_path: Path):
    # 준비
    term, buffer = _terminal()

    # 실행
    choices = launcher._choose_models(_settings(tmp_path), term, found=[CPU], ask=False)

    # 확인
    assert choices == []
    assert "임베딩 · bge-m3 → 올리지 않음 · 고른 적 없음" in buffer.getvalue()


def test_choose_models_without_asking_uses_last_choice(tmp_path: Path):
    # 준비: 지난번에 임베딩은 GPU 0, Jev는 올리지 않음
    settings = _settings(tmp_path)
    embedding = ModelChoice(ConnectionRole.EMBEDDING, device="cuda:0")
    embedded_models.save_choices(
        settings, {ConnectionRole.EMBEDDING: embedding, ConnectionRole.JEV: None}
    )
    term, buffer = _terminal()

    # 실행
    choices = launcher._choose_models(settings, term, found=[GPU, CPU], ask=False)

    # 확인
    assert choices == [embedding]
    assert "임베딩 · bge-m3 → GPU 0 · RTX 4090 · 받기 · 2.1 GB · 지난번" in buffer.getvalue()
    assert "Jev · laya → 올리지 않음 · 지난번" in buffer.getvalue()


def test_choose_models_skips_device_that_is_gone(tmp_path: Path):
    # 준비: 지난번에 고른 GPU 1이 이제 없다
    settings = _settings(tmp_path)
    embedded_models.save_choices(
        settings,
        {ConnectionRole.EMBEDDING: ModelChoice(ConnectionRole.EMBEDDING, device="cuda:1")},
    )
    term, buffer = _terminal()

    # 실행
    choices = launcher._choose_models(settings, term, found=[GPU, CPU], ask=False)

    # 확인: 말없이 다른 장치로 바꾸지 않고 올리지 않는다
    assert choices == []
    assert "지난번 장치(cuda:1)가 이 PC에 없어 올리지 않습니다" in buffer.getvalue()


def test_ensure_packages_without_asking_puts_up_nothing_when_packages_are_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    # 준비
    monkeypatch.setattr(embedded_models, "missing_packages", lambda _models: ["torch"])
    term, buffer = _terminal()
    choice = ModelChoice(ConnectionRole.EMBEDDING, device="cpu")

    # 실행
    kept = launcher._ensure_packages([choice], term, found=[CPU], ask=False)

    # 확인: 묻지 않고는 설치하지 않는다
    assert kept == []
    assert "모델 패키지(PyTorch 등)가 없어" in buffer.getvalue()


def test_clear_leftovers_does_nothing_when_ports_are_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비
    monkeypatch.setattr(supervisor, "is_port_in_use", lambda _port: False)
    term, buffer = _terminal()

    # 실행
    launcher._clear_leftovers(
        _settings(tmp_path), [ModelChoice(ConnectionRole.EMBEDDING, device="cpu")], term
    )

    # 확인
    assert buffer.getvalue() == ""


def test_clear_leftovers_stops_leftover_model_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: 앞서 강제로 끝나 같은 모델의 서버가 남아 있다(종료하라고 하면 포트가 빈다)
    stopped: list[int] = []
    monkeypatch.setattr(supervisor, "is_port_in_use", lambda _port: not stopped)
    leftover = ServerState(phase=ServerPhase.READY, model="bge-m3", pid=4321)
    monkeypatch.setattr(embedded_models, "probe_server", lambda _port: leftover)
    monkeypatch.setattr(launcher, "_stop_pid", stopped.append)
    term, buffer = _terminal()

    # 실행
    launcher._clear_leftovers(
        _settings(tmp_path), [ModelChoice(ConnectionRole.EMBEDDING, device="cpu")], term
    )

    # 확인: 이번에 고른 장치 · 파일로 새로 띄우려고 종료한다
    assert stopped == [4321]
    assert "앞서 남은 모델 서버 · 종료했습니다" in buffer.getvalue()


def test_clear_leftovers_stops_when_other_program_uses_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비
    monkeypatch.setattr(supervisor, "is_port_in_use", lambda _port: True)
    monkeypatch.setattr(embedded_models, "probe_server", lambda _port: None)
    term, _buffer = _terminal()

    # 실행
    with pytest.raises(StartupError) as caught:
        launcher._clear_leftovers(
            _settings(tmp_path), [ModelChoice(ConnectionRole.EMBEDDING, device="cpu")], term
        )

    # 확인
    assert caught.value.message.startswith("8101 포트를 다른 프로그램이 쓰고 있습니다.")


def test_default_device_is_off_before_first_choice():
    # 확인: 처음에는 '올리지 않음'(목록 끝), 지난번 장치가 있으면 그 자리
    assert launcher._default_device_index(ConnectionRole.EMBEDDING, last={}, found=[GPU, CPU]) == 2
    last = {ConnectionRole.EMBEDDING: ModelChoice(ConnectionRole.EMBEDDING, device="cpu")}
    assert (
        launcher._default_device_index(ConnectionRole.EMBEDDING, last=last, found=[GPU, CPU]) == 1
    )


def test_stop_command_says_not_running_when_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: 기록도 남은 프로세스도 없다
    monkeypatch.setattr(supervisor, "dent_processes", lambda *, port: [])
    term, buffer = _terminal()

    # 실행
    code = launcher.stop_command(_settings(tmp_path), term)

    # 확인
    assert code == 0
    assert buffer.getvalue().strip().endswith("실행 중이 아닙니다")


def test_stop_command_asks_supervisor_and_waits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 준비: 관리 프로세스가 살아 있다가 종료 요청을 받고 끝난다
    settings = _settings(tmp_path)
    supervisor.write_record(
        settings, supervisor.RunRecord(pid=1, created=0.0, port=8100, started_at=0.0)
    )
    answers = iter([True, True, False])
    monkeypatch.setattr(supervisor, "is_alive", lambda _record: next(answers, False))
    term, buffer = _terminal()

    # 실행
    launcher.stop_command(settings, term)

    # 확인: 종료 요청 파일을 남기고, 끝나기를 기다려 알린다
    assert supervisor.stop_requested(settings)
    assert "DENT를 종료했습니다" in buffer.getvalue()


def test_stop_command_cleans_up_leftovers_without_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: 창을 닫아 관리 프로세스 없이 API 서버가 남았다
    stopped: list[object] = []
    monkeypatch.setattr(supervisor, "dent_processes", lambda *, port: ["API 서버"])
    monkeypatch.setattr(supervisor, "stop_leftovers", lambda found: stopped.extend(found) or 1)
    term, buffer = _terminal()

    # 실행
    launcher.stop_command(_settings(tmp_path), term)

    # 확인
    assert stopped == ["API 서버"]
    assert "남아 있던 DENT를 정리했습니다" in buffer.getvalue()


def test_status_command_says_not_running_and_last_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: 지난번에 실행하지 못한 기록이 남아 있다(관리 프로세스는 없다)
    settings = Settings(_env_file=None, storage_dir=tmp_path, port=_free_port())
    record = supervisor.RunRecord(pid=1, created=0.0, port=settings.port, started_at=0.0)
    record.state = supervisor.FAILED
    record.message = "5432 포트를 다른 프로그램이 씁니다"
    supervisor.write_record(settings, record)
    monkeypatch.setattr(supervisor, "is_alive", lambda _record: False)
    monkeypatch.setattr(supervisor, "dent_processes", lambda *, port: [])
    term, buffer = _terminal()

    # 실행
    launcher.status_command(settings, term)

    # 확인
    output = buffer.getvalue()
    assert "지난번에 실행하지 못했습니다 · 5432 포트를 다른 프로그램이 씁니다" in output
    assert output.strip().endswith("실행 중이 아닙니다 · 실행: uv run dent")


def test_model_status_reads_readyz_rows():
    # 확인
    assert launcher._model_status(
        {"role": "embedding", "embedded": "bge-m3", "state": "ok", "device": "mps", "detail": ""}
    ) == ("임베딩 · 내장", "mps · 준비됨")
    assert launcher._model_status(
        {
            "role": "jev",
            "embedded": "laya",
            "state": "downloading",
            "device": None,
            "done_bytes": 1024**3,
            "total_bytes": 2 * 1024**3,
        }
    ) == ("Jev · 내장", "받는 중 50% · 1.0 GB / 2.0 GB")
    assert launcher._model_status({"role": "llm", "embedded": None, "state": "off"}) == (
        "LLM",
        "미연결",
    )


def test_uptime_text_uses_hours_and_minutes():
    # 확인
    assert launcher._uptime_text(3 * 3600 + 12 * 60 + 5) == "3시간 12분"
    assert launcher._uptime_text(12 * 60 + 5) == "12분"
    assert launcher._uptime_text(40) == "40초"


def test_log_text_colors_level_and_keeps_other_lines():
    # 실행
    warned = launcher._log_text("12:01:03  작업    경고  임베딩 요청 다시 보냄")
    step = launcher._log_text("◇  내장 DB · PostgreSQL 18.6")

    # 확인: 글은 그대로, 경고에만 색이 붙는다
    assert warned.plain == "12:01:03  작업    경고  임베딩 요청 다시 보냄"
    assert any("yellow" in str(span.style) for span in warned.spans)
    assert step.plain == "◇  내장 DB · PostgreSQL 18.6"


def test_already_running_without_terminal_only_tells_and_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: 백그라운드에서 한 번 더 실행했다
    opened: list[str] = []
    monkeypatch.setattr(supervisor, "open_browser", opened.append)
    term, buffer = _terminal()
    args = launcher.argparse.Namespace(yes=False, no_browser=False)

    # 실행
    start_again = launcher._already_running(
        _settings(tmp_path), term, "http://127.0.0.1:8100", args
    )

    # 확인
    assert start_again is False
    assert opened == ["http://127.0.0.1:8100"]
    assert "DENT가 이미 실행 중입니다" in buffer.getvalue()


def _free_port() -> int:
    """아무도 쓰지 않는 포트 (상태를 물어도 답이 없게)."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
