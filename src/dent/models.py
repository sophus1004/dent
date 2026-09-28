"""모든 테이블 모델 목록. Alembic과 테스트가 import해서 Base.metadata에 모든 테이블이 올라오게 한다.

모듈을 더하면 그 모듈의 models를 여기에 한 줄 더한다(모듈을 붙이는 세 곳 가운데 하나).
빠뜨리면 Alembic이 테이블을 못 찾는다.
"""

from dent.modules.classification import models as classification_models  # noqa: F401  분류
from dent.modules.retrieval import models as retrieval_models  # noqa: F401  검색
from dent.system import models as system_models  # noqa: F401  시스템
