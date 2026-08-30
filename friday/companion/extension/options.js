const $ = (id) => document.getElementById(id);

chrome.storage.local.get("token").then(({ token }) => {
  if (token) {
    $("token").value = token;
    $("status").textContent = "Paired.";
  }
});

$("save").onclick = async () => {
  await chrome.storage.local.set({ token: $("token").value.trim() });
  $("status").textContent = "Saved. Reconnecting…";
  chrome.runtime.reload();
};

// Built with DOM methods, not innerHTML. These origins come from Chrome's own
// permissions API rather than from a page, but this is the screen that decides
// where Friday may act unattended - it is the wrong place to get into the
// habit of turning strings into markup.
async function render() {
  const all = await chrome.permissions.getAll();
  const origins = all.origins || [];
  const list = $("sites");
  list.textContent = "";

  if (!origins.length) {
    const empty = document.createElement("em");
    empty.textContent =
      "None. Friday works only on tabs you have clicked the icon on.";
    list.appendChild(empty);
    return;
  }

  for (const origin of origins) {
    const row = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = origin + " ";
    const remove = document.createElement("button");
    remove.textContent = "remove";
    remove.onclick = async () => {
      await chrome.permissions.remove({ origins: [origin] });
      render();
    };
    row.append(label, remove);
    list.appendChild(row);
  }
}

$("grant").onclick = async () => {
  let origin;
  try {
    origin = new URL($("origin").value.trim()).origin + "/*";
  } catch (e) {
    $("status").textContent = "That is not a URL.";
    return;
  }
  // permissions.request must come from a user gesture, which this click is.
  await chrome.permissions.request({ origins: [origin] });
  render();
};

render();
