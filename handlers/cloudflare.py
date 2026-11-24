"""The buttons of the Cloudflare audit log (services.cf_audit): «✅ Это я»
under a 🔴 and «📋 Что изменилось» under the morning digest. Both answer
below the message they live on: the alert and the digest stay as they were."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from handlers.common import STALE_TAP, ack
from services import cf_audit
from utils.i18n import t
from utils.text import esc

router = Router(name="cloudflare")


@router.callback_query(F.data.startswith("cfa:me:"))
async def cb_it_was_me(call: CallbackQuery):
    # Trusting an address is a change: a tap that waited in the queue while
    # the bot was down does not make it.
    fresh = await ack(call)
    if call.message is None:
        return
    if not fresh:
        await call.message.answer(t(STALE_TAP))
        return
    ips = await cf_audit.confirm(call.data.removeprefix("cfa:me:"))
    await call.message.answer(
        t("✅ Понял, это ты: {ips} запомнил — изменения оттуда дальше только в списке за сутки.").format(
            ips=esc(", ".join(ips))) if ips else t("Эту тревогу уже разобрали — адрес запомнен."))


@router.callback_query(F.data == "cfa:list")
async def cb_changes(call: CallbackQuery):
    await ack(call)
    if call.message is not None:
        await call.message.answer(await cf_audit.list_text())
