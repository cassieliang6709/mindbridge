import type { Metadata } from "next";
import { Landing } from "./Landing";

export const metadata: Metadata = {
  title: "MindBridge — 让 AI 记得你，也记得你已经改变",
  description:
    "MindBridge 为 Codex 等 AI 工具提供本地优先、可追溯且能识别变化的长期记忆。",
};

export default function Home() {
  return <Landing locale="zh" />;
}
