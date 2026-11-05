// Apply the saved theme before first paint so there is no flash. Light is the default.
// Loaded in <head> without defer so it runs before the page is drawn; a file, not inline, for the CSP.
(function () {
  try {
    if (localStorage.getItem('finsight-theme') === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
      // The phone's status bar matches the page from the first frame.
      document.querySelector('meta[name="theme-color"]').setAttribute('content', '#0F1114');
    }
  } catch (e) { /* storage blocked: keep the light default */ }
})();
