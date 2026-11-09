// Tiny countdown ticker for the pre-start board/home notices (Phase 5).
(function () {
  "use strict";

  function tick() {
    var nodes = document.querySelectorAll("[data-countdown-to]");
    var reloading = false;
    nodes.forEach(function (el) {
      var target = new Date(el.getAttribute("data-countdown-to"));
      var diff = target - new Date();
      if (isNaN(target.getTime())) return;
      if (diff <= 0) {
        if (!reloading) { reloading = true; location.reload(); }
        return;
      }
      var d = Math.floor(diff / 86400000);
      var h = Math.floor((diff % 86400000) / 3600000);
      var m = Math.floor((diff % 3600000) / 60000);
      var s = Math.floor((diff % 60000) / 1000);
      el.textContent = (d ? d + "d " : "") + h + "h " + m + "m " + s + "s";
    });
    setTimeout(tick, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tick);
  } else {
    tick();
  }
})();
