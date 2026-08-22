"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import type { Locale } from "./site";

const FRAME_DURATION_MS = 4400;

const frames = {
  zh: [
    {
      label: "Claude Code 写入",
      src: "/hero-claude-write.png",
      alt: "Claude Code 通过 MindBridge MCP 保存 Python 项目优先使用 uv 的偏好",
    },
    {
      label: "MindBridge 保存",
      src: "/hero-memory-saved.png",
      alt: "MindBridge 返回记忆编号 204 和 operational namespace 的保存结果",
    },
    {
      label: "Codex 召回",
      src: "/hero-codex-recall.png",
      alt: "Codex 从 MindBridge 召回同一条 uv 偏好并继续工作",
    },
  ],
  en: [
    {
      label: "Claude Code writes",
      src: "/hero-claude-write.png",
      alt: "Claude Code saves a preference for uv in Python projects through the MindBridge MCP",
    },
    {
      label: "MindBridge saves",
      src: "/hero-memory-saved.png",
      alt: "MindBridge returns memory 204 in the operational namespace",
    },
    {
      label: "Codex recalls",
      src: "/hero-codex-recall.png",
      alt: "Codex recalls the same uv preference from MindBridge and continues working",
    },
  ],
} satisfies Record<Locale, { label: string; src: string; alt: string }[]>;

export function HeroMemoryDemo({ locale }: { locale: Locale }) {
  const [frame, setFrame] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const localizedFrames = frames[locale];

  useEffect(() => {
    if (isPaused || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }

    const timer = window.setInterval(() => {
      setFrame((current) => (current + 1) % localizedFrames.length);
    }, FRAME_DURATION_MS);

    return () => window.clearInterval(timer);
  }, [isPaused, localizedFrames.length]);

  return (
    <figure
      className="memory-demo"
      aria-label={
        locale === "zh"
          ? "同一条本地记忆从 Claude Code 写入，再由 Codex 召回"
          : "One local memory written in Claude Code and recalled in Codex"
      }
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
      onFocusCapture={() => setIsPaused(true)}
      onBlurCapture={() => setIsPaused(false)}
    >
      <div className="memory-laptop" aria-live="polite">
        {localizedFrames.map((item, index) => (
          <Image
            className={`memory-laptop-frame ${frame === index ? "is-active" : ""}`}
            src={item.src}
            alt={frame === index ? item.alt : ""}
            fill
            priority={index === 0}
            sizes="(max-width: 1020px) 100vw, 62vw"
            aria-hidden={frame !== index}
            key={item.src}
          />
        ))}
      </div>

      <figcaption className="memory-demo-caption">
        <span>{localizedFrames[frame].label}</span>
        <div
          className="memory-demo-controls"
          aria-label={locale === "zh" ? "切换演示画面" : "Change demo frame"}
        >
          {localizedFrames.map((item, index) => (
            <button
              type="button"
              className={frame === index ? "is-active" : ""}
              aria-label={item.label}
              aria-pressed={frame === index}
              onClick={() => setFrame(index)}
              key={item.label}
            />
          ))}
        </div>
        <span>{locale === "zh" ? "同一条记忆 · 本机 MCP" : "One memory · local MCP"}</span>
      </figcaption>
    </figure>
  );
}
