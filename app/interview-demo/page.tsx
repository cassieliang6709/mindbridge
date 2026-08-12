import type { Metadata } from "next";
import { InterviewDemo } from "./InterviewDemo";

export const metadata: Metadata = {
  title: "Project Atlas — MindBridge interview demo",
  description:
    "A privacy-safe synthetic workspace showing how MindBridge writes, updates, rejects and recalls durable engineering memory.",
};

export default function InterviewDemoPage() {
  return <InterviewDemo />;
}
