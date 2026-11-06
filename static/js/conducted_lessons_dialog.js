function openConductedLessonsModal() {
    var modal = document.getElementById('conducted-lessons-modal');
    if (modal) modal.style.display = 'flex';
}
function closeConductedLessonsModal() {
    var modal = document.getElementById('conducted-lessons-modal');
    if (modal) modal.style.display = 'none';
}
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeConductedLessonsModal();
});
document.addEventListener('click', function(e) {
    var modal = document.getElementById('conducted-lessons-modal');
    if (modal && e.target === modal) closeConductedLessonsModal();
});
