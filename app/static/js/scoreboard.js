// Interactive live score-progression graph for ByteBatallion (CTFd-style).
//
// Fetches /scoreboard/top10.json every 10s and draws a rich multi-line
// SVG chart with interactive crosshair, tooltips, range zoom, and legend filtering.
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var POLL_MS = 10000;
  var PAD = { top: 28, right: 36, bottom: 42, left: 60 };

  // Terminal palette: greens, greys, amber, red only — no rainbow.
  var COLORS = [
    "#00ff41", // Phosphor green
    "#22c55e", // Green
    "#16a34a", // Dark green
    "#86efac", // Light green
    "#a3a3a3", // Grey
    "#d4d4d4", // Light grey
    "#737373", // Dim grey
    "#ffb000", // Amber
    "#ff3131", // Red
    "#14532d"  // Dim green
  ];

  var currentData = null;
  var hiddenSeries = {}; // username -> boolean
  var hoveredSeries = null;
  var zoomLevel = 1.0;
  var zoomOffset = 0.0; // 0..1 fraction

  function svgEl(name, attrs) {
    var el = document.createElementNS(SVG_NS, name);
    for (var k in attrs) {
      if (attrs.hasOwnProperty(k)) el.setAttribute(k, attrs[k]);
    }
    return el;
  }

  function formatTime(ms) {
    var d = new Date(ms);
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    var dd = d.getDate();
    var mon = d.toLocaleString(undefined, { month: "short" });
    return mon + " " + dd + " " + hh + ":" + mm;
  }

  function formatShortTime(ms) {
    var d = new Date(ms);
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    return hh + ":" + mm;
  }

  // Escape user-controlled text before it goes into innerHTML (the
  // tooltip below). Player names are locked to [A-Za-z0-9_] at
  // registration, but TEAM names are free text (1-64 chars) and flow
  // into the same `username` field of /scoreboard/top10.json — a team
  // named `<img src=x onerror=...>` would otherwise get stored XSS on
  // every viewer who hovers the graph. `color` is an internal constant
  // from COLORS and needs no escaping; times/scores are numbers.
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function render(root, data) {
    currentData = data;
    while (root.firstChild) root.removeChild(root.firstChild);

    var tooltip = document.getElementById("score-graph-tooltip");
    var W = root.clientWidth || root.parentElement.clientWidth || 800;
    var H = 340;
    root.setAttribute("viewBox", "0 0 " + W + " " + H);
    root.setAttribute("width", "100%");
    root.setAttribute("height", H);

    var plotW = Math.max(100, W - PAD.left - PAD.right);
    var plotH = Math.max(100, H - PAD.top - PAD.bottom);

    var series = data.series || [];
    if (series.length === 0) {
      var msg = svgEl("text", {
        x: W / 2, y: H / 2, "text-anchor": "middle",
        fill: "#737373", "font-size": "14", "font-family": "monospace"
      });
      msg.textContent = "No scores yet — the graph updates live as solves land.";
      root.appendChild(msg);
      return;
    }

    // Determine global bounds
    var fullMin = Infinity, fullMax = data.now || Date.now();
    var pMax = 0;
    series.forEach(function (s) {
      (s.points || []).forEach(function (pt) {
        if (pt[0] < fullMin) fullMin = pt[0];
        if (pt[0] > fullMax) fullMax = pt[0];
        if (pt[1] > pMax) pMax = pt[1];
      });
      if (s.current_points > pMax) pMax = s.current_points;
    });

    if (!isFinite(fullMin) || fullMin >= fullMax) {
      fullMin = fullMax - 3600000;
    }
    if (pMax <= 0) pMax = 100;
    pMax = Math.ceil(pMax * 1.15); // headroom

    // Apply zoom
    var timeSpan = (fullMax - fullMin) / zoomLevel;
    var tMin = fullMin + (fullMax - fullMin - timeSpan) * zoomOffset;
    var tMax = tMin + timeSpan;

    function x(t) {
      return PAD.left + ((t - tMin) / (tMax - tMin)) * plotW;
    }
    function y(p) {
      return PAD.top + plotH - (p / pMax) * plotH;
    }

    // SVG Defs: clip path only (no shadows)
    var defs = svgEl("defs", {});
    var clip = svgEl("clipPath", { id: "plot-clip" });
    clip.appendChild(svgEl("rect", {
      x: PAD.left, y: PAD.top - 10, width: plotW, height: plotH + 20
    }));
    defs.appendChild(clip);
    root.appendChild(defs);

    // Horizontal grid & y-axis labels
    var ySteps = 5;
    for (var i = 0; i <= ySteps; i++) {
      var val = Math.round((pMax / ySteps) * i);
      var gy = y(val);
      root.appendChild(svgEl("line", {
        x1: PAD.left, y1: gy, x2: PAD.left + plotW, y2: gy,
        stroke: "#1e1e1e", "stroke-width": 1,
        "stroke-dasharray": i === 0 ? "none" : "3,3"
      }));
      var yLbl = svgEl("text", {
        x: PAD.left - 10, y: gy + 4, "text-anchor": "end",
        fill: "#737373", "font-size": "11", "font-family": "monospace"
      });
      yLbl.textContent = val;
      root.appendChild(yLbl);
    }

    // Vertical time grid & x-axis labels
    var xSteps = Math.min(6, Math.max(3, Math.floor(plotW / 120)));
    for (var j = 0; j <= xSteps; j++) {
      var tx = tMin + (tMax - tMin) * (j / xSteps);
      var gx = x(tx);
      root.appendChild(svgEl("line", {
        x1: gx, y1: PAD.top, x2: gx, y2: PAD.top + plotH,
        stroke: "#1e1e1e", "stroke-width": 1
      }));
      var xLbl = svgEl("text", {
        x: gx, y: PAD.top + plotH + 22, "text-anchor": "middle",
        fill: "#737373", "font-size": "10", "font-family": "monospace"
      });
      xLbl.textContent = formatShortTime(tx);
      root.appendChild(xLbl);
    }

    // Chart group with clip path
    var chartGroup = svgEl("g", { "clip-path": "url(#plot-clip)" });
    root.appendChild(chartGroup);

    // Step-lines and solve nodes per series
    var solveDots = [];

    series.forEach(function (s, idx) {
      if (hiddenSeries[s.username]) return;

      var pts = s.points || [];
      if (pts.length === 0) return;

      var color = COLORS[idx % COLORS.length];
      var isHovered = hoveredSeries === s.username;
      var isOtherHovered = hoveredSeries && !isHovered;
      var opacity = isOtherHovered ? 0.2 : (isHovered ? 1.0 : 0.85);
      var strokeWidth = isHovered ? 3.5 : 2.2;

      // Draw CTFd step line (horizontal then jump)
      var d = "M " + x(pts[0][0]) + " " + y(0);
      var lastVal = 0;
      pts.forEach(function (pt) {
        d += " L " + x(pt[0]) + " " + y(lastVal);
        d += " L " + x(pt[0]) + " " + y(pt[1]);
        lastVal = pt[1];
      });
      // Extend to latest time
      d += " L " + x(tMax) + " " + y(lastVal);

      var pathEl = svgEl("path", {
        d: d,
        fill: "none",
        stroke: color,
        "stroke-width": strokeWidth,
        "stroke-linejoin": "round",
        opacity: opacity,
        class: "graph-series-line"
      });
      chartGroup.appendChild(pathEl);

      // Solve milestone circles
      pts.forEach(function (pt, ptIdx) {
        if (ptIdx === 0) return; // skip initial zero baseline
        var cx = x(pt[0]);
        var cy = y(pt[1]);

        var circle = svgEl("circle", {
          cx: cx,
          cy: cy,
          r: isHovered ? "5.5" : "4",
          fill: color,
          stroke: "#050505",
          "stroke-width": "2",
          opacity: opacity,
          class: "solve-node"
        });

        circle.addEventListener("mouseenter", function () {
          circle.setAttribute("r", "7");
          circle.setAttribute("stroke", "#d4d4d4");
          if (tooltip) {
            tooltip.innerHTML =
              '<div class="tt-header" style="color: ' + color + '">' +
              '<span class="tt-dot" style="background-color: ' + color + '"></span>' +
              '<strong>' + escapeHtml(s.username) + '</strong></div>' +
              '<div class="tt-time">' + formatTime(pt[0]) + '</div>' +
              '<div class="tt-score">Score: <strong>' + pt[1] + '</strong> pts</div>';
            tooltip.style.display = "block";
            var rect = root.getBoundingClientRect();
            tooltip.style.left = (cx + rect.left + window.scrollX - tooltip.offsetWidth / 2) + "px";
            tooltip.style.top = (cy + rect.top + window.scrollY - 54) + "px";
          }
        });

        circle.addEventListener("mouseleave", function () {
          circle.setAttribute("r", isHovered ? "5.5" : "4");
          circle.setAttribute("stroke", "#050505");
          if (tooltip) tooltip.style.display = "none";
        });

        chartGroup.appendChild(circle);
      });
    });
  }

  function buildLegend(legendEl, data) {
    if (!legendEl) return;
    legendEl.innerHTML = "";
    (data.series || []).forEach(function (s, idx) {
      var color = COLORS[idx % COLORS.length];
      var isHidden = hiddenSeries[s.username];

      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "graph-legend-item" + (isHidden ? " series-hidden" : "");
      chip.setAttribute("data-username", s.username);

      var dot = document.createElement("span");
      dot.className = "graph-legend-dot";
      dot.style.backgroundColor = color;
      if (!isHidden) dot.style.boxShadow = "0 0 8px " + color;

      var name = document.createElement("span");
      name.className = "legend-label";
      name.textContent = s.username + " (" + s.current_points + " pts)";

      chip.appendChild(dot);
      chip.appendChild(name);

      // Toggle series visibility on click
      chip.addEventListener("click", function () {
        hiddenSeries[s.username] = !hiddenSeries[s.username];
        chip.classList.toggle("series-hidden", hiddenSeries[s.username]);
        var root = document.getElementById("score-graph");
        if (root && currentData) render(root, currentData);
      });

      // Highlight series on hover
      chip.addEventListener("mouseenter", function () {
        hoveredSeries = s.username;
        var root = document.getElementById("score-graph");
        if (root && currentData) render(root, currentData);
      });

      chip.addEventListener("mouseleave", function () {
        hoveredSeries = null;
        var root = document.getElementById("score-graph");
        if (root && currentData) render(root, currentData);
      });

      legendEl.appendChild(chip);
    });
  }

  function setupTools(root, data) {
    var btnZoomIn = document.getElementById("graph-zoom-in");
    var btnZoomOut = document.getElementById("graph-zoom-out");
    var btnReset = document.getElementById("graph-reset");
    var btnDownload = document.getElementById("graph-download");

    if (btnZoomIn) {
      btnZoomIn.onclick = function () {
        zoomLevel = Math.min(8.0, zoomLevel * 1.4);
        if (currentData) render(root, currentData);
      };
    }
    if (btnZoomOut) {
      btnZoomOut.onclick = function () {
        zoomLevel = Math.max(1.0, zoomLevel / 1.4);
        if (zoomLevel === 1.0) zoomOffset = 0;
        if (currentData) render(root, currentData);
      };
    }
    if (btnReset) {
      btnReset.onclick = function () {
        zoomLevel = 1.0;
        zoomOffset = 0;
        hiddenSeries = {};
        var legendEl = document.getElementById("score-graph-legend");
        if (legendEl && currentData) buildLegend(legendEl, currentData);
        if (currentData) render(root, currentData);
      };
    }
    if (btnDownload) {
      btnDownload.onclick = function () {
        var serializer = new XMLSerializer();
        var source = serializer.serializeToString(root);
        var blob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "scoreboard_graph.svg";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      };
    }
  }

  function refresh(root, legendEl, url) {
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.ok ? r.json() : { now: Date.now(), series: [] };
      })
      .then(function (data) {
        render(root, data);
        buildLegend(legendEl, data);
        setupTools(root, data);
      })
      .catch(function (err) {
        console.error("Score graph refresh error:", err);
      });
  }

  window.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("score-graph");
    var legendEl = document.getElementById("score-graph-legend");
    if (!root) return;

    var url = root.getAttribute("data-src") || "/scoreboard/top10.json";
    refresh(root, legendEl, url);

    // Auto-refresh cadence every 10 seconds
    setInterval(function () {
      refresh(root, legendEl, url);
    }, POLL_MS);

    window.addEventListener("resize", function () {
      if (currentData) render(root, currentData);
    });
  });
})();
