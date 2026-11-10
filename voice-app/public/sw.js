const CACHE = 'luna-website-v3';
const SHELL = ['/', '/index.html', '/style.css', '/config.js', '/app.js', '/icon.svg', '/manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || new URL(event.request.url).pathname === '/signal') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
