(() => {
  'use strict';
  document.querySelectorAll('[data-copy-target]').forEach(button => {
    button.addEventListener('click', async () => {
      const source = document.getElementById(button.dataset.copyTarget);
      if (!source) return;
      const value = source.value ?? source.textContent;
      let copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(value); copied = true;
        }
      } catch (_) { /* LAN HTTP uses the local selection fallback below. */ }
      if (!copied) {
        const previous = document.activeElement;
        const temporary = document.createElement('textarea');
        temporary.value = value; temporary.setAttribute('readonly', '');
        Object.assign(temporary.style, {position: 'fixed', opacity: '0', top: '0', left: '0'});
        document.body.appendChild(temporary); temporary.select();
        try {copied = document.execCommand('copy');} catch (_) {copied = false;}
        temporary.remove(); previous?.focus({preventScroll: true});
      }
      const status = document.querySelector('[data-connection-copy-status]');
      if (status) status.textContent = copied ? 'Скопійовано.' : 'Виділіть значення й скопіюйте вручну.';
      if (!copied && typeof source.select === 'function') source.select();
    });
  });
})();
