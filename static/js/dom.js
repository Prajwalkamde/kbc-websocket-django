/*
 * KBC safe DOM helpers.
 *
 * Player names, question text, categories and explanations all come from the
 * server (and, for names, from other players), so they must never be handed to
 * `innerHTML` / `insertAdjacentHTML`. Building nodes with `createElement` and
 * writing values through `textContent` / `setAttribute` makes HTML injection
 * impossible by construction: the browser cannot re-interpret text as markup.
 *
 * Usage:
 *   KBCDom.render(node, [
 *     KBCDom.el("b", { text: "Score:" }),
 *     KBCDom.el("span", { text: playerName })
 *   ]);
 */
window.KBCDom = (function () {
  function isNode(value) {
    return typeof Node !== "undefined" && value instanceof Node;
  }

  function text(value) {
    return document.createTextNode(value == null ? "" : String(value));
  }

  /* Append strings, numbers and nodes, skipping empty entries. */
  function append(node, children) {
    if (children == null) return node;
    const list = Array.isArray(children) ? children : [children];
    list.forEach(child => {
      if (child == null || child === false || child === true) return;
      node.appendChild(isNode(child) ? child : text(child));
    });
    return node;
  }

  function clear(node) {
    if (!node) return node;
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  function render(node, children) {
    if (!node) return node;
    clear(node);
    return append(node, children);
  }

  /*
   * el("button", { class: "btn", dataset: { option: "A" }, disabled: true,
   *    on: { click: () => {} } }, ["A."])
   */
  function el(tag, options, children) {
    const node = document.createElement(tag);
    const spec = options || {};
    Object.keys(spec).forEach(key => {
      const value = spec[key];
      if (value == null || value === false) return;
      if (key === "class") {
        node.className = String(value);
      } else if (key === "text") {
        node.textContent = String(value);
      } else if (key === "dataset") {
        Object.keys(value).forEach(name => {
          node.dataset[name] = String(value[name]);
        });
      } else if (key === "on") {
        Object.keys(value).forEach(name => node.addEventListener(name, value[name]));
      } else if (key === "attrs") {
        Object.keys(value).forEach(name => node.setAttribute(name, String(value[name])));
      } else if (key in node) {
        node[key] = value;
      } else {
        node.setAttribute(key, String(value));
      }
    });
    return append(node, children);
  }

  /* Table cell that can carry its own class or colspan. */
  function cell(value, options) {
    return el("td", options || {}, [value]);
  }

  /* Shared results table used by the host and the player views. */
  function table(headers, rows) {
    return el("table", { class: "results-table" }, [
      el("thead", {}, [el("tr", {}, (headers || []).map(header => el("th", { text: header })))]),
      el("tbody", {}, (rows || []).map(cells =>
        el("tr", {}, (cells || []).map(entry =>
          entry && entry.tagName === "TD" ? entry : cell(entry)
        ))
      ))
    ]);
  }

  return { el, text, append, clear, render, cell, table };
})();
