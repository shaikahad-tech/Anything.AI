/**
 * Background service worker for the Rabbit Hole Mapper extension.
 * Handles context menu creation and communication with the API.
 */

const API_BASE = 'http://localhost:8000/api/v1';

// Create context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'send-to-rabbit-hole-new',
    title: 'New Rabbit Hole from this page',
    contexts: ['page', 'link'],
  });
  chrome.contextMenus.create({
    id: 'send-to-rabbit-hole-existing',
    title: 'Add to existing Rabbit Hole...',
    contexts: ['page', 'link'],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.pageUrl || tab?.url;
  const title = tab?.title || '';

  if (info.menuItemId === 'send-to-rabbit-hole-new') {
    await createNewRabbitHole(url, title);
  } else if (info.menuItemId === 'send-to-rabbit-hole-existing') {
    // Open popup for user to select existing rabbit hole
    chrome.action.openPopup();
  }
});

async function createNewRabbitHole(url, title) {
  try {
    const response = await fetch(`${API_BASE}/rabbitholes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: title || url,
        depth: 2,
        source_types: ['article'],
        seed_url: url,
      }),
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    // Open the rabbit hole in a new tab
    chrome.tabs.create({
      url: `http://localhost:3000/rabbit-holes/${data.id}`,
    });
  } catch (err) {
    console.error('Failed to create rabbit hole:', err);
  }
}

// Store the pending seed URL so the popup can pick it up
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'GET_CURRENT_URL') {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      sendResponse({ url: tabs[0]?.url, title: tabs[0]?.title });
    });
    return true; // keep channel open for async response
  }
});
