// THE BATALLION story section (v2.5, hover flip in v2.6): card flips +
// scroll reveal.
//
// Flip (v2.6): CSS does the flipping — :hover flips the card and it
// flips back on mouse-out, :focus-within flips it for keyboard users.
// The click toggle below is the TOUCH fallback only (hover: none): a
// tap flips the card, a second tap flips it back. On hover devices the
// click handler does nothing, so it can never fight the CSS hover
// state and leave a card stuck flipped after mouse-out. Focus/blur
// keep aria-pressed in sync with the focus flip. The flip itself is
// CSS 3D (see style.css): transform-only, 0.6s, no layout shift.
//
// Reveal: the same entrance language as the hero (home.js). Nothing is
// hidden until this script adds .js-anim to the section, so with JS
// disabled or failed the story and cards render complete and readable.
// An IntersectionObserver reveals the section when it scrolls into view
// (immediately if it is already in view), staggered 120ms per item,
// 400ms fade + translateY — the hero's exact timing.
//
// prefers-reduced-motion: no scroll-reveal animation (content shown at
// once) and no flip animation (the CSS media query swaps instantly).

(function () {
  'use strict';

  var section = document.getElementById('battalion');
  if (!section) return;

  // --- card flips ---------------------------------------------------------
  // Touch fallback only (see the header comment): on hover devices the
  // CSS :hover rule owns the flip and a click toggle would fight it.
  var touchOnly = window.matchMedia('(hover: none)').matches;
  var cards = section.querySelectorAll('.flip-card-inner');
  cards.forEach(function (card) {
    card.addEventListener('click', function () {
      if (!touchOnly) return;
      var flipped = card.classList.toggle('flipped');
      card.setAttribute('aria-pressed', flipped ? 'true' : 'false');
    });
    // Keep aria-pressed in sync with the focus flip (CSS
    // :focus-within): a keyboard user "sees" the back while focused.
    card.addEventListener('focus', function () {
      card.setAttribute('aria-pressed', 'true');
    });
    card.addEventListener('blur', function () {
      card.setAttribute('aria-pressed', card.classList.contains('flipped') ? 'true' : 'false');
    });
  });

  // --- scroll reveal --------------------------------------------------------
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return; // final state renders as-is; no motion.

  section.classList.add('js-anim');
  var items = section.querySelectorAll('.battalion-reveal');

  function reveal() {
    items.forEach(function (el, i) {
      window.setTimeout(function () {
        el.classList.add('revealed');
      }, i * 120);
    });
  }

  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          reveal();
          io.disconnect();
        }
      });
    }, { threshold: 0.1 });
    io.observe(section);
  } else {
    reveal();
  }
})();
