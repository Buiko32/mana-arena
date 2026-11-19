// The server substitutes the cache name and the exact versioned dependencies.
const cacheName = __CACHE_NAME__;
const assets = __ASSETS__;
const shell = '/segnavita';
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(cacheName);
    await cache.addAll([shell, ...assets].map(path => new Request(path, {cache:'reload'})));
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    await self.clients.claim();
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('mana-life-shell-') && key !== cacheName)
      .map(key => caches.delete(key)));
  })());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Profile photos have immutable, content-specific URLs. Store only photos
  // displayed on the table, bounded independently from the offline shell.
  if (/^\/photos\/[a-f0-9]{32}\.jpg$/i.test(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open('mana-life-photos-v1');
      const cached = await cache.match(event.request);
      if (cached) return cached;
      const response = await fetch(event.request);
      if (response.ok && response.headers.get('Content-Type')?.startsWith('image/')) {
        try {
          await cache.put(event.request, response.clone());
          const keys = await cache.keys();
          await Promise.all(keys.slice(0, Math.max(0, keys.length - 64)).map(key => cache.delete(key)));
        } catch { /* Storage pressure must not prevent displaying the photo. */ }
      }
      return response;
    })());
    return;
  }
  const page = event.request.mode === 'navigate' && url.pathname === shell;
  if (!page && !assets.includes(url.pathname + url.search)) return;
  event.respondWith((async () => {
    const cache = await caches.open(cacheName);
    return (await cache.match(page ? shell : event.request)) || fetch(event.request);
  })());
});
