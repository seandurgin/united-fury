// United-Fury — Panel interactions
document.addEventListener('DOMContentLoaded', function() {
  const united = document.getElementById('panel-united');
  const fury   = document.getElementById('panel-fury');

  [united, fury].forEach(function(panel) {
    panel.addEventListener('click', function(e) {
      // Only navigate if clicking the Enter button directly
      if (!e.target.classList.contains('enter-btn')) {
        const btn = panel.querySelector('.enter-btn');
        if (btn) btn.click();
      }
    });
  });
});
