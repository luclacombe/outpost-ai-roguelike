import { TurnCard } from "@/components/postmortem/TurnCard";
import {
  type PostMortem,
  type Setting,
} from "@/lib/api/client";

type Props = {
  data: PostMortem;
  setting: Setting;
};

export function Timeline({ data, setting }: Props) {
  return (
    <section className="flex flex-col gap-0 px-6 py-8 md:px-10">
      <h2 className="mb-6 font-display text-[10px] uppercase tracking-widest text-px-accent">
        TIMELINE
      </h2>
      <div className="flex flex-col">
        {data.turns.map((turn) => (
          <TurnCard
            key={turn.turn_number}
            turn={turn}
            agents={data.agents}
            setting={setting}
          />
        ))}
      </div>
    </section>
  );
}
