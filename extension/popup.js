const API_BASE = 'http://localhost:8000/api/v1';
const APP_BASE = 'http://localhost:3000';

document.addEventListener('DOMContentLoaded', async () => {
  const topicInput = document.getElementById('topic');
  const createBtn = document.getElementById('create-btn');
  const status = document.getElementById('status');

  // Get current tab info
  const resp = await chrome.runtime.sendMessage({ type: 'GET_CURRENT_URL' });
  if (resp?.title) {
    topicInput.value = resp.title;
  }

  createBtn.addEventListener('click', async () => {
    const topic = topicInput.value.trim();
    if (!topic) return;

    createBtn.disabled = true;
    status.textContent = 'Creating...';
    status.className = 'status';

    try {
      const response = await fetch(`${API_BASE}/rabbitholes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic,
          depth: 2,
          source_types: ['article', 'reddit'],
          seed_url: resp?.url,
        }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();

      status.textContent = 'Created! Opening...';
      status.className = 'status success';

      setTimeout(() => {
        chrome.tabs.create({ url: `${APP_BASE}/rabbit-holes/${data.id}` });
        window.close();
      }, 800);
    } catch (err) {
      status.textContent = `Error: ${err.message}`;
      status.className = 'status error';
      createBtn.disabled = false;
    }
  });
});
