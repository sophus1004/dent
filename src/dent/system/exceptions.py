"""앱 전체에서 쓰는 도메인 예외.

HTTP를 모르는 층(시스템·모듈)은 상태 코드 대신 이 예외를 raise한다.
종류는 "무슨 일이 일어났는가"로만 나누고, 구체적인 상황은 message로 구분한다.
HTTP 상태 코드로 바꾸는 일은 api/error_handlers.py 한 곳에서만 한다.
"""


class AppError(Exception):
    """모든 도메인 예외의 부모."""

    def __init__(self, message: str) -> None:
        # 부모(Exception)에도 message를 넘긴다. 그래야 로그와 str(error)에 이 문장이 찍힌다.
        super().__init__(message)

        # 사용자에게 그대로 보여줄 문장. 무엇이 안 됐고 무엇을 하면 되는지 담는다.
        self.message = message


class StartupError(AppError):
    """실행할 때 확인에서 멈춰야 하는 문제. 이 예외가 나면 그 프로세스는 실행되지 않는다."""


class NotFoundError(AppError):
    """찾는 것이 없다. 예: 없는 데이터셋 번호."""


class ConflictError(AppError):
    """지금 상태와 부딪친다. 예: 같은 이름이 이미 있음, 다른 곳에서 먼저 고침."""


class InvalidInputError(AppError):
    """입력 모양은 맞지만 규칙에 맞지 않는다. 예: 지원하지 않는 파일 형식."""


class ExternalServiceError(AppError):
    """밖의 서비스(허깅페이스 등)에 붙지 못했거나 그쪽이 오류를 돌려줬다."""
