import App from "../App";
import { useAuth } from "./useAuth";
import { AuthScreen } from "./AuthScreen";

// Gate the whole SPA behind account auth. When no account is active, show the
// login/registration screen. When one is, mount the app keyed by the account id
// so switching accounts fully remounts it — guaranteeing per-account isolation
// of in-memory state (active tab, deploy cards, fetched settings).
export function AuthGate() {
  const { accounts, activeId } = useAuth();
  const active = !!activeId && accounts.some(a => a.id === activeId);
  if (!active) return <AuthScreen />;
  return <App key={activeId} />;
}
