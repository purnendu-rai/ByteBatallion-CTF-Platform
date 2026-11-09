// Custom hacker cursor (v2.6, site-wide): replaces the native OS cursor
// with a phosphor-green square outline + blinking center block and a
// smaller ghost dot trailing behind. States: default (small square),
// hovering a link/button/card (bigger square + ">" prompt glyph),
// text input/textarea (I-beam). Movement is transform-only via
// requestAnimationFrame.
//
// SAFETY:
// - The native cursor is hidden ONLY when this script adds
//   .custom-cursor to <html> (see style.css). If this file fails to
//   load, the native cursor stays — the user is never left without a
//   pointer.
// - The cursor elements have pointer-events: none and never block
//   clicks (the terminal still focuses on click, buttons still work).
// - Touch devices / coarse pointers: the whole thing is gated on
//   (hover: hover) and (pointer: fine) — no custom cursor on touch.
// - prefers-reduced-motion: no trailing lag, no blinking, no ghost —
//   the cursor follows the mouse directly, statically (CSS also hides
//   the ghost and kills the blink via .reduced-motion).
// - This is separate from the terminal's own block cursor
//   (.term-cursor), which is untouched.

(function () {
  'use strict';

  // Touch / coarse pointers: no custom cursor at all.
  if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var root = document.documentElement;
  root.classList.add('custom-cursor');
  if (reduced) root.classList.add('reduced-motion');

  // --- elements -------------------------------------------------------------
  var cursor = document.createElement('div');
  cursor.id = 'bb-cursor';
  cursor.setAttribute('aria-hidden', 'true');
  var box = document.createElement('span');
  box.className = 'bb-cursor-box';
  var core = document.createElement('span');
  core.className = 'bb-cursor-core';
  var gt = document.createElement('span');
  gt.className = 'bb-cursor-gt';
  gt.textContent = '>';
  box.appendChild(core);
  box.appendChild(gt);
  cursor.appendChild(box);
  document.body.appendChild(cursor);

  var ghost = null;
  if (!reduced) {
    ghost = document.createElement('div');
    ghost.id = 'bb-cursor-ghost';
    ghost.setAttribute('aria-hidden', 'true');
    document.body.appendChild(ghost);
  }

  // --- tracking ---------------------------------------------------------------
  var mouseX = window.innerWidth / 2;
  var mouseY = window.innerHeight / 2;
  var x = mouseX;
  var y = mouseY;
  var gx = mouseX;
  var gy = mouseY;
  var visible = false;

  function place(el, px, py) {
    el.style.transform = 'translate3d(' + px + 'px,' + py + 'px, 0)';
  }

  document.addEventListener('mousemove', function (e) {
    mouseX = e.clientX;
    mouseY = e.clientY;
    if (!visible) {
      visible = true;
      cursor.classList.remove('cursor-hidden');
      if (ghost) ghost.classList.remove('cursor-hidden');
      // Snap to the pointer on first appearance (no flying in).
      x = mouseX;
      y = mouseY;
      gx = mouseX;
      gy = mouseY;
    }
  });

  document.documentElement.addEventListener('mouseleave', function () {
    visible = false;
    cursor.classList.add('cursor-hidden');
    if (ghost) ghost.classList.add('cursor-hidden');
  });
  document.documentElement.addEventListener('mouseenter', function () {
    visible = true;
    cursor.classList.remove('cursor-hidden');
    if (ghost) ghost.classList.remove('cursor-hidden');
  });

  // --- states -------------------------------------------------------------------
  var TEXT_SELECTOR = 'input:not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]), textarea, [contenteditable="true"]';
  var CLICK_SELECTOR = 'a, button, input[type="submit"], input[type="button"], select, label, [role="button"], .flip-card-inner';

  document.addEventListener('mouseover', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    if (t.closest(TEXT_SELECTOR)) {
      cursor.classList.remove('cursor-hover');
      cursor.classList.add('cursor-text');
    } else if (t.closest(CLICK_SELECTOR)) {
      cursor.classList.remove('cursor-text');
      cursor.classList.add('cursor-hover');
    } else {
      cursor.classList.remove('cursor-hover', 'cursor-text');
    }
  });

  // --- animation loop -------------------------------------------------------------
  function frame() {
    if (reduced) {
      // No lag, no trail: the cursor sits exactly on the pointer.
      place(cursor, mouseX, mouseY);
    } else {
      // Main cursor: fast lerp (snappy, slight lag). Ghost: slow lerp
      // (visible trail behind).
      x += (mouseX - x) * 0.4;
      y += (mouseY - y) * 0.4;
      gx += (mouseX - gx) * 0.12;
      gy += (mouseY - gy) * 0.12;
      place(cursor, x, y);
      if (ghost) place(ghost, gx, gy);
    }
    window.requestAnimationFrame(frame);
  }
  window.requestAnimationFrame(frame);
})();
