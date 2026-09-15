"""Telegram handlers, assembled into one root router.

Layout: `start` is public (the first /start claims the admin seat), every
other router sits behind AdminFilter, and `deny` catches what non-admins
send so they get a polite ⛔ instead of silence.
"""

from aiogram import Router

from handlers import (
    alerts,
    feedback,
    heartbeats,
    incidents,
    links,
    maintenance,
    menu,
    mute,
    seo,
    settings,
    setup,
    site_settings,
    sites,
    start,
)
from handlers.common import deny_router, on_handler_error
from handlers.filters import AdminFilter

router = Router(name="root")
router.errors.register(on_handler_error)
router.include_router(start.router)

admin = Router(name="admin")
admin.message.filter(AdminFilter())
admin.callback_query.filter(AdminFilter())
admin.include_routers(
    menu.router, mute.router, alerts.router, incidents.router, sites.router,
    site_settings.router, settings.router, maintenance.router, heartbeats.router,
    seo.router, links.router, feedback.router, setup.router,
)
router.include_router(admin)
router.include_router(deny_router)
