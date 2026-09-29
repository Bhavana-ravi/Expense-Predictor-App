(() => {
  if (!window.location.hostname.endsWith('.github.io')) return;

  const dataKey = 'expense-predictor.pages.v1';
  const seededKey = 'expense-predictor.pages.sample-seeded.v1';

  try {
    if (localStorage.getItem(seededKey)) return;

    const existing = localStorage.getItem(dataKey);
    if (existing) {
      localStorage.setItem(seededKey, '1');
      return;
    }

    const pattern = [
      ['Groceries', 480, 'weekly market'],
      ['Transport', 115, 'commute'],
      ['Dining', 240, 'lunch'],
      ['Utilities', 650, 'bill payment'],
      ['Health', 180, 'pharmacy'],
      ['Groceries', 360, 'fresh produce'],
      ['Entertainment', 450, 'movie night'],
      ['Transport', 90, 'metro ride'],
      ['Shopping', 720, 'household items'],
      ['Dining', 320, 'dinner']
    ];
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    const expenses = Array.from({ length: 30 }, (_, index) => {
      const offset = 29 - index;
      const date = new Date(today);
      date.setDate(date.getDate() - offset);
      const [category, amount, note] = pattern[index % pattern.length];
      return {
        id: index + 1,
        amount,
        category,
        expense_date: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`,
        note: `Sample: ${note}`
      };
    });

    localStorage.setItem(dataKey, JSON.stringify({ expenses, goals: [] }));
    localStorage.setItem(seededKey, '1');
  } catch {
    // The app will show its empty state if browser storage is unavailable.
  }
})();