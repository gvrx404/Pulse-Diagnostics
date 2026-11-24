/* Pulse local Qt Chromium bridge. No server, network or browser extensions.
   Exposes the existing promise-based window.pywebview.api contract to React. */
(function () {
  'use strict';
  if (typeof qt === 'undefined' || !qt.webChannelTransport ||
      typeof QWebChannel === 'undefined') return;
  new QWebChannel(qt.webChannelTransport, function (channel) {
    var native = channel.objects.pulse;
    if (!native || typeof native.call !== 'function') return;
    var pending = new Map();
    var seq = 0;
    native.resultReady.connect(function (id, payload) {
      var callbacks = pending.get(id);
      if (!callbacks) return;
      pending.delete(id);
      try {
        var response = JSON.parse(payload);
        if (response.ok) callbacks.resolve(response.value);
        else callbacks.reject(new Error(response.error || 'Pulse API error'));
      } catch (error) {
        callbacks.reject(error);
      }
    });
    var api = new Proxy(Object.create(null), {
      get: function (_, method) {
        if (typeof method !== 'string' || method === 'then') return undefined;
        return function () {
          var args = Array.prototype.slice.call(arguments);
          return new Promise(function (resolve, reject) {
            var id = String(++seq);
            pending.set(id, { resolve: resolve, reject: reject });
            try { native.call(id, method, JSON.stringify(args)); }
            catch (error) { pending.delete(id); reject(error); }
          });
        };
      }
    });
    window.pywebview = { api: api };
    // Only the native Qt WebChannel transport is allowed to set this value.
    window.__pulseQt = { bridge: native };
    window.dispatchEvent(new Event('pywebviewready'));
    // The header is a native drag handle; buttons/inputs remain interactive.
    document.addEventListener('mousedown', function (event) {
      if (event.button !== 0 || !event.target.closest) return;
      if (!event.target.closest('.drag-region')) return;
      if (event.target.closest('.no-drag,button,input,textarea,select,a,[role="button"]')) return;
      native.startDrag();
    }, true);
  });
})();
