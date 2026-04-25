import type { Metadata } from "next";
import { Silkscreen, Press_Start_2P, VT323, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

const silkscreen = Silkscreen({
  weight: ["400", "700"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-silkscreen",
});

const pressStart = Press_Start_2P({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-press-start",
});

const vt323 = VT323({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-vt323",
});

const ibmPlex = IBM_Plex_Mono({
  weight: ["400", "500", "600"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-ibm-plex",
});

export const metadata: Metadata = {
  title: "Outpost",
  description:
    "A daily narrative roguelike. Four advisors run a fragile colony. You give the orders. Permadeath.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      data-setting="arctic"
      className={`${silkscreen.variable} ${pressStart.variable} ${vt323.variable} ${ibmPlex.variable} h-full`}
    >
      <body className="min-h-full flex flex-col font-body">{children}</body>
    </html>
  );
}
