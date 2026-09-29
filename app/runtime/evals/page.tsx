import type { Metadata } from "next";
import { RetrievalEvalConsole } from "./RetrievalEvalConsole";

export const metadata: Metadata = {
  title: "MindBridge — retrieval lab",
  description: "Review source-labelled memory retrieval cases and rerun the baseline.",
};

export default function RetrievalEvalsPage() {
  return <RetrievalEvalConsole />;
}
