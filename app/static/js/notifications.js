// Notifications: unread red dot + toast + beep (v2).
//
// No DB migration: the browser keeps the highest announcement id it has
// seen in localStorage (`bb_seen_notif`). Visiting /notifications marks
// everything seen. Toasts are once-per-session per announcement id
// (sessionStorage `bb_toasted_<id>`).
//
// Audio: browsers block autoplay sound, so the beep is attempted on
// load and — if the AudioContext is still suspended — re-attempted on
// the first pointer/key interaction (one-time listener).

(function () {
  var dot = document.getElementById('notif-dot');
  if (!dot) return; // anonymous users have no nav dot — nothing to do.

  var SEEN_KEY = 'bb_seen_notif';
  var POLL_MS = 60000;
  var TOAST_MS = 8000;
  var lastLatestId = 0;
  var pendingBeep = false;

  function getSeen() {
    try {
      return parseInt(localStorage.getItem(SEEN_KEY) || '0', 10) || 0;
    } catch (e) {
      return 0;
    }
  }

  function setSeen(id) {
    try {
      localStorage.setItem(SEEN_KEY, String(id));
    } catch (e) { /* private mode — dot just stays as-is */ }
  }

  function showDot(count) {
    dot.hidden = false;
    dot.style.display = ''; // clear the hideDot fallback below
    dot.textContent = count > 0 ? String(count) : '';
  }

  function hideDot() {
    dot.hidden = true;
    // v2.3: belt and braces — `.notif-dot { display: inline-block }`
    // used to beat the `hidden` attribute, so the dot stayed red
    // forever even after the notifications were read. The CSS guard
    // `.notif-dot[hidden]` now covers it; this inline style is the
    // second layer in case the stylesheet is stale/cached.
    dot.style.display = 'none';
    dot.textContent = '';
  }

  // v2.3: if this IS the notifications page, the user is reading them
  // right now — hide the dot immediately, before the fetch below even
  // returns, instead of flashing a stale dot on page load.
  var onNotificationsPage = window.location.pathname === '/notifications';
  if (onNotificationsPage) {
    hideDot();
  }
  // Tapping the nav link hides the dot at once too (the page load then
  // marks everything seen properly via check()).
  var navLink = dot.closest('a');
  if (navLink) {
    navLink.addEventListener('click', hideDot);
  }

  function beep() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      var ctx = window._bbAudioCtx || (window._bbAudioCtx = new AC());
      if (ctx.state === 'suspended') {
        // Autoplay blocked — resume on first interaction instead.
        pendingBeep = true;
        ctx.resume().catch(function () {});
        return;
      }
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = 'square';
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.15);
      pendingBeep = false;
    } catch (e) { /* audio unavailable — silent toast only */ }
  }

  function armInteractionBeep() {
    var onFirst = function () {
      if (pendingBeep) beep();
      window.removeEventListener('pointerdown', onFirst);
      window.removeEventListener('keydown', onFirst);
    };
    window.addEventListener('pointerdown', onFirst);
    window.addEventListener('keydown', onFirst);
  }

  function toast(latest) {
    if (!latest) return;
    var flag = 'bb_toasted_' + latest.id;
    try {
      if (sessionStorage.getItem(flag)) return;
      sessionStorage.setItem(flag, '1');
    } catch (e) { /* sessionStorage unavailable — toast anyway */ }

    var box = document.createElement('div');
    box.className = 'notif-toast';
    var title = document.createElement('strong');
    title.textContent = latest.title;
    var body = document.createElement('div');
    body.textContent = latest.body;
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'notif-toast-close';
    close.textContent = '[x]';
    close.setAttribute('aria-label', 'Dismiss notification');
    close.addEventListener('click', function () { box.remove(); });
    box.appendChild(close);
    box.appendChild(title);
    box.appendChild(body);
    document.body.appendChild(box);
    // Shown at least 5s; auto-hides after 8s.
    setTimeout(function () { box.remove(); }, TOAST_MS);
    beep();
  }

  function check() {
    fetch('/notifications/unread.json?seen=' + getSeen())
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        var isNotificationsPage = window.location.pathname === '/notifications';
        if (isNotificationsPage && data.latest_id) {
          setSeen(data.latest_id);
          hideDot();
          lastLatestId = data.latest_id;
          return;
        }
        if (data.unread > 0) {
          showDot(data.unread);
          if (data.latest && data.latest.id !== lastLatestId) {
            lastLatestId = data.latest.id;
            toast(data.latest);
          }
        } else {
          hideDot();
          if (data.latest_id) lastLatestId = data.latest_id;
        }
      })
      .catch(function () { /* endpoint unreachable — leave the dot alone */ });
  }

  armInteractionBeep();
  check();
  setInterval(check, POLL_MS);
})();
