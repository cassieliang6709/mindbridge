import type { Metadata } from "next";
import {
  DM_Sans,
  JetBrains_Mono,
  Manrope,
  Nanum_Pen_Script,
} from "next/font/google";
import "./globals.css";

const body = DM_Sans({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const display = Manrope({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["600", "700", "800"],
});

const accent = Nanum_Pen_Script({
  variable: "--font-accent",
  subsets: ["latin"],
  weight: "400",
});

const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "700"],
});

export const metadata: Metadata = {
  title: "MindBridge — your AI conversations, written up as daily memory",
  description:
    "MindBridge parses the transcripts your local AI coding tools already write, turns each day into one memory card, and serves the same three-tier store to any MCP client. Parsing and storage are local by default.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body
        className={`${body.variable} ${display.variable} ${accent.variable} ${mono.variable}`}
      >
        {children}
      </body>
    </html>
  );
}
