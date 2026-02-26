/**
 * Septago Crossword Component (v2)
 * Fixes:
 * - Intersection pool derives from local grid letters via intersection_pool.cells (no server-stale letters)
 * - Hidden bars rendered using same strip UI as pool
 * - Backspace restores delete behavior (local-first + server event)
 * - Grid autosizes based on the GRID COLUMN width (not the full component width)
 * - Grid size capped to ~1/3 of viewport width (per user request)
 * - Removes right-side whitespace "tail" by keeping grid cell size deterministic and grid shrink-wrapped
 *
 * Option A timer fix:
 * - Removes Streamlit heartbeat TICK events entirely (no periodic setComponentValue)
 * - Timer updates purely client-side from props.status.start_time_epoch
 */
(function () {
  const root = document.getElementById("root");
  root.tabIndex = 0;
  root.innerHTML = `<div class="loading">Loading…</div>`;

  let lastProps = null;

  // client_seq monotonic counter; server acks the max processed seq in props.sync.last_client_seq
  let clientSeq = 0;
  let latestSentSeq = 0;

  // Local state (for lag-free UX)
  // {
  //   meta: { size, bars, crossMap, barOrder, hiddenBars, hiddenOrder, puzzleId, stateId, intersectionCells },
  //   active: { scope, bar_id, index, direction },
  //   gridLetters: Map(cellId -> letter),
  //   hiddenLetters: Map(hiddenId -> Array(letter)),
  // }
  let local = null;

  // ---- Client-side timer (Option A) ----
  let __timerInterval = null;
  let __timerStartEpoch = null; // seconds (int)
  let __timerEl = null;

  function postMessage(type, payload) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, payload || {}), "*");
  }

  function setFrameHeight() {
    const height = document.body.scrollHeight + 8;
    postMessage("streamlit:setFrameHeight", { height });
  }

  function emitEvent(type, payload) {
    const seq = ++clientSeq;
    latestSentSeq = seq;
    const stateId = lastProps?.sync?.state_id || null;

    const ev = {
      schema_version: "crossword.v2",
      event_id: (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + "-" + Math.random(),
      ts_ms: Date.now(),
      type,
      payload: Object.assign({}, payload || {}, {
        client_seq: seq,
        state_id: stateId,
      }),
    };

    postMessage("streamlit:setComponentValue", { value: ev });
    return seq;
  }

  function clamp(x, lo, hi) {
    return Math.max(lo, Math.min(hi, x));
  }

  function cssVarInt(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    const n = parseInt(String(v || "").trim(), 10);
    return Number.isFinite(n) ? n : fallback;
  }

  /**
   * Compute cell size from the *grid column width*,
   * and cap the overall grid width to ~1/3 of the viewport.
   */
  function computeCellSize(gridSize, gridColumnWidthPx) {
    const outerBorderPx = cssVarInt("--outer-border-px", 3);
    const viewportCapPx = Math.floor(window.innerWidth * (1 / 3)); // ~1/3 viewport width

    // available width for the grid itself
    const targetGridOuterPx = Math.min(gridColumnWidthPx, viewportCapPx);

    // subtract outer border on both sides; inner borders are handled by the CSS grid cell borders
    const usablePx = Math.max(0, targetGridOuterPx - outerBorderPx * 2);

    const cell = Math.floor(usablePx / gridSize);

    // clamp to keep usability
    return clamp(cell, 28, 110);
  }

  function parseKey(k) {
    // "h1:3" -> ["h1", 3]
    const parts = String(k).split(":");
    return [parts[0], parseInt(parts[1] || "0", 10)];
  }

  function parseCellId(cid) {
    const parts = String(cid || "").split(",");
    return [parseInt(parts[0] || "0", 10) || 0, parseInt(parts[1] || "0", 10) || 0];
  }

  function cellId(r, c) {
    return `${r},${c}`;
  }

  function nextPlayableFrom(meta, startCid, dr, dc) {
    if (!startCid) return null;
    const [r0, c0] = parseCellId(startCid);
    let r = r0 + dr;
    let c = c0 + dc;
    while (r >= 0 && c >= 0 && r < meta.size && c < meta.size) {
      const cid = cellId(r, c);
      if (meta.playableSet && meta.playableSet.has(cid)) return cid;
      r += dr;
      c += dc;
    }
    return null;
  }

  function pickBestBarForCell(meta, cid, prefer) {
    // prefer: 'h' or 'v'
    const pos = meta.cellPositions ? (meta.cellPositions[cid] || []) : [];
    if (!pos.length) return null;

    const pri = prefer === "h" ? (x) => x.bar_id.startsWith("h") : (x) => x.bar_id.startsWith("v");
    const sec = prefer === "h" ? (x) => x.bar_id.startsWith("v") : (x) => x.bar_id.startsWith("h");

    let best = pos.find(pri);
    if (!best) best = pos.find(sec);
    return best || pos[0];
  }

  function closestBarCell(meta, cid) {
    // If clicked cell isn't directly part of a bar, choose a nearby cell that *is*.
    // Returns { cid, prefer } where prefer is 'h' or 'v' based on direction to the closest bar cell.
    const start = parseCellId(cid);
    if (!start) return null;
    const { r, c } = start;

    const dirs = [
      { dr: 0, dc: -1, prefer: "h" }, // left
      { dr: 0, dc: 1, prefer: "h" },  // right
      { dr: -1, dc: 0, prefer: "v" }, // up
      { dr: 1, dc: 0, prefer: "v" },  // down
    ];

    let best = null; // {dist, cid, prefer}
    const max = meta.size || 7;

    for (const d of dirs) {
      for (let step = 1; step <= max; step++) {
        const rr = r + d.dr * step;
        const cc = c + d.dc * step;
        if (rr < 0 || cc < 0 || rr >= max || cc >= max) break;

        const probe = cellId(rr, cc);
        const cell = meta.cellMap ? meta.cellMap[probe] : null;
        if (!cell || cell.is_black) continue;

        const pos = meta.cellPositions ? (meta.cellPositions[probe] || []) : [];
        if (!pos.length) continue;

        const cand = { dist: step, cid: probe, prefer: d.prefer };
        if (!best || cand.dist < best.dist) best = cand;
        break; // first in this direction is closest along that ray
      }
    }

    return best ? { cid: best.cid, prefer: best.prefer } : null;
  }

  function buildMetaFromProps(props) {
    const grid = props.grid || {};
    const hidden = props.hidden || {};
    const sync = props.sync || {};
    const pool = props.intersection_pool || {};
    const marks = props.marks || {};

    // Bars: bar_id -> [cellId,...]
    const bars = grid.bars || {};
    const barOrder = grid.bar_order || ["h1", "h2", "h3", "v1", "v2", "v3"];

    // cellId -> [{bar_id, index}, ...]
    const cellPositions = {};
    for (const bid of Object.keys(bars)) {
      const arr = bars[bid] || [];
      for (let i = 0; i < arr.length; i++) {
        const cid = arr[i];
        if (!cellPositions[cid]) cellPositions[cid] = [];
        cellPositions[cid].push({ bar_id: bid, index: i });
      }
    }

    // cross_map: "bar:idx" -> "bar:idx"
    const crossMap = grid.cross_map || {};

    const hiddenBars = (hidden.bars || {});
    const hiddenOrder = hidden.bar_order || Object.keys(hiddenBars);

    const size = grid.size || 7;

    const puzzleId = (sync.puzzle_id != null) ? String(sync.puzzle_id) : "";
    const stateId = (sync.state_id != null) ? String(sync.state_id) : "";

    // Intersection pool should be driven by CELL IDS, not letters
    const intersectionCells = Array.isArray(pool.cells) ? pool.cells.slice() : [];

    // cell -> letter from props
    const gridLetters = new Map();
    const playableSet = new Set();
    const cellMap = {};
    for (const cell of (grid.cells || [])) {
      cellMap[cell.id] = cell;
      if (cell && cell.is_playable && !cell.is_black) {
        playableSet.add(cell.id);
        gridLetters.set(cell.id, cell.letter || "");
      }
    }

    // hidden letters from props
    const hiddenLetters = new Map();
    for (const hid of hiddenOrder) {
      const b = hiddenBars[hid];
      hiddenLetters.set(hid, Array.isArray(b?.letters) ? b.letters.slice() : []);
    }

    // marks (optional)
    const marksGrid = (marks && marks.grid) ? marks.grid : {};
    const marksHidden = (marks && marks.hidden) ? marks.hidden : {};

    return {
      size,
      bars,
      barOrder,
      crossMap,
      playableSet,
      cellMap,
      cellPositions,
      hiddenBars,
      hiddenOrder,
      puzzleId,
      stateId,
      intersectionCells,
      gridLetters,
      hiddenLetters,
      marksGrid,
      marksHidden,
    };
  }

  function syncLocalFromProps(props) {
    const meta = buildMetaFromProps(props);
    const focus = props.focus || {};
    const active = (focus.active || { scope: "grid", bar_id: "h1", index: 0, direction: "horizontal" });

    local = {
      meta,
      active: {
        scope: active.scope || "grid",
        bar_id: active.bar_id || "h1",
        index: parseInt(active.index ?? 0, 10) || 0,
        direction: active.direction || "horizontal",
      },
      gridLetters: meta.gridLetters,
      hiddenLetters: meta.hiddenLetters,
    };
  }

  function activeCellIdForGrid(meta, active) {
    const arr = meta.bars[active.bar_id] || [];
    const i = clamp(active.index || 0, 0, Math.max(0, arr.length - 1));
    return arr[i] || null;
  }

  function barLength(meta, scope, barId) {
    if (scope === "grid") return (meta.bars[barId] || []).length;
    const arr = local.hiddenLetters.get(barId) || [];
    return arr.length;
  }

  function setActive(scope, barId, index) {
    if (!local) return;
    const meta = local.meta;

    if (scope === "grid") {
      if (!(barId in meta.bars)) return;
      const len = (meta.bars[barId] || []).length;
      local.active = {
        scope: "grid",
        bar_id: barId,
        index: clamp(index ?? 0, 0, Math.max(0, len - 1)),
        direction: (barId === "v1" || barId === "v2" || barId === "v3") ? "vertical" : "horizontal",
      };
      return;
    }

    // hidden
    if (!meta.hiddenOrder.includes(barId)) return;
    const len = (local.hiddenLetters.get(barId) || []).length;
    local.active = {
      scope: "hidden",
      bar_id: barId,
      index: clamp(index ?? 0, 0, Math.max(0, len - 1)),
      direction: "horizontal",
    };
  }

  function moveWithinActive(step) {
    if (!local) return;
    const a = local.active;
    const len = barLength(local.meta, a.scope, a.bar_id);
    if (!len) return;
    a.index = clamp((a.index || 0) + step, 0, len - 1);
  }

  function crossAtActive() {
    // If active is grid and at an intersection, return partner [barId, idx]
    if (!local) return null;
    const a = local.active;
    if (a.scope !== "grid") return null;
    const key = `${a.bar_id}:${a.index}`;
    const other = local.meta.crossMap[key];
    if (!other) return null;
    const [b, i] = parseKey(other);
    return [b, i];
  }

  function clearGridAt(barId, idx) {
    const parr = local.meta.bars[barId] || [];
    const cid = parr[idx];
    if (cid) local.gridLetters.set(cid, "");
  }

  function applyLocalBackspace() {
    if (!local) return;
    const a = local.active;

    // Hidden
    if (a.scope === "hidden") {
      const arr = (local.hiddenLetters.get(a.bar_id) || []).slice();
      if (!arr.length) return;

      const i = clamp(a.index || 0, 0, arr.length - 1);

      if ((arr[i] || "") !== "") {
        arr[i] = "";
        local.hiddenLetters.set(a.bar_id, arr);
        return;
      }

      // if already empty, move back and clear that
      if (i > 0) {
        const j = i - 1;
        arr[j] = "";
        local.hiddenLetters.set(a.bar_id, arr);
        a.index = j;
      }
      return;
    }

    // Grid
    const meta = local.meta;
    const cellId = activeCellIdForGrid(meta, a);
    if (!cellId) return;

    const current = local.gridLetters.get(cellId) || "";

    if (current !== "") {
      local.gridLetters.set(cellId, "");

      // mirror clear to partner
      const partner = crossAtActive();
      if (partner) {
        const [pb, pi] = partner;
        clearGridAt(pb, pi);
      }
      return;
    }

    // if empty, move back and clear there
    if ((a.index || 0) > 0) {
      a.index = a.index - 1;
      const cid2 = activeCellIdForGrid(meta, a);
      if (!cid2) return;
      local.gridLetters.set(cid2, "");

      const partner = crossAtActive();
      if (partner) {
        const [pb, pi] = partner;
        clearGridAt(pb, pi);
      }
    }
  }

  function applyLocalAction(type, payload) {
    if (!local) return;

    if (type === "SET_ACTIVE_BAR") {
      setActive(payload.scope || "grid", payload.bar_id, payload.index ?? 0);
      return;
    }

    if (type === "MOVE_NEXT") {
      moveWithinActive(1);
      return;
    }
    if (type === "MOVE_PREV") {
      moveWithinActive(-1);
      return;
    }

    if (type === "BACKSPACE") {
      applyLocalBackspace();
      return;
    }

    if (type === "INPUT_LETTER") {
      const letter = String(payload.letter || "").toUpperCase();
      if (!(letter.length === 1 && letter >= "A" && letter <= "Z")) return;
      const a = local.active;

      if (a.scope === "hidden") {
        const arr = (local.hiddenLetters.get(a.bar_id) || []).slice();
        if (a.index < 0 || a.index >= arr.length) return;
        arr[a.index] = letter;
        local.hiddenLetters.set(a.bar_id, arr);
        moveWithinActive(1);
        return;
      }

      // grid
      const cellId = activeCellIdForGrid(local.meta, a);
      if (!cellId) return;
      local.gridLetters.set(cellId, letter);

      // mirror to partner cell
      const partner = crossAtActive();
      if (partner) {
        const [pb, pi] = partner;
        const parr = local.meta.bars[pb] || [];
        const pcid = parr[pi];
        if (pcid) local.gridLetters.set(pcid, letter);
      }

      moveWithinActive(1);
      return;
    }
  }

  function formatMMSS(totalSeconds) {
    const s = Math.max(0, parseInt(totalSeconds || 0, 10) || 0);
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  function stopTimerInterval() {
    if (__timerInterval != null) {
      clearInterval(__timerInterval);
      __timerInterval = null;
    }
  }

  function startOrUpdateClientTimerFromProps(props) {
    const status = props?.status || {};
    const startEpoch = status.start_time_epoch != null ? parseInt(status.start_time_epoch, 10) : null;
    const fallbackElapsed = status.elapsed_seconds != null ? parseInt(status.elapsed_seconds, 10) : 0;

    if (!__timerEl) return;

    // If no start time is provided, fall back to server elapsed (static).
    if (!Number.isFinite(startEpoch) || startEpoch == null) {
      stopTimerInterval();
      __timerStartEpoch = null;
      __timerEl.textContent = formatMMSS(fallbackElapsed);
      return;
    }

    // If start time changed (new puzzle / reset), restart interval.
    const startChanged = (__timerStartEpoch == null) || (startEpoch !== __timerStartEpoch);
    __timerStartEpoch = startEpoch;

    const tick = () => {
      const nowEpoch = Math.floor(Date.now() / 1000);
      const elapsed = Math.max(0, nowEpoch - __timerStartEpoch);
      __timerEl.textContent = formatMMSS(elapsed);
    };

    // Always set immediately
    tick();

    if (startChanged) {
      stopTimerInterval();
      __timerInterval = setInterval(tick, 1000);
    } else {
      // Ensure interval exists (in case it was cleared)
      if (__timerInterval == null) {
        __timerInterval = setInterval(tick, 1000);
      }
    }
  }

  function renderStrip({ letters, marksArr, isActiveBar, activeIndex, onCellClick, readOnly, maxWidthPx }) {
    const strip = document.createElement("div");
    strip.className = "strip";

    // Size tiles so the full strip fits on one line (no wrapping)
    if (maxWidthPx && letters.length) {
      const gap = 6; // must match CSS gap
      const available = Math.max(0, maxWidthPx - gap * (letters.length - 1));
      const per = Math.floor(available / letters.length);
      const minPx = 26;
      const maxPx = cssVarInt("--cell-size", 56);
      const px = Math.max(minPx, Math.min(maxPx, per));
      strip.style.setProperty("--strip-cell-size", `${px}px`);
    }

    for (let i = 0; i < letters.length; i++) {
      const cel = document.createElement("div");
      cel.className = "strip-cell";
      if (isActiveBar) cel.classList.add("active-bar");
      if (isActiveBar && i === activeIndex) cel.classList.add("active-cell");

      const mk = (Array.isArray(marksArr) ? marksArr[i] : "");
      if (mk === "correct") cel.classList.add("mark-correct");
      if (mk === "wrong") cel.classList.add("mark-wrong");

      cel.textContent = String(letters[i] || "");
      if (!readOnly && typeof onCellClick === "function") {
        cel.style.cursor = "pointer";
        cel.addEventListener("click", (e) => {
          e.preventDefault();
          root.focus();
          onCellClick(i);
        });
      }
      strip.appendChild(cel);
    }
    return strip;
  }

  function render(props) {
    root.innerHTML = "";
    if (!props || !local) {
      root.innerHTML = `<div class="loading">Missing props.</div>`;
      setFrameHeight();
      return;
    }

    const meta = local.meta;
    const status = props.status || {};
    const complete = !!status.complete;

    // wrapper
    const wrap = document.createElement("div");
    wrap.className = "wrap";

    // Timer row
    const topRow = document.createElement("div");
    topRow.className = "top-row";

    const timer = document.createElement("div");
    timer.className = "timer";
    timer.textContent = formatMMSS(parseInt(status.elapsed_seconds || 0, 10) || 0);
    __timerEl = timer; // bind
    topRow.appendChild(timer);

    wrap.appendChild(topRow);

    // Main content: grid left, clues right
    const main = document.createElement("div");
    main.className = "main";

    // --- Grid column ---
    const gridCol = document.createElement("div");
    gridCol.className = "grid-col";

    // --- Clues column ---
    const clueCol = document.createElement("div");
    clueCol.className = "clue-col";

    // Attach skeleton first so we can measure widths accurately
    main.appendChild(gridCol);
    main.appendChild(clueCol);
    wrap.appendChild(main);
    root.appendChild(wrap);

    // Start/update client-side timer AFTER timer element exists
    startOrUpdateClientTimerFromProps(props);

    // Measure available width for the grid column
    const gapPx = 14; // must match CSS .main gap
    const mainWidth = main.getBoundingClientRect().width || root.clientWidth || window.innerWidth;

    // If in 2-column mode, approximate grid column width as half minus half the gap
    const twoCol = window.innerWidth > 900;
    const gridColWidth = twoCol ? Math.max(0, (mainWidth - gapPx) / 2) : mainWidth;

    // sizing based on GRID COLUMN width (and capped at ~1/3 viewport)
    const cellPx = computeCellSize(meta.size, gridColWidth);
    document.documentElement.style.setProperty("--cell-size", `${cellPx}px`);

    // Build grid after sizing is known
    const gridEl = document.createElement("div");
    gridEl.className = "grid";
    gridEl.style.gridTemplateColumns = `repeat(${meta.size}, var(--cell-size))`;
    gridEl.style.gridTemplateRows = `repeat(${meta.size}, var(--cell-size))`;

    const activeGridCellId = (local.active.scope === "grid") ? activeCellIdForGrid(meta, local.active) : null;
    const activeSlotSet = new Set();
    if (local.active.scope === "grid") {
      const arr = meta.bars[local.active.bar_id] || [];
      for (const cid of arr) activeSlotSet.add(cid);
    }

    for (let r = 0; r < meta.size; r++) {
      for (let c = 0; c < meta.size; c++) {
        const cid = `${r},${c}`;
        const cell = meta.cellMap[cid];
        const el = document.createElement("div");
        el.className = "cell";
        if (c === meta.size - 1) el.classList.add("last-col");
        if (r === meta.size - 1) el.classList.add("last-row");

        if (!cell || cell.is_black) {
          el.classList.add("black");
        } else {
          el.classList.add("playable");

          const mk = meta.marksGrid ? meta.marksGrid[cid] : "";
          if (mk === "correct") el.classList.add("mark-correct");
          if (mk === "wrong") el.classList.add("mark-wrong");

          if (activeSlotSet.has(cid)) el.classList.add("active-slot");
          if (activeGridCellId && cid === activeGridCellId) el.classList.add("active-cell");

          el.textContent = local.gridLetters.get(cid) || "";
          el.dataset.cellId = cid;

          el.addEventListener("click", (e) => {
            e.preventDefault();
            root.focus();

            const clicked0 = el.dataset.cellId;

            // If the user clicks the *already-active* cell and it's an intersection,
            // toggle the active bar orientation (NYT-style).
            if (clicked0 === activeGridCellId) {
              const pos0 = meta.cellPositions ? (meta.cellPositions[clicked0] || []) : [];
              const hasH0 = pos0.find(p => String(p.bar_id || "").startsWith("h"));
              const hasV0 = pos0.find(p => String(p.bar_id || "").startsWith("v"));
              if (hasH0 && hasV0) {
                const want = (local.active.direction === "horizontal") ? "v" : "h";
                const tgt = pos0.find(p => String(p.bar_id || "").startsWith(want)) || pos0[0];
                if (tgt) {
                  applyLocalAction("SET_ACTIVE_BAR", { scope: "grid", bar_id: tgt.bar_id, index: tgt.index });
                  render(lastProps);
                  emitEvent("SET_ACTIVE_BAR", { scope: "grid", bar_id: tgt.bar_id, index: tgt.index });
                  return;
                }
              }
            }

            // Allow clicking ANY non-black square.
            // If the square is not directly part of a bar, fall back to the closest bar-connected square
            // and choose orientation toward that closest playable neighbor.
            let prefer = (local.active.scope === "grid" && local.active.direction === "vertical") ? "v" : "h";

            let clicked = clicked0;
            let choice = pickBestBarForCell(meta, clicked, prefer);

            if (!choice) {
              const near = closestBarCell(meta, clicked0);
              if (near) {
                clicked = near.cid;
                prefer = near.prefer;
                choice = pickBestBarForCell(meta, clicked, prefer);
              }
            }

            if (!choice) return;

            applyLocalAction("SET_ACTIVE_BAR", { scope: "grid", bar_id: choice.bar_id, index: choice.index });
            render(lastProps);

            emitEvent("SET_ACTIVE_BAR", { scope: "grid", bar_id: choice.bar_id, index: choice.index });
          });
        }

        gridEl.appendChild(el);
      }
    }

    // Clear gridCol and attach real content (now that gridEl exists)
    gridCol.innerHTML = "";
    gridCol.appendChild(gridEl);

    // We want strips to fit the rendered grid width
    const gridWidthPx = gridEl.getBoundingClientRect().width || (cellPx * meta.size);

    // --- Titles + strips ---
    const mixTitle = document.createElement("div");
    mixTitle.className = "section-title";
    mixTitle.textContent = "Letter mix";
    gridCol.appendChild(mixTitle);

    const poolLetters = meta.intersectionCells.map((cid) => local.gridLetters.get(cid) || "");
    const poolStrip = renderStrip({
      letters: poolLetters,
      marksArr: null,
      isActiveBar: false,
      activeIndex: -1,
      readOnly: true,
      maxWidthPx: gridWidthPx
    });
    poolStrip.classList.add("pool-strip");
    gridCol.appendChild(poolStrip);

    const hiddenTitle = document.createElement("div");
    hiddenTitle.className = "section-title";
    hiddenTitle.textContent = "Hidden word";
    gridCol.appendChild(hiddenTitle);

    const hiddenWrap = document.createElement("div");
    hiddenWrap.className = "hidden-wrap";
    for (const hid of meta.hiddenOrder) {
      const arr = local.hiddenLetters.get(hid) || [];
      const isActive = (local.active.scope === "hidden" && local.active.bar_id === hid);
      const strip = renderStrip({
        letters: arr,
        marksArr: (meta.marksHidden && meta.marksHidden[hid]) ? meta.marksHidden[hid] : null,
        isActiveBar: isActive,
        activeIndex: isActive ? local.active.index : -1,
        readOnly: false,
        maxWidthPx: gridWidthPx,
        onCellClick: (i) => {
          applyLocalAction("SET_ACTIVE_BAR", { scope: "hidden", bar_id: hid, index: i });
          render(lastProps);
          emitEvent("SET_ACTIVE_BAR", { scope: "hidden", bar_id: hid, index: i });
        }
      });
      strip.classList.add("hidden-strip");
      hiddenWrap.appendChild(strip);
    }
    gridCol.appendChild(hiddenWrap);

    // Completion banner
    if (complete) {
      const banner = document.createElement("div");
      banner.className = "complete";
      banner.textContent = "🎉 Puzzle Complete";
      gridCol.appendChild(banner);
    }

    // --- Clues column content ---
    const clues = props.clues || {};
    const clueTitle = document.createElement("div");
    clueTitle.className = "clue-title";
    clueTitle.textContent = "Clues";
    clueCol.appendChild(clueTitle);

    function clueItem(scope, barId, label) {
      const item = document.createElement("div");
      item.className = "clue";
      if (local.active.scope === scope && local.active.bar_id === barId) item.classList.add("active");
      const tag = document.createElement("span");
      tag.className = "clue-tag";
      tag.textContent = label;
      const text = document.createElement("span");
      text.className = "clue-text";
      text.textContent = String(clues[barId] || "");
      item.appendChild(tag);
      item.appendChild(text);
      item.addEventListener("click", (e) => {
        e.preventDefault();
        root.focus();
        applyLocalAction("SET_ACTIVE_BAR", { scope, bar_id: barId, index: 0 });
        render(lastProps);
        emitEvent("SET_ACTIVE_BAR", { scope, bar_id: barId });
      });
      return item;
    }

    // Horizontals
    const hHdr = document.createElement("div");
    hHdr.className = "clue-hdr";
    hHdr.textContent = "Horizontal";
    clueCol.appendChild(hHdr);
    clueCol.appendChild(clueItem("grid", "h1", "H1"));
    clueCol.appendChild(clueItem("grid", "h2", "H2"));
    clueCol.appendChild(clueItem("grid", "h3", "H3"));

    // Verticals
    const vHdr = document.createElement("div");
    vHdr.className = "clue-hdr";
    vHdr.textContent = "Vertical";
    clueCol.appendChild(vHdr);
    clueCol.appendChild(clueItem("grid", "v1", "V1"));
    clueCol.appendChild(clueItem("grid", "v2", "V2"));
    clueCol.appendChild(clueItem("grid", "v3", "V3"));

    // Hidden
    const xHdr = document.createElement("div");
    xHdr.className = "clue-hdr";
    xHdr.textContent = "Hidden";
    clueCol.appendChild(xHdr);
    for (const hid of meta.hiddenOrder) {
      const label = (hid === "hidden1") ? "HID 1" : (hid === "hidden2") ? "HID 2" : hid.toUpperCase();
      clueCol.appendChild(clueItem("hidden", hid, label));
    }

    setTimeout(setFrameHeight, 0);
  }

  function handleKeydown(e) {
    if (!local) return;

    const key = e.key;
    const a = local.active;

    // Spacebar => MOVE_NEXT only
    if (key === " ") {
      e.preventDefault();
      applyLocalAction("MOVE_NEXT", {});
      render(lastProps);
      emitEvent("MOVE_NEXT", {});
      return;
    }

    // Backspace => clear cell (local-first) + emit BACKSPACE
    if (key === "Backspace") {
      e.preventDefault();
      applyLocalAction("BACKSPACE", {});
      render(lastProps);
      emitEvent("BACKSPACE", {});
      return;
    }

    // Letters
    if (key && key.length === 1) {
      const ch = key.toUpperCase();
      if (ch >= "A" && ch <= "Z") {
        e.preventDefault();
        applyLocalAction("INPUT_LETTER", { letter: ch });
        render(lastProps);
        emitEvent("INPUT_LETTER", { letter: ch });
        return;
      }
    }

    // Tab / Enter => next/prev clue
    if (key === "Tab" || key === "Enter") {
      e.preventDefault();

      const order = [
        { scope: "grid", bar_id: "h1" },
        { scope: "grid", bar_id: "h2" },
        { scope: "grid", bar_id: "h3" },
        { scope: "grid", bar_id: "v1" },
        { scope: "grid", bar_id: "v2" },
        { scope: "grid", bar_id: "v3" },
        ...local.meta.hiddenOrder.map(h => ({ scope: "hidden", bar_id: h })),
      ];
      const curIdx = order.findIndex(x => x.scope === a.scope && x.bar_id === a.bar_id);
      const step = e.shiftKey ? -1 : 1;
      const nxt = (curIdx < 0) ? 0 : (curIdx + step + order.length) % order.length;
      const target = order[nxt];
      applyLocalAction("SET_ACTIVE_BAR", { scope: target.scope, bar_id: target.bar_id, index: 0 });
      render(lastProps);
      emitEvent("SET_ACTIVE_BAR", { scope: target.scope, bar_id: target.bar_id });
      return;
    }

    // Arrow keys
    if (key.startsWith("Arrow")) {
      e.preventDefault();

      if (a.scope === "hidden") {
        if (key === "ArrowLeft") {
          applyLocalAction("MOVE_PREV", {});
          render(lastProps);
          emitEvent("MOVE_PREV", {});
        }
        if (key === "ArrowRight") {
          applyLocalAction("MOVE_NEXT", {});
          render(lastProps);
          emitEvent("MOVE_NEXT", {});
        }
        return;
      }

      // grid
      const meta = local.meta;
      const curCid = activeCellIdForGrid(meta, a);

      // Movement in the grid should skip black squares by scanning to the next playable cell.
      if (key === "ArrowLeft" || key === "ArrowRight") {
        const dc = (key === "ArrowLeft") ? -1 : 1;
        const nxtCid = nextPlayableFrom(meta, curCid, 0, dc);
        if (nxtCid) {
          const best = pickBestBarForCell(meta, nxtCid, "h");
          if (best) {
            applyLocalAction("SET_ACTIVE_BAR", { scope: "grid", bar_id: best.bar_id, index: best.index });
            render(lastProps);
            emitEvent("SET_ACTIVE_BAR", { scope: "grid", bar_id: best.bar_id, index: best.index });
          }
          return;
        }

        // If no cell exists in that direction, keep legacy "toggle at intersection" behavior.
        if (a.direction === "vertical") {
          const partner = crossAtActive();
          if (partner) {
            const [pb, pi] = partner;
            applyLocalAction("SET_ACTIVE_BAR", { scope: "grid", bar_id: pb, index: pi });
            render(lastProps);
            emitEvent("SET_ACTIVE_BAR", { scope: "grid", bar_id: pb, index: pi });
          }
        }
        return;
      }

      if (key === "ArrowUp" || key === "ArrowDown") {
        const dr = (key === "ArrowUp") ? -1 : 1;
        const nxtCid = nextPlayableFrom(meta, curCid, dr, 0);
        if (nxtCid) {
          const best = pickBestBarForCell(meta, nxtCid, "v");
          if (best) {
            applyLocalAction("SET_ACTIVE_BAR", { scope: "grid", bar_id: best.bar_id, index: best.index });
            render(lastProps);
            emitEvent("SET_ACTIVE_BAR", { scope: "grid", bar_id: best.bar_id, index: best.index });
          }
          return;
        }

        // Legacy toggle at intersection when moving perpendicular.
        if (a.direction === "horizontal") {
          const partner = crossAtActive();
          if (partner) {
            const [pb, pi] = partner;
            applyLocalAction("SET_ACTIVE_BAR", { scope: "grid", bar_id: pb, index: pi });
            render(lastProps);
            emitEvent("SET_ACTIVE_BAR", { scope: "grid", bar_id: pb, index: pi });
          }
        }
        return;
      }
      return;
    }
  }

  root.addEventListener("keydown", handleKeydown);

  // Resize observer for responsive sizing + frame height.
  const ro = new ResizeObserver(() => {
    if (lastProps && local) render(lastProps);
    else setFrameHeight();
  });
  ro.observe(root);

  window.addEventListener("message", (event) => {
    const msg = event.data;
    const type = msg?.type;
    if (typeof type !== "string") return;

    if (type === "streamlit:render") {
      let props = null;
      if (msg.args && msg.args.props) props = msg.args.props;
      else if (msg.args && msg.args.args && msg.args.args.props) props = msg.args.args.props;
      else if (Array.isArray(msg.args) && msg.args[0] && msg.args[0].props) props = msg.args[0].props;
      else if (msg.props) props = msg.props;
      else props = msg.args || msg;

      lastProps = props;

      // Hard resync when the server starts a fresh state (load puzzle / reset)
      const nextStateId = (props?.sync?.state_id != null) ? String(props.sync.state_id) : "";
      const prevStateId = (local && local.meta && local.meta.stateId != null) ? String(local.meta.stateId) : "";
      if (prevStateId && nextStateId && prevStateId !== nextStateId) {
        local = null;
        clientSeq = 0;
        latestSentSeq = 0;
      }

      const ack = props?.sync?.last_client_seq != null ? parseInt(props.sync.last_client_seq, 10) : 0;
      if (!local) {
        syncLocalFromProps(props);
      } else {
        if (ack >= latestSentSeq) syncLocalFromProps(props);
      }

      render(props);
      return;
    }
  });

  postMessage("streamlit:componentReady", { apiVersion: 1, ready: true });
  setTimeout(setFrameHeight, 0);
})();