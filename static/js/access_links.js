(() => {
  const kind = document.getElementById('id_kind');
  const targets = {class: 'class_group', student: 'student', assignment: 'assignment'};
  const descriptions = {
    site: 'Доступ до поточного сайту у звичному режимі. Учні самі вибирають клас та ім’я.',
    class: 'Лише завдання та роботи вибраного класу. Учень вибирає своє ім’я.',
    student: 'Завдання класу та власні роботи учня. Ім’я й клас підставляються автоматично.',
    assignment: 'Лише вибране завдання, його матеріали, здача та роботи до нього.'
  };
  function update() {
    ['class_group', 'student', 'assignment'].forEach(name => {
      const row = document.querySelector(`[data-link-field="${name}"]`);
      if (row) row.hidden = targets[kind.value] !== name;
      const field = document.getElementById(`id_${name}`);
      if (field) field.disabled = targets[kind.value] !== name;
    });
    document.getElementById('access-kind-help').textContent = descriptions[kind.value] || '';
  }
  if (kind) {kind.addEventListener('change', update); update();}
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copy);
    let success = false;
    try {
      if (window.isSecureContext && navigator.clipboard) {await navigator.clipboard.writeText(input.value); success = true;}
      else {input.focus(); input.select(); success = document.execCommand('copy');}
    } catch (_) {input.focus(); input.select();}
    document.getElementById('access-copy-status').textContent = success ? 'Посилання скопійовано.' : 'Адресу виділено. Натисніть Ctrl+C, щоб скопіювати.';
  }));
})();
