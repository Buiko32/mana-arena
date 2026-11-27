(() => {
  'use strict';
  const link = document.querySelector('[data-live-link]');
  const section = document.querySelector('#live');
  const status = section.querySelector('#live-status');
  const list = section.querySelector('#live-games');
  const back = section.querySelector('#live-back');
  const dot = link.querySelector('.live-nav-dot');
  const balance = section.querySelector('#live-balance');
  const upcoming = section.querySelector('#live-upcoming');
  const scheduled = section.querySelector('#live-scheduled');
  const tickets = section.querySelector('#live-tickets');
  const receipts = section.querySelector('#live-receipts');
  const dialog = section.querySelector('#live-bet-dialog');
  let markets, marketLayout, marketsUnavailable = false;
  const labels = {duel:'Duello 1v1', team:'Squadre', ffa:'Tutti contro tutti'};
  let refreshing = false, layout, games, unavailable = false;

  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  const label = game => `${game.kind === 'tournament' ? 'Torneo' : 'Amichevole'} · ${labels[game.format]}`;
  let players = new Map();
  fetch('/api/public/ranking').then(response => response.ok ? response.json() : [])
    .then(rows => {
      players = new Map(rows.map(player => [player.id, player]));
      layout = undefined;
      marketLayout = undefined;
      if (!section.hidden) render();
    }).catch(() => {});

  function renderSide(side, detail) {
    const row = element('div', undefined, 'history-side live-side');
    const members = element('div', undefined, 'live-members');
    for (const member of side.members) {
      const person = element('div', undefined, 'history-player live-player');
      person.innerHTML = window.renderPlayerAvatar(players.get(member.id) || {});
      person.append(element('strong', member.name));
      members.append(person);
    }
    const score = element('div', undefined, 'live-score');
    const change = element('span', undefined, 'live-change');
    change.setAttribute('aria-hidden', 'true');
    score.append(element('strong', undefined, 'live-total'), change);
    row.append(members, score, element('div', undefined, 'live-side-market'));
    if (detail) row.append(element('span', `Vita iniziale · ${side.initial} PV`, 'muted'),
      element('span', undefined, 'live-odds muted'));
    return row;
  }

  function gameHeading(game, tag) {
    const heading = element(tag, undefined, 'live-card-heading');
    heading.append(element('span', 'In corso', 'live-in-progress'), element('span', label(game)));
    return heading;
  }

  function renderSummary(game) {
    const card = element('article', undefined, 'history-match live-summary');
    const open = element('a', 'Apri partita →', 'live-open');
    open.href = '#live/' + encodeURIComponent(game.id);
    const heading = gameHeading(game, 'h3');
    card.append(heading, renderSides(game, false), open, element('div', undefined, 'live-market-host'));
    return card;
  }

  function renderDetail(game) {
    const card = element('article', undefined, 'history-match live-detail');
    const heading = gameHeading(game, 'h3');
    card.append(heading, renderSides(game, true));
    const estimate = element('p', 'Stima basata su vita residua, Elo e scontri diretti pesati per recenza.', 'live-estimate muted');
    card.append(estimate, element('div', undefined, 'live-market-host'));
    return card;
  }

  function renderSides(game, detail) {
    const sides = element('div', undefined, game.format === 'duel' ? 'live-sides live-duel' : 'live-sides');
    sides.append(...game.sides.map(side => renderSide(side, detail)));
    if (game.format === 'duel') {
      const mark = element('img', undefined, 'live-duel-mark');
      mark.src = '/static/mana-fragment.svg';
      mark.alt = '';
      sides.append(mark);
    }
    return sides;
  }

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const scoreTimers = new WeakMap();

  function showLife(total, life, count) {
    clearTimeout(scoreTimers.get(total));
    const displayed = Number(total.textContent);
    const distance = Math.abs(life - displayed);
    // Keep large corrections from leaving the spectator score behind the match.
    if (!count || reducedMotion.matches || distance > 30 || distance === 0) {
      total.textContent = life;
      return 0;
    }

    const direction = Math.sign(life - displayed);
    const delay = Math.min(85, Math.floor(850 / distance));
    let value = displayed;
    function tick() {
      if (!total.isConnected) return;
      value += direction;
      total.textContent = value;
      if (value !== life) scoreTimers.set(total, setTimeout(tick, delay));
    }
    scoreTimers.set(total, setTimeout(tick, delay));
    return distance * delay;
  }

  function updateScores(card, game) {
    card.querySelectorAll('.live-side').forEach((row, index) => {
      const {life} = game.sides[index];
      const total = row.querySelector('.live-total');
      const delta = life - Number(total.dataset.life);
      if (delta !== 0) {
        const isUpdate = Number.isFinite(delta);
        const countTime = showLife(total, life, isUpdate && game.format === 'duel');
        total.dataset.life = life;
        total.setAttribute('aria-label', life + ' punti vita');
        if (isUpdate) {
          const change = row.querySelector('.live-change');
          change.textContent = delta > 0 ? '+' + delta : '−' + Math.abs(delta);
          row.removeAttribute('data-change');
          row.style.setProperty('--live-count-duration', Math.max(550, countTime + 85) + 'ms');
          void change.offsetWidth;
          row.dataset.change = delta > 0 ? 'gain' : 'loss';
        }
      }
      const odds = row.querySelector('.live-odds');
      if (odds) {
        odds.hidden = !game.prediction;
        odds.textContent = game.prediction ? `${game.prediction[index]}% di vittoria stimata` : '';
      }
    });
    const estimate = card.querySelector('.live-estimate');
    if (estimate) estimate.hidden = !game.prediction;
  }

  function render() {
    const id = location.hash.startsWith('#live/') ? location.hash.slice(6) : '';
    back.hidden = !id;
    const game = games?.find(item => encodeURIComponent(item.id) === id);
    const visible = id ? (game ? [game] : []) : (games || []);
    const nextLayout = JSON.stringify([id, visible.map(({id, kind, format, sides}) => ({
      id, kind, format, sides: sides.map(({members, initial}) => ({members, initial}))
    }))]);
    const message = unavailable ? (visible.length ? 'Aggiornamento interrotto. Ultimi valori ricevuti.' : 'Aggiornamento live non disponibile.')
      : !games ? 'Caricamento partite…'
      : visible.length ? '' : id ? 'Questa partita non è più in diretta.' : 'Nessuna partita in corso.';
    if (status.textContent !== message) status.textContent = message;
    status.hidden = !message;
    if (nextLayout !== layout) {
      layout = nextLayout;
      list.replaceChildren(...visible.map(id ? renderDetail : renderSummary));
    }
    visible.forEach((item, index) => {
      const card = list.children[index];
      updateScores(card, item);
      const market = markets?.active[item.id];
      updateSideMarkets(card, item, market);
      const host = card.querySelector('.live-market-host');
      const key = JSON.stringify([market, !!markets?.viewer, markets?.viewer?.balance >= 5]);
      if (host.dataset.market !== key) {
        host.dataset.market = key;
        host.replaceChildren(...(market ? [renderMarket(market, true)] : []));
      }
    });
    renderMarkets(Boolean(id));
  }

  const multiplier = cents => (cents / 100).toLocaleString('it-IT', {minimumFractionDigits:2}) + '×';
  const payout = (amount, cents) => Math.floor(amount * cents / 100);
  const playerName = id => players.get(id)?.name || 'Giocatore';
  const statusLabels = {pending:'In attesa del risultato', won:'Vinta', lost:'Persa', refunded:'Rimborsata'};

  function receipt(bet) {
    const row = element('div', undefined, 'live-receipt');
    row.append(element('strong', (bet.name || playerName(bet.selection)) + ' · ' + bet.amount + ' Mana'));
    const details = element('dl');
    const add = (label, value) => {
      const line = element('div');
      line.append(element('dt', label), element('dd', value));
      details.append(line);
    };
    add('Quota fissata', multiplier(bet.multiplier));
    if (bet.status === 'pending') add('Ritorno potenziale', payout(bet.amount, bet.multiplier) + ' Mana');
    else add('Esito', statusLabels[bet.status]);
    row.append(details);
    return row;
  }

  function updateSideMarkets(card, game, market) {
    card.querySelectorAll('.live-side-market').forEach((row, i) => {
      const index = market?.players.indexOf(game.sides[i].members[0].id) ?? -1;
      const key = index < 0 ? '' : market.quotes[index] + ':' + market.totals[index];
      if (row.dataset.market === key) return;
      row.dataset.market = key;
      row.replaceChildren(...(index < 0 ? [] : [
        element('b', multiplier(market.quotes[index])),
        element('small', market.totals[index] + ' Mana puntati')
      ]));
    });
  }

  function renderMarket(market, inline = false) {
    const panel = element('div', undefined, 'live-market');
    const choices = element('div', undefined, 'live-quote-grid');
    market.players.forEach((id, i) => {
      const row = element('div', undefined, 'live-quote');
      const name = market.names?.[i] || playerName(id);
      if (!inline) {
        row.innerHTML = window.renderPlayerAvatar(players.get(id) || {});
        const info = element('div', undefined, 'live-quote-info');
        info.append(element('strong', name), element('b', multiplier(market.quotes[i])),
          element('small', market.totals[i] + ' Mana puntati'));
        row.append(info);
      }
      if (inline && markets.viewer && !market.participant && !market.own) {
        const button = element('button', inline ? 'Punta su ' + name : 'Punta', 'button primary');
        button.type = 'button';
        button.disabled = markets.viewer.balance < 5;
        button.setAttribute('aria-label', 'Punta su ' + (market.names?.[i] || playerName(id)));
        button.addEventListener('click', () => openBet(market, i, button));
        row.append(button);
      }
      if (row.childElementCount) choices.append(row);
    });
    if (choices.childElementCount) panel.append(choices);
    if (!inline && markets.viewer && !market.participant && !market.own) {
      const button = element('button', 'Punta', 'button primary live-upcoming-bet');
      button.type = 'button';
      button.disabled = markets.viewer.balance < 5;
      button.addEventListener('click', () => openBet(market, null, button));
      panel.append(button);
    }
    if (market.own) panel.append(element('h4', 'La tua puntata'), receipt(market.own));
    else if (market.participant) panel.append(element('p', 'Stai giocando questa partita.', 'muted'));
    else if (!markets.viewer) panel.append(element('p', 'Accedi al tuo profilo per puntare.', 'muted'));
    else if (markets.viewer.balance < 5) panel.append(element('p', 'Servono almeno 5 Frammenti Mana.', 'muted'));
    return panel;
  }

  function renderMarkets(detail) {
    scheduled.hidden = detail;
    balance.hidden = !markets?.viewer;
    if (markets?.viewer) balance.textContent = 'Saldo · ' + markets.viewer.balance + ' Mana';
    const key = JSON.stringify([markets?.scheduled, markets?.tickets, marketsUnavailable, !!markets?.viewer, markets?.viewer?.balance >= 5]);
    if (key === marketLayout) return;
    marketLayout = key;
    upcoming.replaceChildren(...(markets?.scheduled.length ? markets.scheduled.map(market => {
      const card = element('article', undefined, 'history-match live-upcoming-card');
      card.append(element('h4', market.title, 'live-card-heading'), renderMarket(market));
      return card;
    }) : [element('p', marketsUnavailable && !markets ? 'Incontri in programma non disponibili.' : markets ? 'Nessun incontro in programma.' : 'Caricamento incontri…', 'muted')]));
    tickets.hidden = !markets?.tickets.length;
    receipts.replaceChildren(...(markets?.tickets || []).map(receipt));
  }

  function openBet(market, index, trigger) {
    const choose = index === null;
    let selected = index ?? 0;
    const quote = () => market.quotes[selected];
    const max = Math.floor(markets.viewer.balance / 5) * 5;
    const name = i => market.names?.[i] || playerName(market.players[i]);
    const heading = element('h3', choose ? 'Scegli su chi puntare' : 'Punta su ' + name(selected));
    heading.id = 'live-bet-title';
    const form = element('form');
    const label = element('label', 'Frammenti Mana');
    const range = element('input');
    Object.assign(range, {type:'range', min:'5', max:String(max), step:'5', value:'5'});
    label.append(range);
    const total = element('output');
    const price = element('p');
    const update = () => {
      price.textContent = 'Quota ' + multiplier(quote());
      total.textContent = range.value + ' Mana · Ritorno potenziale ' + payout(Number(range.value), quote()) + ' Mana';
    };
    range.addEventListener('input', update);
    update();
    const error = element('p', '', 'live-bet-error');
    error.setAttribute('role', 'alert');
    const cancel = element('button', 'Chiudi', 'button quiet');
    cancel.type = 'button';
    cancel.onclick = () => dialog.close();
    const submit = element('button', 'Conferma puntata', 'button primary');
    submit.type = 'submit';
    if (choose) {
      const playerLabel = element('label', 'Giocatore');
      const select = element('select');
      market.players.forEach((_, i) => {
        const option = element('option', name(i));
        option.value = i;
        select.append(option);
      });
      select.addEventListener('change', () => { selected = Number(select.value); update(); });
      playerLabel.append(select);
      form.append(playerLabel);
    }
    form.append(price, label, total, error, cancel, submit);
    dialog.replaceChildren(heading, form);
    dialog.addEventListener('close', () => { if (trigger.isConnected) trigger.focus(); }, {once:true});
    dialog.showModal();
    form.onsubmit = async event => {
      event.preventDefault();
      submit.disabled = true;
      const select = form.querySelector('select');
      if (select) select.disabled = true;
      error.textContent = '';
      try {
        const response = await fetch('/api/player/live-bets', {
          method:'POST', headers:{'Content-Type':'application/json','X-Player-CSRF':markets.viewer?.csrf || ''},
          body:JSON.stringify({market:market.id, selection:market.players[selected], amount:Number(range.value), multiplier:quote()})
        });
        if (!response.ok) {
          const data = await response.json();
          if (response.status === 409) await refresh();
          throw Error(typeof data.detail === 'string' ? data.detail : 'Puntata non riuscita.');
        }
        dialog.close();
        await refresh();
      } catch (failure) {
        error.textContent = failure.message;
      } finally {
        submit.disabled = false;
        if (select) select.disabled = false;
      }
    };
  }

  async function refreshMarkets(signal) {
    const response = await fetch('/api/public/live-markets', {signal, cache:'no-store'});
    if (!response.ok) throw Error();
    markets = await response.json();
    marketsUnavailable = false;
  }


  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6500);
    const marketRequest = section.hidden ? null : refreshMarkets(controller.signal).catch(() => { marketsUnavailable = true; });
    try {
      const response = await fetch('/api/public/live', {signal: controller.signal});
      if (!response.ok) throw Error();
      games = await response.json();
      unavailable = false;
      dot.hidden = !games.length;
      link.setAttribute('aria-label', games.length ? 'LIVE, partite in corso' : 'LIVE');
    } catch {
      unavailable = true;
    } finally {
      await marketRequest;
      clearTimeout(timeout);
      refreshing = false;
      if (!section.hidden) render();
      document.dispatchEvent(new Event('live-games-updated'));
    }
  }

  window.openLiveGames = () => {
    render();
    refresh();
  };
  window.tournamentLiveHref = tournamentId => {
    const game = games?.find(item => item.kind === 'tournament' && item.tournament_id === tournamentId);
    return game ? '#live/' + encodeURIComponent(game.id) : '';
  };
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 2000);
  document.addEventListener('player-identity-changed', () => { if (!section.hidden) refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
