
document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    const text = document.getElementById(button.dataset.copy).textContent.trim();
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = '已复制';
      document.getElementById('copy-status').textContent = '安装提示词已复制。';
      window.setTimeout(() => { button.textContent = '复制提示词'; }, 2200);
    } catch {
      const range = document.createRange();
      range.selectNodeContents(document.getElementById(button.dataset.copy));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = '请手动复制';
      document.getElementById('copy-status').textContent = '已选中提示词，请手动复制。';
    }
  });
});
