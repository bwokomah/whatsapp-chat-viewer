(function () {
  const chatId = document.body.dataset.chatId;
  const chatEl = document.getElementById("chat");
  const bar = document.getElementById("searchbar");
  const input = document.getElementById("search-input");
  const countEl = document.getElementById("search-count");

  let matches = [];   // message idx values, oldest -> newest
  let pos = -1;       // position inside matches
  let timer = null;
  const originals = new Map(); // text element -> original text

  // Start at the newest message, like WhatsApp
  chatEl.scrollTop = chatEl.scrollHeight;

  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function clearHighlights() {
    originals.forEach((orig, el) => { el.textContent = orig; });
    originals.clear();
    document.querySelectorAll(".bubble.current").forEach(b => b.classList.remove("current"));
  }

  function highlight(idxs, query) {
    const words = (query.match(/\w+/g) || []).map(escapeRe);
    if (!words.length) return;
    const re = new RegExp("(" + words.join("|") + ")", "gi");
    idxs.forEach(idx => {
      const bubble = document.getElementById("m-" + idx);
      if (!bubble) return;
      const textEl = bubble.querySelector(".text");
      if (!textEl) return;
      originals.set(textEl, textEl.textContent);
      textEl.innerHTML = escapeHtml(textEl.textContent).replace(re, "<mark>$1</mark>");
    });
  }

  function goTo(i) {
    if (!matches.length) return;
    pos = (i + matches.length) % matches.length;
    document.querySelectorAll(".bubble.current").forEach(b => b.classList.remove("current"));
    const el = document.getElementById("m-" + matches[pos]);
    if (el) {
      el.classList.add("current");
      el.scrollIntoView({ block: "center" });
      el.classList.remove("flash");
      void el.offsetWidth; // restart the animation
      el.classList.add("flash");
    }
    countEl.textContent = (pos + 1) + " of " + matches.length;
  }

  async function runSearch() {
    const q = input.value.trim();
    clearHighlights();
    if (!q) { matches = []; pos = -1; countEl.textContent = ""; return; }
    const res = await fetch("/chat/" + chatId + "/search?q=" + encodeURIComponent(q));
    if (!res.ok) { countEl.textContent = "Error"; return; }
    const data = await res.json();
    matches = data.results.map(r => r.idx);
    if (!matches.length) { pos = -1; countEl.textContent = "No results"; return; }
    highlight(matches, q);
    goTo(matches.length - 1); // newest match first
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(runSearch, 250);
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") goTo(e.shiftKey ? pos + 1 : pos - 1);
  });
  document.getElementById("prev-btn").addEventListener("click", () => goTo(pos - 1));
  document.getElementById("next-btn").addEventListener("click", () => goTo(pos + 1));

  document.getElementById("search-toggle").addEventListener("click", () => {
    bar.hidden = !bar.hidden;
    if (!bar.hidden) input.focus();
    else { input.value = ""; runSearch(); }
  });

  document.getElementById("delete-btn").addEventListener("click", async () => {
    if (!confirm("Delete this chat from the server? This cannot be undone.")) return;
    const res = await fetch("/chat/" + chatId, { method: "DELETE" });
    if (res.ok) window.location.href = "/";
    else alert("Could not delete this chat.");
  });

  // If the browser can't play a voice note or video, offer a download link instead
  document.addEventListener("error", e => {
    const el = e.target;
    if (!el || (el.tagName !== "AUDIO" && el.tagName !== "VIDEO")) return;
    const link = document.createElement("a");
    link.href = el.getAttribute("src");
    link.className = "media-file";
    link.download = "";
    link.textContent = "Can't play this in your browser. Download it";
    el.replaceWith(link);
  }, true);
})();