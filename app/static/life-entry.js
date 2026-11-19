window.openLifeCounter();
const availability = document.getElementById('offline-ready');
if (window.isSecureContext && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register('/life-worker.js', {scope:'/segnavita'})
    .then(() => navigator.serviceWorker.ready)
    .then(() => { availability.textContent = 'Segnavita disponibile offline su questo dispositivo.'; })
    .catch(() => { availability.textContent = 'Download offline incompleto. Riapri questa pagina con la rete disponibile.'; });
} else {
  availability.textContent = 'Per riaprire il segnavita offline, accedi in HTTPS con un browser compatibile.';
}
