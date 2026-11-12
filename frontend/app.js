/* Squardle — freeplay client. Vanilla JS, no build step, CSP-safe (no inline handlers/styles). */
(() => {
  'use strict';

  // Local QA: open /?test (server started with SQUARDLE_TEST_MODE=1) to always get the fixed test board.
  const TEST = new URLSearchParams(location.search).has('test');

  // ── Helpers ──────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  // Directional arrow pairs on a clue tile: ◀ ▶ for the row, ▲ ▼ for the column, one pair per copy.
  function addArrows(t, rowN, colN) {
    const side = (cls, n) => { const s = el('span', 'arw ' + cls); for (let i = 0; i < n; i++) s.appendChild(el('i')); t.appendChild(s); };
    if (rowN > 0) { side('l', rowN); side('r', rowN); }
    if (colN > 0) { side('t', colN); side('b', colN); }
  }
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode / quota */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
    json(k, fallback) { try { const v = JSON.parse(store.get(k)); return v ?? fallback; } catch { return fallback; } },
  };
  const KEYS = {
    game: TEST ? 'squardle:test-game' : 'squardle:game',
    legacy: 'squardle-freeplay-game',
    stats: 'squardle:stats:v1',
    settings: 'squardle:settings:v1',
    seenHelp: 'squardle:seen-help',
    marks: (id) => `squardle:marks:${id}`,
    notes: (id) => `squardle:notes:${id}`,
  };
  const ROWS = ['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM'];
  const SIZE = 5;
  const EXPAND_MS = 2000;
  const SHRINK_MS = 550;
  const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  // ── State ────────────────────────────────────────────────
  const S = {
    gameId: null, state: null, draft: '', busy: false, loading: true,
    marks: {}, notes: {}, notesMode: false, sel: null,
    // quickNote: notes mode opened by tapping a square; it closes itself after one letter.
    quickNote: false,
    hlGuess: null, hlLetter: null,
    fresh: null, typedIdx: -1, resultShownFor: null, suppressClick: false,
    // Latest guess shown as full-size tiles for EXPAND_MS, then collapsed into chips.
    expanded: null, expTimer: null,
    settings: { cb: false },
  };
  const stats = Object.assign(
    { played: 0, won: 0, streak: 0, max: 0, best: null, totalWinGuesses: 0, last: null },
    store.json(KEYS.stats, {}),
  );

  // ── API ──────────────────────────────────────────────────
  async function api(path, opts = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 20000);
    try {
      const res = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json' }, signal: ctl.signal });
      let data = null;
      try { data = await res.json(); } catch { /* non-JSON body */ }
      if (!res.ok) {
        const err = new Error(typeof data?.detail === 'string' ? data.detail : 'Something went wrong.');
        err.status = res.status;
        throw err;
      }
      return data;
    } catch (e) {
      if (e.status !== undefined) throw e;
      const err = new Error(e.name === 'AbortError' ? 'The server took too long. Try again.' : 'Can’t reach the server. Check your connection.');
      err.status = 0;
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  // ── Toasts / announcements ───────────────────────────────
  function toast(message, ms = 2200) {
    const t = el('div', 'toast', message);
    const box = $('toasts');
    while (box.children.length >= 3) box.firstChild.remove();
    box.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, ms);
  }
  // Celebration pop-up at the top of the screen (never over the board) when a word is solved.
  function celebrate(title, sub, ms = 2000) {
    const box = $('celebrate');
    const card = el('div', 'bonus-pop');
    const burst = el('div', 'burst');
    for (let i = 0; i < 12; i++) { const p = el('i'); p.style.setProperty('--a', `${i * 30}deg`); burst.appendChild(p); }
    card.appendChild(burst);
    card.appendChild(el('div', 'bp-title', title));
    if (sub) card.appendChild(el('div', 'bp-sub', sub));
    box.replaceChildren(card);
    clearTimeout(celebrate.t);
    celebrate.t = setTimeout(() => { card.classList.add('out'); setTimeout(() => card.remove(), 300); }, ms);
  }

  const announce = (msg) => { $('announce').textContent = ''; setTimeout(() => { $('announce').textContent = msg; }, 30); };

  // ── Rendering ────────────────────────────────────────────
  const isGreen = (clues) => clues.some((c) => c.color === 'green');
  const chipsPerRow = (n) => (n <= 12 ? 4 : n <= 20 ? 5 : 6);
  const live = () => S.state && !S.state.ended;

  // Games saved before the crossing fix hold two identical tiles (across + down) for the
  // same guess at a diagonal crossing. Merge them into one for display.
  function dedupeCrossing(clues) {
    const out = [];
    for (const k of clues) {
      const twin = out.find((o) => o.guess_id === k.guess_id && o.letter === k.letter && o.axis !== k.axis && o.axis !== 'cross');
      if (!twin) { out.push(k); continue; }
      const across = twin.axis === 'across' ? twin : k, down = twin.axis === 'across' ? k : twin;
      const row = across.row_arrows, col = down.col_arrows;
      const color = across.color === 'green' ? 'green'
        : row > 0 && col > 0 ? 'orange' : row > 0 ? 'yellow' : col > 0 ? 'red' : across.color;
      out[out.indexOf(twin)] = { ...across, axis: 'cross', id: `${across.guess_id}:cross`, row_arrows: row, col_arrows: col, color };
    }
    return out;
  }

  // Collapse the just-submitted guess into history chips. Each big tile animates (FLIP) from its
  // full-size spot to its chip slot, so it visibly shrinks into place. `mutate` lets a caller change
  // state (e.g. add a typed letter) before the single re-render, so the animation isn't interrupted.
  function collapseExpanded(mutate) {
    clearTimeout(S.expTimer); S.expTimer = null;
    if (S.expanded == null) return false;
    const board = $('board');
    const from = {};
    board.querySelectorAll('.fill .tile[data-flip]').forEach((t) => { from[t.dataset.flip] = t.getBoundingClientRect(); });
    const wasHidden = new Set([...board.querySelectorAll('.cell.expanded')].map((c) => `${c.dataset.r},${c.dataset.c}`));
    S.expanded = null;
    if (mutate) mutate();
    renderBoard();
    if (reducedMotion()) return true;
    board.querySelectorAll('.chips .tile[data-tid]').forEach((t) => {
      const a = from[t.dataset.tid];
      if (a) {
        const b = t.getBoundingClientRect();
        if (!b.width) return;
        t.animate([
          { transformOrigin: '0 0', transform: `translate(${a.left - b.left}px, ${a.top - b.top}px) scale(${a.width / b.width}, ${a.height / b.height})` },
          { transformOrigin: '0 0', transform: 'none' },
        ], { duration: SHRINK_MS, easing: 'cubic-bezier(.45, 0, .2, 1)' });
      } else if (wasHidden.has(t.dataset.tid.split(',').slice(0, 2).join(','))) {
        // Older chips were hidden under the big tile; fade them back in as it lands.
        t.animate([{ opacity: 0 }, { opacity: 1 }], { duration: SHRINK_MS * 0.6, delay: SHRINK_MS * 0.5, easing: 'ease-out', fill: 'backwards' });
      }
    });
    return true;
  }
  function expandGuess(id) {
    clearTimeout(S.expTimer);
    S.expanded = id;
    S.expTimer = setTimeout(() => collapseExpanded(), EXPAND_MS);
  }

  function buildStatic() {
    const mc = $('markCols'), mr = $('markRows'), pr = $('progress');
    for (let i = 0; i < SIZE; i++) { mc.appendChild(el('i')); mr.appendChild(el('i')); }
    for (let i = 0; i < 10; i++) pr.appendChild(el('i'));

    document.querySelectorAll('.sw[data-arrows]').forEach((sw) => {
      const [rowN, colN] = sw.dataset.arrows.split(',').map(Number);
      addArrows(sw, rowN, colN);
    });

    const kb = $('keyboard');
    const key = (cls, label, k, aria) => {
      const b = el('button', cls, label); b.type = 'button'; b.dataset.k = k;
      if (aria) b.setAttribute('aria-label', aria);
      return b;
    };
    ROWS.forEach((letters, idx) => {
      const row = el('div', 'krow');
      if (idx === 1) row.appendChild(el('span', 'key spacer'));
      if (idx === 2) row.appendChild(key('key wide enter-inline', 'ENTER', 'Enter', 'Enter'));
      for (const ch of letters) row.appendChild(key('key', ch, ch));
      if (idx === 2) row.appendChild(key('key wide', '⌫', 'Backspace', 'Delete'));
      if (idx === 1) row.appendChild(el('span', 'key spacer'));
      kb.appendChild(row);
    });
    // Phones: a full-width Enter bar (like a space bar) replaces the small inline Enter key.
    const big = el('div', 'krow enter-row');
    big.appendChild(key('key enter-big', 'ENTER', 'Enter', 'Enter'));
    kb.appendChild(big);
  }

  function tileFor(item) {
    const t = el('div', 'tile ' + item.cls);
    t.appendChild(el('span', 'ch', item.letter));
    if (item.rowN || item.colN) addArrows(t, item.rowN || 0, item.colN || 0);
    if (item.title) t.title = item.title;
    if (item.flipKey) t.dataset.flip = item.flipKey;
    if (item.tid) {
      // Interactive history chip: tap to fade/hide, long-press to highlight its letter.
      t.dataset.tid = item.tid;
      t.dataset.guess = item.guess;
      t.dataset.letter = item.letter;
      const m = S.marks[item.tid] || 0;
      if (m === 1) t.classList.add('faded');
      if (m === 2) t.classList.add('hidden-mark');
      if (S.hlGuess === item.guess || S.hlLetter === item.letter) t.classList.add('hl');
    }
    if (item.guess != null && S.fresh && S.fresh.id === item.guess) {
      t.classList.add('flip');
      t.style.setProperty('--d', `${item.pos * 90}ms`);
    }
    if (item.pop) t.classList.add('pop');
    return t;
  }

  const clueItem = (k, r, c, extra) => {
    const rowN = k.color === 'yellow' || k.color === 'orange' ? k.row_arrows : 0;
    const colN = k.color === 'red' || k.color === 'orange' ? k.col_arrows : 0;
    const counts = (rowN ? ` · ${rowN} in row` : '') + (colN ? ` · ${colN} in column` : '');
    return {
      cls: k.color, letter: k.letter, rowN, colN, guess: k.guess_id,
      pos: k.axis === 'down' ? r : c,
      title: `${k.letter} · guess ${k.guess_id} (${k.axis})${counts}`,
      ...extra,
    };
  };

  function layer(cls, items) {
    const wrap = el('div', cls);
    wrap.style.setProperty('--n', cls === 'chips' ? chipsPerRow(items.length) : items.length);
    items.forEach((it) => wrap.appendChild(tileFor(it)));
    return wrap;
  }

  function renderBoard() {
    const st = S.state;
    const board = $('board');
    const frag = document.createDocumentFragment();
    const active = live();
    const reveal = st.ended && !!st.solution;
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        const clues = st.cells[r][c] || [];
        const green = isGreen(clues);
        const inRow = active && r === st.row;
        const inCol = active && c === st.col;
        const cell = el('div', 'cell');
        cell.setAttribute('role', 'gridcell');
        cell.dataset.r = r; cell.dataset.c = c;
        if (inRow) cell.classList.add('in-row');
        if (inCol) cell.classList.add('in-col');
        if (S.notesMode && S.sel && S.sel[0] === r && S.sel[1] === c) cell.classList.add('selected');
        if (S.fresh && (S.fresh.rows.includes(r) || S.fresh.cols.includes(c)) && green) {
          cell.classList.add('cheer');
          cell.style.setProperty('--d', `${550 + (S.fresh.rows.includes(r) ? c : r) * 70}ms`);
        }

        // Two layers: `big` fills the square (green, reveal, the guess being typed, the
        // just-submitted guess, or a note); `chips` are small history tiles in the corner.
        let big = [];
        const chips = [];
        let note = null;
        // Letter being typed into this square. Row and column always share an index, so the
        // crossing is on the diagonal and gets the same letter from both directions: one tile.
        const typed = active && inRow && S.draft[c] ? { cls: 'draft', letter: S.draft[c], pop: S.typedIdx === c }
          : active && inCol && S.draft[r] ? { cls: 'draft', letter: S.draft[r], pop: S.typedIdx === r } : null;
        if (green) {
          const g = clues.find((x) => x.color === 'green');
          if (typed) {
            // Let the player type over a solved square; keep the green letter visible in the corner.
            big.push({ ...typed, cls: 'draft on-green' });
            chips.push({ cls: 'green', letter: g.letter, title: `Solved: ${g.letter}` });
          } else {
            big.push({ cls: 'green', letter: g.letter, guess: g.guess_id, pos: g.axis === 'down' ? r : c });
          }
        } else if (reveal) {
          // Game over: reveal the answer in every square that wasn't solved.
          big.push({ cls: 'reveal', letter: st.solution[r][c] });
        } else {
          const shown = dedupeCrossing(clues);
          for (let i = shown.length - 1; i >= 0; i--) {
            const k = shown[i];
            if (S.expanded === k.guess_id) big.push(clueItem(k, r, c, { flipKey: `${r},${c},${k.id}` }));
            else chips.push(clueItem(k, r, c, { tid: `${r},${c},${k.id}` }));
          }
          if (typed) big = [typed];
          if (!big.length) note = S.notes[`${r},${c}`] || null;
        }
        if (big.length && !green && !reveal && S.expanded != null && big[0].guess === S.expanded) cell.classList.add('expanded');
        if (chips.length) cell.appendChild(layer('chips', chips));
        if (big.length) cell.appendChild(layer('fill', big));
        else if (note) cell.appendChild(el('span', 'note' + (note.certain ? ' certain' : ''), note.letter));
        cell.setAttribute('aria-label', `Row ${r + 1}, column ${c + 1}. ` + (green ? `Solved: ${big[0].letter}.` : reveal ? `Answer: ${big[0].letter}.` : `${clues.length} clue${clues.length === 1 ? '' : 's'}.` + (note ? ` Note: ${note.letter}.` : '')));
        frag.appendChild(cell);
      }
    }
    board.replaceChildren(frag);
    board.classList.toggle('has-hl', S.hlGuess != null || S.hlLetter != null);
    board.classList.toggle('notes-mode', S.notesMode);
    board.setAttribute('aria-busy', S.busy ? 'true' : 'false');

    [...$('markCols').children].forEach((n, i) => n.classList.toggle('on', active && i === st.col));
    [...$('markRows').children].forEach((n, i) => n.classList.toggle('on', active && i === st.row));
  }

  function renderStatus() {
    const st = S.state;
    const g = $('guesses'), box = $('statGuesses');
    const prev = g.textContent;
    g.textContent = st.guesses_left;
    box.classList.toggle('low', st.guesses_left <= 3 && st.guesses_left > 1);
    box.classList.toggle('critical', st.guesses_left <= 1);
    if (prev !== String(st.guesses_left) && prev !== '–') { box.classList.remove('bump'); void box.offsetWidth; box.classList.add('bump'); }
    [...$('progress').children].forEach((n, i) => n.classList.toggle('on', i < st.words_solved));

  }

  function renderHistory() {
    const h = S.state.history;
    const box = $('history');
    $('hcount').textContent = h.length ? `${h.length}` : '';
    if (!h.length) { box.replaceChildren(el('div', 'empty', 'No guesses yet.')); return; }
    const frag = document.createDocumentFragment();
    h.forEach((item, i) => {
      const id = h.length - i;
      const b = el('button', 'hitem');
      b.type = 'button';
      b.dataset.guess = id;
      b.setAttribute('aria-pressed', S.hlGuess === id ? 'true' : 'false');
      b.setAttribute('aria-label', `Guess ${id}: ${item.word}. Highlight its tiles.`);
      b.appendChild(el('span', 'n', `#${id}`));
      b.appendChild(el('span', 'w', item.word));
      const solved = item.solved.map((s) => s.replace('column', 'col').replace(/^./, (x) => x.toUpperCase()));
      const txt = solved.length ? `${solved.join(', ')}${item.bonus_awarded ? ` · +${item.bonus_awarded}` : ''}` : 'Clues added';
      b.appendChild(el('span', 'r' + (solved.length ? ' good' : ''), txt));
      frag.appendChild(b);
    });
    box.replaceChildren(frag);
  }

  // ── Keyboard intelligence ────────────────────────────────
  // Every clue carries row_arrows / col_arrows = min(copies in the answer line, copies in the
  // guess), and 0 proves the letter is absent from that line. Combined with the green squares
  // this gives, per letter, a lower bound (and sometimes the exact count) for each line.
  function keyStates() {
    const st = S.state;
    const words = {};
    st.history.forEach((h, i) => { words[st.history.length - i] = h.word; });
    const copies = (w, L) => (w ? [...w].filter((x) => x === L).length : 0);
    const line = () => ({ min: 0, exact: null });
    const info = {}; // letter -> { guessed, black, row: [line×5], col: [line×5] }
    const get = (L) => (info[L] ||= { guessed: false, black: false, row: [0, 1, 2, 3, 4].map(line), col: [0, 1, 2, 3, 4].map(line) });
    const bound = (ln, n, exactKnown) => {
      ln.min = Math.max(ln.min, n);
      if (exactKnown) ln.exact = n;
    };
    for (const h of st.history) for (const L of h.word) get(L).guessed = true;
    for (let r = 0; r < SIZE; r++) for (let c = 0; c < SIZE; c++) {
      for (const k of st.cells[r][c]) {
        const I = get(k.letter), n = copies(words[k.guess_id], k.letter);
        if (k.color === 'black') I.black = true;
        // Across/cross tiles report the full min(answer, guess) for the row; down tiles only presence.
        const rowFull = k.axis !== 'down', colFull = k.axis !== 'across';
        bound(I.row[r], k.row_arrows, k.row_arrows === 0 || (rowFull && n > k.row_arrows));
        bound(I.col[c], k.col_arrows, k.col_arrows === 0 || (colFull && n > k.col_arrows));
      }
    }
    const greens = (L, axis, i) => {
      let g = 0;
      for (let j = 0; j < SIZE; j++) {
        const cell = axis === 'row' ? st.cells[i][j] : st.cells[j][i];
        if (cell.some((k) => k.color === 'green' && k.letter === L)) g++;
      }
      return g;
    };
    const solved = (axis, i) => (axis === 'row' ? st.solved_rows : st.solved_cols).includes(i);
    const out = {};
    for (const L of ROWS.join('')) {
      const I = info[L];
      if (!I || !I.guessed) { out[L] = ''; continue; }
      if (I.black) { out[L] = 'black'; continue; }
      const need = (axis, i) => I[axis][i].min > greens(L, axis, i);
      const done = (axis, i) => solved(axis, i) || (I[axis][i].exact != null && I[axis][i].exact <= greens(L, axis, i));
      const nr = need('row', st.row), nc = need('col', st.col);
      out[L] = nr && nc ? 'orange' : nr ? 'yellow' : nc ? 'red'
        : done('row', st.row) && done('col', st.col) ? 'gray' : 'white';
    }
    return out;
  }

  function renderKeyboard() {
    const states = keyStates();
    for (const b of $('keyboard').querySelectorAll('.key[data-k]')) {
      const k = b.dataset.k;
      if (k.length !== 1) { b.classList.toggle('enter-busy', S.busy && k === 'Enter'); continue; }
      const cls = states[k];
      b.className = 'key' + (cls ? ' ' + cls : '');
      b.setAttribute('aria-label', k + ({
        black: ', not in the grid', gray: ', ruled out for this row and column', yellow: ', needed in this row',
        red: ', needed in this column', orange: ', needed in this row and column', white: ', guessed',
      }[cls] || ''));
    }
  }

  function renderToolbar() {
    $('btnNotes').setAttribute('aria-pressed', S.notesMode ? 'true' : 'false');
    $('btnClearHl').hidden = S.hlGuess == null && S.hlLetter == null;
  }

  function render() {
    if (!S.state) return;
    renderStatus(); renderBoard(); renderHistory(); renderKeyboard(); renderToolbar();
  }

  function setLoading(on, message) {
    S.loading = on;
    const box = $('loading');
    box.hidden = !on;
    if (!on) return;
    box.replaceChildren();
    const inner = el('div');
    if (message) {
      inner.appendChild(el('div', null, message));
      const retry = el('button', 'btn', 'Try again');
      retry.type = 'button';
      retry.addEventListener('click', () => (S.gameId ? init() : startGame()));
      inner.appendChild(retry);
    } else {
      inner.appendChild(el('div', 'spinner'));
      inner.appendChild(document.createTextNode('Building your puzzle…'));
    }
    box.appendChild(inner);
  }

  // ── Game flow ────────────────────────────────────────────
  function adopt(id, state) {
    S.gameId = id; S.state = state; S.draft = ''; S.sel = null; S.fresh = null; S.quickNote = false;
    S.hlGuess = S.hlLetter = null; S.typedIdx = -1;
    clearTimeout(S.expTimer); S.expTimer = null; S.expanded = null;
    S.marks = store.json(KEYS.marks(id), {});
    S.notes = store.json(KEYS.notes(id), {});
    store.set(KEYS.game, id);
  }

  async function startGame() {
    const old = S.gameId;
    setLoading(true);
    try {
      const data = await api(TEST ? '/api/test-game' : '/api/game', { method: 'POST', body: '{}' });
      if (old) { store.del(KEYS.marks(old)); store.del(KEYS.notes(old)); }
      adopt(data.game_id, data.state);
      S.notesMode = false;
      setLoading(false);
      render();
      announce('New puzzle ready.');
    } catch (e) {
      S.gameId = null;
      setLoading(true, e.message || 'Could not start a puzzle.');
    }
  }

  async function init() {
    setLoading(true);
    const id = store.get(KEYS.game) || store.get(KEYS.legacy);
    if (id) {
      try {
        const data = await api(`/api/game/${encodeURIComponent(id)}`);
        store.del(KEYS.legacy);
        adopt(id, data.state);
        setLoading(false);
        render();
        maybeShowResult();
        return;
      } catch (e) {
        if (e.status === 404 || e.status === 410) {
          store.del(KEYS.game); store.del(KEYS.legacy);
          toast('Your last puzzle has expired. Here’s a fresh one.', 3200);
        } else {
          S.gameId = id;
          setLoading(true, e.message);
          return;
        }
      }
    }
    await startGame();
    if (S.state && !store.get(KEYS.seenHelp)) { store.set(KEYS.seenHelp, '1'); openDialog('dlgHelp'); }
  }

  function shakeActive() {
    document.querySelectorAll('.cell.in-row, .cell.in-col').forEach((c) => {
      c.classList.remove('shake'); void c.offsetWidth; c.classList.add('shake');
      c.addEventListener('animationend', () => c.classList.remove('shake'), { once: true });
    });
  }

  async function submit() {
    if (!live() || S.busy || S.loading) return;
    if (S.draft.length < SIZE) { toast('Not enough letters'); shakeActive(); return; }
    const word = S.draft;
    S.busy = true; renderKeyboard(); $('board').setAttribute('aria-busy', 'true');
    try {
      const data = await api(`/api/game/${S.gameId}/guess`, { method: 'POST', body: JSON.stringify({ word }) });
      const last = data.last_guess;
      const rows = [], cols = [];
      for (const s of last.solved) { const n = parseInt(s.split(' ')[1], 10) - 1; (s.startsWith('row') ? rows : cols).push(n); }
      S.state = data; S.draft = ''; S.typedIdx = -1;
      S.fresh = { id: data.history.length, rows, cols };
      S.busy = false;
      if (!data.ended) expandGuess(data.history.length);
      render();
      S.fresh = null;
      if (last.solved.length) {
        const what = last.solved.map((x) => x.replace(/^./, (ch) => ch.toUpperCase())).join(' · ');
        const n = last.solved.length;
        if (last.bonus_awarded) {
          celebrate(n > 1 ? `${n}× Combo! Guess Bonus!` : 'Guess Bonus!', `+${last.bonus_awarded} guess${last.bonus_awarded > 1 ? 'es' : ''} · ${what}`);
          const box = $('statGuesses'); box.classList.remove('gain'); void box.offsetWidth; box.classList.add('gain');
        } else if (!data.won) {
          celebrate(n > 1 ? `${n}× Combo!` : 'Word solved!', `${what} · bonus limit reached`);
        }
        announce(`Solved ${last.solved.join(' and ')}. ${data.guesses_left} guesses left.`);
      } else {
        announce(`Clues added. ${data.guesses_left} guesses left.`);
      }
      if (data.ended) { recordResult(); setTimeout(maybeShowResult, 1300); }
    } catch (e) {
      S.busy = false;
      if (e.status === 409 || e.status === 404) {
        // Our copy is out of date (finished in another tab, or expired). Re-sync.
        try {
          const d = await api(`/api/game/${S.gameId}`);
          S.state = d.state; S.draft = ''; render();
          if (d.state.ended) { recordResult(); maybeShowResult(); } else toast(e.message);
        } catch { toast('This puzzle is no longer available.'); startGame(); }
      } else {
        toast(e.message);
        if (e.status === 422) shakeActive();
        renderKeyboard(); $('board').setAttribute('aria-busy', 'false');
      }
    }
  }

  // ── Input ────────────────────────────────────────────────
  function addLetter(ch) {
    if (!live() || S.busy || S.loading) return;
    if (S.notesMode) { collapseExpanded(); setNote(ch); return; }
    if (S.draft.length >= SIZE) return;
    const type = () => { S.draft += ch; S.typedIdx = S.draft.length - 1; };
    // Starting the next guess shrinks the previous one away immediately (same animation).
    if (!collapseExpanded(type)) { type(); renderBoard(); }
  }
  function deleteLetter() {
    if (!live() || S.busy || S.loading) return;
    if (S.notesMode) { clearNote(); if (S.quickNote) endQuickNote(); return; }
    if (!S.draft) return;
    S.draft = S.draft.slice(0, -1); S.typedIdx = -1;
    renderBoard();
  }
  function pressKey(k) {
    if (k === 'Enter') {
      if (S.quickNote) endQuickNote();
      if (S.notesMode) { toast('Turn Notes off to submit a guess.'); return; }
      submit();
    }
    else if (k === 'Backspace') deleteLetter();
    else addLetter(k);
  }

  const persistNotes = () => store.set(KEYS.notes(S.gameId), JSON.stringify(S.notes));
  const persistMarks = () => store.set(KEYS.marks(S.gameId), JSON.stringify(S.marks));

  function setNote(ch) {
    if (!S.sel) { toast('Tap a square first, then type a letter.'); return; }
    const key = S.sel.join(',');
    const cur = S.notes[key];
    if (!cur || cur.letter !== ch) S.notes[key] = { letter: ch, certain: false };
    else if (!cur.certain) cur.certain = true;
    else delete S.notes[key];
    persistNotes();
    if (S.quickNote) endQuickNote(); else renderBoard();
  }
  function clearNote() {
    if (!S.sel) return;
    delete S.notes[S.sel.join(',')];
    persistNotes(); renderBoard();
  }

  // Tap a square (Notes off) -> quick note for that square; the next letter fills it and
  // notes switch off again. The Notes button still gives the sticky mode.
  function startQuickNote(r, c) {
    collapseExpanded();
    S.quickNote = true; S.notesMode = true; S.sel = [r, c];
    renderToolbar(); renderBoard();
  }
  function endQuickNote() {
    S.quickNote = false; S.notesMode = false; S.sel = null;
    renderToolbar(); renderBoard();
  }

  function toggleNotes() {
    if (S.quickNote) { S.quickNote = false; renderToolbar(); toast('Notes on: tap a square, then type a letter.', 2600); return; } // promote to sticky
    S.notesMode = !S.notesMode;
    if (!S.notesMode) S.sel = null;
    renderToolbar(); renderBoard();
    if (S.notesMode) toast('Notes on: tap a square, then type a letter.', 2600);
  }

  function setHighlight(kind, value) {
    if (kind === 'guess') { S.hlGuess = S.hlGuess === value ? null : value; S.hlLetter = null; }
    else { S.hlLetter = S.hlLetter === value ? null : value; S.hlGuess = null; }
    render();
  }
  const clearHighlight = () => { if (S.hlGuess != null || S.hlLetter != null) { S.hlGuess = S.hlLetter = null; render(); } };

  // ── Dialogs ──────────────────────────────────────────────
  function openDialog(id) { const d = $(id); if (d && !d.open) { d.showModal(); d.scrollTop = 0; } }
  const anyDialogOpen = () => !!document.querySelector('dialog[open]');

  function renderStats() {
    const pct = stats.played ? Math.round((stats.won / stats.played) * 100) : 0;
    const fill = (node, items) => {
      node.replaceChildren();
      for (const [v, l] of items) {
        const d = el('div'); d.appendChild(el('b', null, String(v))); d.appendChild(el('span', null, l)); node.appendChild(d);
      }
    };
    fill($('statNumbers'), [[stats.played, 'Played'], [pct, 'Win %'], [stats.streak, 'Streak'], [stats.max, 'Best streak']]);
    fill($('statNumbers2'), [
      [stats.won, 'Wins'], [stats.best ?? '–', 'Fewest guesses'],
      [stats.won ? (stats.totalWinGuesses / stats.won).toFixed(1) : '–', 'Avg guesses'],
    ]);
  }

  function recordResult() {
    const st = S.state;
    if (TEST || !st?.ended || stats.last === S.gameId) return; // test games don't touch real stats
    stats.last = S.gameId;
    stats.played += 1;
    if (st.won) {
      const used = st.history.length;
      stats.won += 1; stats.streak += 1; stats.max = Math.max(stats.max, stats.streak);
      stats.best = stats.best == null ? used : Math.min(stats.best, used);
      stats.totalWinGuesses += used;
    } else {
      stats.streak = 0;
    }
    store.set(KEYS.stats, JSON.stringify(stats));
  }

  function shareText() {
    const st = S.state;
    const used = st.history.length;
    const head = st.won ? `Squardle · solved all 10 words in ${used} guesses` : `Squardle · solved ${st.words_solved}/10 words`;
    const grid = st.cells.map((row) => row.map((c) => (isGreen(c) ? '🟩' : '⬛')).join('')).join('\n');
    return `${head} (${st.bonus_earned}/8 bonus)\n${grid}\n${location.origin}`;
  }

  function maybeShowResult() {
    const st = S.state;
    if (!st?.ended || S.resultShownFor === S.gameId) return;
    S.resultShownFor = S.gameId;
    recordResult();
    $('resEmoji').textContent = st.won ? '🏆' : '🧩';
    $('resTitle').textContent = st.won ? 'Solved!' : 'Out of guesses';
    $('resSub').textContent = st.won
      ? `All ten words in ${st.history.length} guesses.`
      : `You solved ${st.words_solved} of 10 words. Here’s the answer.`;
    const grid = $('resGrid'); grid.replaceChildren();
    if (st.solution) {
      for (let r = 0; r < SIZE; r++) for (let c = 0; c < SIZE; c++) {
        grid.appendChild(el('i', isGreen(st.cells[r][c]) ? '' : 'miss', st.solution[r][c]));
      }
    }
    renderStats();
    openDialog('dlgResult');
  }

  function flash(btn, text, ms = 1600) {
    const old = btn.textContent;
    btn.textContent = text; btn.disabled = true;
    setTimeout(() => { btn.textContent = old; btn.disabled = false; }, ms);
  }

  async function share() {
    const text = shareText();
    try {
      if (navigator.share) { await navigator.share({ text }); return; }
      await navigator.clipboard.writeText(text);
      flash($('btnShare'), 'Copied!');
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      flash($('btnShare'), 'Couldn’t copy', 2200);
    }
  }

  function requestNewGame() {
    if (live() && S.state.history.length > 0) openDialog('dlgNew');
    else startGame();
  }

  function applySettings() {
    document.documentElement.dataset.cb = S.settings.cb ? '1' : '0';
    $('optCb').checked = S.settings.cb;
  }

  // ── Wiring ───────────────────────────────────────────────
  function wire() {
    // mousedown preventDefault keeps focus off the on-screen keys.
    const kb = $('keyboard');
    kb.addEventListener('mousedown', (e) => e.preventDefault());
    kb.addEventListener('click', (e) => { const b = e.target.closest('.key[data-k]'); if (b) pressKey(b.dataset.k); });

    // Pointer-activated buttons shouldn't keep focus, so a later Enter can't re-trigger them.
    document.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (b && e.detail > 0 && !b.closest('dialog')) b.blur();
    });

    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey || anyDialogOpen()) return;
      const a = document.activeElement;
      if (e.key === 'Enter') {
        // Defer only to a button reached by keyboard navigation.
        if (a && a.tagName === 'BUTTON' && a.matches(':focus-visible')) return;
        e.preventDefault(); pressKey('Enter');
      } else if (e.key === 'Backspace') { e.preventDefault(); pressKey('Backspace'); }
      else if (e.key === 'Escape') { clearHighlight(); if (S.quickNote) endQuickNote(); else if (S.sel) { S.sel = null; renderBoard(); } }
      else if (/^[a-zA-Z]$/.test(e.key)) {
        e.preventDefault(); pressKey(e.key.toUpperCase());
      }
    });

    // Board: tap = fade/hide a tile (or select a square in Notes mode); long-press / right-click = highlight letter.
    const board = $('board');
    let pressTimer = null;
    const cancelPress = () => { clearTimeout(pressTimer); pressTimer = null; };
    board.addEventListener('pointerdown', (e) => {
      const t = e.target.closest('.tile[data-tid]');
      if (!t || S.notesMode) return;
      cancelPress();
      pressTimer = setTimeout(() => { S.suppressClick = true; setHighlight('letter', t.dataset.letter); }, 450);
    });
    ['pointerup', 'pointerleave', 'pointercancel', 'pointermove'].forEach((ev) => board.addEventListener(ev, cancelPress));
    board.addEventListener('contextmenu', (e) => {
      const t = e.target.closest('.tile[data-tid]');
      if (t) { e.preventDefault(); cancelPress(); setHighlight('letter', t.dataset.letter); }
    });
    board.addEventListener('click', (e) => {
      if (S.suppressClick) { S.suppressClick = false; return; }
      const cell = e.target.closest('.cell');
      if (!cell || !S.state) return;
      const r = +cell.dataset.r, c = +cell.dataset.c;
      const same = S.sel && S.sel[0] === r && S.sel[1] === c;
      if (S.quickNote) {
        // Tap the same square again to cancel; tap another unsolved square to move the note there.
        if (same || isGreen(S.state.cells[r][c])) endQuickNote();
        else { S.sel = [r, c]; renderBoard(); }
        return;
      }
      if (S.notesMode) {
        if (isGreen(S.state.cells[r][c])) { toast('That square is already solved.'); return; }
        S.sel = same ? null : [r, c];
        renderBoard();
        return;
      }
      const tile = e.target.closest('.tile[data-tid]');
      if (!tile) {
        // Tapping a square (not one of its clue chips) opens a one-letter note for it.
        if (live() && !S.busy && !isGreen(S.state.cells[r][c])) startQuickNote(r, c);
        return;
      }
      const id = tile.dataset.tid;
      const next = ((S.marks[id] || 0) + 1) % 3;
      if (next === 0) delete S.marks[id]; else S.marks[id] = next;
      tile.classList.toggle('faded', next === 1);
      tile.classList.toggle('hidden-mark', next === 2);
      persistMarks();
    });

    $('history').addEventListener('click', (e) => {
      const b = e.target.closest('.hitem');
      if (b) setHighlight('guess', +b.dataset.guess);
    });
    $('btnClearHl').addEventListener('click', clearHighlight);
    $('btnNotes').addEventListener('click', toggleNotes);
    $('btnNew').addEventListener('click', requestNewGame);
    $('btnHelp').addEventListener('click', () => openDialog('dlgHelp'));
    $('btnStats').addEventListener('click', () => { renderStats(); openDialog('dlgStats'); });
    $('btnSettings').addEventListener('click', () => openDialog('dlgSettings'));
    $('btnShare').addEventListener('click', share);
    $('btnPlayAgain').addEventListener('click', () => { $('dlgResult').close(); startGame(); });
    $('btnConfirmNew').addEventListener('click', () => { $('dlgNew').close(); startGame(); });
    $('btnResetMarks').addEventListener('click', (e) => {
      if (!S.gameId) return;
      S.marks = {}; persistMarks(); if (S.state) renderBoard(); flash(e.currentTarget, 'Done ✓');
    });
    $('optCb').addEventListener('change', (e) => { S.settings.cb = e.target.checked; store.set(KEYS.settings, JSON.stringify(S.settings)); applySettings(); });

    document.querySelectorAll('dialog').forEach((d) => {
      d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
      d.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => d.close()));
      // Browsers return focus to the opener on close; drop it so the next Enter submits a guess
      // instead of re-activating that button.
      d.addEventListener('close', () => { const a = document.activeElement; if (a && a !== document.body && !d.contains(a)) a.blur(); });
    });
  }

  // ── Boot ─────────────────────────────────────────────────
  Object.assign(S.settings, store.json(KEYS.settings, {}));
  applySettings();
  buildStatic();
  wire();
  init();
})();
