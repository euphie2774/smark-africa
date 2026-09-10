(() => {
  const valid = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
  function makeMap(element, point, editable) {
    if (!window.L) { element.textContent = 'Map unavailable. Coordinates and the map link still work.'; return null; }
    const map = L.map(element, {scrollWheelZoom: false}).setView(point || [-1.286389, 36.817223], point ? 16 : 6);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    return map;
  }
  document.querySelectorAll('[data-service-location-editor]').forEach(editor => {
    const lat = editor.querySelector('[name="location_lat"]'), lng = editor.querySelector('[name="location_lng"]');
    const status = editor.querySelector('[data-location-status]'), link = editor.querySelector('[data-location-link]');
    const point = () => lat.value !== '' && lng.value !== '' && valid(Number(lat.value), Number(lng.value)) ? [Number(lat.value), Number(lng.value)] : null;
    const map = makeMap(editor.querySelector('[data-location-map]'), point(), true);
    let marker;
    function refresh() {
      const at = point();
      if (!at) { marker?.remove(); marker = null; link.classList.add('d-none'); return; }
      link.href = `https://www.openstreetmap.org/?mlat=${at[0]}&mlon=${at[1]}#map=17/${at[0]}/${at[1]}`;
      link.classList.remove('d-none');
      if (!map) return;
      if (marker) marker.setLatLng(at);
      else {
        marker = L.marker(at, {draggable:true, alt:'Service location pin'}).addTo(map);
        marker.on('dragend', () => setPoint(marker.getLatLng().lat, marker.getLatLng().lng));
      }
      map.setView(at, 16);
    }
    function setPoint(a, b) {
      if (!valid(a, b)) return;
      lat.value = a.toFixed(6); lng.value = b.toFixed(6);
      status.textContent = 'Pin selected. Check that it marks the correct entrance.';
      refresh();
    }
    map?.on('click', event => setPoint(event.latlng.lat, event.latlng.lng));
    [lat, lng].forEach(input => input.addEventListener('input', refresh));
    editor.querySelector('[data-clear-location]').addEventListener('click', () => {
      lat.value = ''; lng.value = ''; status.textContent = 'Pin cleared.'; refresh();
    });
    editor.querySelector('[data-use-location]').addEventListener('click', function () {
      if (!navigator.geolocation) { status.textContent = 'Location is unavailable. Select a pin manually.'; return; }
      const button = this; button.disabled = true;
      navigator.geolocation.getCurrentPosition(position => {
        setPoint(position.coords.latitude, position.coords.longitude); button.disabled = false;
      }, () => { status.textContent = 'Location could not be read. Select a pin or enter coordinates.'; button.disabled = false; }, {enableHighAccuracy:true, timeout:10000});
    });
    editor.querySelector('[data-find-address]').addEventListener('click', async function () {
      const query = editor.querySelector('[name="location_label"]').value.trim();
      if (query.length < 2) { status.textContent = 'Enter an address first.'; return; }
      const button = this; button.disabled = true;
      try {
        const response = await fetch(editor.dataset.geocodeUrl + '?' + new URLSearchParams({q:query}));
        if (!response.ok) throw Error();
        const result = (await response.json()).results?.[0];
        if (!result || !valid(Number(result.lat), Number(result.lng))) throw Error();
        setPoint(Number(result.lat), Number(result.lng));
        status.textContent = 'Approximate address located. Move the pin to the entrance before publishing.';
        if (result.county) editor.querySelector('[name="location_county"]').value = result.county;
      } catch (_) { status.textContent = 'Address not found. Use the map, your location or coordinates.'; }
      finally { button.disabled = false; }
    });
    refresh();
  });
  document.querySelectorAll('[data-service-location-view]').forEach(element => {
    const lat = Number(element.dataset.lat), lng = Number(element.dataset.lng);
    if (!valid(lat, lng)) return;
    const map = makeMap(element, [lat, lng], false);
    if (map) L.marker([lat, lng], {alt:'Service location pin'}).addTo(map);
  });
})();
