import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ConsolePage } from "./pages/ConsolePage";
import { AuditPage } from "./pages/AuditPage";
import { PolicyPage } from "./pages/PolicyPage";
import { SafetyPage } from "./pages/SafetyPage";
import { AboutPage } from "./pages/AboutPage";
import { HistoryPage } from "./pages/HistoryPage";
import { SettingsPage } from "./pages/SettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/console" replace />} />
        <Route path="console" element={<ConsolePage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="policy" element={<PolicyPage />} />
        <Route path="safety" element={<SafetyPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<Navigate to="/console" replace />} />
      </Route>
    </Routes>
  );
}
