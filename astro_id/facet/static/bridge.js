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
  function facetTarget(ctx) {
    if (!ctx) return null;
    const s = ctx.scope || {};
    if (s.kind === "selection" && ctx.selection && ctx.selection.length) {
      return {kind: "selection", key: "selection:" + ctx.selection.join("\n"), paths: ctx.selection.slice(0, MAX_SELECTION)};
    }
    if (ctx.album_id != null && /^\d+$/.test(String(ctx.album_id))) {
      return {kind: "album", key: "album:" + Number(ctx.album_id), album_id: Number(ctx.album_id)};
    }
    const f = folderOfPath(ctx.photo);
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

  /* fetch wrapper: relative URLs only, X-Addon-Nonce on every request, JSON in and out; no credentials. */
  function makeApi(fetchFn, nonce) {
    function headers(extra) {
      const h = Object.assign({}, extra || {});
      if (nonce) h["X-Addon-Nonce"] = nonce;
      return h;
    }
    async function send(path, opt) {
      if (/^[a-z]+:|^\/\//i.test(path) || path.charAt(0) === "/") throw new Error("relative URLs only");
      try { return await fetchFn(path, opt); } catch (e) { throw new Error(STALE); }
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
      if (!r.ok) throw new Error(stale(r, doc) ? STALE : (doc.error || doc.detail || ("HTTP " + r.status)));
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
          sanitizeView: sanitizeView, folderOfPath: folderOfPath, facetTarget: facetTarget};
});
