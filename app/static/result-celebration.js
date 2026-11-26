(() => {
  'use strict';
  const queue = [];
  let active = false;
  const esc = value => String(value).replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
  const signed = value => value > 0 ? `+${value}` : value < 0 ? `−${-value}` : '0';
  const podium = player => player.previous_rank > 3 && player.rank <= 3;

  function playerRow(player) {
    const elo = player.rating - player.previous_rating;
    const gain = player.previous_rank - player.rank;
    const position = gain ? `#${player.previous_rank} → <b class="elo-rank-updated"${gain > 0 ? ' data-up' : ''}>#${player.rank}</b>` : `#${player.rank} · invariata`;
    const color = elo > 0 ? 'positive' : elo < 0 ? 'negative' : '';
    const fragments = player.mana_fragments_delta;
    return `<li class="elo-result-row${podium(player) ? ' elo-podium-row' : ''}">
      <div class="elo-result-player">${window.renderPlayerAvatar(player)}<strong>${esc(player.name)}</strong></div>
      <div class="elo-result-rating"><span>Elo</span>
        <span class="visually-hidden">Da ${player.previous_rating} a ${player.rating}</span>
        <strong aria-hidden="true">${player.previous_rating} → <span data-elo-count>${player.rating}</span></strong>
        <span class="elo-result-delta ${color}">${signed(elo)}</span>
      </div>
      <div class="elo-result-rank"><span>Posizione</span><strong>${position}</strong></div>
      ${Number.isInteger(fragments) ? `<div class="elo-result-fragments"><span>Frammenti Mana</span>
        <strong class="mana-balance"><span class="mana-fragment-icon" aria-hidden="true"></span>${signed(fragments)}</strong>
      </div>` : ''}
    </li>`;
  }

  function showNext() {
    const players = queue.shift();
    if (!players) return;
    active = true;
    const entrants = players.filter(podium);
    const dialog = document.createElement('dialog');
    dialog.className = 'elo-result-dialog' + (entrants.length ? ' elo-podium-dialog' : '');
    dialog.setAttribute('aria-labelledby', 'elo-result-title');
    const particles = entrants.length ? Array.from({length:24}, (_, index) => {
      const angle = index * Math.PI / 12;
      const distance = 80 + 20 * (index % 4);
      return `<i style="--dx:${Math.round(Math.cos(angle)*distance)}px;--dy:${Math.round(Math.sin(angle)*distance)}px;--delay:${(index%6)*25}ms"></i>`;
    }).join('') : '';
    dialog.innerHTML = `<header class="elo-result-heading">
      ${entrants.length ? `<div class="elo-podium-emblem" aria-hidden="true">🏆<span>${particles}</span></div>` : ''}
      <h2 id="elo-result-title">${entrants.length ? 'Ingresso sul podio!' : 'Elo e classifica'}</h2>
      ${entrants.length ? `<p>${entrants.map(player=>`${esc(player.name)} · #${player.rank}`).join(' / ')}</p>` : ''}
      </header><ul class="elo-result-list">${players.map(playerRow).join('')}</ul>
      <form method="dialog" class="dialog-actions"><button class="button primary" autofocus>Continua</button></form>`;
    document.body.append(dialog);
    dialog.addEventListener('close', () => {
      dialog.remove();
      active = false;
      showNext();
    }, {once:true});
    dialog.showModal();
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const counters = [...dialog.querySelectorAll('[data-elo-count]')];
    counters.forEach((counter, index) => { counter.textContent = players[index].previous_rating; });
    const start = performance.now();
    function count(now) {
      if (!dialog.open) return;
      const progress = Math.min(1, (now - start) / 650);
      const eased = 1 - (1 - progress) ** 3;
      counters.forEach((counter,index) => {
        const {rating, previous_rating:previous} = players[index];
        counter.textContent = Math.round(previous + (rating - previous) * eased);
      });
      if (progress < 1) requestAnimationFrame(count);
    }
    requestAnimationFrame(count);
  }

  window.showEloResult = changes => {
    if (!Array.isArray(changes) || !changes.length) return;
    queue.push(changes);
    if (!active) showNext();
  };
})();
