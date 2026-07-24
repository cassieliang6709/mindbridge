import type { Metadata } from "next";
import { MindBridgeDemo } from "./MindBridgeDemo";

export const metadata: Metadata = {
  title: "MindBridge — A private space to hear yourself",
  description:
    "An interactive product concept for reflective conversations, emotional patterns, and gentle next steps.",
};

export default function Home() {
  return <MindBridgeDemo />;
}
