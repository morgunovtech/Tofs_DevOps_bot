/* TofsDevOps — how fast the page is for real visitors.
 * Sends three numbers per page view: how soon the page appears (LCP), how
 * soon it answers a tap (INP), how much the layout jumps (CLS), plus «phone
 * or not». No cookies, no storage, no address of the page, nothing about
 * the visitor. Put into <head>:
 *   <script src="https://BOT/rum.js" data-site="SIGNATURE.ID" defer></script>
 */
(function () {
  var me = document.currentScript;
  if (!me || !window.PerformanceObserver || !window.JSON) return;
  var site = (me.getAttribute('data-site') || '').split('.');
  if (site.length !== 2) return;
  var endpoint = me.src.replace(/\/rum\.js(\?.*)?$/, '') + '/rum/' + site[0] + '/' + site[1];
  var m = { lcp: null, inp: null, cls: 0, ttfb: null };

  function watch(type, onEntry, extra) {
    try {
      var opts = { type: type, buffered: true };
      for (var k in extra || {}) opts[k] = extra[k];
      new PerformanceObserver(function (list) { list.getEntries().forEach(onEntry); }).observe(opts);
    } catch (e) { /* an old browser: that number stays empty */ }
  }

  watch('largest-contentful-paint', function (e) { m.lcp = Math.round(e.renderTime || e.loadTime || e.startTime); });

  // Layout shifts are summed in windows (≤1 s apart, ≤5 s long); the worst window counts.
  var win = 0, worst = 0, first = 0, last = 0;
  watch('layout-shift', function (e) {
    if (e.hadRecentInput) return;
    if (win && e.startTime - last < 1000 && e.startTime - first < 5000) win += e.value;
    else { win = e.value; first = e.startTime; }
    last = e.startTime;
    if (win > worst) worst = win;
    m.cls = Math.round(worst * 1000) / 1000;
  });

  // The slowest tap or key press; the first one is always reported, however fast.
  function tap(e) { var d = Math.round(e.duration); if (m.inp === null || d > m.inp) m.inp = d; }
  watch('first-input', tap);
  watch('event', function (e) { if (e.interactionId) tap(e); }, { durationThreshold: 16 });

  try {
    var nav = performance.getEntriesByType('navigation')[0];
    if (nav && nav.responseStart > 0) m.ttfb = Math.round(nav.responseStart);
  } catch (e) { /* no navigation timing */ }

  var sent = false;
  function send() {
    if (sent || (m.lcp === null && m.ttfb === null)) return;
    sent = true;
    var body = JSON.stringify({ lcp: m.lcp, inp: m.inp, cls: m.cls, ttfb: m.ttfb,
                                mobile: /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ? 1 : 0 });
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(endpoint, body)) return;
      fetch(endpoint, { method: 'POST', body: body, keepalive: true, mode: 'no-cors' });
    } catch (e) { /* never break the page */ }
  }
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') send(); });
  window.addEventListener('pagehide', send);
})();
