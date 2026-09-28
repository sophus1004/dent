"""데이터셋 목록 API 테스트: 모든 모듈의 목록, 이름·설명 고치기, 가져오기 기록."""

from fastapi import status

from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system.models import ImportSource

API = "/api/v1/datasets"


async def test_list_datasets_returns_every_module_with_module_name(client, db_session):
    # 준비
    first = await datasets_service.create_dataset(db_session, module="classification", name="분류")
    second = await datasets_service.create_dataset(db_session, module="generation", name="생성")
    await db_session.commit()

    # 실행
    response = await client.get(API)

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert [(item["id"], item["module"], item["name"]) for item in body] == [
        (second.id, "generation", "생성"),
        (first.id, "classification", "분류"),
    ]


async def test_list_datasets_returns_empty_list_when_there_is_nothing(client, db_session):
    # 실행
    response = await client.get(API)

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


async def test_update_dataset_renames_and_describes(client, db_session):
    # 준비
    dataset = await datasets_service.create_dataset(db_session, module="classification", name="옛")
    await db_session.commit()

    # 실행
    response = await client.patch(
        f"{API}/{dataset.id}", json={"name": " 새 이름 ", "description": "설명"}
    )

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["name"], body["description"], body["module"]) == (
        "새 이름",
        "설명",
        "classification",
    )


async def test_update_dataset_returns_409_when_name_is_taken(client, db_session):
    # 준비
    await datasets_service.create_dataset(db_session, module="classification", name="가져간 이름")
    mine = await datasets_service.create_dataset(
        db_session, module="classification", name="내 이름"
    )
    await db_session.commit()

    # 실행
    response = await client.patch(f"{API}/{mine.id}", json={"name": "가져간 이름"})

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": "같은 이름의 데이터셋이 이미 있습니다."}


async def test_update_dataset_returns_404_when_missing(client, db_session):
    # 실행
    response = await client.patch(f"{API}/999", json={"name": "새 이름"})

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "데이터셋을 찾을 수 없습니다."}


async def test_list_imports_returns_recent_first(client, db_session):
    # 준비
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="상담"
    )
    created = []
    for repo in ("a/one", "a/two"):
        import_row = await imports_service.create_import(
            db_session,
            module="classification",
            dataset_id=dataset.id,
            source=ImportSource.HUGGINGFACE,
            source_name=repo,
            options={"repo": repo},
        )
        created.append(import_row.id)
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/{dataset.id}/imports")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert [item["id"] for item in body] == list(reversed(created))
    assert body[0]["result"] == {}


async def test_list_imports_returns_404_when_dataset_is_missing(client, db_session):
    # 실행
    response = await client.get(f"{API}/999/imports")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
