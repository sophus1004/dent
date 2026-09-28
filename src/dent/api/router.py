"""API를 /api/v1 아래로 모은다. 시스템 API를 먼저, 모듈 API를 그 뒤에 붙인다.

모듈을 더하면 그 모듈의 라우터를 여기에 한 줄 더한다(모듈을 붙이는 세 곳 가운데 하나).
"""

from fastapi import APIRouter

from dent.api import connections, datasets, exports, helper, huggingface, imports, jobs, uploads
from dent.modules.classification import router as classification
from dent.modules.retrieval import router as retrieval

api_router = APIRouter(prefix="/api/v1")

# 시스템: 올린 파일, 허깅페이스 미리 보기, 데이터셋 목록, 가져오기 기록, 작업 진행률, 외부 모델 연결, 도우미,
# 내보낸 파일
api_router.include_router(uploads.router)
api_router.include_router(huggingface.router)
api_router.include_router(datasets.router)
api_router.include_router(imports.router)
api_router.include_router(jobs.router)
api_router.include_router(connections.router)
api_router.include_router(helper.router)
api_router.include_router(exports.router)

# 모듈: 분류 (/api/v1/classification) · 검색 (/api/v1/retrieval)
api_router.include_router(classification.router)
api_router.include_router(retrieval.router)
