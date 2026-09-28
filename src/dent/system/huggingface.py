"""허깅페이스 데이터셋 읽기. 데이터셋 뷰어 API(datasets-server)만 쓴다(datasets 라이브러리 없이).

미리 보기는 /splits(구성·분할 목록), /info(줄 수·라벨 이름), /first-rows(앞쪽 줄)를 부른다.
가져오기는 /parquet이 알려 주는 Parquet 파일을 내려받아 조금씩 읽는다(tables.iter_parquet_rows).
번호로 저장된 라벨(ClassLabel)은 읽을 때 이름으로 바꿔서, 파일에서 읽은 줄과 같은 모양이 되게 한다.
.env의 HF_TOKEN이 있으면 붙여서 비공개·동의가 필요한 데이터셋도 읽는다.
테스트는 transport에 가짜 응답을 넘겨 인터넷 없이 돌린다.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from dent.system import tables
from dent.system.config import get_settings
from dent.system.exceptions import ExternalServiceError, NotFoundError
from dent.system.schemas import CellValue, HuggingFacePreviewRead, HuggingFaceSplitRead

# 허깅페이스 데이터셋 뷰어 API 주소
VIEWER_URL = "https://datasets-server.huggingface.co"

# 요청 하나의 시간 한도(초). 내려받기는 조각마다 이 시간 안에 와야 한다.
REQUEST_TIMEOUT_S = 30.0

# 미리 보기로 보여 줄 줄 수
PREVIEW_ROWS = 20

# 내려받을 때 한 번에 쓰는 크기
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

# 없는 데이터셋으로 보는 응답. 비공개·동의가 필요한 데이터셋도 토큰 없이는 이렇게 온다.
NOT_FOUND_STATUS_CODES = (
    httpx.codes.UNAUTHORIZED,
    httpx.codes.FORBIDDEN,
    httpx.codes.NOT_FOUND,
)

# 라벨 번호가 이름 목록에 있는 열의 종류 이름
CLASS_LABEL_TYPE = "ClassLabel"

# 앞부분만 변환된 분할의 파일이 든 폴더 이름 앞부분 (예: .../partial-train/0000.parquet)
PARTIAL_DIR_PREFIX = "partial-"

OFFLINE_MESSAGE = "허깅페이스에 붙지 못했습니다. 인터넷 연결을 확인하세요."


@dataclass
class ParquetFiles:
    """한 구성의 Parquet 파일들."""

    # 분할별 파일 주소 {분할 이름: [주소, ...]}. 큰 분할은 파일 여러 개로 나뉘어 있다.
    urls_by_split: dict[str, list[str]] = field(default_factory=dict)

    # 허깅페이스가 앞부분만 변환해 둔 분할 이름들
    partial_splits: set[str] = field(default_factory=set)


def not_found_message(repo: str) -> str:
    """데이터셋을 찾지 못했을 때의 문장."""
    return f"허깅페이스에서 데이터셋을 찾지 못했습니다: {repo}"


async def preview(
    *,
    repo: str,
    config: str | None,
    split: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HuggingFacePreviewRead:
    """구성·분할 목록과 앞쪽 몇 줄. 없으면 NotFoundError, 붙지 못하면 ExternalServiceError."""
    async with _new_client(transport) as client:
        split_names_by_config = await _read_splits(client, repo=repo)
        configs = list(split_names_by_config)
        chosen_config = config or configs[0]
        if chosen_config not in split_names_by_config:
            raise NotFoundError(f"허깅페이스 데이터셋 {repo}에 '{chosen_config}' 구성이 없습니다.")
        split_names = split_names_by_config[chosen_config]
        chosen_split = split or split_names[0]
        if chosen_split not in split_names:
            raise NotFoundError(f"허깅페이스 데이터셋 {repo}에 '{chosen_split}' 분할이 없습니다.")

        info = await _get_json(
            client, "/info", {"dataset": repo, "config": chosen_config}, repo=repo
        )
        split_infos = (info.get("dataset_info") or {}).get("splits") or {}
        first_rows = await _get_json(
            client,
            "/first-rows",
            {"dataset": repo, "config": chosen_config, "split": chosen_split},
            repo=repo,
        )

    features = first_rows.get("features") or []
    columns = [feature["name"] for feature in features]
    label_names = class_label_names(features)
    rows = [
        _convert_row(item.get("row") or {}, columns, label_names)
        for item in (first_rows.get("rows") or [])[:PREVIEW_ROWS]
    ]
    return HuggingFacePreviewRead(
        repo=repo,
        configs=configs,
        config=chosen_config,
        splits=[
            HuggingFaceSplitRead(
                name=name, num_rows=(split_infos.get(name) or {}).get("num_examples")
            )
            for name in split_names
        ],
        split=chosen_split,
        columns=columns,
        label_names=label_names,
        rows=rows,
    )


async def first_config(*, repo: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """데이터셋의 첫 구성 이름. 가져오기에 구성을 주지 않았을 때 쓴다."""
    async with _new_client(transport) as client:
        split_names_by_config = await _read_splits(client, repo=repo)
    return next(iter(split_names_by_config))


async def fetch_label_names(
    *, repo: str, config: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, list[str]]:
    """번호로 저장된 라벨 열의 이름 목록 {열 이름: [라벨 이름, ...]}."""
    async with _new_client(transport) as client:
        info = await _get_json(client, "/info", {"dataset": repo, "config": config}, repo=repo)
    features = (info.get("dataset_info") or {}).get("features") or {}
    return class_label_names(features)


async def list_parquet_files(
    *, repo: str, config: str, transport: httpx.AsyncBaseTransport | None = None
) -> ParquetFiles:
    """구성의 분할별 Parquet 파일 주소와, 앞부분만 변환된 분할. 받을 파일이 없으면 NotFoundError."""
    async with _new_client(transport) as client:
        body = await _get_json(client, "/parquet", {"dataset": repo, "config": config}, repo=repo)
    files = ParquetFiles()
    for item in body.get("parquet_files") or []:
        if item.get("config") != config:
            continue
        split = item["split"]
        files.urls_by_split.setdefault(split, []).append(item["url"])
        # 큰 분할은 앞부분(약 5GB)만 변환되고, 파일 주소가 .../partial-{분할}/... 이 된다.
        if f"/{PARTIAL_DIR_PREFIX}{split}/" in item["url"]:
            files.partial_splits.add(split)
    if not files.urls_by_split:
        raise NotFoundError(f"허깅페이스 데이터셋 {repo}의 '{config}' 구성에 받을 파일이 없습니다.")
    return files


async def download(
    *, url: str, target: Path, repo: str, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """파일 하나를 조각조각 내려받아 target에 쓴다. 다른 주소로 넘겨 주면 따라간다."""
    target.parent.mkdir(parents=True, exist_ok=True)
    async with _new_client(transport) as client:
        try:
            async with client.stream("GET", url) as response:
                _raise_for_status(response, repo=repo)
                with target.open("wb") as file:
                    async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                        file.write(chunk)
        except httpx.HTTPError:
            raise ExternalServiceError(OFFLINE_MESSAGE) from None


def read_rows(path: Path, *, label_names: dict[str, list[str]]) -> Iterator[dict[str, CellValue]]:
    """내려받은 Parquet 파일의 줄을 {열 이름: 칸 값}으로 조금씩 낸다. 라벨 번호는 이름으로 바꾼다."""
    columns = tables.parquet_columns(path)
    for row in tables.iter_parquet_rows(path):
        yield _convert_row(row, columns, label_names)


def class_label_names(features: list[dict[str, Any]] | dict[str, Any]) -> dict[str, list[str]]:
    """열 설명에서 ClassLabel 열의 이름 목록을 찾는다.

    /first-rows는 [{"name", "type"}, ...] 목록으로, /info는 {열 이름: 종류} 사전으로 준다.
    """
    if isinstance(features, dict):
        types_by_column = features
    else:
        types_by_column = {feature["name"]: feature.get("type") or {} for feature in features}
    return {
        column: list(feature_type.get("names") or [])
        for column, feature_type in types_by_column.items()
        if isinstance(feature_type, dict) and feature_type.get("_type") == CLASS_LABEL_TYPE
    }


def _convert_row(
    row: dict[str, Any], columns: list[str], label_names: dict[str, list[str]]
) -> dict[str, CellValue]:
    """허깅페이스 한 줄을 {열 이름: 칸 값}으로 바꾼다. 라벨 번호 -1(라벨 없음)은 None이 된다."""
    values: dict[str, CellValue] = {}
    for column in columns:
        value = row.get(column)
        if column in label_names:
            value = _label_name(value, label_names[column])
        values[column] = tables.cell_value(value)
    return values


def _label_name(value: Any, names: list[str]) -> Any:
    """라벨 번호를 이름으로 바꾼다. 번호가 아니면 그대로, 범위 밖(-1 등)이면 None."""
    is_index = isinstance(value, int) and not isinstance(value, bool)
    if not is_index:
        return value
    is_known = 0 <= value < len(names)
    return names[value] if is_known else None


def _new_client(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    """뷰어 API용 클라이언트. 토큰이 설정돼 있으면 붙인다."""
    headers = {}
    token = get_settings().hf_token
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    return httpx.AsyncClient(
        base_url=VIEWER_URL,
        headers=headers,
        timeout=REQUEST_TIMEOUT_S,
        follow_redirects=True,
        transport=transport,
    )


async def _read_splits(client: httpx.AsyncClient, *, repo: str) -> dict[str, list[str]]:
    """구성별 분할 이름 {구성: [분할, ...]}. 하나도 없으면 NotFoundError."""
    body = await _get_json(client, "/splits", {"dataset": repo}, repo=repo)
    split_names_by_config: dict[str, list[str]] = {}
    for item in body.get("splits") or []:
        split_names_by_config.setdefault(item["config"], []).append(item["split"])
    if not split_names_by_config:
        raise NotFoundError(not_found_message(repo))
    return split_names_by_config


async def _get_json(
    client: httpx.AsyncClient, path: str, params: dict[str, str], *, repo: str
) -> dict[str, Any]:
    """뷰어 API를 불러 JSON을 돌려준다."""
    try:
        response = await client.get(path, params=params)
    except httpx.HTTPError:
        raise ExternalServiceError(OFFLINE_MESSAGE) from None
    _raise_for_status(response, repo=repo)
    try:
        body = response.json()
    except ValueError:
        raise ExternalServiceError("허깅페이스가 알 수 없는 답을 보냈습니다.") from None
    if not isinstance(body, dict):
        raise ExternalServiceError("허깅페이스가 알 수 없는 답을 보냈습니다.")
    return body


def _raise_for_status(response: httpx.Response, *, repo: str) -> None:
    """실패 응답이면 NotFoundError(없음·권한 없음)나 ExternalServiceError(그 밖)를 낸다."""
    if response.status_code in NOT_FOUND_STATUS_CODES:
        raise NotFoundError(not_found_message(repo))
    if response.is_error:
        raise ExternalServiceError(
            f"허깅페이스가 요청을 처리하지 못했습니다 (HTTP {response.status_code}). "
            "잠시 뒤 다시 해 보세요."
        )
