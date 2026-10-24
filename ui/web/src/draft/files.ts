import { type Draft, parseDraft, serializeDraft } from "./draft";

/** Скачивание черновика как `<имя>.json`. */
export function downloadDraft(draft: Draft): void {
  const url = URL.createObjectURL(new Blob([serializeDraft(draft)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${fileName(draft.name)}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

export async function readDraft(file: File): Promise<Draft> {
  return parseDraft(await file.text());
}

function fileName(name: string | null | undefined): string {
  const safe = (name ?? "").trim().replace(/[^\p{L}\p{N}_.-]+/gu, "_");
  return safe || "problem";
}
