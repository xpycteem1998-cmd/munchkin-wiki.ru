(function () {
  "use strict";
  function normalize(value) {
    return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("ru-RU").replace(/ё/g, "е").replace(/[^a-zа-я0-9]+/gi, " ").trim();
  }
  function revealHash() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    const target = document.getElementById(id);
    if (!target) return;
    let parent = target;
    while (parent) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
      parent = parent.parentElement;
    }
    requestAnimationFrame(function () { target.scrollIntoView({ block: "start" }); });
  }
  function setupListFilter() {
    const input = document.querySelector("[data-list-filter]");
    const list = document.querySelector("[data-filter-list]");
    if (!input || !list) return;
    const items = Array.from(list.querySelectorAll("[data-filter-item]"));
    const groups = Array.from(list.querySelectorAll("[data-filter-group]"));
    const subgroups = Array.from(list.querySelectorAll("[data-filter-subgroup]"));
    groups.forEach(function (group) { group.dataset.initialOpen = String(group.open); });
    items.forEach(function (item) { item._search = normalize(item.dataset.search || item.textContent); });
    const counter = document.querySelector("[data-list-count]");
    if (counter) { counter.setAttribute("role", "status"); counter.setAttribute("aria-live", "polite"); }
    function filter() {
      const terms = normalize(input.value).split(" ").filter(Boolean);
      let shown = 0;
      items.forEach(function (item) {
        item.hidden = !terms.every(function (term) { return item._search.includes(term); });
        if (!item.hidden) shown++;
      });
      subgroups.forEach(function (group) { group.hidden = !group.querySelector("[data-filter-item]:not([hidden])"); });
      groups.forEach(function (group) {
        group.hidden = !group.querySelector("[data-filter-item]:not([hidden])");
        group.open = terms.length ? !group.hidden : group.dataset.initialOpen === "true";
      });
      if (counter) counter.textContent = shown.toLocaleString("ru-RU");
      const url = new URL(location.href);
      if (input.value) url.searchParams.set("filter", input.value); else url.searchParams.delete("filter");
      history.replaceState(null, "", url);
    }
    input.addEventListener("input", filter);
    const saved = new URL(location.href).searchParams.get("filter");
    if (saved) { input.value = saved; filter(); }
  }
  function resultNode(item) {
    const link = document.createElement("a"), type = document.createElement("span"), content = document.createElement("div");
    const title = document.createElement("strong"), subtitle = document.createElement("small");
    link.className = "search-result";
    link.href = item.url;
    type.textContent = item.type;
    title.textContent = item.title;
    subtitle.textContent = (item.subtitle || "") + (item.type === "Карточка" ? (item.notes ? " · Есть пояснения" : " · Название и наборы") : "");
    content.append(title, subtitle);
    link.append(type, content);
    return link;
  }
  function setupSearch() {
    const input = document.querySelector("[data-site-search]"), results = document.querySelector("[data-search-results]"), status = document.querySelector("[data-search-status]");
    if (!input || !results || !status) return;
    const catalog = Boolean(document.querySelector("[data-catalog]"));
    const select = document.querySelector("[data-search-type]"), notes = document.querySelector("[data-notes-only]"), more = document.querySelector("[data-search-more]");
    const pagination = document.querySelector(".pagination"), initial = results.innerHTML;
    let index, loading, generation = 0, matches = [], shown = 0, timer;
    function load() {
      if (!loading) {
        loading = fetch(document.body.dataset.searchIndex).then(function (response) {
          if (!response.ok) throw new Error("index unavailable");
          return response.json();
        }).then(function (records) {
          index = records.map(function (item) {
            item._normalized = normalize(item.search + " " + item.title + " " + (item.subtitle || ""));
            item._names = (item.names || [item.title]).map(normalize);
            return item;
          });
        }).catch(function (error) { loading = null; throw error; });
      }
      return loading;
    }
    function appendPage() {
      const fragment = document.createDocumentFragment(), next = matches.slice(shown, shown + 100);
      next.forEach(function (item) { fragment.appendChild(resultNode(item)); });
      results.appendChild(fragment);
      shown += next.length;
      status.textContent = matches.length ? "Найдено: " + matches.length.toLocaleString("ru-RU") + ". Показано: " + shown.toLocaleString("ru-RU") : "Ничего не найдено";
      more.hidden = shown >= matches.length;
    }
    function search() {
      const ticket = ++generation, query = normalize(input.value), type = catalog ? "Карточка" : select.value, onlyNotes = Boolean(notes && notes.checked);
      const url = new URL(location.href);
      if (input.value) url.searchParams.set("q", input.value); else url.searchParams.delete("q");
      if (!catalog && type) url.searchParams.set("type", type); else url.searchParams.delete("type");
      if (onlyNotes) url.searchParams.set("notes", "1"); else url.searchParams.delete("notes");
      history.replaceState(null, "", url);
      results.replaceChildren();
      more.hidden = true;
      if (pagination) pagination.hidden = true;
      if (catalog && !query && !onlyNotes) {
        results.innerHTML = initial;
        pagination.hidden = false;
        status.textContent = "Карточки текущей страницы";
        return;
      }
      if (!catalog && query.length < 2) { status.textContent = "Введите два или больше символов"; return; }
      status.textContent = "Ищу…";
      load().then(function () {
        if (ticket !== generation) return;
        const terms = query.split(" ").filter(Boolean);
        function score(item) {
          if (!query) return 0;
          if (item._names.includes(query)) return 300;
          if (item._names.some(function (name) { return name.startsWith(query); })) return 200;
          if (item._names.some(function (name) { return name.includes(query); })) return 100;
          return 0;
        }
        matches = index.filter(function (item) {
          return (!type || item.type === type) && (!onlyNotes || item.notes) && terms.every(function (term) { return item._normalized.includes(term); });
        }).sort(function (a, b) { return score(b) - score(a) || a.title.localeCompare(b.title, "ru"); });
        shown = 0;
        appendPage();
      }).catch(function () {
        if (ticket === generation) status.textContent = "Не удалось загрузить поиск. Проверьте соединение и повторите запрос.";
      });
    }
    input.addEventListener("input", function () { generation++; clearTimeout(timer); timer = setTimeout(search, 90); });
    if (select) select.addEventListener("change", search);
    if (notes) notes.addEventListener("change", search);
    more.addEventListener("click", appendPage);
    function restore() {
      const params = new URL(location.href).searchParams;
      input.value = params.get("q") || "";
      if (select) select.value = params.get("type") || "";
      if (notes) notes.checked = params.get("notes") === "1";
      search();
    }
    window.addEventListener("popstate", restore);
    restore();
    if (!catalog && matchMedia("(pointer: fine)").matches) input.focus();
  }
  document.addEventListener("keydown", function (event) {
    const target = event.target;
    const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target.isContentEditable;
    if (event.key === "/" && !editing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      const local = document.querySelector("[data-site-search], [data-list-filter], #home-query");
      if (local) local.focus(); else location.assign("/search/");
    }
  });
  setupListFilter();
  setupSearch();
  window.addEventListener("hashchange", revealHash);
  revealHash();
})();
