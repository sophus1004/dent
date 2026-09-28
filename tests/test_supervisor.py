"""관리 프로세스 테스트: 기록 · 종료 요청, 자식 출력 거르기, 진행 알림 칸 · 한 줄씩 알리기, 자식에게 넘기는 환경 변수,
종료 요청이 오면 지켜보기를 멈춤."""

import io
import json
import os
from pathlib import Path

import psutil
import pytest
from rich.console import Console

from dent import supervisor
from dent.system import embedded_models
from dent.system.config import Settings
from dent.system.embedded_models import ModelChoice, ServerPhase, ServerState
from dent.system.models import ConnectionRole
from dent.system.terminal import Terminal


def _terminal() -> tuple[Terminal, io.StringIO]:
    buffer = io.StringIO()
    return Terminal(Console(file=buffer, width=120, color_system=None)), buffer


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_dir=tmp_path, port=8100)


def test_record_round_trip_and_alive_check(tmp_path: Path):
    # 준비: 이 프로세스를 관리 프로세스로 적는다
    settings = _settings(tmp_path)
    me = psutil.Process()
    record = supervisor.RunRecord(
        pid=me.pid, created=me.create_time(), port=8100, started_at=1.0, steps={"내장 DB": "18.6"}
    )

    # 실행
    supervisor.write_record(settings, record)
    found = supervisor.read_record(settings)

    # 확인: 같은 번호라도 만든 시각이 다르면 다른 프로세스로 본다
    assert found == record
    assert supervisor.is_alive(record)
    record.created -= 100
    assert not supervisor.is_alive(record)


def test_request_stop_is_seen_and_cleared(tmp_path: Path):
    # 준비
    settings = _settings(tmp_path)

    # 실행 · 확인
    assert not supervisor.stop_requested(settings)
    supervisor.request_stop(settings)
    assert supervisor.stop_requested(settings)
    supervisor.remove_record(settings)
    assert not supervisor.stop_requested(settings)


def test_watch_returns_when_stop_is_requested(tmp_path: Path):
    # 준비
    settings = _settings(tmp_path)
    supervisor.request_stop(settings)
    term, _buffer = _terminal()

    # 실행: 지켜볼 프로세스가 없어도 종료 요청에 바로 멈춘다
    stopped = supervisor._watch(
        {},
        supervisor.ModelServers(),
        settings=settings,
        term=term,
        output=supervisor.ChildOutput(term),
    )

    # 확인
    assert stopped is None


def test_dent_processes_skips_this_process():
    # 확인: 테스트 프로세스 자신은 DENT 프로세스로 잡지 않는다
    assert all(process.pid != os.getpid() for process in supervisor.dent_processes(port=8100))


def test_child_output_hides_model_server_lines_and_info_until_ready():
    # 준비
    term, buffer = _terminal()
    output = supervisor.ChildOutput(term)

    # 실행
    output._show("임베딩 모델", "[임베딩 모델] 오류: 모델 파일을 받지 못했습니다")
    output._show("API", "[API] 확인을 마쳤습니다.")
    output._show("API", "[API] 경고: 느립니다")
    output.show_info = True
    output._show("작업 실행기", "[작업 실행기] 가져오기 끝")

    # 확인: 모델 서버의 줄은 로그 파일에만, 정보 줄은 실행된 뒤에만
    lines = buffer.getvalue().splitlines()
    assert [line[10:] for line in lines] == ["API     경고  느립니다", "작업    가져오기 끝"]


def test_report_key_moves_every_ten_percent_while_downloading():
    # 준비
    def downloading(done: int) -> ServerState:
        return ServerState(phase=ServerPhase.DOWNLOADING, done_bytes=done, total_bytes=100)

    # 확인
    assert supervisor.report_key(downloading(5)) == supervisor.report_key(downloading(9))
    assert supervisor.report_key(downloading(9)) != supervisor.report_key(downloading(10))
    assert supervisor.report_key(ServerState(phase=ServerPhase.READY)) == (ServerPhase.READY, 0)
    assert supervisor.report_key(None) == (None, 0)


def test_child_env_passes_choices_to_child_processes():
    # 준비
    choice = ModelChoice(ConnectionRole.JEV, device="mps", folder="/models/laya")

    # 실행
    env = supervisor.child_env([choice])

    # 확인
    assert json.loads(env[embedded_models.RUN_ENV]) == {
        "jev": {"model": "laya", "device": "mps", "folder": "/models/laya"}
    }
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_report_models_tells_each_change_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 준비: 모델 서버가 불러오는 중 → 준비됨으로 바뀐다
    answers = iter(
        [
            ServerState(phase=ServerPhase.LOADING, device="mps"),
            ServerState(phase=ServerPhase.LOADING, device="mps"),
            ServerState(phase=ServerPhase.READY, device="mps"),
        ]
    )
    monkeypatch.setattr(embedded_models, "probe_server", lambda _port: next(answers))
    models = supervisor.ModelServers()
    models.spawned[ConnectionRole.EMBEDDING] = None  # type: ignore[assignment]
    models.device_texts[ConnectionRole.EMBEDDING] = "Apple GPU"
    term, buffer = _terminal()

    # 실행: 세 번 묻는다
    for _ in range(3):
        supervisor.report_models(models, settings=_settings(tmp_path), term=term)

    # 확인: 같은 단계는 한 번만, 준비됨에는 장치와 걸린 시간
    lines = [line[10:] for line in buffer.getvalue().splitlines()]
    assert lines[0] == "임베딩  불러오는 중 · mps"
    assert lines[1].startswith("임베딩  준비됨 · mps · ")
    assert len(lines) == 2


def test_report_models_tells_failure_as_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 준비
    failed = ServerState(phase=ServerPhase.FAILED, detail="GPU 2번이 없습니다 · 이 PC의 GPU 1개")
    monkeypatch.setattr(embedded_models, "probe_server", lambda _port: failed)
    models = supervisor.ModelServers()
    models.spawned[ConnectionRole.JEV] = None  # type: ignore[assignment]
    models.device_texts[ConnectionRole.JEV] = "GPU 2"
    term, buffer = _terminal()

    # 실행
    supervisor.report_models(models, settings=_settings(tmp_path), term=term)

    # 확인
    assert (
        buffer.getvalue().splitlines()[0][10:]
        == "Jev     오류  GPU 2번이 없습니다 · 이 PC의 GPU 1개"
    )
