// Interactive charts for the CTF User Profile page
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs) {
    var el = document.createElementNS(SVG_NS, name);
    for (var k in attrs) {
      if (attrs.hasOwnProperty(k)) el.setAttribute(k, attrs[k]);
    }
    return el;
  }

  function formatTime(ms) {
    var d = new Date(ms);
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  // --- 1. Donut Chart Hover Interactivity ---
  function initDonutInteractivity() {
    // Attempts Donut
    var attemptsDonut = document.getElementById("attempts-donut");
    if (attemptsDonut) {
      var container = attemptsDonut.closest(".donut-chart-container");
      var centerMetric = container.querySelector(".center-metric");
      var centerSub = container.querySelector(".center-sub");
      var origMetric = centerMetric ? centerMetric.textContent.trim() : "";
      var origSub = centerSub ? centerSub.textContent.trim() : "";

      var segs = attemptsDonut.querySelectorAll(".donut-segment");
      segs.forEach(function (seg) {
        seg.addEventListener("mouseenter", function () {
          var label = seg.getAttribute("data-label");
          var count = seg.getAttribute("data-count");
          var pct = seg.getAttribute("data-pct");
          if (centerMetric) centerMetric.textContent = pct;
          if (centerSub) centerSub.textContent = label + " (" + count + ")";
          seg.style.strokeWidth = "24";
        });
        seg.addEventListener("mouseleave", function () {
          if (centerMetric) centerMetric.textContent = origMetric;
          if (centerSub) centerSub.textContent = origSub;
          seg.style.strokeWidth = "";
        });
      });
    }

    // Category Donut
    var catDonut = document.getElementById("category-donut");
    if (catDonut) {
      var catContainer = catDonut.closest(".donut-chart-container");
      var catCenterMetric = catContainer.querySelector(".center-metric");
      var catCenterSub = catContainer.querySelector(".center-sub");
      var catOrigMetric = catCenterMetric ? catCenterMetric.textContent.trim() : "";
      var catOrigSub = catCenterSub ? catCenterSub.textContent.trim() : "";

      var catSegs = catDonut.querySelectorAll(".cat-segment");
      catSegs.forEach(function (seg) {
        seg.addEventListener("mouseenter", function () {
          var cat = seg.getAttribute("data-cat");
          var count = seg.getAttribute("data-count");
          var pct = seg.getAttribute("data-pct");
          if (catCenterMetric) catCenterMetric.textContent = count;
          if (catCenterSub) catCenterSub.textContent = cat.toUpperCase() + " (" + pct + ")";
          seg.style.strokeWidth = "24";
        });
        seg.addEventListener("mouseleave", function () {
          if (catCenterMetric) catCenterMetric.textContent = catOrigMetric;
          if (catCenterSub) catCenterSub.textContent = catOrigSub;
          seg.style.strokeWidth = "";
        });
      });

      // Legend items hover
      var legendItems = catContainer.querySelectorAll(".category-legend .legend-item");
      legendItems.forEach(function (item) {
        var catName = item.getAttribute("data-cat");
        var matchingSeg = catDonut.querySelector('.cat-segment[data-cat="' + catName + '"]');
        if (matchingSeg) {
          item.addEventListener("mouseenter", function () {
            matchingSeg.style.strokeWidth = "24";
            matchingSeg.style.filter = "drop-shadow(0 0 8px currentColor)";
          });
          item.addEventListener("mouseleave", function () {
            matchingSeg.style.strokeWidth = "";
            matchingSeg.style.filter = "";
          });
        }
      });
    }
  }

  // --- 2. Score Over Time Area Line Chart ---
  function renderScoreChart() {
    var svg = document.getElementById("profile-score-chart");
    if (!svg) return;
    var tooltip = document.getElementById("profile-chart-tooltip");

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    // Chart data comes from the JSON data block in profile.html
    // (#score-history-data) — a data block, not an executable inline
    // script, so the Content-Security-Policy stays strict. The old
    // window.USER_SCORE_HISTORY global is kept as a fallback only.
    var data = [];
    var dataEl = document.getElementById("score-history-data");
    if (dataEl) {
      try { data = JSON.parse(dataEl.textContent || "[]"); } catch (e) { data = []; }
    }
    if (!data.length && window.USER_SCORE_HISTORY) data = window.USER_SCORE_HISTORY;
    var W = svg.clientWidth || 800;
    var H = 240;
    var PAD = { top: 24, right: 32, bottom: 36, left: 56 };
    var plotW = W - PAD.left - PAD.right;
    var plotH = H - PAD.top - PAD.bottom;

    svg.setAttribute("viewBox", "0 0 " + W + " " + H);


    if (data.length <= 1) {
      var emptyText = svgEl("text", {
        x: W / 2,
        y: H / 2,
        "text-anchor": "middle",
        fill: "#737373",
        "font-size": "13",
        "font-family": "monospace"
      });
      emptyText.textContent = "No solve timeline recorded yet. Solve challenges to track progression.";
      svg.appendChild(emptyText);
      return;
    }

    var tMin = data[0].time;
    var tMax = data[data.length - 1].time;
    if (tMin === tMax) tMin = tMax - 3600000;
    var pMax = 0;
    data.forEach(function (d) {
      if (d.score > pMax) pMax = d.score;
    });
    if (pMax <= 0) pMax = 100;
    pMax = Math.ceil(pMax * 1.15); // headroom

    function getX(t) { return PAD.left + ((t - tMin) / (tMax - tMin)) * plotW; }
    function getY(p) { return PAD.top + plotH - (p / pMax) * plotH; }

    // Horizontal grid & y-axis labels
    var yTicks = 4;
    for (var i = 0; i <= yTicks; i++) {
      var val = Math.round((pMax / yTicks) * i);
      var gy = getY(val);
      svg.appendChild(svgEl("line", {
        x1: PAD.left, y1: gy, x2: PAD.left + plotW, y2: gy,
        stroke: "#1e1e1e", "stroke-width": "1",
        "stroke-dasharray": i === 0 ? "none" : "3,3"
      }));
      var yLbl = svgEl("text", {
        x: PAD.left - 10, y: gy + 4, "text-anchor": "end",
        fill: "#737373", "font-size": "11", "font-family": "monospace"
      });
      yLbl.textContent = val;
      svg.appendChild(yLbl);
    }

    // Time ticks on x-axis
    var xTicks = Math.min(5, data.length);
    for (var j = 0; j <= xTicks; j++) {
      var tx = tMin + (tMax - tMin) * (j / xTicks);
      var gx = getX(tx);
      svg.appendChild(svgEl("line", {
        x1: gx, y1: PAD.top, x2: gx, y2: PAD.top + plotH,
        stroke: "#1e1e1e", "stroke-width": "1"
      }));
      var xLbl = svgEl("text", {
        x: gx, y: PAD.top + plotH + 18, "text-anchor": "middle",
        fill: "#737373", "font-size": "10", "font-family": "monospace"
      });
      xLbl.textContent = formatTime(tx);
      svg.appendChild(xLbl);
    }

    // Step line + Area polygon
    var pathD = "M " + getX(data[0].time) + " " + getY(data[0].score);
    var areaD = "M " + getX(data[0].time) + " " + (PAD.top + plotH);
    areaD += " L " + getX(data[0].time) + " " + getY(data[0].score);

    var prevScore = data[0].score;
    for (var k = 1; k < data.length; k++) {
      var pt = data[k];
      var px = getX(pt.time);
      var py = getY(pt.score);
      // Step function: horizontal then jump
      pathD += " L " + px + " " + getY(prevScore) + " L " + px + " " + py;
      areaD += " L " + px + " " + getY(prevScore) + " L " + px + " " + py;
      prevScore = pt.score;
    }
    areaD += " L " + getX(data[data.length - 1].time) + " " + (PAD.top + plotH) + " Z";

    // Append Area Fill — flat green, no gradient
    svg.appendChild(svgEl("path", {
      d: areaD,
      fill: "#00ff41",
      "fill-opacity": "0.08"
    }));

    // Append Line — flat green, no glow
    svg.appendChild(svgEl("path", {
      d: pathD,
      fill: "none",
      stroke: "#00ff41",
      "stroke-width": "2.5",
      "stroke-linejoin": "round"
    }));

    // Circle milestone dots on solve points
    data.forEach(function (pt, idx) {
      if (idx === 0) return; // skip initial baseline zero
      var cx = getX(pt.time);
      var cy = getY(pt.score);

      var dot = svgEl("circle", {
        cx: cx,
        cy: cy,
        r: "4.5",
        fill: "#00ff41",
        stroke: "#050505",
        "stroke-width": "2",
        class: "profile-chart-dot"
      });

      dot.addEventListener("mouseenter", function (e) {
        dot.setAttribute("r", "7");
        dot.setAttribute("stroke", "#d4d4d4");
        if (tooltip) {
          tooltip.innerHTML = '<div class="tt-time">' + formatTime(pt.time) + '</div>' +
                              '<div class="tt-score"><strong>' + pt.score + '</strong> pts</div>';
          tooltip.style.display = "block";
          var rect = svg.getBoundingClientRect();
          tooltip.style.left = (cx + rect.left + window.scrollX - tooltip.offsetWidth / 2) + "px";
          tooltip.style.top = (cy + rect.top + window.scrollY - 44) + "px";
        }
      });

      dot.addEventListener("mouseleave", function () {
        dot.setAttribute("r", "4.5");
        dot.setAttribute("stroke", "#050505");
        if (tooltip) tooltip.style.display = "none";
      });

      svg.appendChild(dot);
    });
  }

  window.addEventListener("DOMContentLoaded", function () {
    initDonutInteractivity();
    renderScoreChart();
    window.addEventListener("resize", renderScoreChart);
  });
})();
