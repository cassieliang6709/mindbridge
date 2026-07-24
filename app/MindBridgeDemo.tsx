"use client";

import { FormEvent, useMemo, useState } from "react";

type Message = {
  id: number;
  author: "you" | "mira";
  text: string;
};

const starterMessages: Message[] = [
  {
    id: 1,
    author: "you",
    text: "最近每次准备投简历，我都会拖到很晚。明明很在意，却一直不敢点提交。",
  },
  {
    id: 2,
    author: "mira",
    text: "我记得你上周提到：越重要的机会，你越容易把“还没准备好”当作保护自己的方式。今天的犹豫，更像是在担心失败，还是担心一旦成功，生活会发生变化？",
  },
];

const prompts = [
  "我其实更怕别人看到我还不够好。",
  "我想先把今天能做的一小步找出来。",
  "帮我梳理一下刚才反复出现的想法。",
];

export function MindBridgeDemo() {
  const [activeView, setActiveView] = useState<"conversation" | "reflection">(
    "conversation",
  );
  const [messages, setMessages] = useState(starterMessages);
  const [draft, setDraft] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [checkIn, setCheckIn] = useState(3);

  const rememberedCount = useMemo(
    () => messages.filter((message) => message.author === "you").length + 6,
    [messages],
  );

  function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || isThinking) return;

    const nextId = Date.now();
    setMessages((current) => [
      ...current,
      { id: nextId, author: "you", text },
    ]);
    setDraft("");
    setIsThinking(true);

    window.setTimeout(() => {
      setMessages((current) => [
        ...current,
        {
          id: nextId + 1,
          author: "mira",
          text:
            "听起来，你不是没有行动力，而是在用“再准备一下”避免被评价。如果今天不要求自己证明什么，只做一个能让明天轻一点的动作，你愿意先完成哪一步？",
        },
      ]);
      setIsThinking(false);
    }, 700);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="MindBridge home">
          <span className="brand-mark" aria-hidden="true">
            m
          </span>
          <span>mindbridge</span>
        </a>

        <nav className="primary-nav" aria-label="Primary navigation">
          <button
            className={activeView === "conversation" ? "active" : ""}
            onClick={() => setActiveView("conversation")}
          >
            <span aria-hidden="true">◌</span> Conversation
          </button>
          <button
            className={activeView === "reflection" ? "active" : ""}
            onClick={() => setActiveView("reflection")}
          >
            <span aria-hidden="true">◇</span> Reflection
          </button>
          <button onClick={() => setActiveView("reflection")}>
            <span aria-hidden="true">⌁</span> Patterns
          </button>
        </nav>

        <div className="memory-card">
          <span className="eyebrow">Your memory garden</span>
          <strong>{rememberedCount} moments remembered</strong>
          <p>You decide what stays. Delete or export it anytime.</p>
          <div className="memory-meter">
            <span />
          </div>
        </div>

        <div className="safety-note">
          <span aria-hidden="true">✦</span>
          <p>
            MindBridge supports reflection. It is not therapy, diagnosis, or
            emergency care.
          </p>
        </div>
      </aside>

      <section className="workspace" id="top">
        <header className="topbar">
          <div>
            <span className="eyebrow">Thursday · a quiet check-in</span>
            <h1>{activeView === "conversation" ? "Good morning, Cassie" : "Your reflection"}</h1>
          </div>
          <div className="top-actions">
            <button className="privacy-pill">Private by design</button>
            <button className="avatar" aria-label="Profile">
              C
            </button>
          </div>
        </header>

        {activeView === "conversation" ? (
          <div className="conversation-layout">
            <section className="chat-panel" aria-label="Reflection conversation">
              <div className="guide-intro">
                <div className="guide-orb" aria-hidden="true">
                  <span />
                </div>
                <div>
                  <span className="eyebrow">Mira · reflective companion</span>
                  <p>
                    I&apos;ll listen for the pattern underneath the moment—not
                    just agree with the first story.
                  </p>
                </div>
              </div>

              <div className="messages" aria-live="polite">
                {messages.map((message) => (
                  <div className={`message ${message.author}`} key={message.id}>
                    <span className="message-author">
                      {message.author === "you" ? "You" : "Mira"}
                    </span>
                    <p>{message.text}</p>
                    {message.author === "mira" && message.id === 2 && (
                      <span className="memory-cue">
                        ↳ connected to “performance pressure” · 7 days ago
                      </span>
                    )}
                  </div>
                ))}
                {isThinking && (
                  <div className="thinking" aria-label="Mira is reflecting">
                    <span />
                    <span />
                    <span />
                  </div>
                )}
              </div>

              <div className="prompt-row" aria-label="Suggested replies">
                {prompts.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => setDraft(prompt)}
                    type="button"
                  >
                    {prompt}
                  </button>
                ))}
              </div>

              <form className="composer" onSubmit={sendMessage}>
                <textarea
                  aria-label="Write what is on your mind"
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="Say what feels difficult to say out loud..."
                  rows={2}
                  value={draft}
                />
                <div className="composer-actions">
                  <span>Stored locally for this demo</span>
                  <button disabled={!draft.trim() || isThinking} type="submit">
                    Send
                  </button>
                </div>
              </form>
            </section>

            <aside className="insight-rail">
              <section className="checkin-card">
                <span className="eyebrow">Before we begin</span>
                <h2>How heavy does today feel?</h2>
                <div className="mood-scale" role="group" aria-label="Mood check-in">
                  {[1, 2, 3, 4, 5].map((value) => (
                    <button
                      className={checkIn === value ? "selected" : ""}
                      key={value}
                      onClick={() => setCheckIn(value)}
                      aria-label={`Mood level ${value}`}
                    >
                      {value}
                    </button>
                  ))}
                </div>
                <p className="scale-caption">
                  {checkIn <= 2
                    ? "There is room to breathe."
                    : checkIn === 3
                      ? "Some weight, some room."
                      : "Let’s make this moment smaller."}
                </p>
              </section>

              <button
                className="portrait-card"
                onClick={() => setActiveView("reflection")}
              >
                <span className="eyebrow">Today&apos;s inner landscape</span>
                <div className="landscape" aria-hidden="true">
                  <span className="sun" />
                  <span className="hill hill-one" />
                  <span className="hill hill-two" />
                  <span className="path" />
                </div>
                <strong>Between shelter and possibility</strong>
                <span>See the reflection →</span>
              </button>

              <section className="continuity-card">
                <span className="eyebrow">A thread worth returning to</span>
                <blockquote>
                  “I want the work to feel like an expression of me, not a test
                  of whether I deserve to be here.”
                </blockquote>
                <button onClick={() => setDraft("我想继续聊聊，为什么工作总像一种资格考试。")}>
                  Continue this thread
                </button>
              </section>
            </aside>
          </div>
        ) : (
          <Reflection onBack={() => setActiveView("conversation")} />
        )}
      </section>
    </main>
  );
}

function Reflection({ onBack }: { onBack: () => void }) {
  return (
    <div className="reflection-view">
      <button className="back-button" onClick={onBack}>
        ← Back to conversation
      </button>
      <div className="reflection-grid">
        <section className="reflection-hero">
          <span className="eyebrow">Generated from today&apos;s conversation</span>
          <h2>Between shelter and possibility</h2>
          <p>
            You are not avoiding the application itself. You are protecting the
            part of you that equates being evaluated with being reduced to a
            score.
          </p>
          <div className="large-landscape" aria-label="Abstract inner landscape">
            <span className="moon" />
            <span className="ridge ridge-one" />
            <span className="ridge ridge-two" />
            <span className="bridge" />
          </div>
        </section>

        <div className="reflection-notes">
          <section>
            <span className="eyebrow">Pattern noticed</span>
            <h3>Preparation becomes protection</h3>
            <p>
              When the outcome matters, polishing gives you control—but it also
              delays the moment someone else gets to respond.
            </p>
          </section>
          <section>
            <span className="eyebrow">A gentler reframe</span>
            <h3>Submission is information, not a verdict</h3>
            <p>
              The next application can be one data point in a longer process,
              not proof of your worth.
            </p>
          </section>
          <section className="practice-card">
            <span className="eyebrow">2-minute practice</span>
            <h3>Name the smallest complete action</h3>
            <ol>
              <li>Choose one role you already match.</li>
              <li>Allow one final ten-minute review.</li>
              <li>Submit before making another improvement.</li>
            </ol>
            <button onClick={onBack}>Talk through this practice</button>
          </section>
        </div>
      </div>
      <p className="demo-disclaimer">
        Demo content only. MindBridge is a reflective wellness concept, not a
        clinical service or substitute for professional care.
      </p>
    </div>
  );
}
