import "./globals.css";
import type { Metadata } from "next";
import Sidebar from "@/components/Sidebar";
import { AuthProvider } from "@/lib/auth";
import AuthGate from "@/components/AuthGate";

export const metadata: Metadata = {
  title: "ResolveFlow | Autonomous AI Customer Support Platform",
  description: "Deterministic, policy-controlled multi-agent customer support resolution system",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full font-sans bg-slate-50 text-slate-900 antialiased selection:bg-indigo-500 selection:text-white">
        <AuthProvider>
          <AuthGate>
            <div className="min-h-screen md:flex">
              <Sidebar />
              <div className="flex-1 flex flex-col min-w-0 overflow-y-auto">
                <main className="flex-1 p-4 md:p-8 max-w-7xl w-full mx-auto">{children}</main>
              </div>
            </div>
          </AuthGate>
        </AuthProvider>
      </body>
    </html>
  );
}
