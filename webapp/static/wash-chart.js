(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const CHART_WIDTH = 1200;
  const CHART_HEIGHT = 860;
  const CHART_LEFT = 116;
  const CHART_RIGHT = 28;
  const CHART_TOP = 118;
  const CHART_BOTTOM = 64;
  const PANEL_GAP = 18;
  const X_TICK_COUNT = 6;
  const Y_TICK_TARGET = 5;
  const MODAL_CHART_WIDTH = 1920;
  const MODAL_CHART_HEIGHT = 700;
  const MODAL_CHART_LEFT = 82;
  const MODAL_CHART_RIGHT = 18;
  const MODAL_CHART_TOP = 82;
  const MODAL_CHART_BOTTOM = 64;
  const MODAL_PANEL_GAP = 16;
  const SERIES_STYLE_STORAGE_KEY = "washChartSeriesStylesV1";
  const LINE_STYLE_OPTIONS = [
    { id: "solid", label: "Сплошная", dasharray: "" },
    { id: "dashed", label: "Штриховая", dasharray: "12 8" },
    { id: "dashdot", label: "Штрих-пунктир", dasharray: "14 7 3 7" },
    { id: "dotted", label: "Точечная", dasharray: "2 7" },
    { id: "longdash", label: "Длинный штрих", dasharray: "18 10" },
  ];

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function isValidHexColor(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || ""));
  }

  function getLineStyleOption(id) {
    return LINE_STYLE_OPTIONS.find((option) => option.id === id) || LINE_STYLE_OPTIONS[0];
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function loadStoredSeriesStyles() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(SERIES_STYLE_STORAGE_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  function saveStoredSeriesStyles(styles) {
    try {
      window.localStorage.setItem(SERIES_STYLE_STORAGE_KEY, JSON.stringify(styles));
    } catch (_error) {
      // Ignore storage failures and keep in-memory behavior.
    }
  }

  function buildSeriesStyleState(seriesList) {
    const stored = loadStoredSeriesStyles();
    return Object.fromEntries(
      seriesList.map((series) => {
        const saved = stored[series.id] || {};
        return [
          series.id,
          {
            color: isValidHexColor(saved.color) ? saved.color : series.color,
            lineStyle: getLineStyleOption(saved.lineStyle).id,
          },
        ];
      })
    );
  }

  function applySeriesStyleState(payload, styleState) {
    return {
      ...payload,
      series: payload.series.map((series) => {
        const style = styleState[series.id] || {};
        const lineStyle = getLineStyleOption(style.lineStyle);
        return {
          ...series,
          color: isValidHexColor(style.color) ? style.color : series.color,
          lineStyle: lineStyle.id,
          dasharray: lineStyle.dasharray,
        };
      }),
    };
  }

  function createSvgNode(name, attributes) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => {
      node.setAttribute(key, String(value));
    });
    return node;
  }

  function appendSvgText(parent, attributes, content) {
    const textNode = createSvgNode("text", attributes);
    textNode.textContent = content;
    parent.append(textNode);
    return textNode;
  }

  function appendSvgMultilineText(parent, attributes, content, lineHeight = 13) {
    const textNode = createSvgNode("text", attributes);
    const lines = String(content).split("\n");
    lines.forEach((line, index) => {
      const tspan = createSvgNode("tspan", {
        x: attributes.x,
        dy: index === 0 ? 0 : lineHeight,
      });
      tspan.textContent = line;
      textNode.append(tspan);
    });
    parent.append(textNode);
    return textNode;
  }

  function formatValue(value, unit) {
    if (!Number.isFinite(value)) {
      return "—";
    }
    return `${value.toFixed(2)} ${unit}`.trim();
  }

  function formatTime(timestamp) {
    return new Date(timestamp).toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function formatTimeAxisLabel(timestamp, domainStart, domainEnd) {
    const dateFormatter = new Intl.DateTimeFormat("ru-RU");
    const timeFormatter = new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });

    const datePart =
      dateFormatter.format(domainStart) === dateFormatter.format(domainEnd)
        ? ""
        : new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" }).format(timestamp);

    return datePart ? `${datePart}\n${timeFormatter.format(timestamp)}` : timeFormatter.format(timestamp);
  }

  function getTickPrecision(step) {
    if (!Number.isFinite(step) || step === 0) {
      return 0;
    }

    let precision = 0;
    let scaled = Math.abs(step);
    while (precision < 4 && Math.abs(Math.round(scaled) - scaled) > 1e-8) {
      scaled *= 10;
      precision += 1;
    }
    return precision;
  }

  function formatAxisValue(value, step = 1) {
    if (!Number.isFinite(value)) {
      return "—";
    }
    return value.toFixed(getTickPrecision(step));
  }

  function getNiceStep(rawStep) {
    if (!Number.isFinite(rawStep) || rawStep <= 0) {
      return 1;
    }

    const exponent = Math.floor(Math.log10(rawStep));
    const base = 10 ** exponent;
    const fraction = rawStep / base;

    if (fraction <= 1) {
      return base;
    }
    if (fraction <= 2) {
      return 2 * base;
    }
    if (fraction <= 5) {
      return 5 * base;
    }
    return 10 * base;
  }

  function padRange(min, max) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return [0, 1];
    }

    if (min === max) {
      const delta = Math.max(Math.abs(min) * 0.1, 1);
      return [min - delta, max + delta];
    }

    const span = max - min;
    const padding = span * 0.08;
    return [min - padding, max + padding];
  }

  function buildAxisTicks(min, max, targetCount = Y_TICK_TARGET) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return [0, 0.25, 0.5, 0.75, 1];
    }

    if (min === max) {
      return [min];
    }

    const step = getNiceStep((max - min) / Math.max(targetCount - 1, 1));
    const start = Math.floor(min / step) * step;
    const end = Math.ceil(max / step) * step;
    const ticks = [];

    for (let value = start; value <= end + step * 0.5; value += step) {
      ticks.push(Number(value.toFixed(8)));
    }

    return ticks;
  }

  function buildTimeTicks(start, end, count = X_TICK_COUNT) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || count <= 1) {
      return [start];
    }

    const span = Math.max(end - start, 1000);
    return Array.from({ length: count }, (_, index) => start + (span * index) / (count - 1));
  }

  function buildPanelHeights(panelCount, chartHeight, chartTop, chartBottom, panelGap) {
    const availableHeight = chartHeight - chartTop - chartBottom - panelGap * Math.max(panelCount - 1, 0);
    if (panelCount <= 1) {
      return [availableHeight];
    }

    const primaryHeight = Math.round(availableHeight * 0.5);
    const secondaryHeight = Math.floor((availableHeight - primaryHeight) / (panelCount - 1));
    const heights = [primaryHeight];

    for (let index = 1; index < panelCount; index += 1) {
      heights.push(secondaryHeight);
    }

    let allocated = heights.reduce((sum, value) => sum + value, 0);
    let cursor = 0;
    while (allocated < availableHeight) {
      heights[cursor] += 1;
      allocated += 1;
      cursor = (cursor + 1) % heights.length;
    }

    return heights;
  }

  function getChartLayout(container) {
    // Печатный макет: пропорция 1920×920 ≈ 2.09 подобрана под форму печатной
    // области (без шапки, компактная сводка). Чуть шире области, чтобы при
    // вписывании график занимал всю ширину листа и не обрезался по высоте.
    const isPrint = container.classList.contains("chart-host--print");
    if (isPrint) {
      return {
        width: 1920,
        height: 920,
        left: MODAL_CHART_LEFT,
        right: MODAL_CHART_RIGHT,
        top: MODAL_CHART_TOP,
        bottom: MODAL_CHART_BOTTOM,
        gap: MODAL_PANEL_GAP,
        labelLaneTop: 20,
        labelLaneGap: 38,
        axisTickFontSize: 14,
        axisLabelFontSize: 15,
      };
    }

    const isModal = container.classList.contains("chart-host--modal");
    if (isModal) {
      return {
        width: MODAL_CHART_WIDTH,
        height: MODAL_CHART_HEIGHT,
        left: MODAL_CHART_LEFT,
        right: MODAL_CHART_RIGHT,
        top: MODAL_CHART_TOP,
        bottom: MODAL_CHART_BOTTOM,
        gap: MODAL_PANEL_GAP,
        labelLaneTop: 20,
        labelLaneGap: 38,
        axisTickFontSize: 14,
        axisLabelFontSize: 15,
      };
    }

    return {
      width: CHART_WIDTH,
      height: CHART_HEIGHT,
      left: CHART_LEFT,
      right: CHART_RIGHT,
      top: CHART_TOP,
      bottom: CHART_BOTTOM,
      gap: PANEL_GAP,
      labelLaneTop: 24,
      labelLaneGap: 42,
      axisTickFontSize: 11,
      axisLabelFontSize: 12,
    };
  }

  function describePanels(payload) {
    return payload.panels
      .map((panel, panelIndex) => {
        const series = payload.series.filter((item) => item.panel === panelIndex && item.points?.length);
        if (!series.length) {
          return null;
        }

        const values = series.flatMap((item) => item.points.map((point) => point[1])).filter(Number.isFinite);
        if (!values.length) {
          return null;
        }

        const [rangeMin, rangeMax] = padRange(Math.min(...values), Math.max(...values));
        const ticks = buildAxisTicks(rangeMin, rangeMax);

        return {
          ...panel,
          panelIndex,
          series,
          min: ticks[0],
          max: ticks[ticks.length - 1],
          ticks,
          tickStep: ticks.length > 1 ? ticks[1] - ticks[0] : 1,
        };
      })
      .filter(Boolean);
  }

  function normalizeSegmentLabel(label) {
    const compact = String(label).replace(/\s+/g, " ").trim();
    if (compact.length <= 24) {
      return compact;
    }

    const words = compact.split(" ");
    if (words.length < 3) {
      return compact;
    }

    let bestSplit = 1;
    let bestDelta = Infinity;
    for (let index = 1; index < words.length; index += 1) {
      const left = words.slice(0, index).join(" ");
      const right = words.slice(index).join(" ");
      const delta = Math.abs(left.length - right.length);
      if (delta < bestDelta) {
        bestDelta = delta;
        bestSplit = index;
      }
    }

    return `${words.slice(0, bestSplit).join(" ")}\n${words.slice(bestSplit).join(" ")}`;
  }

  function assignSegmentLanes(segments, scaleX, plotWidth, chartLayout) {
    const laneEnds = [];
    const preferredLaneCount = Math.max(3, Math.min(6, Math.ceil(segments.length / 3)));
    for (let lane = 0; lane < preferredLaneCount; lane += 1) {
      laneEnds.push(-Infinity);
    }

    const isModal = chartLayout.width >= MODAL_CHART_WIDTH;
    const laneSpacing = isModal ? 20 : 16;
    const labelLineHeight = 11;

    return segments.map((segment, index) => {
      const startX = scaleX(segment.start);
      const endX = scaleX(segment.end);
      const midpoint = startX + (endX - startX) / 2;
      const displayLabel = normalizeSegmentLabel(segment.label);
      const lines = displayLabel.split("\n");
      const longestLineLength = lines.reduce((lineMax, line) => Math.max(lineMax, line.length), 1);
      const labelWidth = clamp(longestLineLength * 6.4 + 26, isModal ? 150 : 132, plotWidth * 0.24);
      const labelHeight = lines.length * labelLineHeight + (isModal ? 18 : 16);
      const labelX = clamp(
        midpoint,
        chartLayout.left + labelWidth / 2,
        chartLayout.width - chartLayout.right - labelWidth / 2
      );
      const labelLeft = labelX - labelWidth / 2;
      const labelRight = labelX + labelWidth / 2;

      let bestLane = 0;
      let bestPenalty = Infinity;
      for (let lane = 0; lane < laneEnds.length; lane += 1) {
        const penalty = Math.max(0, laneEnds[lane] - labelLeft);
        if (penalty < bestPenalty) {
          bestPenalty = penalty;
          bestLane = lane;
        }
      }

      const labelY = chartLayout.labelLaneTop + bestLane * chartLayout.labelLaneGap;
      laneEnds[bestLane] = labelRight + laneSpacing;
      return {
        ...segment,
        lane: bestLane,
        startX,
        endX,
        midpoint,
        labelX,
        labelY,
        displayLabel,
        labelWidth,
        labelHeight,
      };
    });
  }

  function resolvePlotTop(chartLayout, labeledSegments) {
    if (!labeledSegments.length) {
      return chartLayout.top;
    }

    const maxLabelBottom = labeledSegments.reduce(
      (maxBottom, segment) => Math.max(maxBottom, segment.labelY + segment.labelHeight - 10),
      0
    );
    return Math.max(chartLayout.top, maxLabelBottom + 14);
  }

  function findNearestPointIndex(points, targetTimestamp) {
    if (!points.length) {
      return -1;
    }

    let left = 0;
    let right = points.length - 1;
    while (left < right) {
      const middle = Math.floor((left + right) / 2);
      if (points[middle][0] < targetTimestamp) {
        left = middle + 1;
      } else {
        right = middle;
      }
    }

    if (left === 0) {
      return 0;
    }

    const previous = left - 1;
    return Math.abs(points[left][0] - targetTimestamp) < Math.abs(points[previous][0] - targetTimestamp)
      ? left
      : previous;
  }

  function renderSeriesBadges(svg, panelLayouts, scaleX, scaleY, chartLayout) {
    panelLayouts.forEach((panelLayout) => {
      const badges = panelLayout.panel.series
        .map((series) => {
          const point = series.points.at(-1);
          if (!point) {
            return null;
          }

          return {
            series,
            value: point[1],
            x: scaleX(point[0]),
            y: scaleY(point[1], panelLayout.top, panelLayout.height, panelLayout.panel),
          };
        })
        .filter(Boolean)
        .sort((left, right) => left.y - right.y);

      for (let index = 1; index < badges.length; index += 1) {
        if (badges[index].y - badges[index - 1].y < 22) {
          badges[index].y = badges[index - 1].y + 22;
        }
      }

      badges.forEach((badge) => {
        const label = formatValue(badge.value, badge.series.unit);
        const labelWidth = clamp(label.length * 7.2 + 16, 68, 116);
        const x = Math.min(badge.x + 10, chartLayout.width - chartLayout.right - labelWidth);
        const y = clamp(badge.y - 12, panelLayout.top + 4, panelLayout.top + panelLayout.height - 22);

        svg.append(
          createSvgNode("rect", {
            x,
            y,
            rx: 10,
            ry: 10,
            width: labelWidth,
            height: 22,
            fill: "#ffffff",
            stroke: badge.series.color,
            "stroke-width": 1.2,
          })
        );

        appendSvgText(
          svg,
          {
            x: x + 8,
            y: y + 15,
            fill: badge.series.color,
            "font-size": 11,
            "font-weight": 700,
          },
          label
        );
      });
    });
  }

  function mount(container, payload) {
    if (!container || !payload?.has_data || !Array.isArray(payload.series) || !payload.series.length) {
      return false;
    }
    const styleState = buildSeriesStyleState(payload.series);

    function persistSeriesStyleState() {
      const stored = loadStoredSeriesStyles();
      payload.series.forEach((series) => {
        stored[series.id] = {
          color: styleState[series.id]?.color || series.color,
          lineStyle: styleState[series.id]?.lineStyle || "solid",
        };
      });
      saveStoredSeriesStyles(stored);
    }

    function render() {
      const styledPayload = applySeriesStyleState(payload, styleState);
      const panels = describePanels(styledPayload);
      if (!panels.length) {
        container.innerHTML = "";
        return false;
      }

      const chartLayout = getChartLayout(container);
      const canvas = document.createElement("div");
      canvas.className = "wash-chart-canvas";

      const tooltip = document.createElement("div");
      tooltip.className = "wash-chart-tooltip";

      const svg = createSvgNode("svg", {
        class: "wash-chart-svg",
        viewBox: `0 0 ${chartLayout.width} ${chartLayout.height}`,
        preserveAspectRatio: "xMinYMin meet",
        width: chartLayout.width,
        height: chartLayout.height,
      });

      if (container.classList.contains("chart-host--modal")) {
        svg.style.height = `${chartLayout.height}px`;
        svg.style.maxWidth = "100%";
      }

      canvas.append(svg, tooltip);
      container.innerHTML = "";
      container.append(canvas);

      const plotWidth = chartLayout.width - chartLayout.left - chartLayout.right;
      const firstSeriesWithPoints = styledPayload.series.find((series) => series.points?.length);
      const domainStart =
        styledPayload.meta?.start ?? firstSeriesWithPoints?.points?.[0]?.[0] ?? 0;
      const domainEnd = Math.max(styledPayload.meta?.end ?? domainStart, domainStart + 1000);
      const scaleX = (timestamp) => {
        const ratio = (timestamp - domainStart) / Math.max(domainEnd - domainStart, 1);
        return chartLayout.left + ratio * plotWidth;
      };
      const labeledSegments = assignSegmentLanes(styledPayload.segments || [], scaleX, plotWidth, chartLayout);
      const plotTop = resolvePlotTop(chartLayout, labeledSegments);
      const panelHeights = buildPanelHeights(
        panels.length,
        chartLayout.height,
        plotTop,
        chartLayout.bottom,
        chartLayout.gap
      );

      svg.append(
        createSvgNode("rect", {
          x: 0,
          y: 0,
          width: chartLayout.width,
          height: chartLayout.height,
          fill: "#ffffff",
        })
      );

      const scaleY = (value, panelTop, panelHeight, panel) => {
        const ratio = (value - panel.min) / Math.max(panel.max - panel.min, 1e-9);
        return panelTop + panelHeight - ratio * panelHeight;
      };

      let currentTop = plotTop;
      const panelLayouts = panelHeights.map((panelHeight, index) => {
        const layout = {
          panel: panels[index],
          top: currentTop,
          height: panelHeight,
          chartWidth: chartLayout.width,
          chartRight: chartLayout.right,
        };
        currentTop += panelHeight + chartLayout.gap;
        return layout;
      });
      const labelLineHeight = 11;

      labeledSegments.forEach((segment) => {
        svg.append(
          createSvgNode("rect", {
            x: segment.labelX - segment.labelWidth / 2,
            y: segment.labelY - 10,
            width: segment.labelWidth,
            height: segment.labelHeight,
            rx: 11,
            ry: 11,
            fill: "#ffffff",
            stroke: segment.color,
            "stroke-width": 1.2,
            opacity: 0.98,
          })
        );

        svg.append(
          createSvgNode("line", {
            x1: segment.labelX,
            y1: segment.labelY + segment.labelHeight - 10,
            x2: clamp(segment.midpoint, chartLayout.left, chartLayout.width - chartLayout.right),
            y2: plotTop - 8,
            stroke: segment.color,
            "stroke-width": 1,
            opacity: 0.65,
          })
        );

        appendSvgMultilineText(
          svg,
          {
            x: segment.labelX,
            y: segment.labelY + 1,
            fill: "#314338",
            "font-size": chartLayout.width >= MODAL_CHART_WIDTH ? 10 : 9,
            "font-weight": 800,
            "text-anchor": "middle",
          },
          segment.displayLabel,
          labelLineHeight
        );
      });

      panelLayouts.forEach((layout) => {
        const bottom = layout.top + layout.height;

        layout.panel.ticks.forEach((tickValue) => {
          const y = scaleY(tickValue, layout.top, layout.height, layout.panel);

          svg.append(
            createSvgNode("line", {
              x1: chartLayout.left,
              y1: y,
              x2: chartLayout.width - chartLayout.right,
              y2: y,
              stroke: "rgba(38, 67, 49, 0.09)",
              "stroke-width": 1,
            })
          );

          svg.append(
            createSvgNode("line", {
              x1: chartLayout.left - 6,
              y1: y,
              x2: chartLayout.left,
              y2: y,
              stroke: "rgba(38, 67, 49, 0.18)",
              "stroke-width": 1,
            })
          );

          appendSvgText(
            svg,
            {
              x: chartLayout.left - 10,
              y: y + 4,
              fill: "#627164",
              "font-size": chartLayout.axisTickFontSize,
              "font-weight": 600,
              "text-anchor": "end",
            },
            formatAxisValue(tickValue, layout.panel.tickStep)
          );
        });

        svg.append(
          createSvgNode("line", {
            x1: chartLayout.left,
            y1: layout.top,
            x2: chartLayout.left,
            y2: bottom,
            stroke: "rgba(38, 67, 49, 0.18)",
            "stroke-width": 1,
          })
        );

        svg.append(
          createSvgNode("line", {
            x1: chartLayout.left,
            y1: bottom,
            x2: chartLayout.width - chartLayout.right,
            y2: bottom,
            stroke: "rgba(38, 67, 49, 0.18)",
            "stroke-width": 1,
          })
        );

        appendSvgText(
          svg,
          {
            x: 24,
            y: layout.top + layout.height / 2,
            fill: "#627164",
            "font-size": chartLayout.axisLabelFontSize,
            "font-weight": 700,
            "text-anchor": "middle",
            transform: `rotate(-90 24 ${layout.top + layout.height / 2})`,
          },
          `${layout.panel.label}, ${layout.panel.unit}`
        );

        labeledSegments.forEach((segment) => {
          svg.append(
            createSvgNode("rect", {
              x: segment.startX,
              y: layout.top,
              width: Math.max(segment.endX - segment.startX, 1),
              height: layout.height,
              fill: segment.color,
              opacity: 0.08,
            })
          );
        });

        layout.panel.series.forEach((series) => {
          const path = series.points
            .map(([timestamp, value], pointIndex) => {
              const x = scaleX(timestamp);
              const y = scaleY(value, layout.top, layout.height, layout.panel);
              return `${pointIndex === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
            })
            .join(" ");

          const pathAttributes = {
            d: path,
            fill: "none",
            stroke: series.color,
            "stroke-width": layout.panel.panelIndex === 0 ? 2.4 : 2,
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
          };
          if (series.dasharray) {
            pathAttributes["stroke-dasharray"] = series.dasharray;
          }

          svg.append(createSvgNode("path", pathAttributes));
        });
      });

      const plotBottom = panelLayouts.at(-1).top + panelLayouts.at(-1).height;
      const timeTicks = buildTimeTicks(domainStart, domainEnd);
      timeTicks.forEach((timestamp, index) => {
        const x = scaleX(timestamp);
        const anchor =
          index === 0 ? "start" : index === timeTicks.length - 1 ? "end" : "middle";

        svg.append(
          createSvgNode("line", {
            x1: x,
            y1: plotBottom,
            x2: x,
            y2: plotBottom + 8,
            stroke: "rgba(38, 67, 49, 0.18)",
            "stroke-width": 1,
          })
        );

        appendSvgMultilineText(
          svg,
          {
            x,
            y: plotBottom + 24,
            fill: "#627164",
            "font-size": container.classList.contains("chart-host--modal") ? 13 : 11,
            "font-weight": 700,
            "text-anchor": anchor,
          },
          formatTimeAxisLabel(timestamp, domainStart, domainEnd),
          container.classList.contains("chart-host--modal") ? 15 : 13
        );
      });

      const legend = document.createElement("div");
      legend.className = "wash-chart-legend";
      legend.innerHTML = styledPayload.series
        .map((series) => {
          const dashAttribute = series.dasharray ? `stroke-dasharray="${series.dasharray}"` : "";
          return `
            <span class="wash-chart-legend-item">
              <svg class="wash-chart-legend-line" viewBox="0 0 36 12" aria-hidden="true">
                <line x1="2" y1="6" x2="34" y2="6" stroke="${series.color}" stroke-width="2.6" stroke-linecap="round" ${dashAttribute}></line>
              </svg>
              <span>${escapeHtml(series.label)}</span>
            </span>
          `;
        })
        .join("");
      container.append(legend);

      const controls = document.createElement("div");
      controls.className = "wash-chart-controls";
      controls.innerHTML = `
        ${
          container.classList.contains("chart-host--modal")
            ? `
              <div class="wash-chart-controls-header">
                <div class="wash-chart-controls-copy">
                  <span class="wash-chart-controls-eyebrow">Настройка кривых</span>
                  <strong>Цвет и вид линий</strong>
                  <p>Подберите оформление каждой линии прямо в окне графика.</p>
                </div>
              </div>
            `
            : ""
        }
        <div class="wash-chart-controls-grid">
          ${styledPayload.series
            .map((series) => {
              const dashAttribute = series.dasharray ? `stroke-dasharray="${series.dasharray}"` : "";
              const safeLabel = escapeHtml(series.label);
              const lineStyleLabel = getLineStyleOption(series.lineStyle).label;
              return `
                <div class="wash-chart-control" style="--wash-chart-series-color: ${series.color};">
                  <div class="wash-chart-control-header">
                    <div class="wash-chart-control-heading">
                      <span class="wash-chart-control-kicker">Кривая</span>
                      <div class="wash-chart-control-name">${safeLabel}</div>
                    </div>
                    <span class="wash-chart-control-swatch" aria-hidden="true"></span>
                  </div>
                  <div class="wash-chart-control-preview" aria-hidden="true">
                    <svg viewBox="0 0 168 34" focusable="false">
                      <path d="M8 25C30 25 30 10 52 10C74 10 74 25 96 25C118 25 118 12 140 12C152 12 157 18 160 22" fill="none" stroke="${series.color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" ${dashAttribute}></path>
                    </svg>
                  </div>
                  <div class="wash-chart-control-fields">
                    <label class="wash-chart-control-field wash-chart-control-field--color">
                      <span class="wash-chart-control-label">Цвет</span>
                      <span class="wash-chart-color-input-shell">
                        <input type="color" value="${series.color}" data-series-color="${series.id}" aria-label="Цвет ${safeLabel}">
                        <span class="wash-chart-color-value">${series.color.toUpperCase()}</span>
                      </span>
                    </label>
                    <label class="wash-chart-control-field wash-chart-control-field--line">
                      <span class="wash-chart-control-label">Вид кривой</span>
                      <span class="wash-chart-select-shell">
                        <select data-series-line-style="${series.id}" aria-label="Тип линии ${safeLabel}">
                          ${LINE_STYLE_OPTIONS.map(
                            (option) =>
                              `<option value="${option.id}" ${option.id === series.lineStyle ? "selected" : ""}>${option.label}</option>`
                          ).join("")}
                        </select>
                      </span>
                      <span class="wash-chart-control-hint">${lineStyleLabel}</span>
                    </label>
                  </div>
                </div>
              `;
            })
            .join("")}
        </div>
      `;
      container.append(controls);

      controls.querySelectorAll("[data-series-color]").forEach((input) => {
        input.addEventListener("input", (event) => {
          const seriesId = event.currentTarget.dataset.seriesColor;
          const value = event.currentTarget.value;
          if (!seriesId || !isValidHexColor(value)) {
            return;
          }
          styleState[seriesId] = { ...(styleState[seriesId] || {}), color: value };
          persistSeriesStyleState();
          render();
        });
      });

      controls.querySelectorAll("[data-series-line-style]").forEach((select) => {
        select.addEventListener("change", (event) => {
          const seriesId = event.currentTarget.dataset.seriesLineStyle;
          if (!seriesId) {
            return;
          }
          styleState[seriesId] = {
            ...(styleState[seriesId] || {}),
            lineStyle: getLineStyleOption(event.currentTarget.value).id,
          };
          persistSeriesStyleState();
          render();
        });
      });

      const hoverLine = createSvgNode("line", {
        x1: chartLayout.left,
        y1: chartLayout.top,
        x2: chartLayout.left,
        y2: plotBottom,
        stroke: "rgba(32, 49, 38, 0.35)",
        "stroke-width": 1,
        "stroke-dasharray": "4 4",
        opacity: 0,
        "data-chart-interactive": "hover-line",
      });
      svg.append(hoverLine);

      const hoverDots = styledPayload.series.map((series) => {
        const dot = createSvgNode("circle", {
          r: 4,
          fill: series.color,
          stroke: "#ffffff",
          "stroke-width": 2,
          opacity: 0,
          "data-chart-interactive": "hover-dot",
        });
        svg.append(dot);
        return dot;
      });

      const overlay = createSvgNode("rect", {
        x: chartLayout.left,
        y: chartLayout.top,
        width: plotWidth,
        height: plotBottom - chartLayout.top,
        fill: "transparent",
        style: "cursor:crosshair",
        "data-chart-interactive": "overlay",
      });
      svg.append(overlay);

      const referenceSeries = styledPayload.series
        .filter((series) => series.points?.length)
        .sort((left, right) => right.points.length - left.points.length)[0];
      if (!referenceSeries) {
        return true;
      }

      const panelIndexToLayout = new Map(panelLayouts.map((layout) => [layout.panel.panelIndex, layout]));

      const onPointerMove = (event) => {
        const overlayBounds = overlay.getBoundingClientRect();
        const hostBounds = canvas.getBoundingClientRect();
        const ratioX = plotWidth / Math.max(overlayBounds.width, 1);
        const cursorX = clamp(
          chartLayout.left + (event.clientX - overlayBounds.left) * ratioX,
          chartLayout.left,
          chartLayout.width - chartLayout.right
        );
        const cursorRatio = (cursorX - chartLayout.left) / Math.max(plotWidth, 1);
        const targetTimestamp = domainStart + (domainEnd - domainStart) * cursorRatio;
        const referenceIndex = findNearestPointIndex(referenceSeries.points, targetTimestamp);
        const nearestTimestamp = referenceSeries.points[referenceIndex][0];
        const lineX = scaleX(nearestTimestamp);

        hoverLine.setAttribute("x1", lineX);
        hoverLine.setAttribute("x2", lineX);
        hoverLine.setAttribute("opacity", 1);

        const tooltipRows = [`<strong>${formatTime(nearestTimestamp)}</strong>`];
        styledPayload.series.forEach((series, seriesIndex) => {
          const pointIndex = findNearestPointIndex(series.points, nearestTimestamp);
          if (pointIndex < 0) {
            hoverDots[seriesIndex].setAttribute("opacity", 0);
            return;
          }

          const point = series.points[pointIndex];
          const layout = panelIndexToLayout.get(series.panel);
          if (!layout) {
            hoverDots[seriesIndex].setAttribute("opacity", 0);
            return;
          }

          const y = scaleY(point[1], layout.top, layout.height, layout.panel);
          hoverDots[seriesIndex].setAttribute("cx", lineX);
          hoverDots[seriesIndex].setAttribute("cy", y);
          hoverDots[seriesIndex].setAttribute("opacity", 1);

          tooltipRows.push(
            `<span><i style="background:${series.color}"></i>${series.label}: ${formatValue(point[1], series.unit)}</span>`
          );
        });

        tooltip.innerHTML = tooltipRows.join("");
        tooltip.classList.add("visible");
        const tooltipWidth = Math.max(tooltip.offsetWidth, 220);
        const tooltipHeight = Math.max(tooltip.offsetHeight, 110);

        const tooltipX = clamp(
          event.clientX - hostBounds.left + 18,
          12,
          Math.max(hostBounds.width - tooltipWidth - 12, 12)
        );
        const tooltipY = clamp(
          event.clientY - hostBounds.top + 18,
          12,
          Math.max(hostBounds.height - tooltipHeight - 12, 12)
        );
        tooltip.style.left = `${tooltipX}px`;
        tooltip.style.top = `${tooltipY}px`;
      };

      const onPointerLeave = () => {
        hoverLine.setAttribute("opacity", 0);
        hoverDots.forEach((dot) => dot.setAttribute("opacity", 0));
        tooltip.classList.remove("visible");
      };

      overlay.addEventListener("pointermove", onPointerMove);
      overlay.addEventListener("pointerdown", onPointerMove);
      overlay.addEventListener("mouseleave", onPointerLeave);
      overlay.addEventListener("pointerleave", onPointerLeave);
      overlay.addEventListener("pointercancel", onPointerLeave);

      container.dataset.chartMode = "interactive";
      return true;
    }

    return render();
  }

  window.WashChart = { mount };
})();
