const CACHE = 'hanta-v3';
const STATIC = ['./', './index.html', './data.json', './icon.svg', './manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  const isHTML = e.request.mode === 'navigate' ||
                 e.request.destination === 'document' ||
                 url.pathname.endsWith('/') ||
                 url.pathname.endsWith('.html');
  const isData = url.pathname.endsWith('data.json');

  // HTML y data.json: NETWORK FIRST (siempre fresco; cache solo offline)
  if (isHTML || isData) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          if (r.ok) {
            const clone = r.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
          }
          return r;
        })
        .catch(() => caches.match(e.request).then(c => c || caches.match('./index.html')))
    );
    return;
  }

  // Resto (assets estáticos): CACHE FIRST
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
      if (resp.ok) {
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return resp;
    }))
  );
});

self.addEventListener('push', e => {
  const data = e.data?.json() || { title: 'HantaTracker', body: 'Nueva actualización del brote.' };
  const scope = self.registration.scope;
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: scope + 'icon.svg',
      badge: scope + 'icon.svg',
      vibrate: [200, 100, 200],
      data: { url: data.url || scope },
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data?.url || self.registration.scope));
});
