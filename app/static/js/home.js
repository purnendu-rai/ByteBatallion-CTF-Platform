// Home-page motion (v2.2): boot overlay + hero typing + staggered reveal.
//
// Order: boot overlay (once per session, ~2s max, click/key skips it) ->
// hero name typed letter by letter with a blinking underscore cursor ->
// taglines/terminal fade in staggered. Everything is transform/opacity
// only (never width/height). prefers-reduced-motion: no motion at all —
// the full hero text renders immediately.
//
// Safety: the overlay starts with the `hidden` attribute and the reveal
// elements only hide once this script adds the `js-anim` class, so if
// this file fails to load the page still renders complete and readable.

(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return; // final state renders as-is; no boot, no typing.

  var root = document.documentElement;
  root.classList.add('js-anim');

  var hero = document.querySelector('.hero');
  var title = document.getElementById('hero-title');
  var overlay = document.getElementById('boot-overlay');
  if (!hero || !title) return;

  var fullName = title.getAttribute('aria-label') || title.textContent;
  var started = false; // typing must start exactly once.

  // Pause looping CSS animations (terminal float, blinking cursors) while
  // the tab is hidden, per the ui-ux-pro-max motion rules.
  document.addEventListener('visibilitychange', function () {
    document.body.classList.toggle('page-hidden', document.hidden);
  });

  // --- staggered entrance: taglines, notice, terminal -------------------
  function revealAll() {
    var items = hero.querySelectorAll('.hero-reveal');
    items.forEach(function (el, i) {
      window.setTimeout(function () {
        el.classList.add('revealed');
      }, i * 120);
    });
  }

  // --- hero typing: one letter at a time, human-ish jitter ---------------
  function startTyping() {
    if (started) return;
    started = true;
    var text = fullName;
    title.textContent = '';
    var cursor = document.createElement('span');
    cursor.className = 'hero-cursor';
    cursor.setAttribute('aria-hidden', 'true');
    cursor.textContent = '_';
    title.appendChild(cursor);
    var i = 0;
    (function typeNext() {
      if (i < text.length) {
        var ch = document.createTextNode(text.charAt(i));
        title.insertBefore(ch, cursor);
        i += 1;
        // ~65ms per char with 40-90ms jitter: typed, not robotic.
        window.setTimeout(typeNext, 40 + Math.random() * 50);
      } else {
        // Typing done — the underscore keeps blinking indefinitely.
        revealAll();
      }
    })();
  }

  // --- boot overlay --------------------------------------------------------
  var BOOT_LINES = [
    '[ OK ] mounting /dev/bytebatallion',
    '[ OK ] decrypting flag vault ... DONE',
    '[ OK ] establishing secure channel ... DONE',
    '[ OK ] loading challenges ... DONE',
    '[ !! ] tracing intruders ... 0 found'
  ];
  var SEEN_KEY = 'bb_boot_seen';
  var bootTimers = [];

  function clearBootTimers() {
    bootTimers.forEach(function (t) { window.clearTimeout(t); });
    bootTimers = [];
  }

  function finishBoot() {
    clearBootTimers();
    if (!overlay) { startTyping(); return; }
    try { window.sessionStorage.setItem(SEEN_KEY, '1'); } catch (e) { /* private mode */ }
    overlay.classList.add('boot-fading');
    window.setTimeout(function () {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      startTyping();
    }, 400);
  }

  function runBoot() {
    if (!overlay) { startTyping(); return; }
    var linesEl = document.getElementById('boot-lines');
    var progressEl = document.getElementById('boot-progress');
    var grantedEl = document.getElementById('boot-granted');
    overlay.hidden = false;

    // Skip on any click or keypress.
    var skip = function () { finishBoot(); };
    overlay.addEventListener('click', skip);
    document.addEventListener('keydown', skip, { once: true });

    // Print the boot lines one by one, ~150ms apart.
    BOOT_LINES.forEach(function (line, i) {
      bootTimers.push(window.setTimeout(function () {
        var div = document.createElement('div');
        div.className = 'boot-line';
        div.textContent = line;
        linesEl.appendChild(div);
      }, 150 * (i + 1)));
    });

    // Progress bar fills over ~500ms once the lines are out.
    var barStart = 150 * BOOT_LINES.length + 100;
    var steps = 10;
    for (var s = 0; s <= steps; s += 1) {
      (function (step) {
        bootTimers.push(window.setTimeout(function () {
          var pct = step * 10;
          var filled = '#'.repeat(step);
          var empty = '-'.repeat(steps - step);
          progressEl.textContent = '[' + filled + empty + '] ' + pct + '%';
        }, barStart + step * 50));
      })(s);
    }

    // Access granted, then fade the overlay out.
    bootTimers.push(window.setTimeout(function () {
      grantedEl.textContent = '> ACCESS GRANTED — welcome, hacker';
    }, barStart + 550));
    bootTimers.push(window.setTimeout(finishBoot, barStart + 850));
  }

  var seen = false;
  try { seen = !!window.sessionStorage.getItem(SEEN_KEY); } catch (e) { /* private mode */ }
  if (seen) {
    startTyping();
  } else {
    runBoot();
  }
})();
