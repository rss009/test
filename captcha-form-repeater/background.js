// The runner lives in a full tab rather than the popup so that it keeps its
// state (pending captcha, iteration count) when the user clicks elsewhere.
chrome.action.onClicked.addListener(() => {
  chrome.tabs.create({ url: chrome.runtime.getURL('runner.html') });
});
