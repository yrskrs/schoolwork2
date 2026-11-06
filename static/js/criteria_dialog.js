function openCriteriaModal() {
    var modal = document.getElementById('criteria-modal');
    if (modal) {
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
}

function closeCriteriaModal() {
    var modal = document.getElementById('criteria-modal');
    if (modal) {
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeCriteriaModal();
    }
});

function initializeCriteriaDialog() {
    var modal = document.getElementById('criteria-modal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                closeCriteriaModal();
            }
        });
    }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeCriteriaDialog);
else initializeCriteriaDialog();
