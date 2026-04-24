import type { Citation } from "@/lib/api";

interface Props {
  citations: Citation[];
  activeRef?: number | null;
  onRefClick?: (ref: number) => void;
}

export default function CitationList({ citations, activeRef, onRefClick }: Props) {
  if (citations.length === 0) return null;

  return (
    <div className="mt-3 border-t border-gray-100 pt-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
        引用来源
      </p>
      <div className="flex flex-col gap-1.5">
        {citations.map((c) => (
          <div
            key={c.chunk_id}
            onClick={() => onRefClick?.(c.ref)}
            className={`flex cursor-pointer items-start gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
              activeRef === c.ref
                ? "bg-blue-50 ring-1 ring-blue-300"
                : "bg-gray-50 hover:bg-gray-100"
            }`}
          >
            <span
              className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-bold ${
                activeRef === c.ref
                  ? "bg-blue-600 text-white"
                  : "bg-blue-100 text-blue-700"
              }`}
            >
              {c.ref}
            </span>
            <div className="min-w-0">
              <p className="truncate font-medium text-gray-700">{c.source_file}</p>
              {c.section_path && (
                <p className="truncate text-xs text-gray-400">{c.section_path}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
