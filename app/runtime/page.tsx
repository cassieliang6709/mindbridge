import type { Metadata } from "next";
import { RuntimeConsole } from "./RuntimeConsole";

export const metadata: Metadata = {
  title: "MindBridge — local memory agent",
  description:
    "Ask a local agent to recall long-term memory through a replayable tool run.",
};

export default function RuntimePage() {
  return <RuntimeConsole />;
}
