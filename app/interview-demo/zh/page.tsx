import type { Metadata } from "next";
import { InterviewDemo } from "../InterviewDemo";

export const metadata: Metadata = {
  title: "Project Atlas — MindBridge 面试演示",
  description: "使用完全虚构的数据，体验 MindBridge 如何写入、更新、拒绝和召回长期工程记忆。",
};

export default function InterviewDemoChinesePage() {
  return <InterviewDemo locale="zh" />;
}
