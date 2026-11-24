// The server substitutes the cache name and the exact versioned dependencies.
const cacheName = __CACHE_NAME__;
const assets = __ASSETS__;
const shell = '/segnavita';
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(cacheName);
    // An older worker may still serve its cached page after the access gate is deployed.
    // Activate the new worker even when the browser still needs to sign in.
    const page = await fetch(shell, {cache:'reload'});
    if (page.status === 401) {
      await caches.delete('mana-life-photos-v1');
    } else {
      if (!page.ok) throw new Error('Segnavita non disponibile');
      await cache.put(shell, page);
      await cache.addAll(assets.map(path => new Request(path, {cache:'reload'})));
    }
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
  const photo = /^\/photos\/[a-f0-9]{32}\.jpg$/i.test(url.pathname);
  const page = event.request.mode === 'navigate' && url.pathname === shell;
  if (!photo && !page && !assets.includes(url.pathname + url.search)) return;
  event.respondWith((async () => {
    const photoCache = 'mana-life-photos-v1';
    const cache = await caches.open(photo ? photoCache : cacheName);
    let response;
    try {
      response = await fetch(event.request);
    } catch (error) {
      const cached = await cache.match(page ? shell : event.request, {ignoreVary:true});
      if (cached) return cached;
      throw error;
    }
    if (response.status === 401) {
      await Promise.all([caches.delete(photoCache), caches.delete(cacheName)]);
    } else if (response.ok && (page || !photo || response.headers.get('Content-Type')?.startsWith('image/'))) {
      try {
        await cache.put(page ? shell : event.request, response.clone());
        if (photo) {
          const keys = await cache.keys();
          await Promise.all(keys.slice(0, Math.max(0, keys.length - 64)).map(key => cache.delete(key)));
        }
      } catch { /* Storage pressure must not prevent displaying the response. */ }
    }
    return response;
  })());
});
