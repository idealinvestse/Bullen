// Simple component factory for Gain/Mute card that matches existing IDs
// Exposed as window.BullenComponents.buildGainMuteCard(index, { onMute, onGainDb })
(function(){
  function buildGainMuteCard(i, handlers = {}) {
    const { onMute, onGainDb } = handlers;
    const card = document.createElement('div');
    card.className = 'card';

    const row1 = document.createElement('div');
    row1.className = 'row';
    const mute = document.createElement('button');
    mute.textContent = 'Mute';
    mute.className = 'mute';
    mute.id = `mute-${i}`;
    mute.setAttribute('aria-label', `Mute channel ${i}`);
    mute.setAttribute('aria-pressed', 'false');
    mute.onclick = () => {
      try {
        const on = !(mute.classList.contains('on'));
        if (typeof onMute === 'function') onMute(i, on);
      } catch(_){}
    };
    row1.appendChild(mute);
    card.appendChild(row1);

    const slider = document.createElement('div');
    slider.className = 'slider';
    const label = document.createElement('div'); label.textContent = `CH ${i}`; slider.appendChild(label);
    const range = document.createElement('input');
    range.type = 'range'; range.min = -60; range.max = 20; range.step = 0.5; range.value = 0; range.id = `gain-${i}`;
    range.setAttribute('aria-label', `Gain channel ${i} (dB)`);
    range.oninput = () => { try { if (typeof onGainDb === 'function') onGainDb(i, parseFloat(range.value)); } catch(_){} };
    slider.appendChild(range);
    const val = document.createElement('div'); val.className = 'small'; val.id = `gaindb-${i}`; val.textContent = '0.0 dB'; slider.appendChild(val);
    card.appendChild(slider);

    return card;
  }

  window.BullenComponents = window.BullenComponents || {};
  window.BullenComponents.buildGainMuteCard = buildGainMuteCard;
})();
