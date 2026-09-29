import type { Metadata } from "next";
import { NightShiftConsole } from "./NightShiftConsole";

export const metadata: Metadata = {
  title: "MindBridge — Night Shift",
  description: "Review durable background jobs and proposed long-term memories.",
};

export default function NightShiftPage() {
  return <NightShiftConsole />;
}
