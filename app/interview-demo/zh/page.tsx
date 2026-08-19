import type { Metadata } from "next";
import { InterviewDemo } from "../InterviewDemo";

export const metadata: Metadata = {
  title: "MindBridge Companion Loop — 面试演示",
  description: "使用完全虚构的数据，体验 MindBridge 如何区分猜测与确认、记住变化，并展示记忆来源。",
};

export default function InterviewDemoChinesePage() {
  return <InterviewDemo locale="zh" />;
}
