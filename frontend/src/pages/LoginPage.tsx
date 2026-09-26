import { useState, type FormEvent } from 'react';
import { useAuth } from '../auth/AuthContext';

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError((err as Error).message);
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={onSubmit} aria-labelledby="login-title">
        <h1 id="login-title">
          DAYANERA<span>.ai</span>
        </h1>
        <p className="muted">Yerel mühendislik asistanı — yalnızca bu bilgisayarda (localhost) çalışır.</p>
        <label htmlFor="username">Kullanıcı adı</label>
        <input id="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        <label htmlFor="password">Parola</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary" disabled={busy || !username || !password}>
          {busy ? 'Giriş yapılıyor…' : 'Giriş yap'}
        </button>
        <p className="muted small">Beta: tek yerel yönetici hesabı. Şirket hesabı/SSO gelecekteki bir aşamadır.</p>
      </form>
    </div>
  );
}
