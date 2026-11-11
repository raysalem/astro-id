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
    if (s.kind === "import") return {kind: "import", run_id: s.run_id, category: category || ""};
    if (s.kind === "view") return {kind: "view", query: s.query == null ? "" : s.query};
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
      close: function () { return post({type: "close"}); },
    };
  }

  /* fetch wrapper: relative URLs only, X-Addon-Nonce on every request, JSON in and out; no credentials. */
  function makeApi(fetchFn, nonce) {
    function headers(extra) {
      const h = Object.assign({}, extra || {});
      if (nonce) h["X-Addon-Nonce"] = nonce;
      return h;
    }
    async function call(path, body) {
      if (/^[a-z]+:|^\/\//i.test(path) || path.charAt(0) === "/") throw new Error("relative URLs only");
      const opt = body === undefined
        ? {method: "GET", headers: headers(), credentials: "omit"}
        : {method: "POST", headers: headers({"Content-Type": "application/json"}), body: JSON.stringify(body),
           credentials: "omit"};
      const r = await fetchFn(path, opt);
      const doc = await r.json().catch(function () { return {error: "HTTP " + r.status}; });
      if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
      return doc;
    }
    async function blob(path) {
      if (/^[a-z]+:|^\/\//i.test(path) || path.charAt(0) === "/") throw new Error("relative URLs only");
      const r = await fetchFn(path, {method: "GET", headers: headers(), credentials: "omit"});
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    }
    return {call: call, blob: blob};
  }

  return {V: V, MAP_SITES: MAP_SITES, readNonce: readNonce, constEq: constEq, sanitizeContext: sanitizeContext,
          scopeRequest: scopeRequest, scopeKey: scopeKey, createBridge: createBridge, makeApi: makeApi};
});
