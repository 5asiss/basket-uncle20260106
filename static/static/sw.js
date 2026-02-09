self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open('basam-v1').then((cache) => {
      return cache.addAll([
        '/',
        '/static/logo/side1.jpg'
      ]);
    })
  );
});

self.addEventListener('fetch', (e) => {
  e.respondWith(
    caches.match(e.request).then((response) => {
      return response || fetch(e.request);
    })
  );
});