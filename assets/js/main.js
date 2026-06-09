// Cecil FC United & Fury — Split Screen Interactions
document.addEventListener('DOMContentLoaded', () => {
  const sides = document.querySelectorAll('.side');
  sides.forEach(side => {
    side.addEventListener('click', (e) => {
      const target = side.id === 'united-side' ? '#united' : '#fury';
      console.log('Entering', target);
      // Future: navigate to team page
    });
  });
});
