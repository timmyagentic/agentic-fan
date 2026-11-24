async function copyPrompt(top) {
  const button = top.querySelector("button[data-copy]");
  const prompt = document.getElementById(button.dataset.copy);
  const status = document.getElementById("copy-status");
  const label = (value) => { top.querySelector("button").textContent = value; };
  try {
    await navigator.clipboard.writeText(prompt.textContent.trim());
    label("已复制");
    status.textContent = "安装提示词已复制。";
    window.setTimeout(() => label("复制提示词"), 2200);
  } catch {
    const range = document.createRange();
    range.selectNodeContents(prompt);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    label("请手动复制");
    status.textContent = "已选中提示词，请手动复制。";
  }
}

for (const top of document.querySelectorAll(".copy-top")) {
  top.querySelector("button[data-copy]").addEventListener("click", () => copyPrompt(top));
  // The installation section can be several screens below the product overview.
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    import("./ui/controls.js").then(({ mountCopy }) => mountCopy(top, copyPrompt)).catch(() => {
      // The original button stays usable if the optional module is unavailable.
    });
  }, { rootMargin: "160px" });
  observer.observe(top);
}
