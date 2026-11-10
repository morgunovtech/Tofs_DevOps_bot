/**
 * TofsDevOps — виджет «Сообщить о проблеме» / «Нашли опечатку?»
 *
 * Готовый сниппет — в боте: ⚙️ Настройки → 📩 Обратная связь. Он запрашивает
 * этот файл только после загрузки страницы и проверяет его хеш (integrity):
 * зависший бот не тормозит сайт, а подменённый файл браузер не исполнит.
 *
 * Строгий Content-Security-Policy или полная независимость от бота — положи
 * этот файл к себе на сайт и подключи без inline-скрипта (CSP: connect-src <адрес бота>):
 *
 *   <script defer src="/js/feedback-widget.js"
 *           data-webhook-url="https://YOUR_BOT/api/feedback"
 *           data-secret="YOUR_WEBHOOK_SECRET" data-button="false"></script>
 *
 * Настройки — window.DevOpsFeedback = {…} или data-атрибуты тега:
 *   webhookUrl, secret     обязательны;
 *   siteUrl                по умолчанию — origin страницы;
 *   button                 true | false | 'desktop' (плавающая кнопка только на широких экранах);
 *   position, offsetY      'bottom-right' | 'bottom-left' и отступ кнопки снизу, px;
 *   title, notice          заголовок формы и строка о том, что уходит вместе с сообщением;
 *   lang                   'ru' | 'en' — язык формы; без него — по <html lang> страницы
 *                          (пустой — русский, любой другой — английский);
 *   texts                  { button, title, label, placeholder, notice, cancel, send, … } —
 *                          свои слова вместо любых из словаря I18N ниже;
 *   fullUrl                true — отправлять адрес страницы целиком, с ?query и #fragment.
 * Любая ссылка с атрибутом data-devops-feedback открывает форму.
 * Программно: window.DevOpsFeedback.open('текст по умолчанию').
 *
 * NOTE: the "secret" is visible to every site visitor (view-source), so it is
 * a spam filter, not authentication. The server treats the endpoint as public
 * and enforces rate limits + size caps on its side.
 */

(function () {
  'use strict';

  const prev = window.DevOpsFeedback;
  if (prev && prev.__ready) return;                 // a second copy: nothing to do

  const ds = (document.currentScript && document.currentScript.dataset) || {};
  const fromTag = {};
  ['webhookUrl', 'secret', 'siteUrl', 'title', 'notice', 'position', 'lang'].forEach(function (k) {
    if (ds[k]) fromTag[k] = ds[k];
  });
  if (ds.button !== undefined) fromTag.button = ds.button === 'desktop' ? 'desktop' : ds.button !== 'false';
  if (ds.offsetY) fromTag.offsetY = Number(ds.offsetY);
  if (ds.fullUrl) fromTag.fullUrl = ds.fullUrl === 'true';
  const cfg = Object.assign(fromTag, prev || {});

  const WEBHOOK_URL = cfg.webhookUrl || '';
  const SECRET      = cfg.secret     || '';
  const SITE_URL    = cfg.siteUrl    || window.location.origin;
  const BUTTON      = cfg.button === undefined ? true : cfg.button;
  const TIMEOUT_MS  = 15000;

  // Visitors may not read the owner's language: texts follow <html lang>
  // of the page (ru when it is empty, en for any other language); any of
  // them can be replaced via cfg.texts. Every visible word lives here —
  // nothing in the markup — so a replaced word goes in as text, not HTML.
  const I18N = {
    ru: {
      button: '⚠️ Проблема?', title: 'Сообщить о проблеме', label: 'Что случилось?',
      placeholder: 'Что не работает, где именно, на каком устройстве…',
      notice: 'Вместе с сообщением владельцу сайта уйдут адрес этой страницы и ваш IP-адрес.',
      cancel: 'Отмена', send: 'Отправить', empty: 'Пожалуйста, опишите проблему.',
      sending: 'Отправляю…', sent: '✅ Спасибо! Сообщение отправлено.',
      failed: 'Не получилось отправить. Попробуйте позже.',
      tooMany: 'Слишком много сообщений — попробуйте через минуту.',
      typo: 'Опечатка: «{text}»',
    },
    en: {
      button: '⚠️ Problem?', title: 'Report a problem', label: 'What happened?',
      placeholder: 'What does not work, where exactly, on which device…',
      notice: 'The address of this page and your IP address go to the site owner with the message.',
      cancel: 'Cancel', send: 'Send', empty: 'Please describe the problem.',
      sending: 'Sending…', sent: '✅ Thank you! Your message has been sent.',
      failed: 'Could not send. Please try again later.',
      tooMany: 'Too many messages — please try again in a minute.',
      typo: 'Typo: “{text}”',
    },
  };
  const pageLang = String(cfg.lang || document.documentElement.lang || 'ru').slice(0, 2).toLowerCase();
  const T = Object.assign({}, I18N[pageLang] || I18N.en, cfg.texts || {});
  if (cfg.title) T.title = cfg.title;       // the older options keep working
  if (cfg.notice) T.notice = cfg.notice;

  if (!WEBHOOK_URL) {
    console.warn('[DevOpsFeedback] webhookUrl not configured — widget disabled.');
    return;
  }

  const css = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    #btn { position: fixed; bottom: 24px; right: 24px; z-index: 99999;
      background: #2563eb; color: #fff; border: none; border-radius: 999px;
      padding: 10px 18px; font: 400 14px/1.4 sans-serif; cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,.25); }
    #btn.left { right: auto; left: 24px; }
    #btn:hover { background: #1d4ed8; }
    @media (max-width: 599px) { #btn { bottom: 88px; } #btn.desktop { display: none; } }
    #overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 100000;
      display: flex; align-items: center; justify-content: center; }
    #overlay[hidden] { display: none; }
    #modal { background: #fff; color: #111; border-radius: 12px; padding: 28px 24px 20px;
      width: min(420px, calc(100vw - 32px)); box-shadow: 0 8px 32px rgba(0,0,0,.2);
      font: 400 14px/1.4 sans-serif; }
    h3 { margin: 0 0 14px; font-size: 17px; font-weight: 700; line-height: 1.3; color: #111; }
    label { display: block; margin: 0 0 6px; font-weight: 600; }
    textarea { display: block; width: 100%; border: 1px solid #d1d5db; border-radius: 8px;
      padding: 10px 12px; font: inherit; resize: vertical; min-height: 100px; color: #111; background: #fff; }
    textarea:focus { outline: 2px solid #2563eb; outline-offset: 1px; border-color: #2563eb; }
    #notice { margin: 8px 0 0; font-size: 12px; color: #4b5563; }
    #status { margin-top: 10px; font-size: 13px; min-height: 18px; text-align: center; }
    #status.ok    { color: #15803d; }
    #status.error { color: #dc2626; }
    #actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
    #cancel, #submit { min-height: 44px; border: none; border-radius: 8px; padding: 8px 16px;
      font: inherit; cursor: pointer; }
    #cancel { background: #f3f4f6; color: #374151; }
    #cancel:hover { background: #e5e7eb; }
    #submit { background: #2563eb; color: #fff; }
    #submit:hover:not(:disabled) { background: #1d4ed8; }
    #submit:disabled { opacity: .55; cursor: default; }
    button:focus-visible { outline: 2px solid #1d4ed8; outline-offset: 2px; }
  `;
  // Static markup only, without a word in it: every text (the dictionary's
  // or the owner's) goes in as text below, never as HTML.
  const markup = `
    <button id="btn" type="button" hidden></button>
    <div id="overlay" hidden>
      <div id="modal" role="dialog" aria-modal="true" aria-labelledby="title">
        <h3 id="title"></h3>
        <label id="label" for="text"></label>
        <textarea id="text" maxlength="2000"></textarea>
        <p id="notice"></p>
        <div id="status" aria-live="polite"></div>
        <div id="actions">
          <button id="cancel" type="button"></button>
          <button id="submit" type="button"></button>
        </div>
      </div>
    </div>`;

  function init() {
    if (window.DevOpsFeedback && window.DevOpsFeedback.__ready) return;   // two tags in <head>
    const host = document.createElement('div');
    host.id = 'devops-fb';
    const root = host.attachShadow({ mode: 'open' });
    if ('adoptedStyleSheets' in root && typeof CSSStyleSheet.prototype.replaceSync === 'function') {
      const sheet = new CSSStyleSheet();          // a strict style-src does not block it
      sheet.replaceSync(css);
      root.adoptedStyleSheets = [sheet];
    } else {
      const style = document.createElement('style');
      style.textContent = css;
      root.appendChild(style);
    }
    const tpl = document.createElement('template');
    tpl.innerHTML = markup;
    root.appendChild(tpl.content);
    document.body.appendChild(host);

    const $ = function (id) { return root.getElementById(id); };
    const btn = $('btn'), overlay = $('overlay'), textarea = $('text');
    const cancel = $('cancel'), submit = $('submit'), status = $('status');
    btn.textContent = T.button;                 // no aria-label: the visible text is the name
    $('title').textContent = '📩 ' + T.title;
    $('label').textContent = T.label;
    textarea.setAttribute('placeholder', T.placeholder);
    $('notice').textContent = T.notice;
    cancel.textContent = T.cancel;
    submit.textContent = T.send;
    if (BUTTON) {
      btn.hidden = false;
      if (BUTTON === 'desktop') btn.classList.add('desktop');
      if (cfg.position === 'bottom-left') btn.classList.add('left');
      if (typeof cfg.offsetY === 'number' && cfg.offsetY >= 0) btn.style.bottom = cfg.offsetY + 'px';
    }

    let lastFocus = null;
    function setStatus(text, cls) { status.textContent = text; status.className = cls; }
    function selectedText() {
      const sel = window.getSelection ? String(window.getSelection()).trim() : '';
      return sel.length > 1 && sel.length <= 500 ? sel : '';
    }
    function openModal(prefill) {
      lastFocus = root.activeElement || document.activeElement;
      const text = typeof prefill === 'string' ? prefill : '';
      const sel = selectedText();
      if (text) textarea.value = text;
      // a function, so that «$&» in the selected text is not a replacement pattern
      else if (sel) textarea.value = T.typo.replace('{text}', function () { return sel; }) + '\n';
      overlay.hidden = false;
      textarea.focus();
      textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
    }
    function closeModal(keepText) {
      overlay.hidden = true;
      if (!keepText) textarea.value = '';
      setStatus('', '');
      submit.disabled = false;
      if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
    }
    function pageUrl() {
      return cfg.fullUrl === true ? location.href : location.origin + location.pathname;
    }
    async function send() {
      const msg = textarea.value.trim();
      if (!msg) { setStatus(T.empty, 'error'); return; }
      submit.disabled = true;
      setStatus(T.sending, '');
      const body = { site_url: SITE_URL, page_url: pageUrl(), message: msg };
      if (cfg.fullUrl === true) body.full_url = true;
      const ctrl = new AbortController();
      const timer = setTimeout(function () { ctrl.abort(); }, TIMEOUT_MS);
      try {
        const res = await fetch(WEBHOOK_URL, {
          method: 'POST', signal: ctrl.signal,
          headers: { 'Content-Type': 'application/json', 'X-Webhook-Secret': SECRET },
          body: JSON.stringify(body),
        });
        if (res.ok) {
          setStatus(T.sent, 'ok');
          setTimeout(function () { closeModal(false); }, 1800);
          return;
        }
        if (res.status === 403) console.warn('[DevOpsFeedback] 403: the widget secret is stale — take a new snippet from the bot.');
        else console.warn('[DevOpsFeedback] HTTP ' + res.status);
        setStatus(res.status === 429 ? T.tooMany : T.failed, 'error');
      } catch (err) {
        console.warn('[DevOpsFeedback]', err && err.name === 'AbortError' ? 'no answer in 15 s' : err);
        setStatus(T.failed, 'error');
      } finally {
        clearTimeout(timer);
      }
      submit.disabled = false;
    }

    btn.addEventListener('click', function () { openModal(); });
    cancel.addEventListener('click', function () { closeModal(false); });
    submit.addEventListener('click', send);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(true); });
    overlay.addEventListener('keydown', function (e) {          // focus stays inside the dialog
      if (e.key !== 'Tab') return;
      const f = [textarea, cancel, submit].filter(function (el) { return !el.disabled; });
      const i = f.indexOf(root.activeElement);
      if (e.shiftKey && i <= 0) { f[f.length - 1].focus(); e.preventDefault(); }
      else if (!e.shiftKey && i === f.length - 1) { f[0].focus(); e.preventDefault(); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !overlay.hidden) closeModal(true);
    });
    document.addEventListener('click', function (e) {
      const trigger = e.target.closest && e.target.closest('[data-devops-feedback]');
      if (!trigger) return;
      e.preventDefault();
      openModal(trigger.getAttribute('data-devops-feedback') || '');
    });

    window.DevOpsFeedback = Object.assign(prev || {}, cfg,
      { open: openModal, close: function () { closeModal(false); }, __ready: true });
  }

  if (document.body) init();
  else document.addEventListener('DOMContentLoaded', init);
})();
