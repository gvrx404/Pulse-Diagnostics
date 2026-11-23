(() => {
  'use strict';

  const themes = {
    onyx: {
      src: 'assets/onyx.webp',
      label: 'Pure Black — focused, minimal and distraction-free',
      alt: 'Pure Black theme showing Pulse Diagnostics desktop hardware monitoring overview'
    },
    glass: {
      src: 'assets/glass.webp',
      label: 'Prism Glass — a layered interface with a luminous finish',
      alt: 'Prism Glass theme showing the Pulse Diagnostics overview, eight performance metrics, and hardware details'
    },
    titanium: {
      src: 'assets/titanium.webp',
      label: 'Titanium — polished industrial steel and graphite',
      alt: 'Titanium theme showing Pulse Diagnostics hardware metrics in a metallic gray interface'
    }
  };

  const preview = document.getElementById('preview-image');
  const caption = document.getElementById('preview-caption');
  const tabs = [...document.querySelectorAll('.theme-pill')];
  let changeSequence = 0;
  const setTheme = (key) => {
    if (!themes[key] || !preview) return;
    const config = themes[key];
    const seq = ++changeSequence;
    tabs.forEach((tab) => {
      const selected = tab.dataset.theme === key;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-pressed', String(selected));
    });
    if (preview.getAttribute('src') === config.src) return;
    preview.classList.add('is-changing');
    const next = new Image();
    next.onload = () => {
      if (seq !== changeSequence) return;
      preview.src = config.src;
      preview.alt = config.alt;
      caption.textContent = config.label;
      preview.classList.remove('is-changing');
    };
    next.onerror = () => {
      if (seq === changeSequence) preview.classList.remove('is-changing');
    };
    next.src = config.src;
  };
  tabs.forEach((tab) => tab.addEventListener('click', () => setTheme(tab.dataset.theme)));

  // Determine the current repository when GitHub Pages hosts this project.
  // Local file previews continue to reference the repository's own Markdown files.
  const host = window.location.hostname.toLowerCase();
  let repoUrl = null;
  if (/^[a-z0-9-]+\.github\.io$/.test(host)) {
    const owner = host.split('.')[0];
    const slug = window.location.pathname.split('/').filter(Boolean)[0] || `${owner}.github.io`;
    repoUrl = `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}`;
    document.querySelectorAll('[data-repo-link]').forEach((a) => { a.href = repoUrl; });
    document.querySelectorAll('[data-build-link]').forEach((a) => { a.href = `${repoUrl}/blob/main/docs/BUILDING.md`; });
    document.querySelectorAll('.repo-git-url').forEach((el) => { el.textContent = `${repoUrl}.git`; });
  } else {
    // Avoid misleading clone URLs when viewing an unpublished website locally.
    document.querySelectorAll('.repo-git-url').forEach((el) => { el.textContent = '<YOUR_REPOSITORY_URL>'; });
  }

  const copy = document.getElementById('copy-build');
  if (copy) copy.addEventListener('click', async () => {
    const commands = [
      `git clone ${repoUrl ? repoUrl + '.git' : '<YOUR_REPOSITORY_URL>'}`,
      'cd PulseDiagnostics',
      'build_release.bat'
    ].join('\n');
    try {
      await navigator.clipboard.writeText(commands);
      const label = copy.querySelector('span');
      label.textContent = 'Copied';
      window.setTimeout(() => { label.textContent = 'Copy'; }, 1500);
    } catch {
      const label = copy.querySelector('span');
      label.textContent = 'Select commands';
      window.setTimeout(() => { label.textContent = 'Copy'; }, 2000);
    }
  });
})();
