"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Hint, Label, Notice, Select, Textarea } from "@/components/ui/Field";
import { DocKind, DocSummary, useApi } from "@/lib/api";

export type SessionType = "mixed" | "behavioral" | "technical";
export type SetupChoice = { cvId: string | null; jdId: string | null; sessionType: SessionType };

const STORAGE_KEY = "copilot.setup";

// Remembering the last choice is a per-browser convenience only; the server stays the source of truth.
const JD_NONE_KEY = "copilot.jdNone"; // set when the user picks "None" for the job description on purpose

function loadSaved(): SetupChoice {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { cvId: null, jdId: null, sessionType: "mixed", ...JSON.parse(raw) };
  } catch {
    /* storage unavailable */
  }
  return { cvId: null, jdId: null, sessionType: "mixed" };
}

function jdNoneChosen(): boolean {
  try {
    return localStorage.getItem(JD_NONE_KEY) === "1";
  } catch {
    return false;
  }
}

function setJdNoneChosen(none: boolean) {
  try {
    if (none) localStorage.setItem(JD_NONE_KEY, "1");
    else localStorage.removeItem(JD_NONE_KEY);
  } catch {
    /* storage unavailable */
  }
}

function save(choice: SetupChoice) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choice));
  } catch {
    /* storage unavailable */
  }
}

export function SessionSetup({ onChange }: { onChange: (c: SetupChoice) => void }) {
  const { api, token, onError } = useApi();
  const [docs, setDocs] = useState<DocSummary[]>([]);
  const [choice, setChoice] = useState<SetupChoice>({ cvId: null, jdId: null, sessionType: "mixed" });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jdDraft, setJdDraft] = useState("");
  const [showJdBox, setShowJdBox] = useState(false);
  const cvInput = useRef<HTMLInputElement>(null);
  const jdInput = useRef<HTMLInputElement>(null);

  const update = useCallback((next: SetupChoice) => {
    setChoice(next);
    save(next);
    onChange(next);
  }, [onChange]);

  const refresh = useCallback(async (prefer?: Partial<SetupChoice>) => {
    if (!token) return;
    try {
      const list = await api.listDocuments();
      setDocs(list);
      setError(null);
      const saved = { ...loadSaved(), ...prefer };
      const exists = (id: string | null, kind: DocKind) => !!id && list.some((d) => d.id === id && d.kind === kind);
      // The list is newest first. Without a valid earlier choice, use the newest CV, and the newest job
      // description unless "None" was chosen on purpose.
      const newest = (kind: DocKind) => list.find((d) => d.kind === kind)?.id ?? null;
      update({
        sessionType: saved.sessionType,
        cvId: exists(saved.cvId, "cv") ? saved.cvId : newest("cv"),
        jdId: exists(saved.jdId, "jd") ? saved.jdId : (jdNoneChosen() ? null : newest("jd")),
      });
    } catch (e) {
      setError(onError(e));
      update({ sessionType: loadSaved().sessionType, cvId: null, jdId: null });
    }
  }, [api, token, onError, update]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(onError(e));
    } finally {
      setBusy(null);
    }
  };

  const onUpload = (kind: DocKind) => async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    await run(`Reading ${file.name}...`, async () => {
      const doc = await api.uploadDocument(kind, file);
      if (kind === "jd") setJdNoneChosen(false);
      await refresh(kind === "cv" ? { ...choice, cvId: doc.id } : { ...choice, jdId: doc.id });
    });
  };

  const onSaveJd = () =>
    run("Saving job description...", async () => {
      const doc = await api.saveText("jd", jdDraft);
      setJdNoneChosen(false);
      setJdDraft("");
      setShowJdBox(false);
      await refresh({ ...choice, jdId: doc.id });
    });

  const onDelete = (id: string) =>
    run("Deleting...", async () => {
      await api.deleteDocument(id);
      await refresh(choice);
    });

  const cvs = docs.filter((d) => d.kind === "cv");
  const jds = docs.filter((d) => d.kind === "jd");
  const selectedCv = cvs.find((d) => d.id === choice.cvId);
  const selectedJd = jds.find((d) => d.id === choice.jdId);
  const link = "text-sm text-muted underline underline-offset-4 hover:text-text disabled:opacity-50";

  return (
    <section aria-label="Interview setup" className="rounded-panel border border-line p-4 sm:p-5">
      <div className="grid gap-4 md:grid-cols-[1.2fr_1.2fr_0.8fr]">
        <Label htmlFor="cv-select">
          Your CV
          <Select id="cv-select" value={choice.cvId ?? ""} disabled={cvs.length === 0}
                  onChange={(e) => update({ ...choice, cvId: e.target.value || null })}>
            {cvs.length === 0 && <option value="">No CV yet: upload one to start</option>}
            {cvs.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </Select>
          <span className="flex gap-4 font-normal">
            <button type="button" className={link} disabled={!!busy} onClick={() => cvInput.current?.click()}>
              Upload a CV (PDF or Word)
            </button>
            {selectedCv && (
              <button type="button" className={link} disabled={!!busy} onClick={() => onDelete(selectedCv.id)}>
                Delete
              </button>
            )}
          </span>
        </Label>
        <input ref={cvInput} type="file" accept=".pdf,.docx,.txt,.md" hidden onChange={onUpload("cv")} />

        <Label htmlFor="jd-select">
          Job description
          <Select id="jd-select" value={choice.jdId ?? ""}
                  onChange={(e) => {
                    setJdNoneChosen(!e.target.value);
                    update({ ...choice, jdId: e.target.value || null });
                  }}>
            <option value="">None</option>
            {jds.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </Select>
          <span className="flex gap-4 font-normal">
            <button type="button" className={link} disabled={!!busy} onClick={() => setShowJdBox((v) => !v)}>Paste</button>
            <button type="button" className={link} disabled={!!busy} onClick={() => jdInput.current?.click()}>Upload</button>
            {selectedJd && (
              <button type="button" className={link} disabled={!!busy} onClick={() => onDelete(selectedJd.id)}>
                Delete
              </button>
            )}
          </span>
        </Label>
        <input ref={jdInput} type="file" accept=".pdf,.docx,.txt,.md" hidden onChange={onUpload("jd")} />

        <Label htmlFor="type-select">
          Interview type
          <Select id="type-select" value={choice.sessionType}
                  onChange={(e) => update({ ...choice, sessionType: e.target.value as SessionType })}>
            <option value="mixed">Mixed</option>
            <option value="behavioral">Behavioral</option>
            <option value="technical">Technical</option>
          </Select>
        </Label>
      </div>

      {showJdBox && (
        <div className="mt-4 flex flex-col gap-3">
          <Textarea aria-label="Job description" placeholder="Paste the job description here"
                    value={jdDraft} onChange={(e) => setJdDraft(e.target.value)} />
          <div className="flex items-center gap-3">
            <Button disabled={!!busy || jdDraft.trim().length < 50} onClick={onSaveJd}>Save job description</Button>
            <Button variant="ghost" onClick={() => setShowJdBox(false)}>Cancel</Button>
            {jdDraft.trim().length > 0 && jdDraft.trim().length < 50 && <Hint>Paste at least 50 characters.</Hint>}
          </div>
        </div>
      )}

      {error && <Notice className="mt-4">{error}</Notice>}
      {busy && !error && <p className="mt-3 text-sm text-muted" role="status">{busy}</p>}
      {selectedCv && !busy && !error && (
        <p className="mt-3 truncate text-xs text-muted">
          Read {selectedCv.chars.toLocaleString()} characters from {selectedCv.title}: {selectedCv.preview.slice(0, 120)}
        </p>
      )}
    </section>
  );
}
