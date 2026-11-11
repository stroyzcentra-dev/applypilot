import type { Metadata } from "next";
import { Public_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Guide } from "./guide";

const sans = Public_Sans({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "ApplyPilot",
  description: "Watch your job-search agent work, and take over when you need to.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sans.variable}>
      <body>
        <header className="masthead">
          <Link href="/" className="wordmark">ApplyPilot</Link>
          <nav>
            <Link href="/">Jobs</Link>
            <Link href="/profile">Profile</Link>
            <Link href="/settings">Settings</Link>
            <Guide />
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
