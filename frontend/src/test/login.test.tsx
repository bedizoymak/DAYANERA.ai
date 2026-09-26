import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { AuthProvider } from '../auth/AuthContext';
import { adminUser, calls, installFetch, json } from './mockApi';

function renderApp() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('Giriş ekranı', () => {
  it('oturum yoksa giriş formunu gösterir ve hatalı parolada Türkçe hata verir', async () => {
    installFetch([
      { method: 'GET', path: /\/auth\/me$/, handler: () => json({ detail: 'Oturum açmanız gerekiyor.' }, 401) },
      { method: 'POST', path: /\/auth\/login$/, handler: () => json({ detail: 'Kullanıcı adı veya parola hatalı.' }, 401) },
    ]);
    renderApp();
    expect(await screen.findByRole('button', { name: 'Giriş yap' })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Kullanıcı adı'), 'admin');
    await userEvent.type(screen.getByLabelText('Parola'), 'yanlis');
    await userEvent.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Kullanıcı adı veya parola hatalı.');
    // password field is cleared after a failed attempt and never persisted in web storage
    expect(screen.getByLabelText('Parola')).toHaveValue('');
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it('başarılı girişte CSRF başlığıyla istek atar ve sohbet ekranını açar', async () => {
    installFetch([
      { method: 'GET', path: /\/auth\/me$/, handler: () => json({ detail: 'x' }, 401) },
      { method: 'POST', path: /\/auth\/login$/, handler: () => json(adminUser) },
      { method: 'GET', path: /\/conversations\?status=active$/, handler: () => json([]) },
      { method: 'GET', path: /\/readiness$/, handler: () => json({ ready: true, database: { ok: true }, ollama: { reachable: true, model_available: true }, messages: [] }) },
      { method: 'GET', path: /\/system\/status$/, handler: () => json({ corpus: { empty_verified_corpus: false, jobs: {} } }) },
    ]);
    renderApp();
    await userEvent.type(await screen.findByLabelText('Kullanıcı adı'), 'admin');
    await userEvent.type(screen.getByLabelText('Parola'), 'dogru-parola');
    await userEvent.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(await screen.findByRole('button', { name: '+ Yeni sohbet' })).toBeInTheDocument();
    const login = calls.find((c) => c.url.endsWith('/auth/login'));
    expect(login?.body).toEqual({ username: 'admin', password: 'dogru-parola' });
    expect(login?.headers['X-DAYANERA-CSRF']).toBe('1');
    await waitFor(() => expect(screen.getByText('DAYANERA.ai', { selector: 'h1' })).toBeInTheDocument());
    expect(window.localStorage.length).toBe(0);
  });
});
