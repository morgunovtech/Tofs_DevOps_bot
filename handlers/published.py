"""«➕ Следить» and «Не надо» under the Sunday line «N адресов без присмотра»
(services.published). An address of a site is added the way a typed one is
(handlers.sites.add_site), the answer comes below the report; a Worker route
has no page to watch, so the owner is asked for the API's address under it."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.common import STALE_TAP, ack, cancel_kb
from handlers.sites import AddSiteForm, add_site
from services import published
from utils.i18n import t
from utils.text import esc

router = Router(name="published")


@router.callback_query(F.data.startswith(("pub:add:", "pub:no:")))
async def cb_published(call: CallbackQuery, state: FSMContext):
    # Adding a site or forgetting an address is a change: a tap that waited
    # in the queue while the bot was down does not make it.
    fresh = await ack(call)
    if call.message is None:
        return
    if not fresh:
        await call.message.answer(t(STALE_TAP))
        return
    _, action, h = call.data.split(":", 2)
    item = await published.pick(h)
    if item is None:
        await call.message.answer(t("Этот адрес уже под присмотром, или от него уже отказались."))
        return
    key = str(item["key"])
    if action == "no":
        await published.ignore(key)
        await call.message.answer(t("Понял: {key} не предлагаю. Передумаешь — добавь его через «➕ Добавить».").format(
            key=esc(key)))
        return
    if "/" in key:
        await state.set_state(AddSiteForm.url)
        example = key.rstrip("*") + "health"
        await call.message.answer(
            t("🔌 {key} — это Worker без страницы: следить можно за его API. Пришли адрес, который отвечает, "
              "например <code>{example}</code>.").format(key=esc(key), example=esc(example)),
            reply_markup=cancel_kb())
        return
    await add_site(call.message, str(item["url"]), state)
