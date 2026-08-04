import type { Metadata } from "next";
import { MemoryConsole } from "./MemoryConsole";

export const metadata: Metadata = {
  title: "MindBridge diary — one memory card a day",
  description:
    "The MindBridge diary: each day written up from that day's AI transcripts, with the raw T1/T2/T3 memory state one click underneath.",
};

export default function DemoPage() {
  return <MemoryConsole />;
}
