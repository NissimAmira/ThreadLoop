import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function HomePage() {
  const { state } = useAuth();
  const signedIn = state.status === "authenticated";

  return (
    <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-12">
      <section className="rounded-2xl border bg-white p-8 shadow-sm">
        <h2 className="text-2xl font-semibold mb-2">Welcome to ThreadLoop</h2>
        <p className="text-neutral-600 mb-6">
          Peer-to-peer second-hand fashion with AR try-on. Buy, sell, swap — all in one place.
        </p>
        {signedIn ? (
          <Link
            to="/me"
            className="inline-flex items-center justify-center rounded-md bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-dark focus:outline-none focus:ring-2 focus:ring-brand"
          >
            View your profile
          </Link>
        ) : (
          <Link
            to="/sign-in"
            className="inline-flex items-center justify-center rounded-md bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-dark focus:outline-none focus:ring-2 focus:ring-brand"
          >
            Sign in to get started
          </Link>
        )}
      </section>
    </main>
  );
}
