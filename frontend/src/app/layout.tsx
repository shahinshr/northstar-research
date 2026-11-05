import type { Metadata } from "next";
import AppProviders from "@/components/AppProviders";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Northstar Research",
  description: "Daily investment discovery, transparent scoring and comparison across US, Canadian and UK equities.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* apply saved theme before paint to avoid a flash */}
        <script dangerouslySetInnerHTML={{ __html: `try{var q=new URLSearchParams(location.search).get("theme");var t=q||localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}` }} />
      </head>
      <body className="min-h-screen antialiased">
        <AppProviders>
          <Nav />
          <main className="mx-auto max-w-[1600px] px-4 pb-16 pt-5">{children}</main>
        </AppProviders>
      </body>
    </html>
  );
}
