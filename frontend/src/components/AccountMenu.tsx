import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export default function AccountMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  if (!user) return null;
  const owner = user.role === 'owner_admin';
  const links: Array<[string, string, boolean]> = [
    ['/belgeler', 'Belge arşivi', true],
    ['/inceleme', 'İnceleme kuyruğu', true],
    ['/hesap', 'Dişli hesapları', true],
    ['/hafiza', 'Hafıza', true],
    ['/oneriler', 'Öneri notları', true],
    ['/sistem', 'Sistem durumu', true],
    ['/denetim', owner ? 'Denetim kaydı' : 'İşlem geçmişim', true],
    ['/kullanicilar', 'Kullanıcılar ve kapsamlar', owner],
  ];
  return (
    <div className="account" ref={ref}>
      <button
        type="button"
        className="account-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="avatar" aria-hidden>{user.display_name.slice(0, 1).toUpperCase()}</span>
        <span className="account-name">
          {user.display_name}
          <small>{owner ? 'owner_admin' : 'member'}</small>
        </span>
      </button>
      {open && (
        <div className="account-menu" role="menu">
          {links
            .filter(([, , show]) => show)
            .map(([to, label]) => (
              <NavLink key={to} to={to} role="menuitem" className="menu-item" onClick={() => setOpen(false)}>
                {label}
              </NavLink>
            ))}
          <button type="button" role="menuitem" className="menu-item danger" onClick={() => void logout()}>
            Çıkış yap
          </button>
          <p className="menu-note">Şirket hesabı / SSO: gelecekteki şirket içi aşama (henüz yok).</p>
        </div>
      )}
    </div>
  );
}
