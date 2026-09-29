(() => {
  if (!window.location.hostname.endsWith('.github.io')) return;
  document.documentElement.setAttribute('data-pages-mode', '');

  const storageKey = 'expense-predictor.pages.v1';
  const originalFetch = window.fetch.bind(window);
  const round = value => Math.round((value + Number.EPSILON) * 100) / 100;
  const todayKey = () => dateKey(new Date());

  function dateKey(value) {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function addDays(value, days) {
    const result = new Date(value);
    result.setDate(result.getDate() + days);
    return result;
  }

  function readStore() {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) || '{}');
      return {
        expenses: Array.isArray(value.expenses) ? value.expenses : [],
        goals: Array.isArray(value.goals) ? value.goals : []
      };
    } catch {
      return { expenses: [], goals: [] };
    }
  }

  function writeStore(store) {
    localStorage.setItem(storageKey, JSON.stringify(store));
  }

  function jsonResponse(data, status = 200) {
    return new Response(JSON.stringify(data), {
      status,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  function errorResponse(message, status = 400) {
    return jsonResponse({ error: message }, status);
  }

  function requestBody(options) {
    try {
      return JSON.parse(options.body || '{}');
    } catch {
      return {};
    }
  }

  function amountValue(value) {
    return Number(String(value ?? '').replaceAll(',', ''));
  }

  function forecast(rows) {
    if (!rows.length) return { has_data: false };

    const daily = new Map();
    for (const row of rows) {
      daily.set(row.expense_date, (daily.get(row.expense_date) || 0) + Number(row.amount));
    }

    const end = new Date();
    const start = addDays(end, -27);
    const current = Array.from({ length: 28 }, (_, index) =>
      round(daily.get(dateKey(addDays(start, index))) || 0)
    );
    const recent = current.slice(-7);
    const firstWeek = current.slice(-14, -7).reduce((total, value) => total + value, 0);
    const lastWeek = recent.reduce((total, value) => total + value, 0);
    const trend = firstWeek ? (lastWeek - firstWeek) / 7 : 0;
    const weekly = Array.from({ length: 7 }, (_, index) => Math.max(0, round(
      recent[index] * 0.65 + lastWeek / 7 * 0.35 + trend * (index + 1) * 0.25
    )));

    const average = current.reduce((total, value) => total + value, 0) / 28;
    const monthly = Array.from({ length: 30 }, (_, index) =>
      Math.max(0, round(average + trend * (index + 1) * 0.2))
    );
    const weekdayTotals = Array(7).fill(0);
    const weekdayCounts = Array(7).fill(0);
    current.forEach((amount, index) => {
      const weekday = (addDays(start, index).getDay() + 6) % 7;
      weekdayTotals[weekday] += amount;
      weekdayCounts[weekday] += 1;
    });

    const tomorrow = addDays(end, 1);
    monthly.forEach((amount, index) => {
      const weekday = (addDays(tomorrow, index).getDay() + 6) % 7;
      const weekdayAverage = weekdayTotals[weekday] / weekdayCounts[weekday];
      const weekdayPattern = (recent[index % recent.length] + weekdayAverage) / 2;
      monthly[index] = round(Math.max(0, amount * 0.25 + weekdayPattern * 0.75));
    });

    const categories = new Map();
    for (const row of rows) {
      categories.set(row.category, (categories.get(row.category) || 0) + Number(row.amount));
    }
    const categoryList = [...categories.entries()]
      .map(([name, amount]) => ({ name, amount: round(amount) }))
      .sort((left, right) => right.amount - left.amount);
    const activeDays = current.filter(value => value > 0).length;
    const confidence = activeDays >= 20 ? 'High' : activeDays >= 10 ? 'Medium' : 'Building';
    const discretionary = [...categories.entries()]
      .filter(([name]) => ['Dining', 'Coffee & dining', 'Entertainment', 'Shopping', 'Other'].includes(name))
      .reduce((total, [, amount]) => total + amount, 0);
    const weeklyTotal = weekly.reduce((total, value) => total + value, 0);
    const monthlyTotal = monthly.reduce((total, value) => total + value, 0);

    return {
      has_data: true,
      current,
      weekly,
      monthly,
      current_total: round(current.reduce((total, value) => total + value, 0)),
      weekly_total: round(weeklyTotal),
      monthly_total: round(monthlyTotal),
      categories: categoryList,
      arima: false,
      confidence,
      active_days: activeDays,
      week_dates: Array.from({ length: 7 }, (_, index) => dateKey(addDays(tomorrow, index))),
      month_dates: Array.from({ length: 30 }, (_, index) => dateKey(addDays(tomorrow, index))),
      weekly_reduce: round(Math.min(weeklyTotal * 0.12, discretionary * 0.25)),
      monthly_reduce: round(Math.min(monthlyTotal * 0.12, discretionary))
    };
  }

  function nextId(rows) {
    return rows.reduce((highest, row) => Math.max(highest, Number(row.id) || 0), 0) + 1;
  }

  window.fetch = async (resource, options = {}) => {
    const rawUrl = typeof resource === 'string' ? resource : resource.url;
    const url = new URL(rawUrl, window.location.href);
    if (!url.pathname.startsWith('/api/')) return originalFetch(resource, options);

    const method = (options.method || 'GET').toUpperCase();
    const body = requestBody(options);
    const store = readStore();
    const sortExpenses = () => store.expenses.sort((left, right) =>
      right.expense_date.localeCompare(left.expense_date) || right.id - left.id
    );
    const sortGoals = () => store.goals.sort((left, right) => right.id - left.id);

    try {
      if (url.pathname === '/api/me' && method === 'GET') {
        return jsonResponse({ authenticated: true, username: 'Local profile' });
      }
      if (url.pathname === '/api/logout' && method === 'POST') {
        return jsonResponse({ ok: true });
      }
      if (url.pathname === '/api/expenses' && method === 'GET') {
        return jsonResponse(sortExpenses());
      }
      if (url.pathname === '/api/expenses' && method === 'POST') {
        const amount = round(amountValue(body.amount));
        const category = String(body.category || '').trim().slice(0, 40);
        const expenseDate = String(body.expense_date || '');
        if (!Number.isFinite(amount) || amount <= 0 || amount > 10000000 || !category ||
            !/^\d{4}-\d{2}-\d{2}$/.test(expenseDate) || expenseDate > todayKey()) {
          return errorResponse('Enter a positive amount, a category, and a date that is not in the future.');
        }
        store.expenses.push({
          id: nextId(store.expenses),
          amount,
          category,
          expense_date: expenseDate,
          note: String(body.note || '').trim().slice(0, 160)
        });
        writeStore(store);
        return jsonResponse({ ok: true }, 201);
      }
      const expenseMatch = url.pathname.match(/^\/api\/expenses\/(\d+)$/);
      if (expenseMatch && method === 'DELETE') {
        store.expenses = store.expenses.filter(row => row.id !== Number(expenseMatch[1]));
        writeStore(store);
        return jsonResponse({ ok: true });
      }
      if (url.pathname === '/api/sample-week' && method === 'POST') {
        if (store.expenses.length) {
          return errorResponse('Sample data can only be added to an empty expense list.', 409);
        }
        const samples = [
          ['Groceries', 680, 'Weekly market'],
          ['Transport', 140, 'Metro and auto'],
          ['Coffee & dining', 245, 'Lunch with friends'],
          ['Utilities', 510, 'Mobile recharge'],
          ['Groceries', 390, 'Fresh produce'],
          ['Entertainment', 180, 'Movie night'],
          ['Health', 325, 'Pharmacy']
        ];
        store.expenses = samples.map(([category, amount, note], index) => ({
          id: index + 1,
          amount,
          category,
          expense_date: dateKey(addDays(new Date(), index - 6)),
          note
        }));
        writeStore(store);
        return jsonResponse({ ok: true });
      }
      if (url.pathname === '/api/goals' && method === 'GET') {
        return jsonResponse(sortGoals());
      }
      if (url.pathname === '/api/goals' && method === 'POST') {
        const title = String(body.title || '').trim().slice(0, 80);
        const target = round(amountValue(body.target_amount));
        const saved = round(amountValue(body.saved_amount ?? 0));
        const targetDate = String(body.target_date || '');
        if (!title || !Number.isFinite(target) || target <= 0 || !Number.isFinite(saved) ||
            saved < 0 || saved > target ||
            (targetDate && !/^\d{4}-\d{2}-\d{2}$/.test(targetDate))) {
          return errorResponse('Enter a goal name, a positive target, and a saved amount no greater than the target.');
        }
        store.goals.push({
          id: nextId(store.goals),
          title,
          target_amount: target,
          saved_amount: saved,
          target_date: targetDate || null
        });
        writeStore(store);
        return jsonResponse({ ok: true }, 201);
      }
      const savingMatch = url.pathname.match(/^\/api\/goals\/(\d+)\/save$/);
      if (savingMatch && method === 'POST') {
        const goal = store.goals.find(row => row.id === Number(savingMatch[1]));
        const amount = round(amountValue(body.amount));
        if (!goal || !Number.isFinite(amount) || amount <= 0 || goal.saved_amount + amount > goal.target_amount) {
          return errorResponse('That amount would exceed the goal target, or the goal was not found.');
        }
        goal.saved_amount = round(goal.saved_amount + amount);
        writeStore(store);
        return jsonResponse({ ok: true });
      }
      if (url.pathname === '/api/predictions' && method === 'GET') {
        return jsonResponse(forecast(store.expenses));
      }
      return errorResponse('This action is not available in the browser-only version.', 404);
    } catch {
      return errorResponse('This browser could not save the data. Check that browser storage is available.', 500);
    }
  };

  const style = document.createElement('style');
  style.textContent = 'html[data-pages-mode] #logout { display: none !important; }';
  document.head.append(style);

  document.addEventListener('DOMContentLoaded', () => {
    const appRoot = document.querySelector('#app');
    if (!appRoot) return;
    const updateAboutCopy = () => {
      const heading = [...appRoot.querySelectorAll('.about h2')]
        .find(item => item.textContent.trim() === 'Your account and privacy');
      if (!heading) return;
      heading.textContent = 'Your data on this browser';
      const paragraph = heading.nextElementSibling;
      if (paragraph) {
        paragraph.textContent = 'Expenses and goals are saved in this browser only. There is no account login or cloud sync. Clearing this browser’s site data will erase saved records.';
      }
    };
    new MutationObserver(updateAboutCopy).observe(appRoot, { childList: true, subtree: true });
    updateAboutCopy();
  });
})();
