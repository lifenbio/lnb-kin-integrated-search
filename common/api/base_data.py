"""
통합검색 API 라우터
- 파일 업로드 API (/upload)
- 검색/테스트 API (/search)
"""
from ninja import Router
from .upload import upload_router
from .search import search_router


base_data_router = Router()

# 서브 라우터 연결
base_data_router.add_router("/upload", upload_router, tags=["Upload"])
base_data_router.add_router("/search", search_router, tags=["Search"])
