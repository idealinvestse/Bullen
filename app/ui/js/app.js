// ES module entry point to bootstrap Bullen UI
// It invokes a global window.BullenEntry() that the legacy inline script sets up.
// This allows us to gradually migrate logic into modules without breaking behavior.

function ready(fn) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fn, { once: true });
  } else {
    queueMicrotask(fn);
  }
}

ready(() => {
  if (typeof window.BullenEntry === 'function') {
    try { window.BullenEntry(); } catch (e) { console.error('BullenEntry failed', e); }
  }
});
