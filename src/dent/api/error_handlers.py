"""도메인 예외를 HTTP 응답으로 바꾼다. 이 변환은 여기서만 한다.

응답 모양은 FastAPI 기본값과 같은 {"detail": "<한국어 문장>"}이다.
새 예외 종류를 만들면 STATUS_CODE_BY_ERROR_TYPE에 한 줄 더한다.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from dent.system.exceptions import (
    AppError,
    ConflictError,
    ExternalServiceError,
    InvalidInputError,
    NotFoundError,
)

logger = logging.getLogger("dent.api")

# 예외 종류별 상태 코드. 자식 종류까지 찾지 않고 정확히 이 종류일 때만 쓴다.
STATUS_CODE_BY_ERROR_TYPE: dict[type[AppError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ExternalServiceError: status.HTTP_502_BAD_GATEWAY,
}


async def handle_app_error(_request: Request, error: Exception) -> JSONResponse:
    """도메인 예외를 표의 상태 코드와 {"detail": 문장}으로 돌려준다. 표에 없으면 500."""
    message = error.message if isinstance(error, AppError) else str(error)
    status_code = STATUS_CODE_BY_ERROR_TYPE.get(type(error))
    if status_code is None:
        # 표에 없는 예외는 표에 추가하는 것을 잊은 코드 실수다. 숨기지 않고 500으로 드러낸다.
        logger.error("상태 코드 표에 없는 예외입니다: %s: %s", type(error).__name__, message)
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return JSONResponse(status_code=status_code, content={"detail": message})


def register_error_handlers(app: FastAPI) -> None:
    """앱에 도메인 예외 처리기를 붙인다."""
    app.add_exception_handler(AppError, handle_app_error)
