import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import Layout from './components/Layout';
import LoginPage from './pages/LoginPage';
import ChatPage from './pages/ChatPage';
import DocumentsPage from './pages/DocumentsPage';
import ReviewPage from './pages/ReviewPage';
import AuditPage from './pages/AuditPage';
import UsersPage from './pages/UsersPage';
import SystemPage from './pages/SystemPage';
import NotesPage from './pages/NotesPage';
import CalculatorPage from './pages/CalculatorPage';
import MemoryPage from './pages/MemoryPage';

export default function App() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="center-screen" role="status">
        Yükleniyor…
      </div>
    );
  }
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }
  const owner = user.role === 'owner_admin';
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ChatPage />} />
        <Route path="c/:conversationId" element={<ChatPage />} />
        <Route path="belgeler" element={<DocumentsPage />} />
        <Route path="inceleme" element={<ReviewPage />} />
        <Route path="hesap" element={<CalculatorPage />} />
        <Route path="hafiza" element={<MemoryPage />} />
        <Route path="oneriler" element={<NotesPage />} />
        <Route path="sistem" element={<SystemPage />} />
        <Route path="denetim" element={<AuditPage />} />
        <Route path="kullanicilar" element={owner ? <UsersPage /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
