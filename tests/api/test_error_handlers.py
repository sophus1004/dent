"""도메인 예외 → HTTP 변환 테스트."""

import json

from fastapi import status

from dent.api.error_handlers import handle_app_error
from dent.system.exceptions import AppError, ConflictError


async def test_handle_app_error_uses_status_code_table():
    # 실행
    response = await handle_app_error(None, ConflictError("이미 있습니다."))  # type: ignore[arg-type]

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert json.loads(response.body) == {"detail": "이미 있습니다."}


async def test_handle_app_error_returns_500_when_type_is_not_in_table():
    # 준비
    class ForgottenError(AppError):
        """표에 넣는 것을 잊은 예외 종류."""

    # 실행
    response = await handle_app_error(None, ForgottenError("표에 없음"))  # type: ignore[arg-type]

    # 확인
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert json.loads(response.body) == {"detail": "표에 없음"}
