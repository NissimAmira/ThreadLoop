import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function AppHeader() {
  const { state } = useAuth();

  return (
    <header className="border-b bg-white">
      <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
        <Link to="/" className="text-xl font-semibold focus:outline-none focus:ring-2 focus:ring-brand rounded">
          <span className="text-brand">Thread</span>Loop
        </Link>
        <nav
          aria-label="User menu"
          data-testid="app-header-user"
          data-auth-status={state.status}
          className="text-sm text-neutral-600 flex items-center gap-3"
        >
          {state.status === "authenticated" ? (
            <Link
              to="/me"
              className="text-neutral-800 hover:text-brand focus:outline-none focus:ring-2 focus:ring-brand rounded"
              data-testid="app-header-display-name"
            >
              {state.user.displayName}
            </Link>
          ) : state.status === "loading" ? (
            <span aria-hidden className="text-neutral-400">…</span>
          ) : (
            <Link
              to="/sign-in"
              className="text-brand hover:text-brand-dark focus:outline-none focus:ring-2 focus:ring-brand rounded"
            >
              Sign in
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
