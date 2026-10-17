"""관리 프로세스 테스트: 기록 · 종료 요청, 자식 출력 거르기, 진행 알림 칸 · 한 줄씩 알리기, 자식에게 넘기는 환경 변수,
종료 요청이 오면 지켜보기를 멈춤, 작업 실행기 늘리기 · 줄이기 · 멈춘 번호만 다시 띄우기 · 함께 종료."""

import io
import json
import os
from pathlib import Path

import psutil
import pytest
from rich.console import Console

from dent import supervisor
from dent.system import embedded_models, workers
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


# ---------- 작업 실행기 늘리기 · 줄이기 · 다시 띄우기 ----------


class _FakeWorker:
    """가짜 작업 실행기 프로세스: 종료하라고 하면 하던 작업이 끝난 것으로 치고 code로 끝난다."""

    def __init__(self, slot: int) -> None:
        self.slot = slot
        self.code: int | None = None
        self.asked_to_stop = False

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.asked_to_stop = True

    def send_signal(self, _signum: int) -> None:
        self.asked_to_stop = True


def _fake_spawns(monkeypatch: pytest.MonkeyPatch) -> list[_FakeWorker]:
    spawned: list[_FakeWorker] = []

    def spawn(slot: int, **_kwargs: object) -> _FakeWorker:
        worker = _FakeWorker(slot)
        spawned.append(worker)
        return worker

    monkeypatch.setattr(supervisor, "_spawn_worker", spawn)
    return spawned


def _pool_kwargs(tmp_path: Path) -> dict:
    term, _buffer = _terminal()
    return {
        "env": {},
        "output": supervisor.ChildOutput(term),
        "settings": _settings(tmp_path),
    }


def test_sync_pool_spawns_missing_slots_and_drains_extra_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비
    spawned = _fake_spawns(monkeypatch)
    pool = supervisor.WorkerPool()
    kwargs = _pool_kwargs(tmp_path)

    # 실행: 넷으로 띄우고 둘로 줄인다
    supervisor.sync_pool(pool, count=4, **kwargs)
    supervisor.sync_pool(pool, count=2, **kwargs)

    # 확인: 3 · 4에만 종료하라고 알리고, 끝날 때까지 '끝나면 종료'로 둔다
    assert [worker.slot for worker in spawned] == [1, 2, 3, 4]
    assert [worker.asked_to_stop for worker in spawned] == [False, False, True, True]
    assert pool.states[3] is workers.SlotState.DRAINING
    assert workers.read_pool_state(kwargs["settings"]) == {
        1: workers.SlotState.STARTING,
        2: workers.SlotState.STARTING,
        3: workers.SlotState.DRAINING,
        4: workers.SlotState.DRAINING,
    }


def test_reap_workers_keeps_drained_slot_stopped_and_respawns_it_when_count_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: 넷 → 셋으로 줄였고, 4가 하던 작업을 끝내고 종료했다
    spawned = _fake_spawns(monkeypatch)
    pool = supervisor.WorkerPool()
    kwargs = _pool_kwargs(tmp_path)
    term, _buffer = _terminal()
    supervisor.sync_pool(pool, count=4, **kwargs)
    supervisor.sync_pool(pool, count=3, **kwargs)
    spawned[3].code = 0

    # 실행
    supervisor.reap_workers(pool, term=term, **kwargs)
    stopped = pool.states[4]
    supervisor.sync_pool(pool, count=4, **kwargs)

    # 확인: 줄여서 끝난 번호는 다시 띄우지 않다가, 수를 늘리면 다시 띄운다
    assert stopped is workers.SlotState.STOPPED
    assert [worker.slot for worker in spawned] == [1, 2, 3, 4, 4]


def test_reap_workers_respawns_crashed_slot_until_it_crashes_too_often(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비
    spawned = _fake_spawns(monkeypatch)
    pool = supervisor.WorkerPool()
    kwargs = _pool_kwargs(tmp_path)
    term, buffer = _terminal()
    supervisor.sync_pool(pool, count=2, **kwargs)

    # 실행: 실행기 2가 거듭 멈춘다
    for _ in range(supervisor.MAX_RESTARTS):
        latest = next(worker for worker in reversed(spawned) if worker.slot == 2)
        latest.code = 1
        supervisor.reap_workers(pool, term=term, **kwargs)

    # 확인: 한도까지는 그 번호만 다시 띄우고, 넘으면 두고 알린다. 실행기 1은 그대로다.
    assert [worker.slot for worker in spawned].count(2) == supervisor.MAX_RESTARTS
    assert pool.states[2] is workers.SlotState.FAILED
    assert 2 not in pool.spawned and 1 in pool.spawned
    assert "다시 띄우지 않습니다" in buffer.getvalue()
    supervisor.sync_pool(pool, count=2, **kwargs)
    assert [worker.slot for worker in spawned].count(2) == supervisor.MAX_RESTARTS


def test_stop_asks_all_workers_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 준비
    spawned = _fake_spawns(monkeypatch)
    pool = supervisor.WorkerPool()
    supervisor.sync_pool(pool, count=3, **_pool_kwargs(tmp_path))
    for worker in spawned:
        worker.wait = lambda timeout=None: 0  # type: ignore[attr-defined]
    term, _buffer = _terminal()

    # 실행
    supervisor._stop({}, term, pool=pool)

    # 확인
    assert all(worker.asked_to_stop for worker in spawned)
    assert pool.spawned == {}
