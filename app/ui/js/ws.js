// Lightweight WebSocket helper for Bullen UI
// Attaches window.BullenWS.connect({ onMessage, onStatus })
// onStatus receives: 'connected' | 'reconnecting' | 'error'

(function(){
  function connect({ onMessage, onStatus } = {}) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/ws/vu`;
    let ws = new WebSocket(url);
    let pingTimer = null;

    function setStatus(s){ try { onStatus && onStatus(s); } catch(_){} }

    ws.onopen = () => {
      setStatus('connected');
      pingTimer = setInterval(() => { try { ws.send('ping'); } catch(e){} }, 2000);
    };

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        onMessage && onMessage(data);
      } catch(_){}
    };

    ws.onclose = () => {
      setStatus('reconnecting');
      if (pingTimer) { try { clearInterval(pingTimer); } catch(e){} }
      setTimeout(() => connect({ onMessage, onStatus }), 2000);
    };

    ws.onerror = () => {
      setStatus('error');
      if (pingTimer) { try { clearInterval(pingTimer); } catch(e){} }
    };

    return ws;
  }

  window.BullenWS = { connect };
})();
