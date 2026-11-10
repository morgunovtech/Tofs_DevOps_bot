"""⏰ Tasks and bots: «tell me if the backup — or another bot — went
silent». Under the hood a dead-man switch: jobs ping the bot, silence
longer than the interval is an alert. They sit on the main screen next to
the sites, each with a card; the words «cron» and «curl» appear only on
that card, next to the ready-made line."""

import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db.database import get_state
from handlers.common import CANCEL, HEARTBEAT_SECRET_NOTE, STALE_TAP, ack, back_button, cancel_kb, render
from services import heartbeats, public_urls, settings
from services.heartbeats import INTERVALS, every, job_states
from utils.text import esc

router = Router(name="heartbeats")


class AddHeartbeatForm(StatesGroup):
    name = State()


def _kind_rows(name: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="🤖 Это бот или сервис", callback_data=f"hb_bot:{name}")],
            [InlineKeyboardButton(text="🗓 Это задача по расписанию", callback_data=f"hb_cron:{name}")]]


async def job_screen(name: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """The card answers «what is this and is it fine?» first. A task that
    never marked asks one question — a bot, a scheduled task, or nothing
    of the kind — and the address with its how-to sits behind the answer:
    a raw curl line with a secret in it explained nothing to anybody.
    None when the job is no longer watched (a button on an old message)."""
    job = next((j for j in await job_states() if j.name == name), None)
    if job is None:
        return None
    n, rhythm = esc(name), every(job.interval)
    lines = [f"⏰ {n}"]
    rows: list[list[InlineKeyboardButton]] = []
    if job.icon == "✅":
        lines += [f"✅ {esc(job.state[:1].upper() + job.state[1:])}.", f"Выходит на связь {rhythm}. Замолчит — напишу."]
        rows.append([InlineKeyboardButton(text="🔗 Как подключена", callback_data=f"hb_ask:{name}")])
    elif job.icon == "🟠":
        lines += [f"🟠 {esc(job.state[:1].upper() + job.state[1:])}, а выходит на связь {rhythm}.", "",
                  "Обычно это значит, что задача не запустилась или упала, не дойдя до конца. Проверь её. "
                  "Если меняли адрес для отметок — подключи заново."]
        rows.append([InlineKeyboardButton(text="🔗 Подключить заново", callback_data=f"hb_ask:{name}")])
    else:
        lines += [f"{job.icon} Пока не выходит на связь · жду {rhythm}", "",
                  f"Я жду, что «{n}» будет выходить со мной на связь {rhythm} — так я узнаю, что работа "
                  f"запустилась и дошла до конца. Связи ещё не было: похоже, не подключили.", "",
                  f"Что такое «{n}» у тебя?"]
        rows += _kind_rows(name)
    status = await heartbeats.job_status(name)
    if status:
        lines[2:2] = ["", f"{esc(heartbeats.status_title(job, status))}:",
                      *(f"{icon} {esc(item)} · {esc(text)}" for icon, item, text in heartbeats.status_items(job, status))]
        if status.get("open_url"):
            # The host on the button: the owner sees where a bot's link leads before tapping.
            rows.insert(0, [InlineKeyboardButton(text=f"↗️ {heartbeats.link_host(status['open_url'])}",
                                                 url=status["open_url"])])
    if await get_state(f"hb_legacy:{name}"):
        lines += ["", "⚠️ Выходит на связь по старому адресу, общему для всех задач. Он работает, но лучше "
                      "заменить его на собственный адрес задачи — он за кнопкой «🔗» ниже: тогда утечка одной "
                      "строки не откроет остальные."]
    rows.append([InlineKeyboardButton(text="🗑 Такой задачи нет — убрать" if job.icon not in ("✅", "🟠")
                                      else "🗑 Убрать", callback_data=f"hb_del:{name}")])
    rows.append([InlineKeyboardButton(text="← Боты", callback_data="blk:bots")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _url_line(name: str) -> str:
    return f"<code>{esc(public_urls.heartbeat_url(name))}</code>"


def _connect_kb(name: str) -> InlineKeyboardMarkup:
    """The connection screen's buttons — and, while the bot has no public
    address, the way to set one."""
    rows = [] if public_urls.is_public() else [[InlineKeyboardButton(text="🌍 Как задать адрес",
                                                                     callback_data="setup:url")]]
    return InlineKeyboardMarkup(inline_keyboard=[*rows, *_back(name).inline_keyboard])


def _not_public_note() -> str:
    return "" if public_urls.is_public() else f"{public_urls.NOT_PUBLIC}\n\n"


async def _job_gone(call: CallbackQuery, name: str) -> bool:
    """A button from an old message for a job that is no longer watched."""
    if name in settings.heartbeat_jobs():
        return False
    await ack(call, "Задачи больше нет", show_alert=True)
    return True


# A plain string, not an f-string: GitHub reads «${{ }}» itself.
GITHUB_STEP = ("<code>- run: curl -fsS \"$HEARTBEAT_URL\"\n"
               "  env:\n"
               "    HEARTBEAT_URL: ${{ secrets.HEARTBEAT_URL }}</code>")


def _back(name: str) -> InlineKeyboardMarkup:
    # Rotation is always one tap away, not only when the secrets happen to collide.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Адрес мог утечь — сменить", callback_data="setup:hb_regen")],
        [InlineKeyboardButton(text="← Назад", callback_data=f"hb_show:{name}")]])


@router.callback_query(F.data.startswith("hb_ask:"))
async def cb_hb_ask(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    if await _job_gone(call, name):
        return
    await ack(call)
    await render(call, f"⏰ {esc(name)}\n\nЧто такое «{esc(name)}» у тебя?",
                 InlineKeyboardMarkup(inline_keyboard=[*_kind_rows(name), *_back(name).inline_keyboard]))


@router.callback_query(F.data.startswith("hb_bot:"))
async def cb_hb_bot(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    if await _job_gone(call, name):
        return
    await ack(call)
    await render(call, f"🤖 Подключить «{esc(name)}»\n\n{_not_public_note()}"
                       f"Дай этому боту адрес — нажми на него, он скопируется:\n{_url_line(name)}\n\n"
                       f"Бот должен открывать его каждый раз, когда удачно поработал. В его настройках такое "
                       f"поле обычно называется <code>HEALTHCHECK_URL</code>, <code>HEARTBEAT_URL</code> "
                       f"или «Ping URL».\n\n"
                       f"{HEARTBEAT_SECRET_NOTE}\n\n"
                       f"Как только связь появится, строка на главном экране станет ✅.", _connect_kb(name))


@router.callback_query(F.data.startswith("hb_cron:"))
async def cb_hb_cron(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    if await _job_gone(call, name):
        return
    await ack(call)
    url = esc(public_urls.heartbeat_url(name))
    await render(call, f"🗓 Подключить «{esc(name)}»\n\n{_not_public_note()}"
                       f"В самом конце задача должна открыть адрес — нажми на него, он скопируется:\n"
                       f"{_url_line(name)}\n{HEARTBEAT_SECRET_NOTE}\n\n"
                       f"Где задача запускается:\n"
                       f"• <b>GitHub Actions</b> — адрес в файл workflow не вставляй: репозиторий читают другие. "
                       f"В репозитории: Settings → Secrets and variables → Actions → New repository secret, "
                       f"имя <code>HEARTBEAT_URL</code>, значение — адрес выше. Последний шаг workflow:\n"
                       f"{GITHUB_STEP}\n"
                       f"• <b>cron на своём сервере</b> — допиши в конец строки задачи:\n"
                       f"<code>&& curl -fsS {url}</code>\n"
                       f"• <b>Планировщик хостинга</b> (Railway Cron, Render…) — положи адрес в переменную "
                       f"окружения сервиса <code>HEARTBEAT_URL</code> и допиши в конец команды "
                       f"<code>&& curl -fsS \"$HEARTBEAT_URL\"</code>; в render.yaml и railway.json адрес не пиши.\n\n"
                       f"Задача упадёт раньше — адрес не откроется, и я напишу. Как только связь появится, "
                       f"строка на главном экране станет ✅.", _connect_kb(name))


@router.callback_query(F.data.startswith("hb_show:") | F.data.startswith("hb_how:"))
async def cb_hb_show(call: CallbackQuery):
    """hb_show: from the main screen, edits it in place. hb_how: from the
    morning digest or an alert — answers below and leaves that message as is."""
    kind, name = call.data.split(":", 1)
    screen = await job_screen(name) if name in settings.heartbeat_jobs() else None
    if screen is None:
        await ack(call, "Задачи больше нет", show_alert=True)
        return
    await ack(call)
    text, kb = screen
    if kind == "hb_how" and call.message is not None:
        await call.message.answer(text, reply_markup=kb)
    else:
        await render(call, text, kb)


@router.callback_query(F.data == "hb_add")
async def cb_hb_add(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddHeartbeatForm.name)
    await render(call, "⏰ Бот или задача по расписанию\n\n"
                       "Самая коварная поломка бэкапа, выгрузки или другого бота — тишина: он молча "
                       "перестаёт работать, и никто не замечает. Добавь его сюда — он будет выходить со мной "
                       "на связь после каждого запуска, а замолчит — я напишу.\n\n"
                       "Как назвать? Латиницей, коротко: например <code>socialbot</code> или "
                       "<code>site-backup</code>.", cancel_kb())


@router.message(AddHeartbeatForm.name)
async def msg_hb_name(message: Message, state: FSMContext):
    name = (message.text or "").strip().lower()
    if not settings.valid_job_name(name):
        await message.answer("Только латиница, цифры и дефис, до 20 символов. Попробуй ещё раз:",
                             reply_markup=cancel_kb())
        return
    if len(settings.ui_heartbeat_jobs()) >= settings.MAX_HB_JOBS:
        await state.clear()
        await message.answer(f"Больше {settings.MAX_HB_JOBS} задач добавить нельзя — удали ненужные.",
                             reply_markup=back_button())
        return
    jobs = settings.heartbeat_jobs()
    if name in jobs:
        # Re-adding would silently change the interval of a job that works.
        await state.clear()
        await message.answer(f"«{esc(name)}» уже есть — выходит на связь {every(jobs[name])}.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                 [InlineKeyboardButton(text="Открыть её", callback_data=f"hb_show:{name}")],
                                 *back_button().inline_keyboard]))
        return
    await state.update_data(hb_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in INTERVALS[:2]],
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in INTERVALS[2:]],
        [InlineKeyboardButton(text=CANCEL, callback_data="fsm_cancel")]])
    await message.answer(f"Как часто «{esc(name)}» будет выходить на связь?", reply_markup=kb)


@router.callback_query(F.data.startswith("hbint:"))
async def cb_hb_interval(call: CallbackQuery, state: FSMContext):
    again = "Начни заново: 🏠 Главная → «🤖 Боты» → «➕ Бот или задача»"
    if await state.get_state() != AddHeartbeatForm.name.state:
        # A button from an old message: whatever is being typed now is not ours to drop.
        await ack(call, again, show_alert=True)
        return
    name = (await state.get_data()).get("hb_name")
    await state.clear()
    arg = call.data.split(":", 1)[1]
    mins = int(arg) if arg.isdigit() else 0
    # The interval only from the list, and the outcome checked: «Добавил» must be true.
    if not name or mins not in {m for _, m in INTERVALS} or not await settings.add_heartbeat_job(name, mins):
        await ack(call, f"Не добавил. {again}", show_alert=True)
        return
    await ack(call, "Добавил ✅")
    await render(call, f"✅ «{esc(name)}» под присмотром и уже на главном экране.\n\n"
                       f"Осталось подключить. Что такое «{esc(name)}»?",
                 InlineKeyboardMarkup(inline_keyboard=[*_kind_rows(name), *back_button().inline_keyboard]))


@router.callback_query(F.data.startswith("hb_rm:"))
async def cb_hb_remove_ask(call: CallbackQuery):
    """«🗑 Удалить» under an alert or the digest: one tap only asks, below
    the message — the alert itself does not change (CR-515)."""
    name = call.data.split(":", 1)[1]
    if await _job_gone(call, name):
        return
    await ack(call)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, не ждать", callback_data=f"hb_rm_ok:{name}"),
         InlineKeyboardButton(text="Оставить", callback_data="noop")]])
    await call.message.answer(f"Перестать ждать «{esc(name)}»? Если она сломается, я не узнаю.", reply_markup=kb)


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    """«Оставить»: the question loses its buttons, nothing else happens."""
    await ack(call, "Ок, оставил")
    if call.message is not None and hasattr(call.message, "edit_reply_markup"):
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("hb_rm_ok:"))
async def cb_hb_remove_here(call: CallbackQuery):
    """«🗑 Да, не ждать»: remove the task; the question becomes the answer.
    On a «hb_rm:» button of a message from before the question (an old
    alert) — the task goes and its buttons go, the text stays as it was."""
    name = call.data.split(":", 1)[1]
    known = name in settings.heartbeat_jobs()
    if not await ack(call, f"Удалил «{name}» — больше не жду" if known else "Её уже нет"):
        await call.message.answer(STALE_TAP)    # pressed while the bot was down: nothing is deleted
        return
    removed = known and await settings.remove_heartbeat_job(name)
    if removed:
        await heartbeats.forget_status(name)
    if call.data.startswith("hb_rm_ok:"):
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_text(f"🗑 «{esc(name)}» больше не жду." if removed else "Её уже нет.")
        return
    markup = getattr(call.message, "reply_markup", None)
    if removed and markup is not None:
        rows = [row for row in markup.inline_keyboard
                if not any((b.callback_data or "").split(":", 1)[-1] == name for b in row)]
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.callback_query(F.data.startswith("hb_del:"))
async def cb_hb_del(call: CallbackQuery):
    """«🗑 Убрать» on the card: asks first, in place; «Оставить» is the card again."""
    name = call.data.split(":", 1)[1]
    if await _job_gone(call, name):
        return
    await ack(call)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, не ждать", callback_data=f"hb_del_ok:{name}"),
         InlineKeyboardButton(text="Оставить", callback_data=f"hb_show:{name}")]])
    await render(call, f"Перестать ждать «{esc(name)}»? Если она сломается, я не узнаю.", kb)


@router.callback_query(F.data.startswith("hb_del_ok:"))
async def cb_hb_del_ok(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    from_env = settings.heartbeat_job_from_env(name)
    if name not in settings.heartbeat_jobs():
        await ack(call, "Её уже нет", show_alert=True)
        return
    if not await ack(call, "Удалил"):
        await call.message.answer(STALE_TAP)
        return
    await settings.remove_heartbeat_job(name)
    await heartbeats.forget_status(name)
    note = ("\n\nОна была записана в переменной <code>HEARTBEAT_JOBS</code> на сервере бота. Там она "
            "может остаться — я её больше не жду." if from_env else "")
    await render(call, f"🗑 «{esc(name)}» больше не жду.{note}", back_button())
