/* SMARKAFRICA service worker.
 *
 * This worker deliberately caches nothing.
 *
 * A service worker is sticky. Once registered it keeps controlling the site on
 * later visits, and a caching mistake does not go away when the mistake is fixed
 * on the server: the stale copy keeps being served until the worker itself is
 * replaced. That makes a caching strategy the worst possible thing to ship
 * without having run it, so this worker exists only to satisfy the install
 * criteria and to give us something to extend once it can be tested.
 *
 * The fetch listener returns without calling event.respondWith(), so the browser
 * handles every request exactly as it would with no worker registered at all:
 * no interception, no added latency, nothing stale.
 */

self.addEventListener('install', function () {
  // Activate immediately instead of waiting for every old tab to close, so a
  // later version of this file cannot get stuck behind a tab left open for days.
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  // Delete every cache this origin holds. If a future version of this worker
  // ever does cache, and it turns out to be wrong, shipping this file again is
  // enough to clear it - the recovery path is a deploy, not a support request
  // asking people to clear their browser data.
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names.map(function (name) { return caches.delete(name); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function () {
  // Intentionally empty. Registering a fetch handler is part of what makes the
  // app installable; responding to the event is not. See the note above.
});
