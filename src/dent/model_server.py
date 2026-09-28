"""내장 모델 서버: python -m dent.model_server embedding|jev

런처가 실행할 때 고른 내장 모델(system/embedded_models.py, 환경 변수 RUN_ENV)마다 하나씩 띄운다. 127.0.0.1의 PORT+1(임베딩) ·
PORT+2(Jev)에서 연다. 실행하자마자 /health로 단계를 알리고, 뒤에서 파일 확인 · 받기 → 장치 확인 → 불러오기 → 준비됨으로 간다.
준비되기 전의 다른 요청에는 503을 준다. 실패해도 끝나지 않고 /health에 까닭을 둔다(홈의 모델 칸에 보인다).

- 파일: 이 PC의 폴더를 골랐으면 sha256으로 같은 모델인지 확인하고(처음 한 번), 아니면 storage/models에 받는다.
- 장치: 고른 장치(cuda:N · mps · cpu)가 없으면 말없이 바꾸지 않고 실패로 둔다(예: GPU 2번 없음).
  올린 뒤에도 실제 장치를 다시 본다(laya는 메모리가 모자라면 스스로 CPU로 내려가므로).

- 임베딩(bge-m3): OpenAI 호환 POST /v1/embeddings · GET /v1/models. dense 1,024차원(첫 토큰, 길이 1로 정규화).
- Jev(laya): laya의 Jev 호환 서버(POST /v1/systemone)를 그대로 쓰되 체크포인트를 storage/models/laya에서 읽는다.

torch · transformers · laya는 쓸 때만 설치하는 패키지라 불러오기 단계에서만 import한다.
모델은 모델 폴더에서만 읽고(허깅페이스 캐시를 쓰지 않는다) 인터넷에는 파일을 받을 때만 나간다.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from dent.system import devices, embedded_models
from dent.system.config import LOOPBACK_HOST, Settings, get_settings
from dent.system.embedded_models import APP_NAME, EmbeddedModel, Layout, ModelChoice, ServerPhase
from dent.system.exceptions import ExternalServiceError, StartupError
from dent.system.logging import report_startup_error, setup_logging
from dent.system.models import ConnectionRole

logger = logging.getLogger("dent.model_server")

# ASGI 앱의 모양 (uvicorn · FastAPI가 부르는 함수)
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# 역할마다 로그 줄 앞의 이름과 로그 파일 이름
LABELS = {ConnectionRole.EMBEDDING: "임베딩 모델", ConnectionRole.JEV: "Jev 모델"}
PROCESS_NAMES = {ConnectionRole.EMBEDDING: "model-embedding", ConnectionRole.JEV: "model-jev"}

# 준비되기 전 요청에 주는 답
NOT_READY_DETAIL = "모델을 준비하는 중입니다."

# 불러오기가 예상 밖의 까닭으로 실패했을 때 /health에 두는 까닭 (자세한 것은 로그 파일에)
LOAD_FAILED_DETAIL = "모델을 불러오지 못했습니다. storage/logs의 로그를 확인하세요."

# 장치 문제로 올리지 못했을 때의 까닭
NO_APPLE_GPU_MESSAGE = "Apple GPU를 쓸 수 없습니다"
NO_GPU_MESSAGE = "GPU를 쓸 수 없습니다 · 설치된 PyTorch가 GPU판이 아니거나 드라이버가 없습니다"
MISSING_GPU_MESSAGE = "GPU {index}번이 없습니다 · 이 PC의 GPU {count}개"
UNKNOWN_DEVICE_MESSAGE = "모르는 장치입니다 · {device}"
OUT_OF_MEMORY_MESSAGE = "메모리가 모자라 {device}에 올리지 못했습니다"
FELL_BACK_MESSAGE = "{device}에 올리지 못해 CPU로 내려갔습니다(메모리가 모자랄 수 있음)"

# 불러오기 함수: (모델 폴더, 폴더 모양, 장치) → (모델의 앱, /health에 더 실을 값)
Loader = Callable[[Path, Layout, str], tuple[AsgiApp, dict[str, Any]]]

# 남은 시간을 싣기 시작하는 받은 시간(초). 처음 몇 초는 속도가 흔들려 싣지 않는다.
ETA_MIN_ELAPSED_S = 3.0

# 종료할 때 받은 요청을 마치기까지 기다리는 최대 시간(초)
GRACEFUL_SHUTDOWN_S = 5

# ---------- 임베딩 (bge-m3) ----------

# 한 요청에 받을 문장 수 상한. DENT는 128개씩 보낸다.
EMBEDDING_MAX_INPUTS = 512
# bge-m3가 읽을 수 있는 최대 토큰 수. 더 긴 문장은 잘라서 계산한다.
EMBEDDING_MAX_TOKENS = 8192
# GPU에 한 번에 넣는 크기. 가장 긴 문장 길이 × 문장 수가 이 값을 넘지 않게 묶어 메모리를 일정하게 둔다.
EMBEDDING_BATCH_TOKEN_BUDGET = 8192
# 한 번에 넣는 문장 수 상한. Apple M4에서 평균 64토큰 문장으로 16개 묶음이 가장 빨랐다(32개보다 조금 빠름).
EMBEDDING_MAX_BATCH_SIZE = 16
# 불러온 뒤 한 번 계산해 GPU 커널을 미리 준비하는 문장
WARMUP_TEXT = "준비"


class ModelState:
    """모델 서버의 지금 단계와 진행. 준비 스레드가 쓰고 /health가 읽는다(잠금으로 둘을 가른다)."""

    def __init__(self, model: EmbeddedModel) -> None:
        self._lock = threading.Lock()

        # 이 서버의 모델
        self.model = model

        # 단계. 처음에는 파일이 있는지 보는 동안이라 불러오는 중으로 둔다.
        self.phase = ServerPhase.LOADING

        # 까닭이나 값 (실패 까닭 …). 없으면 ''
        self.detail = ""

        # 내려받기: 받은 바이트 · 받기 시작한 시각(time.monotonic)
        self.done_bytes = 0
        self.download_started_at: float | None = None

        # 쓰는 장치 (예: 'cuda:1 · RTX 4090', 'mps', 'cpu'). 불러오기 전에는 None
        self.device: str | None = None

        # 준비된 뒤 /health에 더 싣는 값 (임베딩: dim, Jev: loaded)
        self.ready_facts: dict[str, Any] = {}

    def downloading(self) -> None:
        """내려받기를 시작한다."""
        self._start_files(ServerPhase.DOWNLOADING)

    def verifying(self) -> None:
        """이 PC의 파일을 sha256으로 확인하기 시작한다."""
        self._start_files(ServerPhase.VERIFYING)

    def _start_files(self, phase: ServerPhase) -> None:
        """파일 단계(받기 · 확인)를 시작한다. 진행을 0부터 잰다."""
        with self._lock:
            self.phase = phase
            self.done_bytes = 0
            self.download_started_at = time.monotonic()

    def progress(self, done_bytes: int) -> None:
        """받은 바이트(모든 파일 합)를 적는다."""
        with self._lock:
            self.done_bytes = done_bytes

    def loading(self, device: str) -> None:
        """모델을 불러오기 시작한다."""
        with self._lock:
            self.phase = ServerPhase.LOADING
            self.device = device

    def ready(self, facts: dict[str, Any]) -> None:
        """준비됐다. facts는 /health에 더 싣는다."""
        with self._lock:
            self.phase = ServerPhase.READY
            self.ready_facts = facts

    def failed(self, detail: str) -> None:
        """실패했다. 까닭은 /health에 둔다(서버는 끝나지 않는다)."""
        with self._lock:
            self.phase = ServerPhase.FAILED
            self.detail = detail

    def health(self) -> dict[str, Any]:
        """/health의 답: 앱 · 모델 · 단계 · 진행 · 장치 · 프로세스 번호."""
        with self._lock:
            body: dict[str, Any] = {
                "app": APP_NAME,
                "model": self.model.key,
                "role": self.model.role.value,
                "status": self.phase.value,
                "detail": self.detail,
                "done_bytes": self.done_bytes,
                "total_bytes": self.model.total_bytes,
                "eta_s": self._eta_s(),
                "device": self.device,
                "pid": os.getpid(),
            }
            if self.phase is ServerPhase.READY:
                body |= self.ready_facts
            return body

    def _eta_s(self) -> int | None:
        """받거나 확인하는 중이면 지금까지의 속도로 어림한 남은 초. 아직 모르면 None."""
        is_files_phase = self.phase in (ServerPhase.DOWNLOADING, ServerPhase.VERIFYING)
        if not is_files_phase or self.download_started_at is None:
            return None
        elapsed = time.monotonic() - self.download_started_at
        is_too_early = elapsed < ETA_MIN_ELAPSED_S or self.done_bytes <= 0
        if is_too_early:
            return None
        speed = self.done_bytes / elapsed
        return round((self.model.total_bytes - self.done_bytes) / speed)


class ModelApp:
    """준비되기 전에는 /health만 답하고(나머지는 503), 준비되면 모델의 앱으로 넘기는 ASGI 앱.

    준비(prepare)는 실행할 때 스레드 하나에서 돈다: 파일을 받고 모델을 불러와 모델의 앱을 돌려준다.
    """

    def __init__(self, state: ModelState, prepare: Callable[[ModelState], AsgiApp]) -> None:
        # 단계와 진행
        self.state = state

        # 준비 함수: 모델의 앱을 돌려준다. 실패하면 ExternalServiceError(까닭) 또는 다른 예외
        self._prepare = prepare

        # 준비된 모델의 앱. 준비 전에는 None
        self._inner: AsgiApp | None = None

        # 준비 스레드 (테스트가 끝나기를 기다린다)
        self.thread: threading.Thread | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        if scope["path"] == "/health":
            await _send_json(send, status.HTTP_200_OK, self.state.health())
            return
        inner = self._inner
        if inner is None:
            body = {"detail": NOT_READY_DETAIL}
            await _send_json(send, status.HTTP_503_SERVICE_UNAVAILABLE, body)
            return
        await inner(scope, receive, send)

    def start(self) -> None:
        """준비 스레드를 띄운다. 서버를 종료할 때 기다리지 않게 daemon으로 둔다(받던 .part는 다음에 다시 받는다)."""
        self.thread = threading.Thread(target=self._run, name="prepare", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        """준비를 돌리고, 실패하면 까닭을 /health에 둔다."""
        try:
            self._inner = self._prepare(self.state)
        except ExternalServiceError as error:
            logger.error("%s", error.message)
            self.state.failed(error.message)
        except Exception:
            logger.exception("모델을 불러오지 못했습니다.")
            self.state.failed(LOAD_FAILED_DETAIL)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        """실행할 때 준비 스레드를 띄우고, 종료할 때 바로 끝낸다."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self.start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


def prepare_model(
    state: ModelState,
    *,
    settings: Settings,
    choice: ModelChoice,
    loader: Loader,
    resolve_device: Callable[[str], tuple[str, str]],
) -> AsgiApp:
    """파일을 확인하거나 받고, 고른 장치를 확인해 모델을 불러온 뒤 모델의 앱을 돌려준다.

    패키지가 없거나, 파일이 다르거나 받지 못하거나, 장치가 없으면 ExternalServiceError(까닭).
    """
    model = state.model
    missing = embedded_models.missing_packages([model])
    if missing:
        raise ExternalServiceError(embedded_models.packages_missing_message(missing))
    folder = embedded_models.model_folder(settings, choice)
    layout = embedded_models.files_ready(settings, folder, model)
    if layout is None:
        layout = _prepare_files(state, settings=settings, choice=choice, folder=folder)
    device, label = resolve_device(choice.device)
    state.loading(label)
    started = time.perf_counter()
    app, facts = loader(folder, layout, device)
    logger.info("모델 준비: %s · %s · %.1f초", model.key, label, time.perf_counter() - started)
    state.ready(facts)
    return app


def _prepare_files(
    state: ModelState, *, settings: Settings, choice: ModelChoice, folder: Path
) -> Layout:
    """이 PC의 폴더면 sha256으로 확인하고, storage/models면 빠진 파일을 받는다. 폴더 모양을 돌려준다."""
    model = state.model
    is_own_folder = choice.folder is None
    has_all_files = embedded_models.layout_of(folder, model) is not None
    if is_own_folder and not has_all_files:
        logger.info("모델 파일을 받습니다: %s → %s", model.repo, folder)
        state.downloading()
        embedded_models.download_files(settings, folder, model, on_progress=state.progress)
        logger.info("모델 파일을 모두 받았습니다: %s", folder)
        return Layout.FILES
    logger.info("모델 파일을 확인합니다(sha256): %s", folder)
    state.verifying()
    return embedded_models.verify_files(settings, folder, model, on_progress=state.progress)


def resolve_torch_device(requested: str) -> tuple[str, str]:
    """고른 장치가 있는지 보고 (PyTorch 장치 글자, 보이는 이름)을 돌려준다. 없으면 ExternalServiceError.

    예: 'cuda:1' → ('cuda:1', 'cuda:1 · RTX 4090'), 'mps' → ('mps', 'mps'), 'cpu' → ('cpu', 'cpu').
    """
    import torch

    if requested == "cpu":
        return "cpu", "cpu"
    if requested == "mps":
        if not torch.backends.mps.is_available():
            raise ExternalServiceError(NO_APPLE_GPU_MESSAGE)
        return "mps", "mps"
    if requested == "cuda" or requested.startswith("cuda:"):
        if not torch.cuda.is_available():
            raise ExternalServiceError(NO_GPU_MESSAGE)
        index = int(requested.split(":", 1)[1]) if ":" in requested else 0
        count = torch.cuda.device_count()
        if index >= count:
            raise ExternalServiceError(MISSING_GPU_MESSAGE.format(index=index, count=count))
        name = devices.short_name(torch.cuda.get_device_name(index))
        return f"cuda:{index}", f"cuda:{index} · {name}"
    raise ExternalServiceError(UNKNOWN_DEVICE_MESSAGE.format(device=requested))


def _is_out_of_memory(error: BaseException) -> bool:
    """장치 메모리가 모자라 난 오류인지 (PyTorch는 CUDA · MPS 모두 글에 'out of memory'를 담는다)."""
    return "out of memory" in str(error).lower()


# ---------- 임베딩 (bge-m3) ----------


class EmbeddingRequest(BaseModel):
    """OpenAI 호환 임베딩 요청."""

    # 모델 이름. 비우면 이 서버의 모델로 본다.
    model: str | None = None

    # 문장 하나 또는 문장 목록
    input: str | list[str] = Field(min_length=1)

    # 벡터 형식. 숫자 목록(float)만 지원한다.
    encoding_format: Literal["float"] = "float"


class Encoder:
    """bge-m3 모델과 토크나이저. GPU 계산은 한 번에 하나씩만 한다."""

    def __init__(self, folder: Path, device: str) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer
        from transformers.utils import logging as transformers_logging

        # 불러오기 진행 막대가 로그에 섞이지 않게 한다.
        transformers_logging.disable_progress_bar()

        self._torch = torch

        # 쓰는 장치
        self.device = device

        self.tokenizer = AutoTokenizer.from_pretrained(folder, local_files_only=True)
        # GPU는 반 정밀도로 빠르게, CPU는 반 정밀도가 느려 그대로 둔다.
        dtype = torch.float32 if device == "cpu" else torch.float16
        model = AutoModel.from_pretrained(folder, dtype=dtype, local_files_only=True)
        self.model = model.to(device).eval()

        # 벡터 차원
        self.dim: int = self.model.config.hidden_size
        self.encode([WARMUP_TEXT])

    def encode(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """문장마다 정규화한 벡터와 전체 토큰 수를 돌려준다. 입력 순서를 지킨다."""
        torch = self._torch
        ids = self.tokenizer(texts, truncation=True, max_length=EMBEDDING_MAX_TOKENS)["input_ids"]
        vectors: list[list[float]] = [[] for _ in texts]
        # 길이가 비슷한 문장끼리 묶어 패딩을 줄인다.
        order = sorted(range(len(texts)), key=lambda i: len(ids[i]))
        with torch.inference_mode():
            for batch in _batches(order, lengths=[len(x) for x in ids]):
                padded = self.tokenizer.pad(
                    {"input_ids": [ids[i] for i in batch]}, return_tensors="pt"
                )
                output = self.model(
                    input_ids=padded["input_ids"].to(self.device),
                    attention_mask=padded["attention_mask"].to(self.device),
                )
                # bge-m3 dense 벡터는 첫 토큰(CLS)의 값을 길이 1로 정규화한 것이다.
                first = output.last_hidden_state[:, 0].float()
                normalized = torch.nn.functional.normalize(first, dim=-1)
                for i, vector in zip(batch, normalized.cpu().tolist(), strict=True):
                    vectors[i] = vector
        # 길이가 제각각인 묶음이 남긴 GPU 메모리를 돌려줘 메모리가 늘지 않게 한다.
        if self.device == "mps":
            torch.mps.empty_cache()
        return vectors, sum(len(x) for x in ids)


def _batches(order: list[int], *, lengths: list[int]) -> Iterator[list[int]]:
    """짧은 문장부터 차례로, 문장 수와 (가장 긴 길이 × 문장 수) 상한 안에서 묶는다."""
    batch: list[int] = []
    for i in order:
        # 짧은 순으로 정렬돼 있어서 지금 문장이 이 묶음에서 가장 길다.
        is_over_budget = lengths[i] * (len(batch) + 1) > EMBEDDING_BATCH_TOKEN_BUDGET
        if batch and (len(batch) == EMBEDDING_MAX_BATCH_SIZE or is_over_budget):
            yield batch
            batch = []
        batch.append(i)
    if batch:
        yield batch


def embedding_app(
    encode: Callable[[list[str]], tuple[list[list[float]], int]], *, name: str
) -> FastAPI:
    """OpenAI 호환 임베딩 앱: POST /v1/embeddings · GET /v1/models. encode는 한 스레드에서 돈다."""
    app = FastAPI(title=name)
    # GPU(MPS)는 여러 스레드가 동시에 쓰면 안전하지 않아서 계산 스레드를 하나만 둔다.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="encode")

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """OpenAI 호환 모델 목록."""
        return {"object": "list", "data": [{"id": name, "object": "model", "owned_by": "dent"}]}

    @app.post("/v1/embeddings")
    async def create_embeddings(request: EmbeddingRequest) -> dict[str, Any]:
        """문장마다 벡터를 돌려준다. 모델 이름이 다르면 404, 문장이 너무 많으면 422."""
        if request.model and request.model != name:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"모델 '{request.model}'이 없습니다.")
        texts = [request.input] if isinstance(request.input, str) else request.input
        if len(texts) > EMBEDDING_MAX_INPUTS:
            message = f"한 요청에 {EMBEDDING_MAX_INPUTS}개까지 보낼 수 있습니다."
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)
        loop = asyncio.get_running_loop()
        vectors, tokens = await loop.run_in_executor(executor, encode, texts)
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
            ],
            "model": name,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }

    return app


def load_embedding(folder: Path, _layout: Layout, device: str) -> tuple[AsgiApp, dict[str, Any]]:
    """bge-m3를 불러와 임베딩 앱과 /health에 실을 값(dim)을 돌려준다. 메모리가 모자라면 ExternalServiceError.

    bge-m3는 두 폴더 모양의 파일 자리가 같아 모양을 보지 않는다.
    """
    try:
        encoder = Encoder(folder, device)
    except Exception as error:
        if _is_out_of_memory(error):
            raise ExternalServiceError(OUT_OF_MEMORY_MESSAGE.format(device=device)) from None
        raise
    name = embedded_models.CATALOG["bge-m3"].served_name or "bge-m3"
    return embedding_app(encoder.encode, name=name), {"dim": encoder.dim}


# ---------- Jev (laya) ----------


def load_jev(folder: Path, layout: Layout, device: str) -> tuple[AsgiApp, dict[str, Any]]:
    """laya 체크포인트를 모두 불러와 Jev 호환 앱과 /health에 실을 값(loaded)을 돌려준다.

    고른 장치에 올라가지 못하고 CPU로 내려갔으면 ExternalServiceError(말없이 CPU로 돌지 않게).
    """
    import torch
    from laya.router import Router
    from laya.serve import create_app
    from transformers.utils import logging as transformers_logging

    # 불러오기 진행 막대가 로그에 섞이지 않게 한다.
    transformers_logging.disable_progress_bar()

    router = Router(models=jev_checkpoints(folder, layout), device=device)
    # 실행할 때 모두 올려 첫 판정을 빠르게 한다.
    router.preload()
    wanted = torch.device(device)
    # laya는 장치에 올리지 못하면 스스로 CPU로 내려간다. 올라간 장치를 체크포인트마다 다시 본다.
    placed = [agent.device for agent in getattr(router, "_agents", {}).values()]
    fell_back = [
        where for where in placed if where.type != wanted.type or where.index != wanted.index
    ]
    if fell_back:
        raise ExternalServiceError(FELL_BACK_MESSAGE.format(device=device))
    return create_app(router), {"loaded": sorted(router.loaded)}


def jev_checkpoints(folder: Path, layout: Layout) -> dict[str, str]:
    """laya 체크포인트 이름 → 폴더. 체크포인트 폴더는 그 체크포인트의 rl_agent_config.json이 있는 곳이다.

    DENT 모양은 english · multilingual · typed-decisions 폴더가 나란하고, 저장소 모양은 english가 맨 위에 있다.
    """
    model = embedded_models.CATALOG["laya"]
    found: dict[str, str] = {}
    for item in model.files:
        name, _, rest = item.path.partition("/")
        if rest == "rl_agent_config.json":
            found[name] = str(embedded_models.file_path(folder, item, layout).parent)
    return found


LOADERS: dict[ConnectionRole, Loader] = {
    ConnectionRole.EMBEDDING: load_embedding,
    ConnectionRole.JEV: load_jev,
}


async def _send_json(send: Send, status_code: int, body: dict[str, Any]) -> None:
    """JSON 답 하나를 보낸다."""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def main(argv: list[str] | None = None) -> int:
    """역할의 내장 모델 서버를 실행한다. 설정이 틀렸거나 그 역할에 내장 모델을 고르지 않았으면 1."""
    parser = argparse.ArgumentParser(
        prog="python -m dent.model_server", description="DENT 내장 모델 서버"
    )
    parser.add_argument("role", choices=[role.value for role in LOADERS], help="모델의 역할")
    args = parser.parse_args(argv)
    role = ConnectionRole(args.role)
    label = LABELS[role]

    try:
        settings = get_settings()
        setup_logging(settings, process=PROCESS_NAMES[role], label=label)
        choice = embedded_models.chosen_choice(role)
        if choice is None:
            raise StartupError(
                "이번에 올리기로 고른 내장 모델이 없습니다. uv run dent를 실행하며 고르세요."
            )
    except StartupError as error:
        report_startup_error(error, label=label)
        return 1

    # 라이브러리의 경고(warnings)도 같은 모양의 로그로 남긴다.
    logging.captureWarnings(True)
    # 허깅페이스 라이브러리가 캐시(~/.cache/huggingface)에 쓰거나 인터넷에 묻지 않게 한다.
    os.environ["HF_HUB_OFFLINE"] = "1"
    state = ModelState(choice.model)

    def prepare(current: ModelState) -> AsgiApp:
        return prepare_model(
            current,
            settings=settings,
            choice=choice,
            loader=LOADERS[role],
            resolve_device=resolve_torch_device,
        )

    port = embedded_models.model_port(settings, role)
    logger.info(
        "%s 서버를 실행합니다: http://%s:%d · %s · %s",
        label,
        LOOPBACK_HOST,
        port,
        choice.model.key,
        choice.device,
    )
    uvicorn.run(
        ModelApp(state, prepare),
        host=LOOPBACK_HOST,
        port=port,
        log_config=None,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
