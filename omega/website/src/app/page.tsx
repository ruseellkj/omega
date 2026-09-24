import { Hero } from "@/components/home/hero";
import {
  BoundarySection,
  Claims,
  Closing,
  GetStarted,
  Loop,
  Numbers,
  Origin,
  Providers,
  Stack,
  Timeline,
} from "@/components/home/sections";

/**
 * Composition only.
 *
 * The order is the argument: what it is and how to get it, what it weighs, the
 * commands you will type, how it works, how it is split, what that bought, the
 * layers, the providers, where it stands, where it came from — and the install
 * box once more for whoever read that far. Each section owns its own markup and
 * data, so changing one cannot disturb another — and no section repeats the
 * shape of its neighbour.
 */
export default function Home() {
  return (
    <>
      <Hero />
      <Numbers />
      <GetStarted />
      <Loop />
      <BoundarySection />
      <Claims />
      <Stack />
      <Providers />
      <Timeline />
      <Origin />
      <Closing />
    </>
  );
}
