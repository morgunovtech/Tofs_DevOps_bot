/**
 * DevOps Bot — виджет «Сообщить о проблеме»
 *
 * Подключение на сайте (вставить перед </body>):
 *
 *   <script>
 *     window.DevOpsFeedback = {
 *       webhookUrl: 'https://YOUR_SERVER:8080/api/feedback',
 *       secret:     'YOUR_WEBHOOK_SECRET',
 *       siteUrl:    'https://yoursite.example.com',
 *     };
 *   </script>
 *   <script src="https://YOUR_SERVER:8080/feedback-widget.js"></script>
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

  if (!WEBHOOK_URL) {
    console.warn('[DevOpsFeedback] webhookUrl not configured — widget disabled.');
    return;
  }

  /* ── Styles ─────────────────────────────────────────────────────────── */
  const css = `
    #devops-fb-btn {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 99999;
      background: #2563eb;
      color: #fff;
      border: none;
      border-radius: 999px;
      padding: 10px 18px;
      font-size: 14px;
      font-family: sans-serif;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,.25);
      transition: background .2s;
    }
    #devops-fb-btn:hover { background: #1d4ed8; }

    #devops-fb-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.45);
      z-index: 100000;
      align-items: center;
      justify-content: center;
    }
    #devops-fb-overlay.open { display: flex; }

    #devops-fb-modal {
      background: #fff;
      border-radius: 12px;
      padding: 28px 24px 20px;
      width: min(420px, calc(100vw - 32px));
      box-shadow: 0 8px 32px rgba(0,0,0,.2);
      font-family: sans-serif;
    }
    #devops-fb-modal h3 {
      margin: 0 0 14px;
      font-size: 17px;
      color: #111;
    }
    #devops-fb-modal textarea {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 14px;
      resize: vertical;
      min-height: 100px;
      outline: none;
      font-family: sans-serif;
    }
    #devops-fb-modal textarea:focus { border-color: #2563eb; }
    #devops-fb-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 14px;
    }
    #devops-fb-cancel {
      background: #f3f4f6;
      color: #374151;
      border: none;
      border-radius: 8px;
      padding: 8px 16px;
      font-size: 14px;
      cursor: pointer;
    }
    #devops-fb-cancel:hover { background: #e5e7eb; }
    #devops-fb-submit {
      background: #2563eb;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 8px 16px;
      font-size: 14px;
      cursor: pointer;
    }
    #devops-fb-submit:hover:not(:disabled) { background: #1d4ed8; }
    #devops-fb-submit:disabled { opacity: .55; cursor: default; }
    #devops-fb-status {
      margin-top: 10px;
      font-size: 13px;
      min-height: 18px;
      text-align: center;
    }
    #devops-fb-status.ok    { color: #16a34a; }
    #devops-fb-status.error { color: #dc2626; }
  `;

  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  /* ── HTML ────────────────────────────────────────────────────────────── */
  const html = `
    <button id="devops-fb-btn" aria-label="Сообщить о проблеме">⚠️ Проблема?</button>
    <div id="devops-fb-overlay" role="dialog" aria-modal="true">
      <div id="devops-fb-modal">
        <h3>📩 Сообщить о проблеме</h3>
        <textarea id="devops-fb-text" placeholder="Опишите проблему — что не работает, на каком устройстве, браузере…" maxlength="2000"></textarea>
        <div id="devops-fb-status"></div>
        <div id="devops-fb-actions">
          <button id="devops-fb-cancel">Отмена</button>
          <button id="devops-fb-submit">Отправить</button>
        </div>
      </div>
    </div>
  `;

  const wrapper = document.createElement('div');
  wrapper.innerHTML = html;
  document.body.appendChild(wrapper);

  /* ── Logic ───────────────────────────────────────────────────────────── */
  const btn      = document.getElementById('devops-fb-btn');
  const overlay  = document.getElementById('devops-fb-overlay');
  const textarea = document.getElementById('devops-fb-text');
  const cancel   = document.getElementById('devops-fb-cancel');
  const submit   = document.getElementById('devops-fb-submit');
  const status   = document.getElementById('devops-fb-status');

  function openModal() {
    overlay.classList.add('open');
    textarea.focus();
  }
  function closeModal() {
    overlay.classList.remove('open');
    textarea.value = '';
    status.textContent = '';
    status.className = '';
    submit.disabled = false;
  }

  btn.addEventListener('click', openModal);
  cancel.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeModal();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeModal();
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
        headers: {
          'Content-Type': 'application/json',
          'X-Webhook-Secret': SECRET,
        },
        body: JSON.stringify({
          site_url: SITE_URL,
          page_url: window.location.href,
          message:  msg,
        }),
      });

      if (res.ok) {
        status.textContent = '✅ Спасибо! Сообщение отправлено.';
        status.className = 'ok';
        setTimeout(closeModal, 1800);
      } else if (res.status === 429) {
        throw new Error('слишком много сообщений, попробуйте через минуту');
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (err) {
      status.textContent = `Ошибка отправки: ${err.message}`;
      status.className = 'error';
      submit.disabled = false;
    }
  });
})();
