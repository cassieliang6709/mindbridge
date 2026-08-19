import type { Metadata } from "next";
import { InterviewDemo } from "./InterviewDemo";

export const metadata: Metadata = {
  title: "MindBridge Companion Loop — interview demo",
  description:
    "A privacy-safe interactive scenario showing how MindBridge separates inference from confirmation, remembers change, and cites its memory.",
};

export default function InterviewDemoPage() {
  return <InterviewDemo />;
}
