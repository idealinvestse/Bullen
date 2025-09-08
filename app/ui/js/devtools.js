// Developer Tools module: tabs behavior and basic utilities
(function(){
  function setTab(key) {
    var keys = ['api','ws','seq','metrics'];
    keys.forEach(function(t){
      var btn = document.getElementById('tab-' + t);
      var panel = document.getElementById('tabc-' + t);
      var active = (t === key);
      if (btn) {
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
        btn.tabIndex = active ? 0 : -1;
      }
      if (panel) {
        panel.classList.toggle('active', active);
        if (active) panel.removeAttribute('hidden'); else panel.setAttribute('hidden', '');
      }
    });
    var current = document.getElementById('tab-' + key);
    if (current) current.focus();
  }

  window.DevTools = window.DevTools || {};
  window.DevTools.setTab = setTab;
})();
