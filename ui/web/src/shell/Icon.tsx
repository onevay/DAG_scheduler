// Контурные иконки 24×24 из макета; цвет — currentColor.
const PATHS = {
  logo: "M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5",
  home: "M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z",
  builder:
    "M7.5 8l3.3 7.8M16.5 8l-3.3 7.8M8.5 6a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zM20.5 6a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zM14.5 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
  cluster:
    "M6 4h12a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM6 14h12a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a2 2 0 0 1 2-2zM8 7h.01M8 17h.01",
  run: "M4 6h9M8 12h11M4 18h7",
  compare: "M5 20V10M12 20V4M19 20v-7",
  explain:
    "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z",
  containers: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9",
  system: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  fit: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
