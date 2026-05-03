import { Route, Routes } from "react-router-dom";
import { AppHeader } from "./components/AppHeader";
import { StatusBar } from "./components/StatusBar";
import { HomePage } from "./pages/HomePage";
import { SignInPage } from "./pages/SignInPage";
import { MePage } from "./pages/MePage";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <AppHeader />

      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/sign-in" element={<SignInPage />} />
        <Route path="/me" element={<MePage />} />
      </Routes>

      <StatusBar />
    </div>
  );
}
