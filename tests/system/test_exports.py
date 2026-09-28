"""내보낸 파일 테스트: 기록과 작업 만들기, 파일 이름 다듬기, 끝내기 · 실패, 보관 기간 정리."""

from datetime import UTC, datetime, timedelta

import pytest

from dent.system import exports, jobs
from dent.system.exceptions import NotFoundError
from dent.system.models import Dataset, Export, ExportStatus, JobStatus


async def _make_export(db_session, *, file_name: str = "상담.jsonl") -> Export:
    dataset = Dataset(module="retrieval", name=f"검색 {file_name}")
    db_session.add(dataset)
    await db_session.flush()
    export = await exports.create_export(
        db_session,
        module="retrieval",
        dataset_id=dataset.id,
        format="train_jsonl",
        file_name=file_name,
        options={"negatives": 7},
    )
    await db_session.commit()
    return export


def test_safe_file_name_removes_path_characters():
    # 실행 · 확인
    assert exports.safe_file_name("../상담/학습:v1.jsonl") == "상담-학습-v1.jsonl"
    assert exports.safe_file_name(" /// ") == "export"


async def test_create_export_enqueues_job(db_session):
    # 실행
    export = await _make_export(db_session)

    # 확인
    job = await jobs.get_job(db_session, job_id=export.job_id)
    assert (job.module, job.kind) == ("retrieval", exports.EXPORT_JOB_KIND)
    assert job.params == {"export_id": export.id}
    assert export.status == ExportStatus.QUEUED


async def test_finish_export_records_size_and_file_is_found(db_session):
    # 준비
    export = await _make_export(db_session)
    exports.start_export(export)
    exports.file_path(export).write_text('{"query": "환불"}\n', encoding="utf-8")

    # 실행
    await exports.finish_export(db_session, export, job_id=export.job_id, result={"lines": 1})
    await db_session.commit()

    # 확인
    found, path = await exports.find_file(db_session, export_id=export.id)
    assert found.status == ExportStatus.DONE
    assert found.size_bytes == path.stat().st_size
    job = await jobs.get_job(db_session, job_id=export.job_id)
    assert job.status == JobStatus.DONE


async def test_find_file_raises_not_found_while_running(db_session):
    # 준비
    export = await _make_export(db_session)

    # 실행 · 확인
    with pytest.raises(NotFoundError):
        await exports.find_file(db_session, export_id=export.id)


async def test_fail_export_removes_partial_file(db_session):
    # 준비
    export = await _make_export(db_session)
    path = exports.file_path(export)
    path.write_text("반쯤", encoding="utf-8")

    # 실행
    await exports.fail_export(
        db_session, export_id=export.id, job_id=export.job_id, message="쓰지 못했습니다."
    )
    await db_session.commit()

    # 확인
    failed = await exports.get_export(db_session, export_id=export.id)
    assert failed.status == ExportStatus.FAILED
    assert failed.error == "쓰지 못했습니다."
    assert not path.exists()


async def test_discard_expired_exports_removes_old_files_only(db_session):
    # 준비
    old = await _make_export(db_session, file_name="old.jsonl")
    fresh = await _make_export(db_session, file_name="fresh.jsonl")
    exports.file_path(old).write_text("old", encoding="utf-8")
    exports.file_path(fresh).write_text("fresh", encoding="utf-8")
    old.created_at = datetime.now(UTC) - exports.EXPORT_RETENTION - timedelta(hours=1)
    await db_session.commit()

    # 실행
    removed = await exports.discard_expired_exports(db_session)
    await db_session.commit()

    # 확인
    assert removed == 1
    assert not exports.file_path(old).exists()
    assert exports.file_path(fresh).exists()
    assert (await exports.get_export(db_session, export_id=old.id)).deleted_at is not None
