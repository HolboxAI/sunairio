const AUTH_TOKEN_KEY = 'sunairio_nl2sql_token';
const AUTH_USER_KEY = 'sunairio_nl2sql_user';

function getToken() {
    return localStorage.getItem(AUTH_TOKEN_KEY) || '';
}

function getUser() {
    try {
        const raw = localStorage.getItem(AUTH_USER_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

function setAuth(token, user) {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
}

function clearAuth() {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
}

function authHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
}

function displayName(user) {
    if (!user) return 'User';
    return user.metadata_username || (user.email || '').split('@')[0] || 'User';
}

async function verifySession() {
    const token = getToken();
    if (!token) return null;

    try {
        const res = await fetch('/api/me', { headers: authHeaders() });
        if (!res.ok) {
            clearAuth();
            return null;
        }
        const data = await res.json();
        const user = data.user || data;
        setAuth(token, user);
        return user;
    } catch {
        return getUser();
    }
}

async function login(email, password) {
    const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data.detail;
        const message = typeof detail === 'string' ? detail : 'Invalid credentials';
        throw new Error(message);
    }
    setAuth(data.access_token, data.user);
    return data.user;
}

function logout() {
    clearAuth();
    window.location.href = '/';
}
