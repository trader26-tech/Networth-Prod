/* Minimal service worker — its only job is to make the app installable
   ("Add to Home screen" / "Install app") on Android/Chrome.

   It deliberately does NO caching: every request passes straight through to the
   network, so the installed app is never stuck on a stale build (the classic
   PWA pitfall). Freshness is owned by the server's cache headers. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => { /* pass through to the network */ });
