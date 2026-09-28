"""원본 열기 테스트: 올린 파일과 허깅페이스를 줄(줄 번호, {열 이름: 값})로 읽는다."""

import pytest

from dent.system import sources, uploads
from dent.system.exceptions import InvalidInputError, NotFoundError
from dent.system.models import Import, ImportSource
from dent.system.schemas import SourceCreate
from tests.system.helpers import HF_REPO, huggingface_transport, upload, xlsx_bytes

# 가져오기 기록 번호. 원본 열기는 이 번호로 허깅페이스 파일을 내려받을 폴더를 짓는다.
IMPORT_ID = 7


def _import_row(source: ImportSource, **options: object) -> Import:
    """DB에 넣지 않은 가져오기 기록. 원본 열기는 source와 options만 읽는다."""
    return Import(id=IMPORT_ID, source=source, options=options)


async def test_describe_source_names_file_and_keeps_upload_options(db_session):
    # 준비
    preview = await upload(db_session, "상담.csv", "text,label\n안녕,인사\n".encode())
    data = SourceCreate(source="file", upload_id=preview.upload_id)

    # 실행
    source_name, options = await sources.describe_source(db_session, data=data)

    # 확인
    assert source_name == "상담.csv"
    assert options == {"upload_id": preview.upload_id, "sheet": None}


async def test_describe_source_names_huggingface_with_config(db_session):
    # 준비
    data = SourceCreate(source="huggingface", repo="klue/klue", config="ynat", splits=["train"])

    # 실행
    source_name, options = await sources.describe_source(db_session, data=data)

    # 확인
    assert source_name == "klue/klue · ynat"
    assert options == {"repo": "klue/klue", "config": "ynat", "splits": ["train"]}


async def test_describe_source_fails_when_required_value_is_missing(db_session):
    # 실행
    with pytest.raises(InvalidInputError) as without_upload:
        await sources.describe_source(db_session, data=SourceCreate(source="file"))
    with pytest.raises(InvalidInputError) as without_repo:
        await sources.describe_source(db_session, data=SourceCreate(source="huggingface"))

    # 확인
    assert "upload_id" in without_upload.value.message
    assert "repo" in without_repo.value.message


async def test_describe_source_fails_when_upload_is_missing(db_session):
    # 실행
    with pytest.raises(NotFoundError):
        await sources.describe_source(
            db_session, data=SourceCreate(source="file", upload_id="b" * 32)
        )


async def test_open_source_reads_file_rows_with_line_numbers(db_session):
    # 준비
    content = 'text,label\n안녕,인사\n\n"두 줄\n문장",인사\n'.encode()
    preview = await upload(db_session, "줄.csv", content)
    import_row = _import_row(ImportSource.FILE, upload_id=preview.upload_id, sheet=None)

    # 실행
    async with sources.open_source(db_session, import_row) as source:
        rows = list(source.rows)

    # 확인
    assert (source.columns, source.total_rows) == (["text", "label"], 2)
    assert [(row.line, row.values, row.split_name) for row in rows] == [
        (2, {"text": "안녕", "label": "인사"}, None),
        (4, {"text": "두 줄\n문장", "label": "인사"}, None),
    ]


async def test_open_source_reads_chosen_xlsx_sheet(db_session):
    # 준비
    preview = await upload(db_session, "두 시트.xlsx", xlsx_bytes())
    import_row = _import_row(ImportSource.FILE, upload_id=preview.upload_id, sheet="둘째")

    # 실행
    async with sources.open_source(db_session, import_row) as source:
        lines = [row.line for row in source.rows]

    # 확인
    assert source.columns == ["문장", "의도", "번호"]
    assert lines == [3, 4, 5]


async def test_open_source_reads_huggingface_splits_and_removes_downloads(db_session):
    # 준비
    import_row = _import_row(ImportSource.HUGGINGFACE, repo=HF_REPO, config=None, splits=None)
    download_dir = uploads.uploads_root() / sources.HF_DOWNLOAD_DIR_NAME / str(IMPORT_ID)

    # 실행
    async with sources.open_source(
        db_session, import_row, transport=huggingface_transport()
    ) as source:
        rows = list(source.rows)
        downloaded_while_open = download_dir.is_dir()

    # 확인
    assert (source.columns, source.total_rows) == (["text", "label"], 4)
    assert [(row.line, row.split_name, row.values["label"]) for row in rows] == [
        (1, "train", "긍정"),
        (2, "train", "부정"),
        (3, "train", None),
        (4, "validation", "긍정"),
    ]
    assert downloaded_while_open
    assert not download_dir.exists()


async def test_open_source_fails_when_huggingface_split_is_only_partly_converted(db_session):
    # 준비
    import_row = _import_row(ImportSource.HUGGINGFACE, repo=HF_REPO, config="default", splits=None)
    transport = huggingface_transport(partial_train=True)

    # 실행
    with pytest.raises(InvalidInputError) as caught:
        async with sources.open_source(db_session, import_row, transport=transport):
            pass

    # 확인
    # 앞부분만 받아 '다 가져왔다'고 하지 않는다.
    assert "'train' 분할을 앞부분만" in caught.value.message


async def test_open_source_fails_when_huggingface_split_is_missing(db_session):
    # 준비
    import_row = _import_row(
        ImportSource.HUGGINGFACE, repo=HF_REPO, config="default", splits=["없는분할"]
    )

    # 실행
    with pytest.raises(NotFoundError) as caught:
        async with sources.open_source(db_session, import_row, transport=huggingface_transport()):
            pass

    # 확인
    assert "'없는분할' 분할이 없습니다" in caught.value.message
