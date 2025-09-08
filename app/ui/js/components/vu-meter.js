// VuMeter component factory
// Exposed as window.BullenComponents.buildVuMeter(index)
(function(){
  function buildVuMeter(i) {
    const mcard = document.createElement('div');
    mcard.className = 'card';

    const title = document.createElement('div');
    title.className = 'small';
    title.textContent = `CH ${i}`;
    mcard.appendChild(title);

    const meter = document.createElement('div');
    meter.className = 'meter';
    meter.id = `meter-${i}`;

    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.id = `bar-${i}`;
    meter.appendChild(bar);

    const pk = document.createElement('div');
    pk.className = 'peak';
    pk.id = `peak-${i}`;
    meter.appendChild(pk);

    // Add dB tick marks (-60, -40, -20, -6, 0)
    const ticksWrap = document.createElement('div');
    ticksWrap.className = 'ticks';
    const levels = [-60, -40, -20, -6, 0];
    const toPct = (db) => {
      const minDb = -60, maxDb = 0;
      const cl = Math.max(minDb, Math.min(maxDb, db));
      return ((cl - minDb) / (maxDb - minDb)) * 100;
    };
    for (const db of levels) {
      const t = document.createElement('div'); t.className = 'tick'; t.style.left = `${toPct(db)}%`;
      const lbl = document.createElement('div'); lbl.className = 'tick-label'; lbl.textContent = `${db}`;
      t.appendChild(lbl);
      ticksWrap.appendChild(t);
    }
    meter.appendChild(ticksWrap);

    mcard.appendChild(meter);
    return mcard;
  }

  window.BullenComponents = window.BullenComponents || {};
  window.BullenComponents.buildVuMeter = buildVuMeter;
})();
