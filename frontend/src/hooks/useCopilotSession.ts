"use client";

import { signOut } from "next-auth/react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { SetupChoice } from "@/components/SessionSetup";
import { AuthError, createApi, WS_URL } from "@/lib/api";
import { CaptureError, CaptureHandle, Channel, startCapture } from "@/lib/audioCapture";

export type Speaker = "interviewer" | "candidate";
export type Line = { id: number; speaker: Speaker; text: string };
export type Status = "idle" | "connecting" | "live" | "reconnecting";
export type Timings = {
  detect_ms: number | null;
  retrieval_ms?: number | null;
  first_token_ms: number | null;
  total_ms: number | null;
};
export type QA = {
  id: string;
  question: string;
  category: string;
  answer: string;
  streaming: boolean;
  stopped: boolean;
  timings?: Timings;
  unverified?: string[];
};
export type Notice = { kind: "error" | "warning" | "info"; text: string; code?: string };
export type Using = { cv: string; jd: string | null; type: string };

/** After a dropped connection: wait this long before each new attempt (about 30 s in total). */
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];
/** Close codes that end the session for good: not signed in, plan limit reached. */
const FINAL_CLOSE_CODES = new Set([4401, 4402]);

/** All live-session state and the connection to the backend (audio capture, WebSocket, messages).
 *  If the connection drops mid-interview (Wi-Fi blip, server restart), it reconnects by itself and
 *  continues the same interview: same transcript, same history record. */
export function useCopilotSession(token: string | undefined) {
  const [status, setStatus] = useState<Status>("idle");
  const [paused, setPaused] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [partial, setPartial] = useState<Partial<Record<Speaker, string>>>({});
  const [qas, setQas] = useState<QA[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [using, setUsing] = useState<Using | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const tokenRef = useRef(token);
  tokenRef.current = token;
  const wsRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<CaptureHandle | null>(null);
  const readyRef = useRef(false);
  const pausedRef = useRef(false);
  const lineId = useRef(0);
  const activeRef = useRef(false); // between Start and End (stays true while reconnecting)
  const setupRef = useRef<SetupChoice | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const attemptRef = useRef(0);
  const retryTimer = useRef<number | null>(null);
  const infoTimer = useRef<number | null>(null);

  const updateQa = useCallback((id: string, fn: (qa: QA) => QA) => {
    setQas((prev) => prev.map((qa) => (qa.id === id ? fn(qa) : qa)));
  }, []);

  const end = useCallback(() => {
    activeRef.current = false;
    if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
    retryTimer.current = null;
    readyRef.current = false;
    pausedRef.current = false;
    setPaused(false);
    captureRef.current?.stop();
    captureRef.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "end" }));
      ws.close();
    } else if (ws) {
      ws.close();
    }
    setPartial({});
    setQas((prev) => prev.map((qa) => ({ ...qa, streaming: false })));
    setStatus("idle");
  }, []);

  const send = useCallback((payload: object) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }, []);

  const sendChunk = useCallback((channel: Channel, pcm: ArrayBuffer) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !readyRef.current) return;
    const frame = new Uint8Array(pcm.byteLength + 1);
    frame[0] = channel;
    // While paused, send silence so the transcription session stays open but hears nothing.
    if (!pausedRef.current) frame.set(new Uint8Array(pcm), 1);
    ws.send(frame);
  }, []);

  const showInfo = useCallback((text: string) => {
    setNotice({ kind: "info", text });
    if (infoTimer.current !== null) window.clearTimeout(infoTimer.current);
    infoTimer.current = window.setTimeout(
      () => setNotice((n) => (n?.kind === "info" && n.text === text ? null : n)),
      5000,
    );
  }, []);

  // Assigned below: connect and reconnect call each other.
  const reconnectRef = useRef<() => void>(() => undefined);

  const connect = useCallback(
    async (resume: string | null) => {
      const setup = setupRef.current;
      const capture = captureRef.current;
      if (!setup || !capture || !activeRef.current) return;
      // A single-use ticket, so the access token never appears in a URL (and server logs).
      const { ticket } = await createApi(tokenRef.current).sessionTicket();
      if (!activeRef.current) return;
      const ws = new WebSocket(`${WS_URL}?ticket=${encodeURIComponent(ticket)}`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () =>
        ws.send(JSON.stringify({
          type: "start",
          sample_rate: capture.sampleRate,
          cv_id: setup.cvId,
          jd_id: setup.jdId,
          session_type: setup.sessionType,
          resume,
        }));
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data as string);
        switch (msg.type) {
          case "ready":
            readyRef.current = true;
            attemptRef.current = 0;
            setUsing({ cv: msg.cv, jd: msg.jd, type: setup.sessionType });
            setRemaining(msg.remaining_seconds ?? null);
            sessionIdRef.current = msg.session_id ?? null;
            setSessionId(msg.session_id ?? null);
            setStatus("live");
            if (resume) {
              showInfo(msg.resumed
                ? "Reconnected. Words spoken while offline were not captured."
                : "Reconnected. Continuing as a new interview in your history.");
            }
            break;
          case "transcript": {
            const speaker = msg.speaker as Speaker;
            if (msg.is_final) {
              setLines((prev) => [...prev, { id: ++lineId.current, speaker, text: msg.text }]);
              setPartial((p) => ({ ...p, [speaker]: undefined }));
            } else {
              setPartial((p) => ({ ...p, [speaker]: msg.text }));
            }
            break;
          }
          case "question":
            // A narrowed follow-up ("...tell me about a time you convinced someone") replaces the
            // broader question asked a moment earlier in the same breath.
            setQas((prev) => [
              ...prev.filter((qa) => qa.id !== msg.replaces).map((qa) => ({ ...qa, streaming: false })),
              { id: msg.id, question: msg.text, category: msg.category, answer: "", streaming: false, stopped: false },
            ]);
            break;
          case "answer_start":
            updateQa(msg.question_id, (qa) => ({
              ...qa, answer: "", streaming: true, stopped: false, timings: undefined, unverified: [],
            }));
            break;
          case "token":
            updateQa(msg.question_id, (qa) => ({ ...qa, answer: qa.answer + msg.text }));
            break;
          case "answer_done":
            updateQa(msg.question_id, (qa) => ({
              ...qa, streaming: false, stopped: msg.stopped, timings: msg.timings, unverified: msg.unverified ?? [],
            }));
            break;
          case "usage":
            setRemaining(msg.remaining_seconds ?? null);
            break;
          case "info":
            showInfo(msg.message);
            break;
          case "warning":
            setNotice({ kind: "warning", text: msg.message });
            break;
          case "error":
            setNotice({ kind: "error", text: msg.message, code: msg.code });
            if (msg.code === "auth") void signOut({ callbackUrl: "/login?expired=1" });
            // A failure before the session is ready, or the plan limit, ends the session.
            if (!readyRef.current || msg.code === "limit") end();
            break;
        }
      };
      ws.onclose = (e) => {
        if (wsRef.current !== ws) return; // an old socket, already replaced
        wsRef.current = null;
        const wasReady = readyRef.current;
        readyRef.current = false;
        if (!activeRef.current) return; // ended on purpose
        // Normal close by the server, sign-in or plan problems: the session is over.
        // Anything else (network drop, server restart) mid-interview: reconnect and continue.
        if (FINAL_CLOSE_CODES.has(e.code) || e.code === 1000 || (!wasReady && !resume)) {
          if (!wasReady && !resume && e.code !== 1000 && !FINAL_CLOSE_CODES.has(e.code)) {
            setNotice({ kind: "error", text: "Couldn't connect to the server. Check your internet connection and try again." });
          }
          end();
          return;
        }
        reconnectRef.current();
      };
    },
    [end, showInfo, updateQa],
  );

  const reconnect = useCallback(() => {
    if (!activeRef.current) return;
    const attempt = attemptRef.current;
    if (attempt >= RETRY_DELAYS_MS.length) {
      end();
      setNotice({
        kind: "error",
        text: "The connection was lost and couldn't be restored. Check your internet connection, then start again. "
          + "Everything up to the drop is saved in Dashboard.",
      });
      return;
    }
    attemptRef.current = attempt + 1;
    setStatus("reconnecting");
    setPartial({});
    setQas((prev) => prev.map((qa) => ({ ...qa, streaming: false })));
    setNotice({ kind: "warning", text: "Connection lost. Reconnecting..." });
    retryTimer.current = window.setTimeout(() => {
      retryTimer.current = null;
      connect(sessionIdRef.current).catch((e) => {
        if (e instanceof AuthError) {
          end();
          setNotice({ kind: "error", text: "Your session has expired. Sign in again to continue.", code: "auth" });
          void signOut({ callbackUrl: "/login?expired=1" });
          return;
        }
        reconnect(); // still offline: try again after the next delay
      });
    }, RETRY_DELAYS_MS[attempt]);
  }, [connect, end]);
  reconnectRef.current = reconnect;

  const start = useCallback(
    async (setup: SetupChoice) => {
      setNotice(null);
      setLines([]);
      setPartial({});
      setQas([]);
      setUsing(null);
      setSessionId(null);
      sessionIdRef.current = null;
      attemptRef.current = 0;
      setupRef.current = setup;
      setStatus("connecting");
      let capture: CaptureHandle;
      try {
        capture = await startCapture(sendChunk, end);
      } catch (e) {
        setNotice({ kind: "error", text: e instanceof CaptureError ? e.message : String(e) });
        setStatus("idle");
        return;
      }
      captureRef.current = capture;
      activeRef.current = true;
      if (!capture.hasMic) {
        setNotice({ kind: "warning", text: "Microphone not available: only the interviewer will be transcribed." });
      }
      try {
        await connect(null);
      } catch (e) {
        end();
        if (e instanceof AuthError) {
          setNotice({ kind: "error", text: "Your session has expired. Sign in again to continue.", code: "auth" });
          void signOut({ callbackUrl: "/login?expired=1" });
        } else {
          setNotice({ kind: "error", text: e instanceof Error ? e.message : String(e) });
        }
      }
    },
    [connect, end, sendChunk],
  );

  const togglePause = useCallback(() => {
    pausedRef.current = !pausedRef.current;
    setPaused(pausedRef.current);
  }, []);

  useEffect(() => () => {
    end();
    if (infoTimer.current !== null) window.clearTimeout(infoTimer.current);
  }, [end]);

  return {
    status, paused, lines, partial, qas, notice, using, remaining, sessionId,
    start, end, togglePause, setNotice,
    regenerate: () => send({ type: "regenerate" }),
    stopAnswer: () => send({ type: "stop" }),
  };
}
