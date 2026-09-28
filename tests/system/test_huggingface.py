"""허깅페이스 읽기 테스트. 인터넷 대신 가짜 응답(tests.system.helpers)을 쓴다."""

import httpx
import pytest

from dent.system import huggingface as huggingface_service
from dent.system.exceptions import ExternalServiceError, NotFoundError
from tests.system.helpers import HF_REPO, huggingface_transport, parquet_bytes


async def test_preview_converts_class_label_numbers_to_names():
    # 실행
    preview = await huggingface_service.preview(
        repo=HF_REPO, config=None, split=None, transport=huggingface_transport()
    )

    # 확인
    assert preview.configs == ["default"]
    assert preview.config == "default"
    assert [(split.name, split.num_rows) for split in preview.splits] == [
        ("train", 3),
        ("validation", 1),
    ]
    assert preview.split == "train"
    assert preview.columns == ["text", "label"]
    assert preview.label_names == {"label": ["부정", "긍정"]}
    assert preview.rows == [
        {"text": "좋아요", "label": "긍정"},
        {"text": "라벨 없는 줄", "label": None},
    ]


async def test_preview_fails_when_dataset_is_missing():
    # 준비
    transport = httpx.MockTransport(lambda _request: httpx.Response(404, json={"error": "x"}))

    # 실행
    with pytest.raises(NotFoundError) as caught:
        await huggingface_service.preview(
            repo="nobody/nothing", config=None, split=None, transport=transport
        )

    # 확인
    assert caught.value.message == "허깅페이스에서 데이터셋을 찾지 못했습니다: nobody/nothing"


async def test_preview_fails_when_config_is_missing():
    # 실행
    with pytest.raises(NotFoundError) as caught:
        await huggingface_service.preview(
            repo=HF_REPO, config="없는구성", split=None, transport=huggingface_transport()
        )

    # 확인
    assert "'없는구성' 구성이 없습니다" in caught.value.message


async def test_preview_fails_when_offline():
    # 준비
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("연결 거부", request=request)

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        await huggingface_service.preview(
            repo=HF_REPO, config=None, split=None, transport=httpx.MockTransport(refuse)
        )

    # 확인
    assert caught.value.message == "허깅페이스에 붙지 못했습니다. 인터넷 연결을 확인하세요."


async def test_list_parquet_files_marks_partly_converted_splits():
    # 실행
    files = await huggingface_service.list_parquet_files(
        repo=HF_REPO, config="default", transport=huggingface_transport(partial_train=True)
    )

    # 확인
    assert sorted(files.urls_by_split) == ["train", "validation"]
    assert files.partial_splits == {"train"}


async def test_download_follows_redirect_and_writes_file(tmp_path):
    # 준비
    target = tmp_path / "hf" / "train.parquet"

    # 실행
    await huggingface_service.download(
        url="https://huggingface.co/files/train/train-0.parquet",
        target=target,
        repo=HF_REPO,
        transport=huggingface_transport(),
    )

    # 확인
    assert target.read_bytes()[:4] == b"PAR1"


def test_read_rows_turns_class_label_numbers_into_names(tmp_path):
    # 준비
    path = tmp_path / "train.parquet"
    path.write_bytes(parquet_bytes(["좋아요", "라벨 없는 줄"], [1, -1]))

    # 실행
    rows = list(huggingface_service.read_rows(path, label_names={"label": ["부정", "긍정"]}))

    # 확인
    # 라벨 번호 -1은 라벨이 없다는 뜻이라 None이 된다.
    assert rows == [{"text": "좋아요", "label": "긍정"}, {"text": "라벨 없는 줄", "label": None}]
