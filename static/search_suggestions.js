(() => {
  document.querySelectorAll('input[data-product-suggestions]').forEach((input, index) => {
    const form = input.form;
    const box = document.createElement('div');
    box.className = 'product-suggestions';
    box.id = `product-suggestions-${index}`;
    box.setAttribute('role', 'listbox');
    box.setAttribute('aria-label', 'Matching products');
    box.hidden = true;
    form.insertAdjacentElement('afterend', box);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', box.id);
    input.setAttribute('aria-expanded', 'false');
    input.autocomplete = 'off';
    let timer, controller, revision = 0, selected = -1, items = [];
    const close = () => {
      box.hidden = true; selected = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    };
    input.addEventListener('input', () => {
      clearTimeout(timer); controller?.abort();
      const version = ++revision, q = input.value.trim();
      close();
      if (q.length < 2 || q.length > 80) return;
      timer = setTimeout(async () => {
        controller = new AbortController();
        const params = new URLSearchParams({q});
        ['category', 'type'].forEach(key => {
          const value = new FormData(form).get(key);
          if (value) params.set(key, value);
        });
        try {
          const response = await fetch(`${input.dataset.productSuggestions}?${params}`, {signal: controller.signal});
          if (!response.ok) throw Error('Search unavailable');
          const data = await response.json();
          if (version !== revision || document.activeElement !== input) return;
          items = data.suggestions || [];
          box.replaceChildren();
          items.forEach((item, n) => {
            const link = document.createElement('a');
            link.href = item.url; link.textContent = item.name;
            link.id = `${box.id}-${n}`; link.setAttribute('role', 'option');
            link.setAttribute('aria-selected', 'false'); link.tabIndex = -1;
            box.appendChild(link);
          });
          box.hidden = items.length === 0;
          input.setAttribute('aria-expanded', String(items.length > 0));
        } catch (error) { if (version === revision) close(); }
      }, 200);
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Escape') { ++revision; controller?.abort(); close(); return; }
      if (box.hidden) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        [...box.children].forEach((node, n) => node.setAttribute('aria-selected', String(n === selected)));
        input.setAttribute('aria-activedescendant', box.children[selected].id);
        box.children[selected].scrollIntoView({block: 'nearest'});
      } else if (event.key === 'Enter' && selected >= 0) {
        event.preventDefault(); window.location.assign(items[selected].url);
      } else if (event.key === 'Tab') close();
    });
    document.addEventListener('pointerdown', event => {
      if (event.target !== input && !box.contains(event.target)) { ++revision; close(); }
    });
    form.addEventListener('submit', close);
  });
})();
