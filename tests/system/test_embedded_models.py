"""내장 모델 테스트: 실행할 때 고른 것 넘기기 · 기억, 연결 주소, 파일 받기(sha256 · 이어 받기 · 실패),
이 PC의 폴더 확인(두 모양 · 다른 파일), 확인 기록, 패키지 설치 명령, 서버 상태 읽기."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from dent.system import embedded_models
from dent.system.config import Settings
from dent.system.embedded_models import (
    RUN_ENV,
    EmbeddedModel,
    Layout,
    ModelChoice,
    ModelFile,
    ServerPhase,
)
from dent.system.exceptions import ExternalServiceError
from dent.system.models import ConnectionRole

FIRST = b"first file"
SECOND = b"second file, a little longer"


def _file(source: str, path: str, content: bytes) -> ModelFile:
    return ModelFile(source, path, len(content), hashlib.sha256(content).hexdigest())


def _small_model(*, packages: tuple[str, ...] = ()) -> EmbeddedModel:
    """파일 두 개짜리 가짜 모델. 두 번째 파일은 저장소 모양과 DENT 모양의 자리가 다르다."""
    return EmbeddedModel(
        key="small",
        role=ConnectionRole.EMBEDDING,
        repo="test/small",
        revision="abc123",
        served_name="test/small",
        packages=packages,
        files=(
            _file("config.json", "config.json", FIRST),
            _file("weights.bin", "main/weights.bin", SECOND),
        ),
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_dir=tmp_path / "storage", port=8100)


def _hub_transport(*, contents: dict[str, bytes], status_code: int = 200) -> httpx.MockTransport:
    """가짜 허깅페이스: /resolve/는 저장소 밖(cdn)으로 넘기고, cdn이 파일을 준다. 받은 요청은 .requests에."""
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "huggingface.co":
            source = request.url.path.split("/resolve/abc123/", 1)[1]
            return httpx.Response(302, headers={"location": f"https://cdn.test/{source}"})
        if status_code != 200:
            return httpx.Response(status_code)
        return httpx.Response(200, content=contents[request.url.path.lstrip("/")])

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


def _write(folder: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_catalog_has_one_model_per_role():
    # 확인
    for role, key in embedded_models.MODEL_OF_ROLE.items():
        assert embedded_models.CATALOG[key].role is role


def test_run_choices_read_what_launcher_passed(monkeypatch: pytest.MonkeyPatch):
    # 준비
    choices = [ModelChoice(ConnectionRole.EMBEDDING, device="cuda:1", folder="/models/bge")]
    monkeypatch.setenv(RUN_ENV, embedded_models.encode_choices(choices))

    # 실행
    found = embedded_models.run_choices()

    # 확인: 고르지 않은 역할(Jev)은 없다
    assert found == {ConnectionRole.EMBEDDING: choices[0]}
    assert embedded_models.chosen_model(ConnectionRole.JEV) is None


def test_run_choices_are_empty_when_env_is_broken(monkeypatch: pytest.MonkeyPatch):
    # 준비
    monkeypatch.setenv(RUN_ENV, "{틀린 JSON")

    # 확인
    assert embedded_models.run_choices() == {}


def test_embedded_connection_points_to_model_servers_after_port(monkeypatch: pytest.MonkeyPatch):
    # 준비
    settings = Settings(_env_file=None, port=8100)
    choices = [
        ModelChoice(ConnectionRole.EMBEDDING, device="mps"),
        ModelChoice(ConnectionRole.JEV, device="cpu"),
    ]
    monkeypatch.setenv(RUN_ENV, embedded_models.encode_choices(choices))

    # 실행
    embedding = embedded_models.embedded_connection(settings, ConnectionRole.EMBEDDING)
    jev = embedded_models.embedded_connection(settings, ConnectionRole.JEV)

    # 확인: 임베딩은 OpenAI 방식이라 /v1, 모델 이름은 외부 서버로 쓰던 이름과 같아 캐시를 그대로 쓴다
    assert embedding is not None and jev is not None
    assert (embedding.base_url, embedding.model) == ("http://127.0.0.1:8101/v1", "BAAI/bge-m3")
    assert (jev.base_url, jev.model) == ("http://127.0.0.1:8102", None)
    assert embedded_models.embedded_connection(settings, ConnectionRole.LLM) is None


def test_save_choices_remembers_off_as_decided(tmp_path: Path):
    # 준비
    settings = _settings(tmp_path)
    decided = {
        ConnectionRole.EMBEDDING: ModelChoice(ConnectionRole.EMBEDDING, device="cuda:0"),
        ConnectionRole.JEV: None,
    }

    # 실행
    embedded_models.save_choices(settings, decided)

    # 확인: 올리지 않음도 '고른 것'으로 남는다(다음에 처음 자리가 '올리지 않음')
    assert embedded_models.load_last_choices(settings) == decided


def test_load_last_choices_is_empty_before_first_choice(tmp_path: Path):
    # 확인
    assert embedded_models.load_last_choices(_settings(tmp_path)) == {}


def test_download_files_follows_redirect_and_remembers_folder(tmp_path: Path):
    # 준비
    settings = _settings(tmp_path)
    model = _small_model()
    folder = tmp_path / "own"
    transport = _hub_transport(contents={"config.json": FIRST, "weights.bin": SECOND})
    progress: list[int] = []

    # 실행
    embedded_models.download_files(
        settings, folder, model, on_progress=progress.append, transport=transport
    )

    # 확인: DENT 모양으로 두고, 다음에 실행할 때는 다시 재지 않는다
    assert (folder / "config.json").read_bytes() == FIRST
    assert (folder / "main" / "weights.bin").read_bytes() == SECOND
    assert embedded_models.files_ready(settings, folder, model) is Layout.FILES
    assert progress[0] == 0 and progress[-1] == model.total_bytes
    assert progress == sorted(progress)
    assert not list(folder.rglob("*.part"))


def test_download_files_skips_file_already_present(tmp_path: Path):
    # 준비: 첫 파일은 받아 둔 채 멈췄다
    settings = _settings(tmp_path)
    model = _small_model()
    folder = tmp_path / "own"
    _write(folder, {"config.json": FIRST})
    transport = _hub_transport(contents={"weights.bin": SECOND})

    # 실행
    embedded_models.download_files(
        settings, folder, model, on_progress=lambda _: None, transport=transport
    )

    # 확인: 남은 파일만 받았다
    asked = [request.url.path for request in transport.requests]
    assert asked == ["/test/small/resolve/abc123/weights.bin", "/weights.bin"]


def test_download_files_raises_when_hash_differs(tmp_path: Path):
    # 준비: 서버가 다른 내용을 준다
    settings = _settings(tmp_path)
    model = _small_model()
    folder = tmp_path / "own"
    transport = _hub_transport(contents={"config.json": b"other", "weights.bin": SECOND})

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        embedded_models.download_files(
            settings, folder, model, on_progress=lambda _: None, transport=transport
        )

    # 확인: 틀린 파일은 남기지 않는다
    assert caught.value.message == "파일이 다릅니다(sha256) · config.json"
    assert not (folder / "config.json").exists()
    assert not list(folder.rglob("*.part"))
    assert embedded_models.files_ready(settings, folder, model) is None


def test_download_files_raises_when_server_fails(tmp_path: Path):
    # 준비
    transport = _hub_transport(contents={}, status_code=404)

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        embedded_models.download_files(
            _settings(tmp_path),
            tmp_path / "own",
            _small_model(),
            on_progress=lambda _: None,
            transport=transport,
        )

    # 확인
    assert caught.value.message == "모델 파일을 받지 못했습니다 · config.json · HTTP 404"


@pytest.mark.parametrize(
    ("files", "layout"),
    [
        ({"config.json": FIRST, "main/weights.bin": SECOND}, Layout.FILES),
        ({"config.json": FIRST, "weights.bin": SECOND}, Layout.REPO),
    ],
)
def test_verify_files_accepts_both_folder_layouts(
    tmp_path: Path, files: dict[str, bytes], layout: Layout
):
    # 준비: 이 PC의 폴더(DENT가 받아 둔 모양 · 저장소를 그대로 받은 모양)
    settings = _settings(tmp_path)
    folder = tmp_path / "mine"
    _write(folder, files)

    # 실행
    found = embedded_models.verify_files(
        settings, folder, _small_model(), on_progress=lambda _: None
    )

    # 확인: 모양을 알아보고, 남의 폴더에는 아무것도 쓰지 않는다
    assert found is layout
    assert embedded_models.files_ready(settings, folder, _small_model()) is layout
    assert sorted(path.name for path in folder.rglob("*") if path.is_file()) == sorted(
        Path(name).name for name in files
    )


def test_verify_files_raises_when_content_differs(tmp_path: Path):
    # 준비: 크기는 같은데 내용이 다르다
    settings = _settings(tmp_path)
    folder = tmp_path / "mine"
    _write(folder, {"config.json": b"X" * len(FIRST), "main/weights.bin": SECOND})

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        embedded_models.verify_files(settings, folder, _small_model(), on_progress=lambda _: None)

    # 확인
    assert caught.value.message.startswith("파일이 다릅니다(sha256) · ")
    assert embedded_models.files_ready(settings, folder, _small_model()) is None


def test_verify_files_raises_when_file_is_missing(tmp_path: Path):
    # 준비
    folder = tmp_path / "mine"
    _write(folder, {"config.json": FIRST})

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        embedded_models.verify_files(
            _settings(tmp_path), folder, _small_model(), on_progress=lambda _: None
        )

    # 확인
    assert caught.value.message == "모델 파일이 없거나 크기가 다릅니다 · main/weights.bin"


def test_files_ready_is_none_after_file_changes(tmp_path: Path):
    # 준비: 확인한 뒤 파일이 바뀌었다
    settings = _settings(tmp_path)
    folder = tmp_path / "mine"
    _write(folder, {"config.json": FIRST, "main/weights.bin": SECOND})
    embedded_models.verify_files(settings, folder, _small_model(), on_progress=lambda _: None)
    (folder / "main" / "weights.bin").write_bytes(b"cut")

    # 확인
    assert embedded_models.files_ready(settings, folder, _small_model()) is None


def test_missing_packages_lists_each_package_once():
    # 준비
    one = _small_model(packages=("httpx", "dent_no_such_package"))
    two = _small_model(packages=("dent_no_such_package",))

    # 확인
    assert embedded_models.missing_packages([one, two]) == ["dent_no_such_package"]


def test_install_command_picks_build_for_this_pc_with_pinned_versions(tmp_path: Path):
    # 실행
    command = embedded_models.install_command("uv", "/venv/python", tmp_path / "c.txt")

    # 확인: 이 PC에 맞는 PyTorch 판을 uv가 고르고(auto), 버전은 고정, DENT 패키지는 잠금 파일대로
    assert command[:3] == ["uv", "pip", "install"]
    assert command[command.index("--torch-backend") + 1] == "auto"
    assert command[command.index("--constraint") + 1] == str(tmp_path / "c.txt")
    assert "torch==2.14.0" in command


def test_parse_health_reads_download_progress():
    # 실행
    state = embedded_models.parse_health(
        {
            "app": "dent-model",
            "model": "laya",
            "status": "downloading",
            "done_bytes": 10,
            "total_bytes": 40,
            "eta_s": 9,
            "device": None,
            "pid": 77,
        }
    )

    # 확인
    assert state.phase is ServerPhase.DOWNLOADING
    assert (state.done_bytes, state.total_bytes, state.eta_s, state.pid) == (10, 40, 9, 77)
    assert state.model == "laya"


def test_parse_health_fails_when_other_program_answers():
    # 실행
    state = embedded_models.parse_health({"status": "ok"})

    # 확인
    assert state.phase is ServerPhase.FAILED
    assert state.detail == "다른 프로그램"


async def test_read_server_returns_none_when_server_is_off():
    # 준비
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("연결 거부", request=request)

    # 실행
    state = await embedded_models.read_server(
        Settings(_env_file=None),
        ConnectionRole.EMBEDDING,
        transport=httpx.MockTransport(refuse),
    )

    # 확인
    assert state is None


def test_folder_bytes_adds_every_file(tmp_path: Path):
    # 준비
    _write(tmp_path, {"a/x.bin": b"12345", "y.bin": b"123"})

    # 확인
    assert embedded_models.folder_bytes(tmp_path) == 8
    assert embedded_models.folder_bytes(tmp_path / "없음") == 0


def test_verified_record_lives_in_storage(tmp_path: Path):
    # 준비
    settings = _settings(tmp_path)
    folder = tmp_path / "mine"
    _write(folder, {"config.json": FIRST, "main/weights.bin": SECOND})

    # 실행
    embedded_models.verify_files(settings, folder, _small_model(), on_progress=lambda _: None)

    # 확인
    record = json.loads((settings.embedded_models_path / "verified.json").read_text())
    assert record[str(folder.resolve())]["layout"] == "files"
