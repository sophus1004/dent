"""내장 모델: 실행할 때 고르면 DENT가 함께 띄우는 임베딩(bge-m3) · Jev 판정(laya) 모델.

런처가 실행할 때 터미널에서 모델마다 올릴 곳(GPU 번호 · Apple GPU · CPU · 올리지 않음)과 파일(받기 · 이 PC의 폴더)을 묻고,
모델 서버(dent.model_server)를 띄운다. 고른 것은 환경 변수(RUN_ENV, JSON)로 API 서버 · 작업 실행기 · 모델 서버에 넘기고
(.env에서는 읽지 않는다), 다음에 실행할 때의 기본값으로 storage/models/choices.json에 적어 둔다.
내장 모델을 올린 역할의 연결은 그 서버로 정해진다(화면의 연결 설정에서는 잠긴다). LLM은 내장하지 않는다.

- 목록(CATALOG): 모델마다 허깅페이스 저장소 · 커밋 · 받을 파일(크기 · sha256). 커밋을 고정해 늘 같은 파일을 받는다.
- 파일: 받으면 storage/models/<이름>/에 파일 그대로 둔다(허깅페이스 캐시를 쓰지 않는다). 이 PC의 폴더를 고르면
  복사하지 않고 그 자리에서 쓴다. 폴더 모양은 둘을 받는다: DENT가 받아 둔 모양(files)과 저장소를 그대로 받은 모양(repo).
- 확인: 파일마다 sha256을 맞춘 뒤 storage/models/verified.json에 폴더 · 모양 · 커밋 · 파일 크기 · 바뀐 시각을 적어,
  다음에 실행할 때는 크기와 시각만 본다(2GB를 매번 다시 재지 않게). 남의 폴더에는 아무것도 쓰지 않는다.
- 내려받기(download_files): 파일마다 .part에 받으며 sha256을 재고, 맞으면 제자리로 옮긴다. 진행은 on_progress로 알린다.
- 상태(read_server): 모델 서버의 /health를 읽어 내려받는 중 · 불러오는 중 · 준비됨 · 실패를 돌려준다.
- 패키지: torch · transformers · laya는 쓸 때만 설치한다. 런처가 uv의 --torch-backend auto로 이 PC에 맞는 판
  (NVIDIA CUDA · AMD ROCm · Apple · CPU)을 고정한 버전(MODEL_PACKAGES)으로 설치한다.
"""

import hashlib
import importlib.util
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from dent.system.config import LOOPBACK_HOST, Settings
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection, ConnectionRole

# 모델 서버의 /health가 돌려주는 앱 이름. 런처는 이 값으로 포트의 주인이 DENT 모델 서버인지 안다.
APP_NAME = "dent-model"

# 실행할 때 고른 것을 런처가 자식 프로세스(API 서버 · 작업 실행기 · 모델 서버)에 넘기는 환경 변수 (JSON). .env에서는 읽지 않는다.
RUN_ENV = "DENT_EMBEDDED_MODELS"

# 지난번에 고른 것(다음에 실행할 때의 기본값)과 확인한 폴더 기록. 둘 다 storage/models 안에 둔다.
CHOICES_NAME = "choices.json"
VERIFIED_NAME = "verified.json"

# 모델 서버가 쓰는 패키지와 고정한 버전 (Apple GPU · CPU에서 bge-m3 · laya를 확인한 조합)
MODEL_PACKAGES = ("torch==2.14.0", "transformers==5.17.0", "laya[serve]==0.3.20")

# 모델 파일을 받는 곳
HUGGINGFACE_URL = "https://huggingface.co"

# 받는 중인 파일의 뒤 이름. 다 받고 sha256이 맞으면 떼고 제자리로 옮긴다.
PART_SUFFIX = ".part"

# 한 번에 읽어 쓰는 크기(바이트). 진행도 이만큼마다 알린다.
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

# 내려받기의 시간 한도(초). 큰 파일이라 전체가 아니라 읽기 한 번마다 잰다.
DOWNLOAD_READ_TIMEOUT_S = 60.0
DOWNLOAD_CONNECT_TIMEOUT_S = 10.0

# 모델 서버 상태를 물을 때 기다리는 시간(초). 이 PC 안이라 짧게 둔다(화면이 기다리지 않게).
STATUS_TIMEOUT_S = 1.5

# sha256을 잴 때 한 번에 읽는 크기(바이트)
HASH_CHUNK_BYTES = 4 * 1024 * 1024

PACKAGES_MISSING_MESSAGE = "내장 모델에 필요한 패키지가 없습니다({packages}). 다시 실행해서 설치를 고르거나 '올리지 않음'을 고르세요."
DOWNLOAD_FAILED_MESSAGE = "모델 파일을 받지 못했습니다 · {path} · {reason}"
HASH_MISMATCH_MESSAGE = "파일이 다릅니다(sha256) · {path}"
FILES_MISSING_MESSAGE = "모델 파일이 없거나 크기가 다릅니다 · {path}"


class ServerPhase(StrEnum):
    """모델 서버의 단계. /health의 status 값이다(준비되면 다른 서버처럼 ok)."""

    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    LOADING = "loading"
    READY = "ok"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelFile:
    """받을 파일 하나."""

    # 저장소 안의 경로 (받는 주소에 쓴다)
    source: str

    # 모델 폴더 안에 둘 경로
    path: str

    # 크기(바이트)
    size: int

    # sha256 (16진수)
    sha256: str


@dataclass(frozen=True)
class EmbeddedModel:
    """내장 모델 하나."""

    # 이름. storage/models 안의 폴더 이름 (예: bge-m3)
    key: str

    # 역할: embedding · jev
    role: ConnectionRole

    # 허깅페이스 저장소
    repo: str

    # 고정한 커밋. 늘 같은 파일을 받으려고 가지 이름이 아니라 커밋을 쓴다.
    revision: str

    # 연결에 쓸 모델 이름. 임베딩 캐시는 이 이름으로 모델을 가르므로, 같은 모델을 외부 서버로 쓰던 캐시를 그대로 쓴다.
    # Jev는 비워 둔다(서버가 체크포인트를 고른다).
    served_name: str | None

    # 모델 서버가 import하는 패키지 (없으면 런처가 실행할 때 설치를 묻는다)
    packages: tuple[str, ...]

    # 받을 파일
    files: tuple[ModelFile, ...]

    @property
    def total_bytes(self) -> int:
        """받을 파일을 모두 더한 크기(바이트)."""
        return sum(item.size for item in self.files)


class Layout(StrEnum):
    """모델 폴더의 모양."""

    # DENT가 받아 둔 모양 (ModelFile.path)
    FILES = "files"

    # 허깅페이스 저장소를 그대로 받은 모양 (ModelFile.source)
    REPO = "repo"


# 내장 모델 목록. 파일 목록 · 크기 · sha256은 고정한 커밋의 허깅페이스 목록과 맞춘 값이다.
CATALOG: dict[str, EmbeddedModel] = {
    "bge-m3": EmbeddedModel(
        key="bge-m3",
        role=ConnectionRole.EMBEDDING,
        repo="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        served_name="BAAI/bge-m3",
        packages=("torch", "transformers"),
        # dense 벡터만 쓰므로 onnx/ · colbert_linear.pt · sparse_linear.pt는 받지 않는다.
        files=(
            ModelFile(
                "config.json",
                "config.json",
                687,
                "26159e7ad065073448460117eb24b7a4572f6f4e78eadff65dc0a11c052449fa",
            ),
            ModelFile(
                "tokenizer_config.json",
                "tokenizer_config.json",
                444,
                "a62b2b6784f990259fddef5f16388693a8043be4f69179e6a5257eeb3f9abac4",
            ),
            ModelFile(
                "special_tokens_map.json",
                "special_tokens_map.json",
                964,
                "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
            ),
            ModelFile(
                "sentencepiece.bpe.model",
                "sentencepiece.bpe.model",
                5_069_051,
                "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
            ),
            ModelFile(
                "tokenizer.json",
                "tokenizer.json",
                17_098_108,
                "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",
            ),
            ModelFile(
                "pytorch_model.bin",
                "pytorch_model.bin",
                2_271_145_830,
                "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",
            ),
        ),
    ),
    "laya": EmbeddedModel(
        key="laya",
        role=ConnectionRole.JEV,
        repo="convaiinnovations/laya",
        revision="55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851",
        served_name=None,
        packages=("torch", "transformers", "laya"),
        # 체크포인트 셋(english는 저장소 맨 위, 나머지는 하위 폴더). 그림 · 예제 코드는 받지 않는다.
        files=(
            ModelFile(
                "rl_agent_config.json",
                "english/rl_agent_config.json",
                745,
                "ae287b56bbcf5f8c4f4541ae9dfd00c914c4c48b940b8398c3058af37ba92bbd",
            ),
            ModelFile(
                "encoder/config.json",
                "english/encoder/config.json",
                2_083,
                "bf3ab80598fdccf414855a2ce80f22859e4492d06ca8a62ddd1cfb63972f8979",
            ),
            ModelFile(
                "tokenizer/tokenizer_config.json",
                "english/tokenizer/tokenizer_config.json",
                308,
                "50044de60daaa73df97d262e15a40d4faf0160e7d742df64b377877a1320dd12",
            ),
            ModelFile(
                "tokenizer/tokenizer.json",
                "english/tokenizer/tokenizer.json",
                3_583_228,
                "6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30",
            ),
            ModelFile(
                "model.safetensors",
                "english/model.safetensors",
                842_609_210,
                "891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c",
            ),
            ModelFile(
                "multilingual/rl_agent_config.json",
                "multilingual/rl_agent_config.json",
                472,
                "25061739243b617ad88d1219ba6f8a9c86c5881ca28df024fa2d9b3b2fcc30c6",
            ),
            ModelFile(
                "multilingual/encoder/config.json",
                "multilingual/encoder/config.json",
                1_938,
                "83f6916d13ef0f556ac461f28308dc2bffa7ebeadee8ec9e2db5812020ea5bb4",
            ),
            ModelFile(
                "multilingual/tokenizer/tokenizer_config.json",
                "multilingual/tokenizer/tokenizer_config.json",
                524,
                "6c6b2d8e3c84ce0e671c129cd6b374b235d6f9863042a5836358d00a89bbb5a1",
            ),
            ModelFile(
                "multilingual/tokenizer/tokenizer.json",
                "multilingual/tokenizer/tokenizer.json",
                34_363_188,
                "609d8f4c067cd3950f88594c5a802616cea245823836ef5848ee4fc40aab5b6f",
            ),
            ModelFile(
                "multilingual/model.safetensors",
                "multilingual/model.safetensors",
                643_835_514,
                "9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204",
            ),
            ModelFile(
                "typed-decisions/rl_agent_config.json",
                "typed-decisions/rl_agent_config.json",
                847,
                "ebf0cd524d92342a6be5e48e9fca3d7c2babfb5a56ccd79d2171ef5d8c7f7be8",
            ),
            ModelFile(
                "typed-decisions/encoder/config.json",
                "typed-decisions/encoder/config.json",
                2_084,
                "5268d24ad3b77c8151de5dcb0762ba4391619aad9ab0bda33e36fb083cfeae6d",
            ),
            ModelFile(
                "typed-decisions/tokenizer/tokenizer_config.json",
                "typed-decisions/tokenizer/tokenizer_config.json",
                337,
                "08d4cf3ac4dca381759441b85b91a6d40e688471dcd33d15d6649eb0a9a854d1",
            ),
            ModelFile(
                "typed-decisions/tokenizer/tokenizer.json",
                "typed-decisions/tokenizer/tokenizer.json",
                3_583_228,
                "6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30",
            ),
            ModelFile(
                "typed-decisions/model.safetensors",
                "typed-decisions/model.safetensors",
                842_609_220,
                "4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e",
            ),
        ),
    ),
}

# 역할마다 모델 (한 역할에 하나)과 터미널 · 안내에 쓰는 이름
MODEL_OF_ROLE = {ConnectionRole.EMBEDDING: "bge-m3", ConnectionRole.JEV: "laya"}
ROLE_LABELS = {ConnectionRole.EMBEDDING: "임베딩", ConnectionRole.JEV: "Jev"}


@dataclass(frozen=True)
class ModelChoice:
    """실행할 때 고른 내장 모델 하나."""

    # 역할: embedding · jev
    role: ConnectionRole

    # 올릴 장치 (PyTorch 장치 글자): cuda:0 · mps · cpu
    device: str

    # 이 PC의 폴더. None이면 storage/models/<이름>에 받아 둔 것(없으면 받는다)
    folder: str | None = None

    @property
    def model(self) -> EmbeddedModel:
        """이 역할의 내장 모델."""
        return CATALOG[MODEL_OF_ROLE[self.role]]

    def to_json(self) -> dict[str, Any]:
        """choices.json · RUN_ENV에 적는 모양."""
        return {"model": self.model.key, "device": self.device, "folder": self.folder}


@dataclass(frozen=True)
class ServerState:
    """모델 서버의 /health를 읽은 것."""

    # 단계: downloading · verifying · loading · ok · failed
    phase: ServerPhase

    # 사람이 읽는 까닭이나 값 (실패 까닭, 받는 파일 …). 없으면 ''
    detail: str = ""

    # 내려받기: 받은 바이트 · 전체 바이트 · 남은 초(속도를 아직 모르면 None)
    done_bytes: int = 0
    total_bytes: int = 0
    eta_s: int | None = None

    # 쓰는 장치 (예: 'cuda:1 · RTX 4090', 'mps', 'cpu'). 아직 모르면 None
    device: str | None = None

    # 서버 프로세스 번호. 런처가 앞서 남은 서버를 종료할 때 쓴다.
    pid: int | None = None

    # 모델 이름 (bge-m3 · laya)
    model: str | None = None


# ---------- 고른 것 ----------


def run_choices() -> dict[ConnectionRole, ModelChoice]:
    """이번에 실행할 때 고른 내장 모델 (런처가 넘긴 RUN_ENV). 없거나 모양이 틀리면 빈 dict."""
    return parse_choices(os.environ.get(RUN_ENV, ""))


def chosen_choice(role: ConnectionRole) -> ModelChoice | None:
    """이 역할에 이번에 고른 내장 모델. 올리지 않았으면(또는 LLM이면) None."""
    return run_choices().get(role)


def chosen_model(role: ConnectionRole) -> EmbeddedModel | None:
    """이 역할에 이번에 올린 내장 모델. 올리지 않았으면 None."""
    choice = chosen_choice(role)
    return None if choice is None else choice.model


def encode_choices(choices: list[ModelChoice]) -> str:
    """RUN_ENV에 넣을 JSON. 고르지 않은 역할은 빠진다."""
    return json.dumps({choice.role.value: choice.to_json() for choice in choices})


def parse_choices(raw: str) -> dict[ConnectionRole, ModelChoice]:
    """RUN_ENV · choices.json의 JSON을 역할 → 고른 것으로. 값이 null인 역할(올리지 않음)과 틀린 값은 빠진다."""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    found: dict[ConnectionRole, ModelChoice] = {}
    for role in MODEL_OF_ROLE:
        item = data.get(role.value)
        if not isinstance(item, dict) or not isinstance(item.get("device"), str):
            continue
        folder = item.get("folder")
        found[role] = ModelChoice(
            role=role, device=item["device"], folder=folder if isinstance(folder, str) else None
        )
    return found


def load_last_choices(settings: Settings) -> dict[ConnectionRole, ModelChoice | None]:
    """지난번에 고른 것. 고른 적이 있는 역할만 들어 있고, 값이 None이면 '올리지 않음'을 고른 것이다."""
    path = settings.embedded_models_path / CHOICES_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    chosen = parse_choices(json.dumps(data))
    decided: dict[ConnectionRole, ModelChoice | None] = {}
    for role in MODEL_OF_ROLE:
        if role.value in data:
            decided[role] = chosen.get(role)
    return decided


def save_choices(settings: Settings, decided: dict[ConnectionRole, ModelChoice | None]) -> None:
    """이번에 고른 것을 다음에 실행할 때의 기본값으로 적는다(올리지 않음은 null)."""
    path = settings.embedded_models_path / CHOICES_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        role.value: (None if choice is None else choice.to_json())
        for role, choice in decided.items()
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def model_port(settings: Settings, role: ConnectionRole) -> int:
    """역할의 내장 모델 서버 포트 (임베딩 PORT + 1 · Jev PORT + 2)."""
    if role is ConnectionRole.EMBEDDING:
        return settings.embedded_embedding_port
    return settings.embedded_jev_port


def model_folder(settings: Settings, choice: ModelChoice) -> Path:
    """모델 파일 폴더: 고른 이 PC의 폴더, 아니면 storage/models/<이름>."""
    if choice.folder is not None:
        return Path(choice.folder).expanduser()
    return settings.embedded_models_path / choice.model.key


def embedded_connection(settings: Settings, role: ConnectionRole) -> Connection | None:
    """이번에 내장 모델을 올린 역할이면 그 서버로 가는 연결(저장하지 않는다), 아니면 None.

    임베딩은 OpenAI 방식이라 주소 끝에 /v1을 붙인다(외부 임베딩 서버를 넣을 때와 같은 모양).
    """
    model = chosen_model(role)
    if model is None:
        return None
    address = f"http://{LOOPBACK_HOST}:{model_port(settings, role)}"
    is_embedding = role is ConnectionRole.EMBEDDING
    return Connection(
        role=role.value,
        base_url=f"{address}/v1" if is_embedding else address,
        model=model.served_name,
        provider=None,
        api_key=None,
    )


# ---------- 패키지 ----------


def missing_packages(models: list[EmbeddedModel]) -> list[str]:
    """모델 서버에 필요한데 설치되지 않은 패키지 이름 (여러 모델이 겹치면 한 번만)."""
    needed = dict.fromkeys(name for model in models for name in model.packages)
    return [name for name in needed if importlib.util.find_spec(name) is None]


def packages_missing_message(missing: list[str]) -> str:
    """패키지가 없을 때 사람이 할 일까지 담은 한 문장."""
    return PACKAGES_MISSING_MESSAGE.format(packages=", ".join(missing))


def export_command(uv: str, output: Path) -> list[str]:
    """잠금 파일(uv.lock)의 버전을 제약 파일로 내보내는 명령. 모델 패키지가 DENT의 패키지 버전을 바꾸지 않게 한다."""
    return [
        uv,
        "export",
        "--frozen",
        "--no-hashes",
        "--no-emit-project",
        "--no-dev",
        "--output-file",
        str(output),
    ]


def install_command(uv: str, python: str, constraints: Path) -> list[str]:
    """모델 패키지를 이 PC에 맞는 판(--torch-backend auto: CUDA · ROCm · Apple · CPU)으로 설치하는 명령."""
    return [
        uv,
        "pip",
        "install",
        "--python",
        python,
        "--torch-backend",
        "auto",
        "--constraint",
        str(constraints),
        *MODEL_PACKAGES,
    ]


# ---------- 파일 ----------


def layout_of(folder: Path, model: EmbeddedModel) -> Layout | None:
    """폴더 모양(파일이 모두 제 크기로 있는지로 가름). 어느 쪽도 아니면 None."""
    for layout in (Layout.FILES, Layout.REPO):
        if all(_has_size(file_path(folder, item, layout), item.size) for item in model.files):
            return layout
    return None


def file_path(folder: Path, item: ModelFile, layout: Layout) -> Path:
    """모양에 따른 파일 자리."""
    return folder / (item.path if layout is Layout.FILES else item.source)


def first_missing(folder: Path, model: EmbeddedModel) -> str | None:
    """DENT 모양으로 보아 없거나 크기가 다른 첫 파일 (안내에 쓴다). 모두 있으면 None."""
    for item in model.files:
        if not _has_size(file_path(folder, item, Layout.FILES), item.size):
            return item.path
    return None


def files_ready(settings: Settings, folder: Path, model: EmbeddedModel) -> Layout | None:
    """확인한 기록이 이 커밋이고 파일 크기 · 바뀐 시각이 그대로면 그 모양, 아니면 None (sha256은 다시 재지 않는다)."""
    record = _read_verified(settings).get(_folder_key(folder))
    if not isinstance(record, dict) or record.get("revision") != model.revision:
        return None
    try:
        layout = Layout(record.get("layout"))
    except ValueError:
        return None
    return layout if record.get("files") == _file_stamps(folder, model, layout) else None


def verify_files(
    settings: Settings,
    folder: Path,
    model: EmbeddedModel,
    *,
    on_progress: Callable[[int], None],
) -> Layout:
    """이 PC의 폴더가 같은 모델인지 sha256으로 확인하고 기록한다. 모양을 돌려준다.

    파일이 없거나 크기가 다르면 FILES_MISSING, sha256이 다르면 HASH_MISMATCH 문장의 ExternalServiceError.
    잰 바이트(모든 파일 합)를 on_progress로 알린다.
    """
    layout = layout_of(folder, model)
    if layout is None:
        missing = first_missing(folder, model) or model.files[0].path
        raise ExternalServiceError(FILES_MISSING_MESSAGE.format(path=missing))
    done = 0
    on_progress(done)
    for item in model.files:
        path = file_path(folder, item, layout)
        if _sha256(path, on_chunk=lambda size, base=done: on_progress(base + size)) != item.sha256:
            raise ExternalServiceError(HASH_MISMATCH_MESSAGE.format(path=path))
        done += item.size
        on_progress(done)
    _remember_verified(settings, folder, model, layout)
    return layout


def download_files(
    settings: Settings,
    folder: Path,
    model: EmbeddedModel,
    *,
    on_progress: Callable[[int], None],
    transport: httpx.BaseTransport | None = None,
) -> None:
    """빠진 파일을 DENT 모양으로 받아 sha256을 맞추고 기록한다. 받은 바이트(모든 파일 합)를 on_progress로 알린다.

    이미 제 크기 · sha256으로 있는 파일은 받지 않는다. 받지 못하거나 sha256이 다르면 ExternalServiceError
    (그 파일의 .part는 지운다, 다시 실행하면 그 파일부터 다시 받는다).
    """
    folder.mkdir(parents=True, exist_ok=True)
    done = 0
    on_progress(done)
    timeout = httpx.Timeout(DOWNLOAD_READ_TIMEOUT_S, connect=DOWNLOAD_CONNECT_TIMEOUT_S)
    with httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as client:
        for item in model.files:
            target = folder / item.path
            if not _file_matches(target, item):
                _download_one(client, model, item, target, on_progress=on_progress, offset=done)
            done += item.size
            on_progress(done)
    _remember_verified(settings, folder, model, Layout.FILES)


def folder_bytes(folder: Path) -> int:
    """폴더 안 파일을 모두 더한 크기(바이트). 폴더가 없으면 0."""
    if not folder.is_dir():
        return 0
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


# ---------- 모델 서버 상태 ----------


async def read_server(
    settings: Settings,
    role: ConnectionRole,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ServerState | None:
    """역할의 내장 모델 서버 상태. 서버가 답하지 않으면 None, 다른 프로그램이 답하면 실패."""
    url = f"http://{LOOPBACK_HOST}:{model_port(settings, role)}/health"
    try:
        async with httpx.AsyncClient(timeout=STATUS_TIMEOUT_S, transport=transport) as client:
            response = await client.get(url)
            body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return parse_health(body)


def probe_server(port: int, *, transport: httpx.BaseTransport | None = None) -> ServerState | None:
    """포트의 모델 서버 상태(런처가 본다). 답이 없으면 None, 다른 프로그램이면 실패."""
    try:
        with httpx.Client(timeout=STATUS_TIMEOUT_S, transport=transport) as client:
            body = client.get(f"http://{LOOPBACK_HOST}:{port}/health").json()
    except (httpx.HTTPError, ValueError):
        return None
    return parse_health(body)


def parse_health(body: Any) -> ServerState:
    """/health의 답을 ServerState로. DENT 모델 서버의 답이 아니면 '다른 프로그램' 실패."""
    is_model_server = isinstance(body, dict) and body.get("app") == APP_NAME
    if not is_model_server:
        return ServerState(phase=ServerPhase.FAILED, detail="다른 프로그램")
    try:
        phase = ServerPhase(body.get("status"))
    except ValueError:
        phase = ServerPhase.FAILED
    return ServerState(
        phase=phase,
        detail=str(body.get("detail") or ""),
        done_bytes=_int_or(body.get("done_bytes"), 0),
        total_bytes=_int_or(body.get("total_bytes"), 0),
        eta_s=_int_or(body.get("eta_s"), None),
        device=body.get("device") if isinstance(body.get("device"), str) else None,
        pid=_int_or(body.get("pid"), None),
        model=body.get("model") if isinstance(body.get("model"), str) else None,
    )


def _download_one(
    client: httpx.Client,
    model: EmbeddedModel,
    item: ModelFile,
    target: Path,
    *,
    on_progress: Callable[[int], None],
    offset: int,
) -> None:
    """파일 하나를 .part에 받으며 sha256을 재고, 맞으면 제자리로 옮긴다. 안 되면 ExternalServiceError.

    진행은 앞 파일들의 크기(offset)에 이 파일에서 받은 바이트를 더해 알린다.
    """
    url = f"{HUGGINGFACE_URL}/{model.repo}/resolve/{model.revision}/{item.source}"
    part = target.with_name(target.name + PART_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    got = 0
    try:
        with client.stream("GET", url) as response, part.open("wb") as out:
            response.raise_for_status()
            for chunk in response.iter_bytes(DOWNLOAD_CHUNK_BYTES):
                out.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                on_progress(offset + got)
    except (httpx.HTTPError, OSError) as error:
        part.unlink(missing_ok=True)
        reason = _describe_failure(error)
        raise ExternalServiceError(
            DOWNLOAD_FAILED_MESSAGE.format(path=item.path, reason=reason)
        ) from None
    if got != item.size or digest.hexdigest() != item.sha256:
        part.unlink(missing_ok=True)
        raise ExternalServiceError(HASH_MISMATCH_MESSAGE.format(path=item.path))
    part.replace(target)


def _file_matches(path: Path, item: ModelFile) -> bool:
    """파일이 제 크기 · sha256으로 이미 있는지 (받다 멈춘 뒤 다시 실행했을 때 받은 파일을 건너뛰려고)."""
    return _has_size(path, item.size) and _sha256(path) == item.sha256


def _sha256(path: Path, *, on_chunk: Callable[[int], None] | None = None) -> str:
    """파일의 sha256. on_chunk에는 지금까지 읽은 바이트를 알린다."""
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
            read += len(chunk)
            if on_chunk is not None:
                on_chunk(read)
    return digest.hexdigest()


def _has_size(path: Path, size: int) -> bool:
    """파일이 있고 크기가 같은지."""
    return path.is_file() and path.stat().st_size == size


def _folder_key(folder: Path) -> str:
    """확인 기록의 열쇠: 폴더의 절대 경로."""
    return str(folder.expanduser().resolve())


def _file_stamps(folder: Path, model: EmbeddedModel, layout: Layout) -> dict[str, list[int]] | None:
    """파일마다 [크기, 바뀐 시각(ns)]. 파일이 하나라도 없으면 None."""
    stamps: dict[str, list[int]] = {}
    for item in model.files:
        path = file_path(folder, item, layout)
        try:
            stat = path.stat()
        except OSError:
            return None
        stamps[item.path] = [stat.st_size, stat.st_mtime_ns]
    return stamps


def _read_verified(settings: Settings) -> dict[str, Any]:
    """확인한 폴더 기록. 없거나 모양이 틀리면 빈 dict."""
    try:
        data = json.loads((settings.embedded_models_path / VERIFIED_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _remember_verified(
    settings: Settings, folder: Path, model: EmbeddedModel, layout: Layout
) -> None:
    """sha256을 맞춘 폴더를 기록한다(다음에 실행할 때는 크기 · 시각만 본다)."""
    records = _read_verified(settings)
    records[_folder_key(folder)] = {
        "model": model.key,
        "revision": model.revision,
        "layout": layout.value,
        "files": _file_stamps(folder, model, layout),
    }
    path = settings.embedded_models_path / VERIFIED_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _describe_failure(error: Exception) -> str:
    """받기가 실패한 까닭을 짧은 낱말로. 예: '연결 거부', '시간 초과', 'HTTP 404', '쓰기 실패'."""
    if isinstance(error, httpx.ConnectError):
        return "연결 거부"
    if isinstance(error, httpx.TimeoutException):
        return "시간 초과"
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, OSError):
        return "쓰기 실패"
    return "연결 실패"


def _int_or[T](value: Any, default: T) -> int | T:
    """정수(불 값 제외)면 그대로, 아니면 default."""
    is_int = isinstance(value, int) and not isinstance(value, bool)
    return value if is_int else default
