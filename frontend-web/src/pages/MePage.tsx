import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function MePage() {
  const { state, signOut } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (state.status === "anonymous") {
      navigate("/sign-in?next=/me", { replace: true });
    }
  }, [state.status, navigate]);

  if (state.status === "loading") {
    return (
      <main className="flex-1 max-w-md mx-auto w-full px-6 py-16" aria-busy="true">
        <p className="text-neutral-500">Loading your profile…</p>
      </main>
    );
  }

  if (state.status !== "authenticated") {
    return null;
  }

  const { user } = state;

  return (
    <main className="flex-1 max-w-md mx-auto w-full px-6 py-16">
      <section
        className="rounded-2xl border bg-white p-8 shadow-sm"
        aria-labelledby="me-heading"
      >
        <h2 id="me-heading" className="text-2xl font-semibold mb-6">
          Your profile
        </h2>

        <dl className="space-y-4">
          <div>
            <dt className="text-xs uppercase tracking-wide text-neutral-500">Display name</dt>
            <dd
              data-testid="me-display-name"
              className="mt-1 text-base font-medium text-neutral-900"
            >
              {user.displayName}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-neutral-500">Email</dt>
            <dd data-testid="me-email" className="mt-1 text-base text-neutral-900">
              {user.email ?? <span className="text-neutral-400">Not provided</span>}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-neutral-500">Provider</dt>
            <dd className="mt-1 text-base text-neutral-900 capitalize">{user.provider}</dd>
          </div>
        </dl>

        <button
          type="button"
          onClick={() => {
            void signOut();
            navigate("/", { replace: true });
          }}
          className="mt-8 inline-flex items-center justify-center rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-brand"
        >
          Sign out
        </button>
      </section>
    </main>
  );
}
