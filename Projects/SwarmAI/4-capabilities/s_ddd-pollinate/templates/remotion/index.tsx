/**
 * Remotion entry point — registers the root component.
 * This is the file that should be passed to `npx remotion render`.
 *
 * Usage:
 *   npx remotion render index.tsx MainVideo --output out/video.mp4 --public-dir <content>/video
 *   npx remotion studio index.tsx --public-dir <content>/video
 */
import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root";

registerRoot(RemotionRoot);
