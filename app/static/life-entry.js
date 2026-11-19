window.openLifeCounter();
const availability = document.getElementById('offline-ready');
if (window.isSecureContext && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register('/life-worker.js', {scope:'/segnavita'})
    .catch(() => { availability.textContent = 'Download offline incompleto. Riapri questa pagina con la rete disponibile.'; });
}
