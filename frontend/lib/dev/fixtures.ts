/**
 * Dev fixtures for visual iteration on the dashboard without a live backend.
 * Mirrors the real `GameSessionResponse` shape from `lib/api/client.ts`.
 *
 * Used by `app/dev/dashboard/page.tsx`. Not loaded into the real run flow.
 */
import type {
  AgentPublic,
  EventPublic,
  GameSessionResponse,
  ProposalPublic,
  ResourcePublic,
} from "@/lib/api/client";

const AGENTS: AgentPublic[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Nadia Petrov",
    role: "Climate Scientist",
    archetype_quadrant: "CONSCIENCE",
    portrait_seed: "nadia-arctic-3",
    verbal_tic: "I keep coming back to —",
    health: 78,
    morale: 62,
    grievance: 4,
    is_alive: true,
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Marko Vance",
    role: "Commissar",
    archetype_quadrant: "OPERATOR",
    portrait_seed: "marko-arctic-1",
    verbal_tic: "Plain enough.",
    health: 41,
    morale: 70,
    grievance: 2,
    is_alive: true,
  },
  {
    id: "33333333-3333-3333-3333-333333333333",
    name: "Irena Kovac",
    role: "Medic",
    archetype_quadrant: "ACCOUNTANT",
    portrait_seed: "irena-arctic-7",
    verbal_tic: "Listen, please —",
    health: 84,
    morale: 28,
    grievance: 7,
    is_alive: true,
  },
  {
    id: "44444444-4444-4444-4444-444444444444",
    name: "Yuri Sato",
    role: "Radio Operator",
    archetype_quadrant: "CRUSADER",
    portrait_seed: "yuri-arctic-5",
    verbal_tic: "Give me two hours.",
    health: 90,
    morale: 80,
    grievance: 1,
    is_alive: true,
  },
];

const RESOURCES: ResourcePublic[] = [
  { id: "fuel", name: "fuel", icon: "▲", value: 58, max: 100, critical_threshold: 20 },
  { id: "food", name: "food", icon: "◆", value: 34, max: 100, critical_threshold: 25 },
  { id: "warmth", name: "warmth", icon: "✦", value: 50, max: 100, critical_threshold: 30 },
  { id: "medical", name: "medical", icon: "✚", value: 18, max: 100, critical_threshold: 25 },
  { id: "morale", name: "morale", icon: "♪", value: 55, max: 100, critical_threshold: 20 },
];

const EVENT: EventPublic = {
  id: "evt-heating-leak",
  category: "infrastructure",
  title: "The heating core has sprung a leak.",
  body: "Temperature is dropping 1.5°C per hour. You have fuel to run the backup generator for three hours, or six at reduced power that leaves the lab unheated overnight.",
  severity: 4,
  origin: "NATURAL",
  origin_seed_id: null,
};

const PROPOSALS: ProposalPublic[] = [
  {
    id: "prop-full-power",
    proposed_by_agent_id: AGENTS[1].id,
    flavor: "DOMINANT",
    name: "Full power, three hours",
    description:
      "Run the backup at full draw for three hours. Patch the core in that window or freeze.",
    effects_preview: ["fuel −12", "warmth +20", "morale −4"],
  },
  {
    id: "prop-infirmary",
    proposed_by_agent_id: AGENTS[2].id,
    flavor: "MORAL",
    name: "Infirmary first",
    description:
      "Reduced draw, six hours, infirmary kept at priority. Elena makes it through the night.",
    effects_preview: ["fuel −8", "warmth +6", "medical +10"],
  },
  {
    id: "prop-bypass",
    proposed_by_agent_id: AGENTS[3].id,
    flavor: "WILDCARD",
    name: "Bypass gamble",
    description:
      "Two hours of full power while Yuri rigs a partial bypass. If it holds, no fuel burn overnight.",
    effects_preview: ["fuel −8", "warmth +10", "20% catastrophic on fail"],
  },
];

export const DEV_GAME_STATE: GameSessionResponse = {
  game_id: "00000000-0000-0000-0000-0000000dec01",
  setting_id: "arctic-vorgrad",
  setting_name: "Vorgrad Station",
  max_turns: 10,
  current_turn: 5,
  legitimacy: 62,
  player_health: 88,
  agents: AGENTS,
  resources: RESOURCES,
  started_at: new Date(Date.now() - 1000 * 60 * 12).toISOString(),
  current_event: EVENT,
  turn_proposals: PROPOSALS,
  end_state: null,
};

export const DEV_DEBATE_LINES: { agent_id: string; text: string }[] = [
  {
    agent_id: AGENTS[0].id,
    text: "The lab samples represent eight months of work. I'd rather freeze three hours earlier than lose them.",
  },
  {
    agent_id: AGENTS[1].id,
    text: "Samples won't keep us warm. Full power. Full three hours. We use that time to patch the core.",
  },
  {
    agent_id: AGENTS[2].id,
    text: "Elena's lungs won't take a cold night. Reduced power, keep the infirmary at priority.",
  },
  {
    agent_id: AGENTS[3].id,
    text: "I can rig a partial bypass. Give me two hours of full power and I think I can get us through the night without burning the reserve.",
  },
];

export const DEV_EVENT_LOG = [
  { id: "1", turn: 2, text: "drought scare; rationing held." },
  { id: "2", turn: 3, text: "fuel theft, no perpetrator." },
  { id: "3", turn: 4, text: "heating core flickered, ignored." },
  { id: "4", turn: 5, text: "heating core leak — full breach." },
];
