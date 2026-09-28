"""시스템 테스트 도우미: 파일 올리기와 올린 파일 상태, 테스트용 엑셀·Parquet 내용, 가짜 허깅페이스,
가짜 외부 모델 서버(임베딩 · Jev). 임베딩은 연결 확인용(늘 같은 벡터)과 계산용(문장마다 다른 벡터) 두 가지다.

모듈 테스트도 가져오기를 돌릴 때 이 도우미를 쓴다(원본을 읽는 것은 시스템의 일이므로).
"""

import hashlib
import io
import json
from collections.abc import Awaitable, Callable

import httpx
import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import uploads as uploads_service
from dent.system.config import get_settings
from dent.system.models import File
from dent.system.schemas import FilePreviewRead

# 가짜 허깅페이스의 저장소 이름과 뷰어 API 주소
HF_REPO = "tester/review-sentiment"
VIEWER_HOST = "datasets-server.huggingface.co"

# 가짜 외부 모델 서버의 주소와 모델 이름
EMBEDDING_URL = "http://embedding.test/v1"
EMBEDDING_MODEL = "test-embedding"
JEV_URL = "http://jev.test"
LLM_URL = "http://llm.test/v1"
LLM_KEY = "sk-test-0123456789abcdef"
LLM_MODELS = ["test-chat-mini", "test-chat"]


def reader_of(content: bytes) -> Callable[[int], Awaitable[bytes]]:
    """올린 파일처럼 조각조각 읽히는 함수."""
    stream = io.BytesIO(content)

    async def read(size: int) -> bytes:
        return stream.read(size)

    return read


async def upload(db: AsyncSession, file_name: str, content: bytes) -> FilePreviewRead:
    """파일을 올린 것처럼 저장하고 미리 보기를 돌려준다."""
    return await uploads_service.save_upload(db, file_name=file_name, read=reader_of(content))


async def upload_state(db: AsyncSession, upload_id: str) -> tuple[bool, bool]:
    """올린 파일의 (원본이 storage에 남아 있는지, 목록에 지운 시각이 적혔는지)."""
    file = await db.get(File, upload_id)
    assert file is not None
    # 작업 실행기의 다른 세션이 고쳤을 수 있어 DB에서 다시 읽는다.
    await db.refresh(file)
    path = get_settings().storage_path / file.stored_path
    return path.exists(), file.deleted_at is not None


def xlsx_bytes() -> bytes:
    """시트 두 개짜리 엑셀 파일. 둘째 시트는 2행이 머리줄이고 4행의 문장이 비어 있다."""
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "첫째"
    first.append(["text", "label"])
    first.append(["첫 시트 문장", "인사"])
    second = workbook.create_sheet("둘째")
    second.append([])
    second.append(["문장", "의도", "번호"])
    second.append(["배송 언제 와요", "배송", 1])
    second.append([None, "배송", 2])
    second.append(["환불해 주세요", "환불", 3.0])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def parquet_bytes(texts: list[str], labels: list[int]) -> bytes:
    """text(문자열)와 label(번호) 열을 가진 Parquet 파일."""
    table = pa.table({"text": texts, "label": pa.array(labels, type=pa.int64())})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def huggingface_transport(*, partial_train: bool = False) -> httpx.MockTransport:
    """허깅페이스 뷰어 API와 파일 서버를 흉내 낸다. 파일 주소는 다른 곳으로 한 번 넘겨 준다.

    train 3줄(마지막 줄은 라벨 번호 -1), validation 1줄. 라벨은 ClassLabel ["부정", "긍정"].
    partial_train이면 train 분할을 앞부분만 변환해 둔 것처럼 주소에 partial-train 폴더를 넣는다.
    """
    label_type = {"names": ["부정", "긍정"], "_type": "ClassLabel"}
    text_type = {"dtype": "string", "_type": "Value"}
    files = {
        "/cdn/train-0.parquet": parquet_bytes(["좋아요", "별로예요", "라벨 없는 줄"], [1, 0, -1]),
        "/cdn/validation-0.parquet": parquet_bytes(["최고예요"], [1]),
    }
    folders = {"train": "partial-train" if partial_train else "train", "validation": "validation"}

    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == VIEWER_HOST and path == "/splits":
            return httpx.Response(
                200,
                json={
                    "splits": [
                        {"dataset": HF_REPO, "config": "default", "split": "train"},
                        {"dataset": HF_REPO, "config": "default", "split": "validation"},
                    ]
                },
            )
        if request.url.host == VIEWER_HOST and path == "/info":
            return httpx.Response(
                200,
                json={
                    "dataset_info": {
                        "features": {"text": text_type, "label": label_type},
                        "splits": {
                            "train": {"name": "train", "num_examples": 3},
                            "validation": {"name": "validation", "num_examples": 1},
                        },
                    }
                },
            )
        if request.url.host == VIEWER_HOST and path == "/first-rows":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"feature_idx": 0, "name": "text", "type": text_type},
                        {"feature_idx": 1, "name": "label", "type": label_type},
                    ],
                    "rows": [
                        {"row_idx": 0, "row": {"text": "좋아요", "label": 1}},
                        {"row_idx": 1, "row": {"text": "라벨 없는 줄", "label": -1}},
                    ],
                },
            )
        if request.url.host == VIEWER_HOST and path == "/parquet":
            return httpx.Response(
                200,
                json={
                    "parquet_files": [
                        {
                            "config": "default",
                            "split": split,
                            "url": f"https://huggingface.co/files/{folders[split]}/{split}-0.parquet",
                        }
                        for split in ("train", "validation")
                    ]
                },
            )
        if request.url.host == "huggingface.co":
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(302, headers={"Location": f"https://cdn.test/cdn/{name}"})
        if request.url.host == "cdn.test" and path in files:
            return httpx.Response(200, content=files[path])
        return httpx.Response(404, json={"error": "없음"})

    return httpx.MockTransport(reply)


def embedding_transport(*, dim: int = 8) -> httpx.MockTransport:
    """가짜 임베딩 서버(OpenAI 호환). /embeddings에 dim차원 벡터를 돌려준다. 받은 요청은 .requests에."""
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1] * dim}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


def vector_transport(*, dim: int = 8, fail_first: int = 0) -> httpx.MockTransport:
    """가짜 임베딩 서버(계산용). 보낸 문장마다 그 문장에서 정해지는 dim차원 벡터를 index와 함께 돌려준다.

    같은 문장이면 늘 같은 벡터다. fail_first번째 요청까지는 503을 준다(다시 보내기 확인).
    받은 요청은 .requests에, 받은 문장은 .inputs에 쌓인다.
    """
    requests: list[httpx.Request] = []
    inputs: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) <= fail_first:
            return httpx.Response(503, json={"error": "바쁨"})
        texts = json.loads(request.content)["input"]
        inputs.extend(texts)
        data = [
            {"index": index, "embedding": vector_of(text, dim=dim)}
            for index, text in enumerate(texts)
        ]
        return httpx.Response(200, json={"data": data})

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    transport.inputs = inputs  # type: ignore[attr-defined]
    return transport


def vector_of(text: str, *, dim: int) -> list[float]:
    """문장에서 정해지는 벡터. 해시 바이트를 -1~1 사이 수로 바꾼다."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [digest[index % len(digest)] / 127.5 - 1 for index in range(dim)]


def jev_transport(*, with_health: bool = True, choice: str = "yes") -> httpx.MockTransport:
    """가짜 Jev 서버(laya 모양). with_health가 아니면 /health가 없는 TypeSafe처럼 404를 준다."""
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            if not with_health:
                return httpx.Response(404)
            body = {"status": "ok", "loaded": ["english", "multilingual"], "device": "mps"}
            return httpx.Response(200, json=body)
        if request.url.path == "/v1/systemone":
            # 받은 질문마다 같은 답을 준다.
            questions = json.loads(request.content)["questions"]
            answer = {
                "type": "choice",
                "choice": choice,
                "probabilities": {"yes": 0.9, "no": 0.1},
                "confidence": 0.7,
            }
            return httpx.Response(
                200,
                json={
                    "model": "laya-rl-agent",
                    "answers": {name: answer for name in questions},
                    "usage": {"input_tokens": 3},
                    "routing": {"model": "english"},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


def model_server_transport(
    *,
    model: str = "bge-m3",
    phase: str = "ok",
    done_bytes: int = 0,
    total_bytes: int = 0,
    device: str = "mps",
    detail: str = "",
    dim: int = 8,
) -> httpx.MockTransport:
    """가짜 내장 모델 서버. /health에 단계 · 진행을 주고, 준비됐으면 임베딩 · Jev 요청에 답한다.

    준비되기 전에는 다른 요청에 503을 준다(모델 서버와 같다). 받은 요청은 .requests에.
    """
    requests: list[httpx.Request] = []
    embedding = embedding_transport(dim=dim)
    jev = jev_transport()

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            body = {
                "app": "dent-model",
                "model": model,
                "status": phase,
                "detail": detail,
                "done_bytes": done_bytes,
                "total_bytes": total_bytes,
                "eta_s": 120 if phase == "downloading" else None,
                "device": device,
                "pid": 4321,
            }
            if phase == "ok" and model == "laya":
                body["loaded"] = ["english", "multilingual", "typed-decisions"]
            return httpx.Response(200, json=body)
        if phase != "ok":
            return httpx.Response(503, json={"detail": "준비 중"})
        if request.url.path.endswith("/embeddings"):
            return embedding.handle_request(request)
        return jev.handle_request(request)

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


def refused_transport() -> httpx.MockTransport:
    """실행 중이 아닌 서버. 모든 요청이 연결 거부로 끝난다."""

    def reply(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("연결 거부", request=request)

    return httpx.MockTransport(reply)


def llm_transport(*, key: str = LLM_KEY, reply: str = "안녕하세요") -> httpx.MockTransport:
    """가짜 LLM 서버. OpenAI 방식(/models · /chat/completions)과 Anthropic 방식(/messages)을 모두 받는다.

    키가 맞지 않으면 401을 준다(Bearer 또는 x-api-key). 받은 요청은 .requests에.
    """
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        given = request.headers.get("x-api-key") or request.headers.get(
            "authorization", ""
        ).removeprefix("Bearer ")
        if given != key:
            return httpx.Response(401, json={"error": {"message": "invalid key"}})
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": name} for name in reversed(LLM_MODELS)]}
            )
        if path.endswith("/chat/completions"):
            return httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": reply}}]}
            )
        if path.endswith("/messages"):
            return httpx.Response(200, json={"content": [{"type": "text", "text": reply}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handle)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport
