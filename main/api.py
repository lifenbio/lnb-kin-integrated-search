from ninja import NinjaAPI
from common.api import router as common_router

api = NinjaAPI()

api.add_router("/common/", common_router)

@api.get("/hc", auth=None)
def health_check(request):
    return 200
