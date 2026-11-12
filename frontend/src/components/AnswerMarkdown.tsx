import ReactMarkdown from "react-markdown";

// Answers are read at a glance: bold lines get the highlighter, bullets stay compact.
export function AnswerMarkdown({ text }: { text: string }) {
  return (
    <div className="answer">
    <ReactMarkdown
      components={{
        p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="mb-3 flex list-disc flex-col gap-1.5 pl-5 last:mb-0">{children}</ul>,
        ol: ({ children }) => <ol className="mb-3 flex list-decimal flex-col gap-1.5 pl-5 last:mb-0">{children}</ol>,
        strong: ({ children }) => <strong className="highlight font-bold">{children}</strong>,
        pre: ({ children }) => (
          <pre className="mb-3 overflow-x-auto rounded-control bg-paper-ink p-3 text-sm text-paper">{children}</pre>
        ),
        code: ({ children }) => <code className="font-mono text-[0.9em]">{children}</code>,
      }}
    >
      {text}
    </ReactMarkdown>
    </div>
  );
}
