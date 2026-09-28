"""올린 파일 테스트: 저장, 목록(system_files), 지우기, 미리 보기(csv·tsv·xlsx 읽기)."""

import hashlib

import pytest
from sqlalchemy import func, select

from dent.system import uploads as uploads_service
from dent.system.exceptions import InvalidInputError, NotFoundError
from dent.system.models import File
from tests.system.helpers import upload, upload_state, xlsx_bytes

# ---------- 저장과 목록 ----------


async def test_save_upload_records_file_with_size_and_sha256(db_session):
    # 준비
    content = "text,label\n안녕,인사\n".encode()

    # 실행
    preview = await upload(db_session, "../인사 목록.csv", content)

    # 확인
    file = await db_session.get(File, preview.upload_id)
    assert file is not None
    assert file.original_name == "../인사 목록.csv"
    assert file.size_bytes == len(content)
    assert file.sha256 == hashlib.sha256(content).hexdigest()
    assert file.stored_path.startswith("uploads/")
    assert file.stored_path.endswith(f"/{preview.upload_id}/인사_목록.csv")
    assert preview.file_name == "인사_목록.csv"


async def test_save_upload_gives_same_sha256_to_same_content(db_session):
    # 준비
    content = "text,label\n안녕,인사\n".encode()

    # 실행
    first = await upload(db_session, "하나.csv", content)
    second = await upload(db_session, "둘.csv", content)

    # 확인
    # 같은 파일을 다시 올렸는지 나중에 이 값으로 알아본다.
    hashes = await db_session.scalars(
        select(File.sha256).where(File.id.in_([first.upload_id, second.upload_id]))
    )
    assert len(set(hashes.all())) == 1


async def test_save_upload_leaves_no_file_or_record_when_file_is_unreadable(db_session):
    # 준비
    content = "\ufefftext,label\n안녕,인사\n".encode() + b"\xff\xfe,x\n"

    # 실행
    with pytest.raises(InvalidInputError) as caught:
        await upload(db_session, "깨짐.csv", content)

    # 확인
    count = await db_session.scalar(select(func.count()).select_from(File))
    assert "인코딩" in caught.value.message
    assert list(uploads_service.uploads_root().glob("*/*/*/깨짐.csv")) == []
    assert count == 0


async def test_save_upload_fails_when_format_is_not_supported(db_session):
    # 실행
    with pytest.raises(InvalidInputError) as caught:
        await upload(db_session, "메모.txt", b"hello")

    # 확인
    assert ".csv 또는 .xlsx" in caught.value.message


async def test_find_upload_fails_when_id_is_not_an_upload_id(db_session):
    # 실행
    with pytest.raises(NotFoundError) as caught:
        await uploads_service.find_upload(db_session, upload_id="../../etc")

    # 확인
    assert caught.value.message == uploads_service.UPLOAD_NOT_FOUND_MESSAGE


async def test_find_upload_fails_when_stored_file_is_gone(db_session):
    # 준비
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())
    path = await uploads_service.find_upload(db_session, upload_id=preview.upload_id)
    path.unlink()

    # 실행
    with pytest.raises(NotFoundError):
        await uploads_service.find_upload(db_session, upload_id=preview.upload_id)


# ---------- 지우기 ----------


async def test_discard_upload_removes_file_and_folder_but_keeps_record(db_session):
    # 준비
    content = "text,label\n안녕,인사\n".encode()
    preview = await upload(db_session, "인사.csv", content)
    path = await uploads_service.find_upload(db_session, upload_id=preview.upload_id)

    # 실행
    await uploads_service.discard_upload(db_session, upload_id=preview.upload_id)
    await db_session.commit()

    # 확인
    file = await db_session.get(File, preview.upload_id)
    assert await upload_state(db_session, preview.upload_id) == (False, True)
    assert not path.parent.exists()
    assert file is not None
    assert (file.original_name, file.size_bytes, file.sha256) == (
        "인사.csv",
        len(content),
        hashlib.sha256(content).hexdigest(),
    )


async def test_find_upload_fails_with_deleted_message_when_upload_is_discarded(db_session):
    # 준비
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())
    await uploads_service.discard_upload(db_session, upload_id=preview.upload_id)
    await db_session.commit()

    # 실행
    with pytest.raises(NotFoundError) as caught:
        await uploads_service.find_upload(db_session, upload_id=preview.upload_id)

    # 확인
    assert caught.value.message == uploads_service.UPLOAD_DELETED_MESSAGE


# ---------- 미리 보기 (표 읽기) ----------


async def test_save_upload_reads_utf8_csv_with_bom(db_session):
    # 준비
    content = "\ufefftext,label\n안녕하세요,인사\n".encode()

    # 실행
    preview = await upload(db_session, "인사.csv", content)

    # 확인
    assert preview.encoding == "utf-8"
    assert preview.delimiter == ","
    assert preview.columns == ["text", "label"]
    assert preview.rows == [{"text": "안녕하세요", "label": "인사"}]
    assert preview.row_count == 1


async def test_save_upload_reads_cp949_csv(db_session):
    # 준비
    content = "문장,라벨\n환불해 주세요,환불\n배송 언제 와요,배송\n".encode("cp949")

    # 실행
    preview = await upload(db_session, "상담.csv", content)

    # 확인
    assert preview.encoding == "cp949"
    assert preview.rows[0] == {"문장": "환불해 주세요", "라벨": "환불"}


async def test_save_upload_reads_tsv_with_tab_delimiter(db_session):
    # 준비
    content = "text\tlabel\tsplit\n쉼표, 있는 문장\t인사\ttrain\n".encode()

    # 실행
    preview = await upload(db_session, "data.tsv", content)

    # 확인
    assert preview.delimiter == "\t"
    assert preview.rows == [{"text": "쉼표, 있는 문장", "label": "인사", "split": "train"}]


async def test_save_upload_fails_when_csv_quote_is_not_closed(db_session):
    # 준비
    content = 'text,label\n"따옴표로 시작하는 리뷰,1\n그냥 리뷰,0\n'.encode()

    # 실행
    with pytest.raises(InvalidInputError) as caught:
        await upload(db_session, "리뷰.csv", content)

    # 확인
    # 닫히지 않은 따옴표가 뒷줄을 말없이 삼키지 않고, 고칠 곳을 알려 준다.
    assert "따옴표" in caught.value.message


async def test_save_upload_reads_tsv_quotes_as_letters_when_quotes_are_unbalanced(db_session):
    # 준비
    content = (
        "id\tdocument\tlabel\n"
        '1\t"따옴표로 시작하는 리뷰\t0\n'
        "2\t그냥 리뷰\t1\n"
        '3\t"좋아요" 라고 했다\t1\n'
        "4\t마지막 리뷰\t0\n"
    ).encode()

    # 실행
    preview = await upload(db_session, "reviews.tsv", content)

    # 확인
    assert preview.row_count == 4
    assert [row["document"] for row in preview.rows] == [
        '"따옴표로 시작하는 리뷰',
        "그냥 리뷰",
        '"좋아요" 라고 했다',
        "마지막 리뷰",
    ]


async def test_save_upload_reports_line_numbers_of_preview_rows(db_session):
    # 준비: 빈 줄과 여러 줄짜리 칸이 있어 데이터 순번과 파일 줄 번호가 다르다.
    content = 'text,label\n안녕,인사\n\n"두 줄\n문장",인사\n끝,인사\n'.encode()

    # 실행
    preview = await upload(db_session, "줄.csv", content)
    sheet = await upload(db_session, "두 시트.xlsx", xlsx_bytes())
    second = await uploads_service.preview_upload(
        db_session, upload_id=sheet.upload_id, sheet="둘째"
    )

    # 확인
    # 가져오기가 건너뛴 줄을 알릴 때와 같은 번호다(칸 이름 줄이 1, 엑셀은 시트 행 번호).
    assert preview.lines == [2, 4, 6]
    assert second.lines == [3, 4, 5]


async def test_save_upload_detects_semicolon_delimiter(db_session):
    # 실행
    preview = await upload(db_session, "유럽식.csv", b"text;label\nhello;greet\n")

    # 확인
    assert preview.delimiter == ";"
    assert preview.columns == ["text", "label"]


async def test_save_upload_names_empty_and_repeated_columns(db_session):
    # 실행
    preview = await upload(db_session, "열.csv", b"text,,text\na,b,c\n")

    # 확인
    assert preview.columns == ["text", "열2", "text_2"]


async def test_preview_upload_reads_each_xlsx_sheet(db_session):
    # 준비
    first = await upload(db_session, "두 시트.xlsx", xlsx_bytes())

    # 실행
    second = await uploads_service.preview_upload(
        db_session, upload_id=first.upload_id, sheet="둘째"
    )

    # 확인
    assert first.sheets == ["첫째", "둘째"]
    assert first.sheet == "첫째"
    assert first.rows == [{"text": "첫 시트 문장", "label": "인사"}]
    assert second.sheet == "둘째"
    assert second.columns == ["문장", "의도", "번호"]
    assert second.row_count == 3
    assert second.rows[2] == {"문장": "환불해 주세요", "의도": "환불", "번호": 3}


async def test_preview_upload_fails_when_sheet_is_missing(db_session):
    # 준비
    preview = await upload(db_session, "두 시트.xlsx", xlsx_bytes())

    # 실행
    with pytest.raises(NotFoundError) as caught:
        await uploads_service.preview_upload(db_session, upload_id=preview.upload_id, sheet="셋째")

    # 확인
    assert "셋째" in caught.value.message


async def test_preview_upload_fails_when_upload_is_missing(db_session):
    # 실행
    with pytest.raises(NotFoundError):
        await uploads_service.preview_upload(db_session, upload_id="0" * 32, sheet=None)
