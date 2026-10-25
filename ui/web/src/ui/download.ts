/** Отдаёт браузеру текст как файл для сохранения. */
export function downloadText(fileName: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

/** Имя файла из произвольной строки: буквы, цифры, `_ . -`; пустое — `fallback`. */
export function safeFileName(name: string | null | undefined, fallback: string): string {
  return (name ?? "").trim().replace(/[^\p{L}\p{N}_.-]+/gu, "_") || fallback;
}
