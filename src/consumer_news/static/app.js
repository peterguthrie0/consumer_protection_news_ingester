document.addEventListener('htmx:responseError', (event) => {
  const banner = document.getElementById('request-error');
  banner.hidden = false;
  banner.textContent = `The request failed (${event.detail.xhr.status}). Reload the page and try again.`;
});
document.addEventListener('htmx:sendError', () => {
  const banner = document.getElementById('request-error');
  banner.hidden = false;
  banner.textContent = 'The local server could not be reached. Check that cp-news web is still running.';
});
document.addEventListener('htmx:beforeRequest', () => {
  document.getElementById('request-error').hidden = true;
});
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  const field = document.getElementById(button.dataset.copy);
  const status = document.getElementById('copy-status');
  try {
    await navigator.clipboard.writeText(field.value);
    status.textContent = 'Copied to clipboard.';
  } catch {
    field.focus();
    field.select();
    status.textContent = 'Text selected. Press Command+C (Mac) or Ctrl+C to copy.';
  }
});
