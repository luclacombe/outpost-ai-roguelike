import { Counterfactuals } from "@/components/postmortem/Counterfactuals";
import { PostMortemHeader } from "@/components/postmortem/PostMortemHeader";
import { Timeline } from "@/components/postmortem/Timeline";
import { type PostMortem, settingFromId } from "@/lib/api/client";

type Props = {
  data: PostMortem;
};

/**
 * Top-level post-mortem layout. Composes header, timeline, counterfactuals,
 * and the closing line. Used by both `/dev/post-mortem` (server-loaded
 * fixture) and the production `/post-mortem/[id]` route.
 */
export function PostMortemView({ data }: Props) {
  const setting = settingFromId(data.setting_id);
  return (
    <div
      data-setting={setting}
      className="flex min-h-svh w-full flex-col bg-px-bg text-px-text"
    >
      <PostMortemHeader data={data} setting={setting} />
      <Timeline data={data} setting={setting} />
      <Counterfactuals data={data} />
      {data.closing_line ? (
        <footer className="border-t border-px-border bg-px-surface px-6 py-10 md:px-10 md:py-12">
          <p className="mx-auto max-w-prose font-body text-lg italic leading-relaxed text-px-text">
            “{data.closing_line}”
          </p>
        </footer>
      ) : null}
    </div>
  );
}
