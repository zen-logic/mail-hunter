let _themes = null;

export async function loadThemes() {
    if (_themes) return _themes;
    const res = await fetch('/api/themes', { credentials: 'same-origin' });
    if (res.ok) {
        const data = await res.json();
        _themes = data.data || [];
    } else {
        _themes = [{ name: 'default', builtIn: true }];
    }
    return _themes;
}

export async function loadThemeNames() {
    const themes = await loadThemes();
    return themes.map(t => t.name);
}

export function applyTheme(name) {
    const link = document.getElementById('theme-link');
    if (!link) return;
    if (name === 'default') {
        link.removeAttribute('href');
    } else {
        link.setAttribute('href', `/static/css/themes/${name}.css`);
    }
    localStorage.setItem('mh-theme', name);
}

// Apply saved theme on module load
const saved = localStorage.getItem('mh-theme');
if (saved && saved !== 'default') {
    applyTheme(saved);
}
