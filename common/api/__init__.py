from ninja import Router
from .base_data import base_data_router


router = Router()


router.add_router("/base", base_data_router)