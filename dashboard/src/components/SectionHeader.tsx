import type { ReactNode } from 'react';

export function SectionHeader({
  title, caption, right,
}: {
  title: string;
  caption?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h2>
        {caption && <div className="mt-1 text-sm text-fg-muted">{caption}</div>}
      </div>
      {right && <div className="text-xs text-fg-subtle">{right}</div>}
    </div>
  );
}
