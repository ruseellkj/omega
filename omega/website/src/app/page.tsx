import { Hero } from "@/components/home/hero";
import {
  Architecture,
  Closing,
  Features,
  Loop,
  Proof,
  Story,
} from "@/components/home/sections";

/**
 * Composition only — and the order is the design.
 *
 * ## One question per section, in the order a first visit asks them
 *
 * 1. **What is it, and how do I get it?** — the hero, alone in the first fold:
 *    one headline across the width, one sentence, one install command.
 * 2. **What does using it look like?** — the loop's four steps beside one real
 *    turn that performs them.
 * 3. **What does it do for me?** — Features: six capabilities, each with the
 *    command that reaches it.
 * 4. **How is it built?** — three packages and the one rule between them.
 * 5. **Should I believe it?** — the measurements, each with what it proves.
 * 6. **Where is it going, where did it come from?** — the tiers, and Pi and Tau.
 * 7. **Ready?** — the install command again, and the docs.
 *
 * Use before internals: a visitor decides whether to read on in the first two
 * sections, so the architecture — the most interesting part to the author and
 * the least urgent to a stranger — waits until they have a reason to care.
 *
 * Show, then list: the demo comes before the feature list because one real
 * turn makes six feature names concrete, and a list read first is six claims.
 *
 * ## The repetition rule
 *
 * Repeat a thing only where a reader arrives at a new decision, and state
 * everything else exactly once.
 *
 * - **The install command** — twice here (the hero, where a visitor first
 *   decides; the close, where a reader finishes) and once on /docs, which is
 *   reference. Never mid-page: a CTA inside an explanation interrupts it.
 * - **Commands and flags** — named inline where they explain a feature; listed
 *   in full only on /docs, the one place a reader goes to look one up.
 * - **Figures** — once each. "The loop is 190 lines" appears in the hero as the
 *   claim and in Proof as the evidence: claim first, backing second, on purpose.
 * - **Architecture** — one framing (packages), one terminal. The page used to
 *   show the same system three ways, which read as three different systems.
 * - **Terminal windows** — two, each half of a two-sided section: the turn
 *   beside the steps, the design split beside the argument. They sit on
 *   opposite sides so the page alternates rather than repeats. The install
 *   command is a pill, not a window, and the hero holds no window at all.
 *
 * Every section closes with the same small link to where its detail lives, so
 * "read more" always looks the same and the page itself stays short.
 */
export default function Home() {
  return (
    <>
      <Hero />
      <Loop />
      <Features />
      <Architecture />
      <Proof />
      <Story />
      <Closing />
    </>
  );
}
