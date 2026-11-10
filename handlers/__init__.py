"""Telegram handlers, assembled into one root router.

Layout: `start` is public (the first /start claims the admin seat), every
other router sits behind AdminFilter, and `deny` catches what non-admins
send so they get a polite ⛔ instead of silence.
"""

from aiogram import Router

from handlers import (
    about,
    alerts,
    blocks,
    bottom,
    feedback,
    heartbeats,
    incidents,
    links,
    mail,
    maintenance,
    menu,
    mute,
    security,
    seo,
    settings,
    setup,
    site_settings,
    sites,
    speed,
    start,
)
from handlers.common import deny_router, on_handler_error, stale_router
from handlers.filters import AdminFilter

router = Router(name="root")
router.errors.register(on_handler_error)
router.include_router(start.router)

admin = Router(name="admin")
# Every update type a handler below listens to needs its own
# `admin.<type>.filter(AdminFilter())` line — otherwise it is public. The
# list of types is pinned by a test in tests/test_dispatcher.py.
admin.message.filter(AdminFilter())
admin.callback_query.filter(AdminFilter())
admin.include_routers(
    bottom.router,   # first: a bottom button aborts any pending input
    menu.router, about.router, blocks.router, mute.router, alerts.router, incidents.router, sites.router,
    site_settings.router, settings.router, maintenance.router, heartbeats.router,
    seo.router, links.router, feedback.router, setup.router, mail.router,
    security.router, speed.router,
    stale_router,   # last: catches only what nothing above handled
)
router.include_router(admin)
router.include_router(deny_router)
