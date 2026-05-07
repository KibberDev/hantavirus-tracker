const CACHE = 'hanta-v2';
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
  // data.json: network first (always fresh), fallback to cache
  if (e.request.url.includes('data.json')) {
    e.respondWith(
      fetch(e.request).then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request))
    );
    return;
  }
  // Everything else: cache first
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
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
