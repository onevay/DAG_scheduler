import { downloadText, safeFileName } from "../ui/download";
import { type Draft, parseDraft, serializeDraft } from "./draft";

/** Скачивание черновика как `<имя>.json`. */
export function downloadDraft(draft: Draft): void {
  downloadText(`${safeFileName(draft.name, "problem")}.json`, serializeDraft(draft), "application/json");
}

export async function readDraft(file: File): Promise<Draft> {
  return parseDraft(await file.text());
}
