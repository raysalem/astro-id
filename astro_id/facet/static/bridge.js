/* astro-id <-> Facet bridge, postMessage v1 (PLUGIN_BOUNDARY.md v0.2 §8).
 *
 * The panel runs in a null-origin sandboxed iframe: no storage, no cookies, no popups, no downloads, no form
 * submits. The per-open nonce arrives once in the document URL (?nonce=) and is kept in memory only; every fetch
 * to the add-on backend (through Facet's proxy) carries it as X-Addon-Nonce. Messages from the host are accepted
 * only when event.source is window.parent and the nonce matches.
 *
 * Plain ES2017, no build step. The page inlines this file; Node's test runner loads it with require().
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AstroBridge = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const V = 1;
  const MAX_SELECTION = 500;
  const MAX_QUERY = 2048;
  const NONCE_RE = /^[A-Za-z0-9_\-.~]{8,256}$/;
  /* open_map: the host opens one of these fixed sites in a new tab (the sandbox forbids popups); a key, never a URL. */
  const MAP_SITES = {earth: "https://earth.google.com/web/", maps: "https://www.google.com/maps/"};

  function readNonce(search) {
    let n = null;
    try { n = new URLSearchParams(search || "").get("nonce"); } catch (e) { return null; }
    return n && NONCE_RE.test(n) ? n : null;
  }

  function constEq(a, b) {
    if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
    let d = 0;
    for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return d === 0;
  }

  function strList(x, max) {
    return Array.isArray(x) ? x.filter((p) => typeof p === "string" && p).slice(0, max) : [];
  }

  /* Keep only the fields spec §8 defines; anything else the host sends is dropped. */
  function sanitizeContext(d) {
    const s = d.scope && typeof d.scope === "object" ? d.scope : {};
    let scope = null;
    if (s.kind === "import" && (typeof s.run_id === "string" || typeof s.run_id === "number")) {
      scope = {kind: "import", run_id: String(s.run_id)};
    } else if (s.kind === "view") {
      const q = s.query;
      if (typeof q === "string" && q.length <= MAX_QUERY) scope = {kind: "view", query: q};
      else if (q && typeof q === "object" && JSON.stringify(q).length <= MAX_QUERY) scope = {kind: "view", query: q};
      else if (q == null) scope = {kind: "view", query: ""};
    } else if (s.kind === "selection") {
      scope = {kind: "selection"};
    }
    // Facet's PanelScope may name the folder the panel was opened on (optional, spec v0.3).
    if (scope && typeof s.path_prefix === "string" && s.path_prefix && s.path_prefix.length <= 4096) {
      scope.path_prefix = s.path_prefix;
    }
    return {
      route: typeof d.route === "string" ? d.route : null,
      album_id: d.album_id == null ? null : d.album_id,
      photo: typeof d.photo === "string" && d.photo ? d.photo : null,
      selection: strList(d.selection, MAX_SELECTION),
      scope: scope,
    };
  }

  /* Bridge context -> the backend's step-1 request (POST api/scope), or null when there is nothing to load. */
  function scopeRequest(ctx, category) {
    if (!ctx || !ctx.scope) return null;
    const s = ctx.scope;
    if (s.kind === "import") return {kind: "import", run_id: s.run_id};
    // A gallery "view" always carries default filters (sort, hides...), so it usually means the whole library.
    // Astro works on folders: opened by hand, the user picks the folder in step 1.
    if (s.kind === "view") return null;
    if (s.kind === "selection") {
      const paths = ctx.selection.length ? ctx.selection : (ctx.photo ? [ctx.photo] : []);
      return paths.length ? {kind: "selection", paths: paths.slice(0, MAX_SELECTION)} : null;
    }
    return null;
  }

  /* Same photo set? (context is re-sent on every change; only a new scope reloads step 1) */
  function scopeKey(req) {
    if (!req) return "";
    if (req.kind === "import") return "import:" + req.run_id;
    if (req.kind === "view") return "view:" + (typeof req.query === "string" ? req.query : JSON.stringify(req.query));
    return "selection:" + req.paths.join("\n");
  }

  /* The folder of a Facet photo path, as Facet stores it (its own separator kept), ending in a separator so
   * "astro" never also matches "astro-annotated". {prefix, name} or null. */
  function folderOfPath(p) {
    if (typeof p !== "string" || !p) return null;
    const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
    if (i <= 0) return null;
    const dir = p.slice(0, i);
    const name = dir.split(/[\\/]/).filter(Boolean).pop() || dir;
    return {prefix: dir + p.charAt(i), name: name};
  }

  /* What Facet is showing, as a thing the panel can load with one click (the top "Load ..." button), or null.
   * Order: photos ticked in the Gallery > the album being viewed (/album/<id>) > the open photo's folder
   * (/photo?path=) > the import the panel was opened from > the folder filter the panel was opened on.
   * The live context carries the route, the album id and the open photo, not the folder being browsed: Facet strips
   * the query (?path_prefix=, ?prefix=) from `route`, so a browsed folder is known only from the open photo's path or
   * from the scope the panel was opened with. */
  /* The album id of a context: its album_id, else an /album/<id> route (also with an auxiliary outlet such as
   * "/album/42(aux:photo)", which Facet's own albumIdOf does not read). */
  function albumOf(ctx) {
    if (!ctx) return null;
    if (ctx.album_id != null && /^\d+$/.test(String(ctx.album_id))) return Number(ctx.album_id);
    const m = /^\/album\/(\d+)(?:[\/(]|$)/.exec(typeof ctx.route === "string" ? ctx.route : "");
    return m ? Number(m[1]) : null;
  }

  /* lastAlbumId: the album the user viewed last. A photo opened from an album arrives as /photo?path= with no
   * album (Facet does not pass it), so the panel offers that album for the photo (setups are kept per album) once
   * it has checked the photo is in it; the returned target then carries `photo` and `folder` for that check and
   * for the fallback. */
  function facetTarget(ctx, lastAlbumId) {
    if (!ctx) return null;
    const s = ctx.scope || {};
    if (s.kind === "selection" && ctx.selection && ctx.selection.length) {
      return {kind: "selection", key: "selection:" + ctx.selection.join("\n"), paths: ctx.selection.slice(0, MAX_SELECTION)};
    }
    const aid = albumOf(ctx);
    if (aid != null) return {kind: "album", key: "album:" + aid, album_id: aid};
    const f = folderOfPath(ctx.photo);
    if (f && lastAlbumId != null && /^\d+$/.test(String(lastAlbumId))) {
      return {kind: "album", key: "album:" + Number(lastAlbumId), album_id: Number(lastAlbumId), photo: ctx.photo,
              folder: {kind: "folder", key: "folder:" + f.prefix, prefix: f.prefix, name: f.name}};
    }
    if (f) return {kind: "folder", key: "folder:" + f.prefix, prefix: f.prefix, name: f.name};
    if (s.kind === "import") return {kind: "import", key: "import:" + s.run_id, run_id: s.run_id};
    let pre = s.path_prefix || null;
    if (!pre && s.kind === "view" && typeof s.query === "string") {
      try { pre = new URLSearchParams(s.query.replace(/^\?/, "")).get("path_prefix"); } catch (e) { pre = null; }
    } else if (!pre && s.kind === "view" && s.query && typeof s.query === "object" && typeof s.query.path_prefix === "string") {
      pre = s.query.path_prefix;
    }
    if (pre) {
      const sep = pre.indexOf("\\") >= 0 ? "\\" : "/";
      const p = /[\\/]$/.test(pre) ? pre : pre + sep;
      const name = p.split(/[\\/]/).filter(Boolean).pop() || p;
      return {kind: "folder", key: "folder:" + p, prefix: p, name: name};
    }
    return null;
  }

  /* The album the panel may offer for a photo opened from an album: the last album seen in a context. Facet passes
   * no album with a photo, so the memory is only valid while the user stays in the album/photo part of Facet: a
   * context with neither an album nor a photo (Albums list, Add-ons page, Gallery) means they left, so forget it. */
  function nextLastAlbum(ctx, last) {
    const aid = albumOf(ctx);
    if (aid != null) return aid;
    if (ctx && ctx.photo) return last == null ? null : last;
    return null;
  }

  /* The panel's top status, from what Facet shows (a facetTarget or null) and the key of what is loaded ("" = nothing):
   *   nothing  - nothing loaded, Facet shows something loadable  -> "Nothing loaded yet." + Load button
   *   empty    - nothing loaded, Facet shows nothing loadable    -> "Open an album, a folder or a photo..."
   *   current  - the loaded set IS what Facet shows              -> "Loaded: ... (check)"
   *   other    - loaded, Facet shows another loadable thing      -> "Loaded: ..." + "Facet is showing: ..." + Load instead
   *   away     - loaded, Facet shows nothing loadable            -> "Loaded: ..." + the open/pick hint */
  function loadState(target, loadedKey) {
    if (!loadedKey) return target ? "nothing" : "empty";
    if (!target) return "away";
    return target.key === loadedKey ? "current" : "other";
  }

  /* The set the add-on already holds (api/loaded .loaded: key, label, album, via "photos"|"scope", body), as the
   * page's own load variables, so a panel opened, reloaded or restarted later starts at "Loaded: <label>" (B-P9).
   * lastAlbumId: a held album is also "the album viewed last", so a photo of it opened next is that album. Null when
   * nothing usable is held. */
  function restoredLoad(meta) {
    if (!meta || typeof meta !== "object" || typeof meta.key !== "string" || !meta.key) return null;
    const label = typeof meta.label === "string" ? meta.label : "";
    const a = meta.album && /^\d+$/.test(String(meta.album.id)) ? {id: Number(meta.album.id), name: String(meta.album.name || "")} : null;
    const body = meta.body && typeof meta.body === "object" ? meta.body : {};
    const out = {loadedKey: meta.key, loadedLabel: label, loadedAlbum: a, lastManual: null, scopeReq: null,
                 lastAlbumId: a ? a.id : null};
    if (meta.via === "scope") out.scopeReq = body;
    else out.lastManual = [body, label, a, meta.key];
    return out;
  }

  /* annotation_view (PLUGIN_BOUNDARY.md §6c): how Facet draws this add-on's sky labels. Only the contract's keys,
   * with the right types, go out; `visible` defaults to false (labels start hidden). */
  const KEY_RE = /^[A-Za-z0-9_.-]{1,40}$/;
  function num(x) { return typeof x === "number" && isFinite(x); }
  function sanitizeView(v) {
    v = v && typeof v === "object" ? v : {};
    const out = {visible: v.visible === true};
    if (v.groups && typeof v.groups === "object") {
      const g = {};
      Object.keys(v.groups).slice(0, 20).forEach(function (k) {
        if (KEY_RE.test(k) && typeof v.groups[k] === "boolean") g[k] = v.groups[k];
      });
      out.groups = g;
    }
    if (v.score === null || num(v.score)) out.score = v.score;
    if (v.metrics && typeof v.metrics === "object") {
      const m = {};
      Object.keys(v.metrics).slice(0, 10).forEach(function (k) { if (KEY_RE.test(k) && num(v.metrics[k])) m[k] = v.metrics[k]; });
      out.metrics = m;
    }
    if (typeof v.show_scores === "boolean") out.show_scores = v.show_scores;
    if (typeof v.show_members === "boolean") out.show_members = v.show_members;
    return out;
  }

  /* opts: {win, parent, nonce, onContext(ctx)}. Without a nonce (standalone page) the bridge stays inert. */
  function createBridge(opts) {
    const win = opts.win, parent = opts.parent, nonce = opts.nonce;
    const active = !!(nonce && parent && parent !== win);
    let ctx = null;

    function post(msg) {
      if (!active) return false;
      const m = Object.assign({}, msg, {v: V, nonce: nonce});
      parent.postMessage(m, "*");   // an opaque origin cannot name the host's origin (spec §8)
      return true;
    }

    function onMessage(ev) {
      if (!active || !ev || ev.source !== parent) return false;
      const d = ev.data;
      if (!d || typeof d !== "object" || d.v !== V || d.type !== "context") return false;
      if (!constEq(d.nonce, nonce)) return false;
      ctx = sanitizeContext(d);
      if (opts.onContext) opts.onContext(ctx);
      return true;
    }

    if (active && win && win.addEventListener) win.addEventListener("message", onMessage);

    return {
      active: active,
      handle: onMessage,
      context: function () { return ctx; },
      ready: function () { return post({type: "ready"}); },
      showPhotos: function (paths) { return post({type: "show_photos", paths: strList(paths, MAX_SELECTION)}); },
      openPhoto: function (path) { return typeof path === "string" && path ? post({type: "open_photo", path: path}) : false; },
      refresh: function (paths) { return post({type: "refresh", paths: strList(paths, 5000)}); },
      openMap: function (site) {
        return Object.prototype.hasOwnProperty.call(MAP_SITES, site) ? post({type: "open_map", site: site}) : false;
      },
      annotationView: function (view) { return post({type: "annotation_view", view: sanitizeView(view)}); },
      close: function () { return post({type: "close"}); },
    };
  }

  /* Facet or the add-on restarted under an open panel: with the add-on gone, Facet answers the CORS preflight 404
     and fetch rejects with a bare "Failed to fetch"; once it is back, this panel's nonce is stale (403). Either
     way the panel is dead and a running solve (held in the add-on's memory) is lost: say so, in plain words. */
  const STALE = "astro-id is not answering: Facet or the add-on restarted, so this panel is out of date and a " +
                "running solve was lost. Close and reopen the Astro panel, then solve again.";

  const HICCUP = "Astro did not answer just now (network hiccup). Trying again.";

  /* Two versions of the "YYYY-MM-DD.n" form (a missing part counts as 0): -1, 0 or 1. */
  function compareVersions(a, b) {
    const parts = function (v) {
      const m = /^(\d{4})-(\d{2})-(\d{2})(?:\.(\d+))?$/.exec(String(v || ""));
      return m ? [+m[1], +m[2], +m[3], +(m[4] || 0)] : null;
    };
    const x = parts(a), y = parts(b);
    if (!x || !y) return 0;
    for (let i = 0; i < 4; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
    return 0;
  }

  /* Self-update (serve.py api/restart, keeper.py). Words the panel shows: */
  const UPDATE_NOTE = "Updating Astro…";
  const UPDATE_MANUAL = "Astro needs one restart to update: on Facet's Add-ons page click Stop, then Open.";
  const UPDATE_REOPEN = "Astro was updated: close and reopen the Astro panel.";
  const UPDATE_WAIT = {solve: "Astro will update after this solve.", export: "Astro will update after this export.",
                       unsaved: "Astro will update after you save these results to Facet."};
  // B-P10: the new code on disk did not start in its trial run; the running add-on keeps the previous version
  const UPDATE_FAILED = "An update to Astro is not ready yet; still running the previous version.";
  // the new version passed its trial run but stopped right after the switch: the keeper answers on its own
  const UPDATE_BROKEN = "An update to Astro did not start; Astro will try again as soon as its files change.";
  // the add-on did not answer at all after an update (an add-on without the safe-update keeper)
  const UPDATE_LOST = "Astro did not come back after updating: on Facet's Add-ons page click Stop, then Open.";

  /* What the panel does about the running add-on's code, from api/state (`st`), this page's version and what it
   * already tried ({restarts: n, reloaded: bool}). {action, message?, busy?}:
   *   none    - same code
   *   restart - the add-on's code changed on disk (and passed its trial run), or this page is newer: POST
   *             api/restart, wait for a new boot
   *   wait    - same, but a solve / export runs: say so, ask again when it ends
   *   pending - new code on disk is settling or being tried: nothing shown, ask again in a few seconds
   *   failed  - the new code did not start: one calm line (message), keep working, ask again now and then
   *   reload  - the add-on is newer than this page: reload the page (once)
   *   manual  - it cannot be done automatically: one sentence (an add-on too old to restart itself, or a restart /
   *             reload that did not help) */
  function updatePlan(st, pageVersion, tried) {
    st = st || {}; tried = tried || {};
    const u = st.update && typeof st.update === "object" ? st.update : null;
    const differs = st.page_version !== pageVersion;
    const changed = !!(u && u.supported && u.code_changed);
    const serverNewer = differs && !changed && compareVersions(st.page_version, pageVersion) > 0;
    if (u && u.supported && u.fallback) return {action: "failed", message: UPDATE_BROKEN, fallback: true};
    // the running add-on is newer than this page: reload first (it says the rest once reloaded)
    if (u && u.supported && u.update_failed && !serverNewer) return {action: "failed", message: UPDATE_FAILED};
    if (u && u.supported && u.pending && !changed && !serverNewer) return {action: "pending"};
    if (!differs && !changed) return {action: "none"};
    if (!changed && compareVersions(st.page_version, pageVersion) > 0) {
      return tried.reloaded ? {action: "manual", message: UPDATE_REOPEN, note: "the add-on has newer code than this panel"}
                            : {action: "reload"};
    }
    if (!u || !u.supported) {
      return differs ? {action: "manual", message: UPDATE_MANUAL, note: "this panel is newer than the running add-on"}
                     : {action: "none"};
    }
    if (!changed || (tried.restarts || 0) >= 2) {   // a restart that did not bring the same code: stop trying
      return differs ? {action: "manual", message: UPDATE_MANUAL, note: "the add-on did not pick up the new code"}
                     : {action: "none"};
    }
    if (u.busy) return {action: "wait", busy: u.busy, message: UPDATE_WAIT[u.busy] || UPDATE_WAIT.solve};
    // This page is older than the code on disk too, so it reloads after the restart, and a reloaded page does not
    // show the last solve's results: ask for Save to Facet first rather than hide them.
    if (u.unsaved && u.disk_page_version && u.disk_page_version !== pageVersion) {
      return {action: "wait", busy: "unsaved", message: UPDATE_WAIT.unsaved};
    }
    return {action: "restart"};
  }

  /* api/restart said no ({restart: false, reason, busy?}): what to show. */
  function restartRefused(r) {
    if (r.reason === "busy") return {action: "wait", busy: r.busy, message: UPDATE_WAIT[r.busy] || UPDATE_WAIT.solve};
    if (r.reason === "unsaved") return {action: "wait", busy: "unsaved", message: UPDATE_WAIT.unsaved};
    if (r.reason === "unsupported") return {action: "manual", message: UPDATE_MANUAL, note: ""};
    if (r.reason === "update_failed") return {action: "failed", message: UPDATE_FAILED};
    if (r.reason === "pending") return {action: "pending"};
    return {action: "none"};   // current: nothing to update after all
  }

  /* After api/restart: ask api/state until it answers from a new process (update.boot differs). Requests made while
   * the add-on restarts wait in its listen queue or fail; both just mean "again". The new state, or null after
   * opts.timeoutMs. opts.sleep (tests) replaces setTimeout. */
  async function waitForBoot(getState, oldBoot, opts) {
    opts = opts || {};
    const sleep = opts.sleep || function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    const now = opts.now || function () { return Date.now(); };
    const end = now() + (opts.timeoutMs || 45000);
    for (;;) {
      await sleep(opts.delayMs || 600);
      try {
        const st = await getState();
        if (st && st.update && st.update.boot && st.update.boot !== oldBoot) return st;
      } catch (e) { /* restarting */ }
      if (now() >= end) return null;
    }
  }

  /* The page's own URL with astro_reloaded=1 (the nonce stays: Facet's proxy accepts it on the document request). */
  function reloadUrl(href) {
    const u = String(href || "");
    if (/[?&]astro_reloaded=1(&|#|$)/.test(u)) return u;
    const i = u.indexOf("#"), base = i >= 0 ? u.slice(0, i) : u, hash = i >= 0 ? u.slice(i) : "";
    return base + (base.indexOf("?") >= 0 ? "&" : "?") + "astro_reloaded=1" + hash;
  }

  /* A poll loop that never overlaps itself: the next call is scheduled only after the previous one finished
   * (setTimeout chain, not setInterval). fn returns false to stop the loop. timers: {set, clear} (tests). */
  function makePoller(fn, ms, timers) {
    const T = timers || {set: function (f, d) { return setTimeout(f, d); }, clear: function (t) { clearTimeout(t); }};
    let running = false, handle = null;
    async function tick() {
      handle = null;
      if (!running) return;
      let again = true;
      try { again = (await fn()) !== false; } catch (e) { again = true; }
      if (running && again) handle = T.set(tick, ms); else running = false;
    }
    return {
      start: function () { if (running) return false; running = true; handle = T.set(tick, ms); return true; },
      stop: function () { running = false; if (handle !== null) T.clear(handle); handle = null; },
      get running() { return running; },
    };
  }

  /* fetch wrapper: relative URLs only, X-Addon-Nonce on every request, JSON in and out; no credentials. */
  function makeApi(fetchFn, nonce) {
    function headers(extra) {
      const h = Object.assign({}, extra || {});
      if (nonce) h["X-Addon-Nonce"] = nonce;
      return h;
    }
    // A fetch that rejects once is a hiccup (e.transient: poll again); two in a row, or a stale nonce, means the
    // add-on or Facet restarted (e.stale: the panel is dead).
    let fails = 0;
    async function send(path, opt) {
      if (/^[a-z]+:|^\/\//i.test(path) || path.charAt(0) === "/") throw new Error("relative URLs only");
      try { const r = await fetchFn(path, opt); fails = 0; return r; }
      catch (e) {
        fails += 1;
        const err = new Error(fails >= 2 ? STALE : HICCUP);
        if (fails >= 2) err.stale = true; else err.transient = true;
        throw err;
      }
    }
    // Facet's own refusals carry {"detail": ...}; the add-on's carry {"error": ...}.
    function stale(r, doc) { return r.status === 403 && /stale panel nonce/i.test((doc && (doc.detail || doc.error)) || ""); }
    async function call(path, body) {
      const opt = body === undefined
        ? {method: "GET", headers: headers(), credentials: "omit"}
        : {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(body),
           credentials: "omit"};
      const r = await send(path, opt);
      const doc = await r.json().catch(function () { return {error: "HTTP " + r.status}; });
      if (!r.ok) {
        const err = new Error(stale(r, doc) ? STALE : (doc.error || doc.detail || ("HTTP " + r.status)));
        if (stale(r, doc)) err.stale = true;
        throw err;
      }
      return doc;
    }
    async function blob(path) {
      const r = await send(path, {method: "GET", headers: headers(), credentials: "omit"});
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    }
    return {call: call, blob: blob};
  }

  return {V: V, MAP_SITES: MAP_SITES, readNonce: readNonce, constEq: constEq, sanitizeContext: sanitizeContext,
          scopeRequest: scopeRequest, scopeKey: scopeKey, createBridge: createBridge, makeApi: makeApi,
          sanitizeView: sanitizeView, compareVersions: compareVersions, makePoller: makePoller, STALE: STALE, folderOfPath: folderOfPath, facetTarget: facetTarget, albumOf: albumOf,
          nextLastAlbum: nextLastAlbum, loadState: loadState, restoredLoad: restoredLoad, updatePlan: updatePlan, restartRefused: restartRefused,
          waitForBoot: waitForBoot, reloadUrl: reloadUrl, UPDATE_NOTE: UPDATE_NOTE, UPDATE_MANUAL: UPDATE_MANUAL,
          UPDATE_REOPEN: UPDATE_REOPEN, UPDATE_WAIT: UPDATE_WAIT, UPDATE_FAILED: UPDATE_FAILED,
          UPDATE_BROKEN: UPDATE_BROKEN, UPDATE_LOST: UPDATE_LOST};
});
