from app.routes.farmers import router as farmers_router
from app.routes.officers import router as officers_router
from app.routes.cards import router as cards_router
from app.routes.weather import router as weather_router
from app.routes.admin import router as admin_router, auth_router as admin_auth_router

all_routers = [
    farmers_router,
    officers_router,
    cards_router,
    weather_router,
    admin_auth_router,  # POST /admin/login — no auth required
    admin_router,       # everything else under /admin — requires admin JWT
]
__all__ = ["all_routers"]
