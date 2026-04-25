import {
  type EndState,
  type PostMortem,
  type Setting,
} from "@/lib/api/client";
import { Portrait } from "@/components/agent/Portrait";

const END_STATE_LABEL: Record<EndState, string> = {
  ENDURED: "ENDURED",
  COLLAPSED: "COLLAPSED",
  OUSTED: "OUSTED",
  KILLED: "KILLED",
};

const END_STATE_TONE: Record<EndState, string> = {
  ENDURED: "text-px-ok",
  COLLAPSED: "text-px-warning",
  OUSTED: "text-px-warning",
  KILLED: "text-px-danger",
};

type Props = {
  data: PostMortem;
  setting: Setting;
};

export function PostMortemHeader({ data, setting }: Props) {
  return (
    <header className="flex flex-col gap-6 border-b border-px-border bg-px-bg px-6 py-8 md:px-10 md:py-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="font-display text-[10px] uppercase tracking-widest text-px-text-dim">
            POST-MORTEM · {data.setting_name}
          </span>
          <h1 className="font-heading text-2xl uppercase tracking-wide text-px-text">
            {END_STATE_LABEL[data.end_state]}
            <span
              className={`ml-3 font-display text-base ${END_STATE_TONE[data.end_state]}`}
            >
              ◼
            </span>
          </h1>
        </div>
        <dl className="flex flex-wrap gap-x-6 gap-y-2 font-heading text-[10px] uppercase tracking-wider text-px-text-dim">
          <div>
            <dt className="inline">Turns </dt>
            <dd className="ml-1 inline font-display text-px-text">
              {data.total_turns}/{data.max_turns}
            </dd>
          </div>
          <div>
            <dt className="inline">Legit </dt>
            <dd className="ml-1 inline font-display text-px-text">
              {data.final_legitimacy}
            </dd>
          </div>
          <div>
            <dt className="inline">Cost </dt>
            <dd className="ml-1 inline font-display text-px-text">
              {(data.cost_cents / 100).toFixed(2)}¢
            </dd>
          </div>
        </dl>
      </div>

      {data.opening_line ? (
        <p className="max-w-prose font-body text-base italic leading-relaxed text-px-text-dim">
          “{data.opening_line}”
        </p>
      ) : null}

      <ul className="flex flex-wrap gap-3">
        {data.agents.map((a) => {
          const aliveTone = a.is_alive
            ? "text-px-text"
            : "text-px-danger line-through decoration-px-danger";
          return (
            <li
              key={a.id}
              className="pixel-border flex items-center gap-3 bg-px-surface px-3 py-2"
            >
              <Portrait
                seed={a.portrait_seed}
                setting={setting}
                size={36}
                variant="thumb"
                alt=""
              />
              <div className="flex flex-col">
                <span
                  className={`font-heading text-sm uppercase tracking-wider ${aliveTone}`}
                >
                  {a.name}
                </span>
                <span className="font-heading text-[9px] uppercase tracking-wider text-px-text-dim">
                  {a.role}
                  {a.transformed ? (
                    <span className="ml-2 text-px-accent">· transformed</span>
                  ) : null}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </header>
  );
}
