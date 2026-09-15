/**
 * TofsDevOps — виджет «Сообщить о проблеме» / «Нашли опечатку?»
 *
 * Подключение на сайте (перед </body>):
 *
 *   <script>
 *     window.DevOpsFeedback = {
 *       webhookUrl: 'https://YOUR_BOT/api/feedback',
 *       secret:     'YOUR_WEBHOOK_SECRET',
 *       siteUrl:    'https://yoursite.example.com',   // optional
 *       button:     true,      // false — без плавающей кнопки, только по ссылкам
 *       title:      'Сообщить о проблеме',             // optional
 *     };
 *   </script>
 *   <script src="https://YOUR_BOT/feedback-widget.js"></script>
 *
 * Любая ссылка с атрибутом data-devops-feedback открывает форму — так
 * «нашли опечатку?» в футере превращается в репорт в Telegram. Если
 * пользователь выделил текст на странице, он подставится в форму.
 * Программно: window.DevOpsFeedback.open('текст по умолчанию').
 *
 * NOTE: the "secret" is visible to every site visitor (view-source), so it is
 * a spam filter, not authentication. The server treats the endpoint as public
 * and enforces rate limits + size caps on its side.
 */

(function () {
  'use strict';

  const cfg = window.DevOpsFeedback || {};
  const WEBHOOK_URL = cfg.webhookUrl || '';
  const SECRET      = cfg.secret     || '';
  const SITE_URL    = cfg.siteUrl    || window.location.origin;
  const SHOW_BUTTON = cfg.button !== false;
  const TITLE       = cfg.title || 'Сообщить о проблеме';

  if (!WEBHOOK_URL) {
    console.warn('[DevOpsFeedback] webhookUrl not configured — widget disabled.');
    return;
  }

  const css = `
    #devops-fb-btn { position: fixed; bottom: 24px; right: 24px; z-index: 99999;
      background: #2563eb; color: #fff; border: none; border-radius: 999px;
      padding: 10px 18px; font-size: 14px; font-family: sans-serif; cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,.25); transition: background .2s; }
    #devops-fb-btn:hover { background: #1d4ed8; }
    #devops-fb-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45);
      z-index: 100000; align-items: center; justify-content: center; }
    #devops-fb-overlay.open { display: flex; }
    #devops-fb-modal { background: #fff; color: #111; border-radius: 12px; padding: 28px 24px 20px;
      width: min(420px, calc(100vw - 32px)); box-shadow: 0 8px 32px rgba(0,0,0,.2);
      font-family: sans-serif; }
    #devops-fb-modal h3 { margin: 0 0 14px; font-size: 17px; color: #111; }
    #devops-fb-modal textarea { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db;
      border-radius: 8px; padding: 10px 12px; font-size: 14px; resize: vertical; min-height: 100px;
      outline: none; font-family: sans-serif; color: #111; background: #fff; }
    #devops-fb-modal textarea:focus { border-color: #2563eb; }
    #devops-fb-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
    #devops-fb-cancel { background: #f3f4f6; color: #374151; border: none; border-radius: 8px;
      padding: 8px 16px; font-size: 14px; cursor: pointer; }
    #devops-fb-cancel:hover { background: #e5e7eb; }
    #devops-fb-submit { background: #2563eb; color: #fff; border: none; border-radius: 8px;
      padding: 8px 16px; font-size: 14px; cursor: pointer; }
    #devops-fb-submit:hover:not(:disabled) { background: #1d4ed8; }
    #devops-fb-submit:disabled { opacity: .55; cursor: default; }
    #devops-fb-status { margin-top: 10px; font-size: 13px; min-height: 18px; text-align: center; }
    #devops-fb-status.ok    { color: #16a34a; }
    #devops-fb-status.error { color: #dc2626; }
  `;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const wrapper = document.createElement('div');
  wrapper.innerHTML = `
    ${SHOW_BUTTON ? '<button id="devops-fb-btn" type="button" aria-label="' + TITLE + '">⚠️ Проблема?</button>' : ''}
    <div id="devops-fb-overlay" role="dialog" aria-modal="true" aria-labelledby="devops-fb-title">
      <div id="devops-fb-modal">
        <h3 id="devops-fb-title">📩 ${TITLE}</h3>
        <textarea id="devops-fb-text" placeholder="Опишите проблему — что не работает, где именно, на каком устройстве…" maxlength="2000"></textarea>
        <div id="devops-fb-status" aria-live="polite"></div>
        <div id="devops-fb-actions">
          <button id="devops-fb-cancel" type="button">Отмена</button>
          <button id="devops-fb-submit" type="button">Отправить</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrapper);

  const btn      = document.getElementById('devops-fb-btn');
  const overlay  = document.getElementById('devops-fb-overlay');
  const textarea = document.getElementById('devops-fb-text');
  const cancel   = document.getElementById('devops-fb-cancel');
  const submit   = document.getElementById('devops-fb-submit');
  const status   = document.getElementById('devops-fb-status');

  function selectedText() {
    const sel = window.getSelection ? String(window.getSelection()).trim() : '';
    return sel.length > 1 && sel.length <= 500 ? sel : '';
  }

  function openModal(prefill) {
    const text = typeof prefill === 'string' ? prefill : '';
    const sel = selectedText();
    if (text) textarea.value = text;
    else if (sel) textarea.value = 'Опечатка: «' + sel + '»\n';
    overlay.classList.add('open');
    textarea.focus();
    textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
  }
  function closeModal() {
    overlay.classList.remove('open');
    textarea.value = '';
    status.textContent = '';
    status.className = '';
    submit.disabled = false;
  }

  if (btn) btn.addEventListener('click', function () { openModal(); });
  cancel.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });
  document.addEventListener('click', function (e) {
    const trigger = e.target.closest && e.target.closest('[data-devops-feedback]');
    if (!trigger) return;
    e.preventDefault();
    openModal(trigger.getAttribute('data-devops-feedback') || '');
  });

  submit.addEventListener('click', async function () {
    const msg = textarea.value.trim();
    if (!msg) {
      status.textContent = 'Пожалуйста, опишите проблему.';
      status.className = 'error';
      return;
    }
    submit.disabled = true;
    status.textContent = 'Отправляю…';
    status.className = '';
    try {
      const res = await fetch(WEBHOOK_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Webhook-Secret': SECRET },
        body: JSON.stringify({ site_url: SITE_URL, page_url: window.location.href, message: msg }),
      });
      if (res.ok) {
        status.textContent = '✅ Спасибо! Сообщение отправлено.';
        status.className = 'ok';
        setTimeout(closeModal, 1800);
      } else if (res.status === 429) {
        throw new Error('слишком много сообщений, попробуйте через минуту');
      } else {
        throw new Error('HTTP ' + res.status);
      }
    } catch (err) {
      status.textContent = 'Ошибка отправки: ' + err.message;
      status.className = 'error';
      submit.disabled = false;
    }
  });

  window.DevOpsFeedback = Object.assign(cfg, { open: openModal, close: closeModal });
})();
