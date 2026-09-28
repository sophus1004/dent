"""내장 모델 서버 테스트: 준비 전 /health · 503, 준비 뒤 넘기기, 실패 까닭, 파일 받기 · 이 PC 폴더 확인 → 장치 확인 →
불러오기 순서, 없는 장치는 실패, 임베딩 앱, laya 체크포인트 자리.

torch 없이 돈다: 모델을 불러오는 함수(loader)와 장치 확인을 가짜로 넘긴다(장치 확인 자체는 torch가 있을 때만 잰다).
"""

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, status

from dent import model_server
from dent.model_server import ModelApp, ModelState
from dent.system import embedded_models
from dent.system.config import Settings
from dent.system.embedded_models import EmbeddedModel, Layout, ModelChoice, ModelFile, ServerPhase
from dent.system.exceptions import ExternalServiceError
from dent.system.models import ConnectionRole

CONTENT = b"weights"

MODEL = EmbeddedModel(
    key="small",
    role=ConnectionRole.EMBEDDING,
    repo="test/small",
    revision="abc123",
    served_name="test/small",
    packages=(),
    files=(ModelFile("w.bin", "w.bin", len(CONTENT), hashlib.sha256(CONTENT).hexdigest()),),
)


def _inner_app() -> FastAPI:
    """준비된 뒤 넘겨받을 가짜 모델 앱."""
    app = FastAPI()

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"data": [{"id": "test/small"}]}

    return app


async def _get(app: Any, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://model.test") as client:
        return await client.get(path)


def _run_prepare(app: ModelApp) -> None:
    """준비 스레드를 띄우고 끝날 때까지 기다린다."""
    app.start()
    assert app.thread is not None
    app.thread.join(timeout=5)


async def test_model_app_answers_health_and_503_before_ready():
    # 준비
    app = ModelApp(ModelState(MODEL), prepare=lambda _state: _inner_app())

    # 실행
    health = await _get(app, "/health")
    other = await _get(app, "/v1/models")

    # 확인
    assert health.status_code == status.HTTP_200_OK
    assert health.json()["app"] == "dent-model"
    assert health.json()["status"] == "loading"
    assert other.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


async def test_model_app_passes_requests_to_model_when_ready():
    # 준비
    def prepare(state: ModelState) -> Any:
        state.loading("cpu")
        state.ready({"dim": 8})
        return _inner_app()

    app = ModelApp(ModelState(MODEL), prepare=prepare)
    _run_prepare(app)

    # 실행
    health = await _get(app, "/health")
    models = await _get(app, "/v1/models")

    # 확인
    assert health.json()["status"] == "ok"
    assert (health.json()["dim"], health.json()["device"]) == (8, "cpu")
    assert models.json() == {"data": [{"id": "test/small"}]}


async def test_model_app_keeps_failure_reason_in_health():
    # 준비
    def prepare(_state: ModelState) -> Any:
        raise ExternalServiceError("모델 파일을 받지 못했습니다 · w.bin · 연결 거부")

    app = ModelApp(ModelState(MODEL), prepare=prepare)
    _run_prepare(app)

    # 실행
    health = await _get(app, "/health")

    # 확인: 서버는 끝나지 않고 까닭을 둔다
    assert health.json()["status"] == "failed"
    assert health.json()["detail"] == "모델 파일을 받지 못했습니다 · w.bin · 연결 거부"


async def test_model_app_hides_unexpected_error_behind_log_hint():
    # 준비
    def prepare(_state: ModelState) -> Any:
        raise RuntimeError("/Users/someone/secret/path")

    app = ModelApp(ModelState(MODEL), prepare=prepare)
    _run_prepare(app)

    # 실행
    health = await _get(app, "/health")

    # 확인
    assert health.json()["detail"] == model_server.LOAD_FAILED_DETAIL


def _fake_loader(calls: list[tuple[Path, Layout, str]], state: ModelState, phases: list[Any]):
    """불러오기 가짜: 받은 값과 그때의 단계를 적고 가짜 앱을 준다."""

    def load(folder: Path, layout: Layout, device: str) -> tuple[Any, dict[str, Any]]:
        phases.append(state.phase)
        calls.append((folder, layout, device))
        return _inner_app(), {"dim": 8}

    return load


def _cpu(requested: str) -> tuple[str, str]:
    """장치 확인 가짜: 고른 대로 쓴다."""
    return requested, requested


def test_prepare_model_downloads_into_own_folder_then_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 준비: storage/models에 파일이 없어 받는다(가짜 받기)
    settings = Settings(_env_file=None, storage_dir=tmp_path)
    state = ModelState(MODEL)
    phases: list[Any] = []
    calls: list[tuple[Path, Layout, str]] = []

    def fake_download(
        _settings: Settings, folder: Path, _model: EmbeddedModel, *, on_progress: Any
    ) -> None:
        phases.append(state.phase)
        folder.mkdir(parents=True)
        (folder / "w.bin").write_bytes(CONTENT)
        on_progress(len(CONTENT))

    monkeypatch.setattr(embedded_models, "download_files", fake_download)
    choice = ModelChoice(ConnectionRole.EMBEDDING, device="cpu")

    # 실행
    model_server.prepare_model(
        state,
        settings=settings,
        choice=choice,
        loader=_fake_loader(calls, state, phases),
        resolve_device=_cpu,
    )

    # 확인
    assert phases == [ServerPhase.DOWNLOADING, ServerPhase.LOADING]
    assert calls == [(tmp_path / "models" / "bge-m3", Layout.FILES, "cpu")]
    assert state.phase is ServerPhase.READY
    assert state.health()["device"] == "cpu"


def test_prepare_model_verifies_folder_of_this_pc_once(tmp_path: Path):
    # 준비: 이 PC의 폴더(같은 파일)
    settings = Settings(_env_file=None, storage_dir=tmp_path / "storage")
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "w.bin").write_bytes(CONTENT)
    choice = ModelChoice(ConnectionRole.EMBEDDING, device="cpu", folder=str(mine))
    first_phases: list[Any] = []
    second_phases: list[Any] = []
    calls: list[tuple[Path, Layout, str]] = []

    # 실행: 두 번 실행한다
    first = ModelState(MODEL)
    model_server.prepare_model(
        first,
        settings=settings,
        choice=choice,
        loader=_fake_loader(calls, first, first_phases),
        resolve_device=_cpu,
    )
    second = ModelState(MODEL)
    model_server.prepare_model(
        second,
        settings=settings,
        choice=choice,
        loader=_fake_loader(calls, second, second_phases),
        resolve_device=_cpu,
    )

    # 확인: 처음에는 sha256을 재고(확인하는 중), 다음에는 기록을 보고 바로 불러온다. 폴더는 그 자리 그대로.
    assert first.done_bytes == len(CONTENT)
    assert calls == [(mine, Layout.FILES, "cpu"), (mine, Layout.FILES, "cpu")]
    assert second.done_bytes == 0
    assert [path.name for path in mine.iterdir()] == ["w.bin"]


def test_prepare_model_fails_when_folder_has_other_files(tmp_path: Path):
    # 준비: 크기는 같은데 내용이 다른 파일
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "w.bin").write_bytes(b"X" * len(CONTENT))
    choice = ModelChoice(ConnectionRole.EMBEDDING, device="cpu", folder=str(mine))

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        model_server.prepare_model(
            ModelState(MODEL),
            settings=Settings(_env_file=None, storage_dir=tmp_path / "storage"),
            choice=choice,
            loader=_fake_loader([], ModelState(MODEL), []),
            resolve_device=_cpu,
        )

    # 확인
    assert caught.value.message.startswith("파일이 다릅니다(sha256)")


def test_prepare_model_fails_when_device_is_missing(tmp_path: Path):
    # 준비: GPU가 하나뿐인데 2번을 골랐다
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "w.bin").write_bytes(CONTENT)
    choice = ModelChoice(ConnectionRole.EMBEDDING, device="cuda:2", folder=str(mine))

    def one_gpu(requested: str) -> tuple[str, str]:
        raise ExternalServiceError(model_server.MISSING_GPU_MESSAGE.format(index=2, count=1))

    state = ModelState(MODEL)

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        model_server.prepare_model(
            state,
            settings=Settings(_env_file=None, storage_dir=tmp_path / "storage"),
            choice=choice,
            loader=_fake_loader([], state, []),
            resolve_device=one_gpu,
        )

    # 확인: 말없이 다른 장치로 올리지 않는다
    assert caught.value.message == "GPU 2번이 없습니다 · 이 PC의 GPU 1개"
    assert state.phase is not ServerPhase.READY


def test_prepare_model_fails_when_packages_are_missing(tmp_path: Path):
    # 준비
    model = EmbeddedModel(
        key="small",
        role=ConnectionRole.EMBEDDING,
        repo="test/small",
        revision="abc123",
        served_name=None,
        packages=("dent_no_such_package",),
        files=MODEL.files,
    )
    state = ModelState(model)

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        model_server.prepare_model(
            state,
            settings=Settings(_env_file=None, storage_dir=tmp_path),
            choice=ModelChoice(ConnectionRole.EMBEDDING, device="cpu"),
            loader=_fake_loader([], state, []),
            resolve_device=_cpu,
        )

    # 확인
    assert "dent_no_such_package" in caught.value.message


def test_resolve_torch_device_accepts_cpu_and_refuses_missing_gpu():
    # 준비
    pytest.importorskip("torch")

    # 실행 · 확인: CPU는 늘 된다, 모르는 장치 · 없는 GPU는 실패로 알린다
    assert model_server.resolve_torch_device("cpu") == ("cpu", "cpu")
    with pytest.raises(ExternalServiceError):
        model_server.resolve_torch_device("cuda:9")
    with pytest.raises(ExternalServiceError) as caught:
        model_server.resolve_torch_device("tpu")
    assert caught.value.message == "모르는 장치입니다 · tpu"


def test_jev_checkpoints_follow_folder_layout(tmp_path: Path):
    # 실행
    own = model_server.jev_checkpoints(tmp_path, Layout.FILES)
    repo = model_server.jev_checkpoints(tmp_path, Layout.REPO)

    # 확인: 저장소 모양은 english가 맨 위에 있다
    assert own == {
        name: str(tmp_path / name) for name in ("english", "multilingual", "typed-decisions")
    }
    assert repo["english"] == str(tmp_path)
    assert repo["multilingual"] == str(tmp_path / "multilingual")


def test_model_state_has_no_eta_right_after_download_starts():
    # 준비
    state = ModelState(MODEL)
    state.downloading()
    state.progress(3)

    # 확인: 처음 몇 초는 속도가 흔들려 남은 시간을 싣지 않는다
    assert state.health()["eta_s"] is None
    assert state.health()["done_bytes"] == 3


async def test_embedding_app_returns_vector_per_text_in_order():
    # 준비
    def encode(texts: list[str]) -> tuple[list[list[float]], int]:
        return [[float(len(text))] for text in texts], 5

    app = model_server.embedding_app(encode, name="BAAI/bge-m3")
    transport = httpx.ASGITransport(app=app)

    # 실행
    async with httpx.AsyncClient(transport=transport, base_url="http://model.test") as client:
        response = await client.post(
            "/v1/embeddings", json={"model": "BAAI/bge-m3", "input": ["가", "가나다"]}
        )
        wrong = await client.post("/v1/embeddings", json={"model": "other", "input": "가"})
        many = await client.post("/v1/embeddings", json={"input": ["가"] * 513})

    # 확인
    body = response.json()
    assert [item["embedding"] for item in body["data"]] == [[1.0], [3.0]]
    assert body["usage"] == {"prompt_tokens": 5, "total_tokens": 5}
    assert wrong.status_code == status.HTTP_404_NOT_FOUND
    assert many.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_batches_keep_sentence_count_and_token_budget():
    # 준비: 짧은 순으로 정렬된 번호와 길이
    lengths = [10] * 20 + [4000, 5000]
    order = list(range(len(lengths)))

    # 실행
    batches = list(model_server._batches(order, lengths=lengths))

    # 확인: 16개 상한, 긴 문장은 (길이 × 수) 상한으로 따로
    assert [len(batch) for batch in batches] == [16, 4, 1, 1]
