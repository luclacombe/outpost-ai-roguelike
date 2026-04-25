export type EventLogEntry = {
  id: string;
  turn: number;
  text: string;
};

type Props = {
  entries: EventLogEntry[];
};

export function EventLog({ entries }: Props) {
  if (entries.length === 0) {
    return (
      <p className="font-terminal text-sm text-px-text-dim">
        no entries yet.
      </p>
    );
  }
  return (
    <ol className="flex flex-col gap-[2px]">
      {entries.map((e) => (
        <li
          key={e.id}
          className="flex gap-2 font-terminal text-base leading-tight text-px-text-dim"
        >
          <span className="shrink-0 text-px-accent">
            d{String(e.turn).padStart(2, "0")}
          </span>
          <span className="truncate" title={e.text}>
            {e.text}
          </span>
        </li>
      ))}
    </ol>
  );
}
