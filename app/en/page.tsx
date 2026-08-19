import type { Metadata } from "next";
import { Landing } from "../Landing";

export const metadata: Metadata = {
  title: "MindBridge — memory that knows you changed",
  description:
    "Local-first, traceable long-term memory for Codex and other AI tools — with sources, dates, and change over time.",
};

export default function EnglishHome() {
  return <Landing locale="en" />;
}
