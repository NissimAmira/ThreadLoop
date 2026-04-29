import { StatusBar } from "./components/StatusBar";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-white">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
          <h1 className="text-xl font-semibold">
            <span className="text-brand">Thread</span>Loop
          </h1>
          <nav className="text-sm text-neutral-600">Marketplace · Sell · AR Try-On</nav>
        </div>
      </header>

      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-12">
        <section className="rounded-2xl border bg-white p-8 shadow-sm">
          <h2 className="text-2xl font-semibold mb-2">Welcome to ThreadLoop</h2>
          <p className="text-neutral-600 mb-6">
            Peer-to-peer second-hand fashion with AR try-on. Buy, sell, swap — all in one place.
          </p>
          <p className="text-sm text-neutral-500">
            This is the scaffold. Listings, search, and SSO sign-in land in subsequent PRs.
          </p>
        </section>
      </main>

      <StatusBar />
    </div>
  );
}
